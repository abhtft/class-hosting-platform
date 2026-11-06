"""Knowledge API Lambda: what a student's backend talks to when the teacher has switched class knowledge on (see knowledge.py).

  student function --HTTPS, Authorization: Bearer ks_<slug>.<secret>--> API Gateway (own throttle) --> this handler

  GET    /v1/collections                                  your collections and the class library's
  POST   /v1/collections                                  body {"name", "title"?}
  DELETE /v1/collections/{name}
  GET    /v1/collections/{name}/documents                 each with state: uploading | queued | processing | ready | failed
  POST   /v1/collections/{name}/documents                 body {"filename", "size"} -> {"doc_id", "url", "fields"} (a size-limited upload form)
  POST   /v1/collections/{name}/documents/{id}/start      after the bytes were sent to the upload form: reads the file in the background
  DELETE /v1/collections/{name}/documents/{id}
  POST   /v1/search                                       body {"query", "collections"?, "k"?} -> passages with page, section, score and `confident`
  GET    /v1/usage

The class library is read-only here (its books are added by the teacher). Every error is JSON: {"error": "<code>", "message": "..."} with codes bad_key,
token_inactive, not_enabled, allowance_used, class_budget, quota_exceeded, too_large, unsupported, not_found, bad_state, busy, bad_request, not_ready.
"""
import json
import re
import time

import embeddings as em
import knowledge
import knowledge_ingest as ki
import knowledge_store as ks
import portal
import trace_core
import traces
from knowledge import ApiError

COLLECTIONS = re.compile(r"^/v1/collections$")
COLLECTION = re.compile(r"^/v1/collections/([^/]+)$")
DOCUMENTS = re.compile(r"^/v1/collections/([^/]+)/documents$")
DOCUMENT = re.compile(r"^/v1/collections/([^/]+)/documents/([^/]+)$")
START = re.compile(r"^/v1/collections/([^/]+)/documents/([^/]+)/start$")
MAX_BODY_CHARS = 20_000


def reply(status, **body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf8", "replace")
    if len(raw) > MAX_BODY_CHARS:
        raise ApiError(413, "too_large", "The request is too large.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise ApiError(400, "bad_request", "The request body must be JSON.")
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "The request body must be a JSON object.")
    return body


def trace_search(item, trace_id, body, result, started, now):
    """One "retrieval" step in the answer's trace: the question, which shelves, which passages and why they were (not) trusted."""
    hits = result.get("hits") or []
    traces.record(item, trace_id, "retrieval", "retriever", {
        "query": body.get("query"), "k": body.get("k", 5), "collections": result.get("collections"), "confident": result.get("confident"),
        "hit_count": len(hits), "embed_tokens": result.get("tokens"),
        "hits": [{key: h.get(key) for key in ("collection", "doc", "page", "section", "score", "vector", "bm25", "coverage")} for h in hits],
        "passages": [h.get("text") for h in hits]},
        ts_ms=int(started * 1000), dur_ms=int((time.time() - started) * 1000), tokens=int(result.get("tokens") or 0), now=now)


MAX_APP_SPANS = 30


def record_app_spans(item, body, now):
    """Steps the student's own app reports for an answer (prompt, answer, agent steps). They join the platform's steps by the trace id.
    The student's slug comes from the key, never from the body. Never an error for the app: tracing off or full only means nothing is kept."""
    trace_id = body.get("trace_id")
    spans = body.get("spans")
    if not trace_core.valid_trace_id(trace_id) or not isinstance(spans, list):
        raise ApiError(400, "bad_request", "Send {trace_id: 'tr_...', spans: [...]}.")
    kept = 0
    for span in spans[:MAX_APP_SPANS]:
        if not isinstance(span, dict):
            continue
        ms = span.get("ts")
        ok = traces.record(item, trace_id, str(span.get("name") or "step"), span.get("kind", "other"), span.get("attrs") if isinstance(span.get("attrs"), dict) else {},
                           ts_ms=ms if isinstance(ms, int) and not isinstance(ms, bool) and ms > 0 else None, dur_ms=span.get("dur_ms") if isinstance(span.get("dur_ms"), int) else 0,
                           status=span.get("status", "ok"), now=now, source="app")
        kept += 1 if ok else 0
    return {"trace_id": trace_id, "recorded": kept}


def handler(event, context=None, now=None):
    now = int(now if now is not None else time.time())
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"].rstrip("/") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    try:
        item = knowledge.authenticate(headers, now)
        owner = knowledge.student_owner(item)
        if COLLECTIONS.match(path):
            if method == "GET":
                return reply(200, collections=knowledge.list_collections(owner, now), library=knowledge.list_collections(knowledge.library_owner(), now))
            if method == "POST":
                body = parse_body(event)
                return reply(201, **knowledge.create_collection(owner, body.get("name"), str(body.get("title") or "")[:100], now))
        match = COLLECTION.match(path)
        if match and method == "DELETE":
            return reply(200, **knowledge.delete_collection(owner, match.group(1), now))
        match = START.match(path)
        if match and method == "POST":
            return reply(200, **knowledge.start(owner, match.group(1), match.group(2), now))
        match = DOCUMENTS.match(path)
        if match and method == "GET":
            return reply(200, documents=[{k: v for k, v in d.items() if k != "reserved"} for d in knowledge.documents(owner, match.group(1), now)])
        if match and method == "POST":
            body = parse_body(event)
            return reply(200, **knowledge.upload_url(owner, match.group(1), body.get("filename"), body.get("size"), now, body.get("sha256")))
        match = DOCUMENT.match(path)
        if match and method == "DELETE":
            return reply(200, **knowledge.delete_document(owner, match.group(1), match.group(2), now))
        if (method, path) == ("POST", "/v1/search"):
            trace_id = trace_core.trace_id_or_new(headers.get("x-trace-id")) if traces.available() else None
            started, body = time.time(), parse_body(event)
            result = knowledge.search(item, body, now)
            if trace_id:
                trace_search(item, trace_id, body, result, started, now)
            out = reply(200, **result)
            if trace_id:
                out["headers"]["x-trace-id"] = trace_id
            return out
        if (method, path) == ("POST", "/v1/trace"):
            return reply(200, **record_app_spans(item, parse_body(event), now))
        if (method, path) == ("GET", "/v1/usage"):
            return reply(200, **knowledge.view(item, now))
        raise ApiError(404, "not_found", "Unknown route.")
    except ApiError as e:
        return reply(e.status, error=e.code, message=str(e))
    except ks.StoreError as e:
        return reply(e.status, error=e.code, message=str(e))
    except ki.IngestError as e:
        return reply(e.status, error=e.code, message=str(e))
    except em.EmbeddingError as e:
        return reply(429 if e.status == 429 else 503 if e.status == 503 else 502, error="embedding_failed", message=f"The question could not be embedded: {e}")
    except portal.PortalError as e:
        return reply(e.status, error="error", message=str(e))
