"""Class knowledge connector: the teacher can give a student's backend a private book shelf it can search, and the class one shared library of books.

State on the token item (item["knowledge"]):
  teacher:  enabled, collections, documents, passages, stored_mb, upload_mb, searches_limit, searches_period, embed_tokens
  platform: key (ks_<slug>.<secret>), the student's own access key

What the student's function receives (only while the connector is on): CLASS_KNOWLEDGE_URL and CLASS_KNOWLEDGE_KEY. The helper in the student kit
(class_knowledge.py / class_knowledge.mjs) turns them into search / ask / add-document calls. The key is only good for THAT student: the API works out the
owner from the key and builds every storage path itself. The class library (scope "class") is read-only for students; only the teacher adds books to it.

Everything heavy runs in platform code: reading a PDF, cutting passages and embedding them happens in the worker (knowledge_worker.py, invoked
asynchronously), searching in the API function. Nothing here scans a table: collections are listed from storage folders, counters are single atomic
items, the embedding cache is single-item reads and writes.

Cost control, in layers: per student a monthly embedding-token budget, per-student caps on documents, passages and stored bytes, a searches allowance, a
size cap per upload, and ONE class-wide monthly spending ceiling (KNOWLEDGE_MONTHLY_CAP_USD) that stops all new book processing when reached.
"""
import json
import os
import re
import secrets
import time

import backends
import embeddings as em
import knowledge_core as kc
import knowledge_ingest as ki
import knowledge_store as ks
import portal
from botocore.exceptions import ClientError
from portal import DAY, PortalError, db

ENV_NAMES = ("CLASS_KNOWLEDGE_URL", "CLASS_KNOWLEDGE_KEY")
PERIODS = ("day", "month", "total")
MB = 1024 * 1024
KEY_RE = re.compile(r"^ks_([a-z0-9][a-z0-9-]{0,29})\.([A-Za-z0-9_-]{20,})$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,99}$")
EXTENSIONS = ("pdf", "txt", "text", "md", "markdown", "html", "htm")
DEFAULTS = {"collections": 5, "documents": 20, "passages": 5000, "stored_mb": 50, "upload_mb": 25, "searches_limit": 500, "searches_period": "day", "embed_tokens": 2_000_000}
BOUNDS = {"collections": (1, 20), "documents": (1, 200), "passages": (100, 60000), "stored_mb": (1, 500), "upload_mb": (1, 50), "searches_limit": (0, 1_000_000),
          "embed_tokens": (0, 100_000_000)}
LIBRARY = {"collections": 20, "documents": 200, "passages": 60000, "stored_mb": 1000, "upload_mb": 50}  # the class library's own caps (teacher only)
MAX_SEARCH_COLLECTIONS, MAX_K, MAX_QUERY_CHARS = 4, 10, 500
UPLOAD_SECONDS = 600
STALE_UPLOAD_SECONDS = 3600     # an upload form nobody used is forgotten (and its document slot freed) after an hour
STALE_WORK_SECONDS = 1200       # work that has not finished long after the worker's own time limit is failed
CACHE_DAYS = 90


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


# ------------------------------------------------------------------ switches, environment

def available():
    return os.environ.get("KNOWLEDGE_ENABLED", "") == "true" and backends.available()


def api_url():
    return os.environ.get("KNOWLEDGE_URL", "").rstrip("/")


def worker_name():
    return os.environ.get("KNOWLEDGE_WORKER_FUNCTION", "")


def ceiling_micro():
    try:
        return int(float(os.environ.get("KNOWLEDGE_MONTHLY_CAP_USD", "3")) * 1_000_000)
    except ValueError:
        return 3_000_000


def embedding_settings():
    """The embedding model and vector size new collections are created with (the owner's choice; one per collection, stored with it)."""
    return os.environ.get("KNOWLEDGE_EMBEDDING_MODEL", em.DEFAULT_MODEL), int(os.environ.get("KNOWLEDGE_EMBEDDING_DIMS", em.DEFAULT_DIMS))


def settings(item):
    return dict(item.get("knowledge") or {})


def _save(item, **fields):
    return db().update(item["pk"], {"knowledge": {**settings(item), **fields}})


def is_on(item):
    s = settings(item)
    return bool(s.get("enabled") and s.get("key"))


def env_for(item):
    """The variables the student's function gets, or {} when the connector is off."""
    if not (available() and is_on(item) and api_url()):
        return {}
    return {"CLASS_KNOWLEDGE_URL": api_url(), "CLASS_KNOWLEDGE_KEY": settings(item)["key"]}


def reserved_name(item, name):
    return available() and is_on(item) and name in ENV_NAMES


def limits(item):
    s = settings(item)
    return {k: s.get(k, v) for k, v in DEFAULTS.items()}


# ------------------------------------------------------------------ owners: a student, or the class library

class Owner:
    def __init__(self, scope, name, caps, item=None):
        self.scope, self.name, self.caps, self.item = scope, name, caps, item

    def base(self, collection):
        return ks.base_of(self.scope, self.name, collection)

    def counter(self, what, *extra):
        return "#".join(("k" + what, self.scope, self.name, *extra))


def student_owner(item):
    return Owner("student", item["slug"], limits(item), item)


def library_owner():
    return Owner("class", "library", dict(LIBRARY))


# ------------------------------------------------------------------ storage handles (tests plug in a memory bucket)

_bucket = []


def use_bucket(bucket):
    _bucket[:] = [bucket]


def bucket():
    if not _bucket:
        _bucket.append(ks.S3Bucket())
    return _bucket[0]


def store(now=None):
    return ks.Store(bucket(), now=(lambda: now) if now is not None else time.time)


def _count(pk):
    return int((db().get(pk) or {}).get("count", 0))


def month_id(now):
    return backends.period_info("month", now)[0]


class DbCache:
    """The embedding cache in the tokens table: one small item per text hash (single-item reads and writes, never a scan), forgotten after 90 days."""

    def __init__(self, now):
        self.now = int(now)

    def get_many(self, keys):
        found = {}
        for key in keys:
            row = db().get(key)
            if row and row.get("v"):
                found[key] = row["v"]
        return found

    def put_many(self, items):
        for key, value in items.items():
            db().put({"pk": key, "v": value, "ttl": self.now + CACHE_DAYS * DAY})


# ------------------------------------------------------------------ usage and the view

def usage(item, now):
    owner = student_owner(item)
    lim = limits(item)
    period_id, resets_at = backends.period_info(lim["searches_period"], now)
    return {"documents": _count(owner.counter("doc")), "passages": _count(owner.counter("pass")), "stored_bytes": _count(owner.counter("bytes")),
            "searches_used": _count(owner.counter("search", period_id)), "resets_at": resets_at, "embed_tokens_month": _count(owner.counter("tok", month_id(now)))}


def view(item, now):
    if not available():
        return {"available": False}
    s, lim = settings(item), limits(item)
    out = {"available": True, "enabled": bool(s.get("enabled")), "limits": {**lim, "bounds": {k: list(v) for k, v in BOUNDS.items()}},
           "env_names": list(ENV_NAMES) if s.get("enabled") else [], "usage": None}
    if s.get("enabled") or s.get("key"):
        out["usage"] = usage(item, now)
    return out


def _whole(body, name):
    low, high = BOUNDS[name]
    value = body[name]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PortalError(400, f"{name} must be a whole number from {low} to {high}.")
    return value


def set_knowledge(item, body, now):
    """Teacher: switch on or off and set the caps. Off stops serving at once (the API checks on every call). Safe to repeat."""
    if not available():
        raise PortalError(503, "The class knowledge connector is not set up on this server.")
    changes = {}
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise PortalError(400, "enabled must be true or false.")
        changes["enabled"] = body["enabled"]
    for name in BOUNDS:
        if name in body:
            changes[name] = _whole(body, name)
    if "searches_period" in body:
        if body["searches_period"] not in PERIODS:
            raise PortalError(400, "searches_period must be day, month or total.")
        changes["searches_period"] = body["searches_period"]
    if not changes:
        return item
    if changes.get("enabled") and not settings(item).get("key"):
        changes["key"] = f"ks_{item['slug']}.{secrets.token_urlsafe(24)}"
    item = _save(item, **changes)
    if "enabled" in changes:
        apply_to_function(item)
    return item


def apply_to_function(item):
    """Make the student's function match the connector state (its two environment variables). A no-op until a function exists."""
    name = backends.settings(item).get("function_name")
    if not name:
        return item
    try:
        variables = backends._current_env(name)
        for var in ENV_NAMES:
            variables.pop(var, None)
        variables.update(env_for(item))
        backends.client("lambda").update_function_configuration(FunctionName=name, Environment={"Variables": variables})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            raise PortalError(409, "The student's function is updating. Press the switch again in a few seconds; nothing is lost.")
        raise PortalError(502, f"Could not update the student's function: {e.response['Error'].get('Message', 'AWS error')}")
    import jobs
    jobs.mirror_env(item, variables)
    return backends._save(item, env_names=sorted(variables))


# ------------------------------------------------------------------ the API's key check

def authenticate(headers, now):
    import hmac
    given = headers.get("authorization") or ""
    given = given[7:].strip() if given.lower().startswith("bearer ") else ""
    match = KEY_RE.match(given)
    lock = db().get(f"slug#{match.group(1)}") if match else None
    item = db().get(f"tok#{lock['token']}") if lock else None
    stored = settings(item).get("key", "") if item else ""
    if not (match and item and stored and hmac.compare_digest(stored.encode(), given.encode())):
        raise ApiError(401, "bad_key", "The knowledge key is not valid.")
    status = portal.status_of(item, now)
    if status != "active":
        raise ApiError(403, "token_inactive", f"This site's token is {status}, so its knowledge shelf is paused.")
    if not (available() and settings(item).get("enabled")):
        raise ApiError(403, "not_enabled", "Class knowledge is switched off for you. Ask your teacher.")
    return item


# ------------------------------------------------------------------ collections

def _check_name(name, what="collection"):
    if not isinstance(name, str) or not ks.NAME_RE.match(name):
        raise ApiError(400, "bad_request", f"Invalid {what} name (lowercase letters, digits, - and _, up to 40 characters).")
    return name


def _summary(meta):
    return {"title": meta["title"], "documents": len(meta["docs"]), "passages": meta["passages"], "version": meta["version"], "embedding": meta["embedding"]}


def create_collection(owner, name, title, now):
    _check_name(name)
    existing = store(now).collections(owner.scope, owner.name)
    if name in existing:
        raise ApiError(409, "exists", "That collection already exists.")
    if len(existing) >= owner.caps["collections"]:
        raise ApiError(413, "quota_exceeded", f"You can have up to {owner.caps['collections']} collections.")
    model, dims = embedding_settings()
    meta = store(now).create_collection(owner.base(name), title or name, model, dims, False)
    return {"name": name, **_summary(meta)}


def list_collections(owner, now):
    out = []
    for name in store(now).collections(owner.scope, owner.name):
        meta = store(now).meta(owner.base(name))
        if meta:
            out.append({"name": name, **_summary(meta)})
    return out


def delete_collection(owner, name, now):
    _check_name(name)
    if not store(now).meta(owner.base(name)):
        raise ApiError(404, "not_found", "No such collection.")
    for doc in documents(owner, name, now):
        _refund(owner, doc.get("reserved") or {})
    store(now).delete_collection(owner.base(name))
    return {"deleted": name}


# ------------------------------------------------------------------ documents: upload form, start, list, delete

def _status_key(owner, collection, doc_id):
    return f"{owner.base(collection)}/docs/{doc_id}/status.json"


def _source_key(owner, collection, doc_id, ext):
    return f"{owner.base(collection)}/docs/{doc_id}/source.{ext}"


def read_status(owner, collection, doc_id):
    raw = bucket().get(_status_key(owner, collection, doc_id))
    return json.loads(raw) if raw else None


def write_status(owner, collection, doc_id, status):
    bucket().put(_status_key(owner, collection, doc_id), json.dumps(status, separators=(",", ":")).encode())


def _refund(owner, reserved):
    for what, key in (("docs", "doc"), ("passages", "pass"), ("bytes", "bytes")):
        amount = int(reserved.get(what) or 0)
        if amount:
            db().counter_add(owner.counter(key), -amount)


def _new_doc_id(filename):
    stem = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(filename)[0].lower()).strip("-")[:28] or "doc"
    return f"{stem}-{secrets.token_hex(3)}"


def upload_url(owner, collection, filename, size, now):
    """Reserve a document slot and give back a short-lived upload form (a size-limited S3 POST). The caller then sends the bytes there and calls start()."""
    _check_name(collection)
    if not store(now).meta(owner.base(collection)):
        raise ApiError(404, "not_found", "No such collection.")
    if not isinstance(filename, str) or not FILE_RE.match(filename) or "." not in filename:
        raise ApiError(400, "bad_request", "The file name must have letters, digits, spaces, . - _ ( ) and an extension, up to 100 characters.")
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in EXTENSIONS:
        raise ApiError(415, "unsupported", "Only PDF, text, Markdown and HTML files can be added.")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ApiError(400, "bad_request", "size must be the file size in bytes.")
    cap = min(owner.caps["upload_mb"] * MB, ki.MAX_FILE_BYTES)
    if size > cap:
        raise ApiError(413, "too_large", f"A file can be up to {cap // MB} MB.")
    if _count(owner.counter("bytes")) + size > owner.caps["stored_mb"] * MB:
        raise ApiError(413, "quota_exceeded", f"Your book shelf is full ({owner.caps['stored_mb']} MB). Remove a book first.")
    if db().counter_add(owner.counter("doc"), 1, limit=owner.caps["documents"], now=now) is None:
        raise ApiError(413, "quota_exceeded", f"You can keep up to {owner.caps['documents']} documents. Remove one first.")
    doc_id = _new_doc_id(filename)
    write_status(owner, collection, doc_id, {"state": "uploading", "filename": filename, "ext": ext, "size": size, "created": int(now), "updated": int(now),
                                              "message": "", "reserved": {"docs": 1, "passages": 0, "bytes": 0}})
    form = bucket().presigned_post(_source_key(owner, collection, doc_id, ext), size, UPLOAD_SECONDS)
    return {"doc_id": doc_id, **form}


def _budget_check(owner, now):
    month = month_id(now)
    if owner.scope == "student":
        limit = owner.caps["embed_tokens"]
        if limit <= 0 or _count(owner.counter("tok", month)) >= limit:
            raise ApiError(429, "allowance_used", "Your monthly allowance for adding books is used up. It resets next month, or ask your teacher.")
    if _count(f"kspend#{month}") >= ceiling_micro():
        raise ApiError(429, "class_budget", "The class has reached its monthly limit for adding books. Ask your teacher.")


WORKERS_KEY = "kwork#global"


def _workers_cap():
    return int(os.environ.get("KNOWLEDGE_WORKERS", "2"))


def _acquire_slot(now):
    """At most KNOWLEDGE_WORKERS books are read at once, so a class uploading together can never use up the account's few concurrent Lambda executions and
    starve the portal. Held for minutes: a leaked slot heals after the worker's own time limit (the same rule background jobs use)."""
    if db().counter_add(WORKERS_KEY, 1, limit=_workers_cap(), now=now) is not None:
        return True
    record = db().get(WORKERS_KEY) or {}
    if record.get("count", 0) > 0 and now - record.get("updated_at", now) > STALE_WORK_SECONDS:
        db().put({"pk": WORKERS_KEY, "count": 0, "updated_at": int(now)})
        return db().counter_add(WORKERS_KEY, 1, limit=_workers_cap(), now=now) is not None
    return False


def _release_slot(now):
    db().counter_sub(WORKERS_KEY, now=now)


def start(owner, collection, doc_id, now):
    """The bytes are uploaded: queue the worker. Returns the document's status."""
    _check_name(collection)
    _check_name(doc_id, "document")
    status = read_status(owner, collection, doc_id)
    if not status:
        raise ApiError(404, "not_found", "No such document.")
    if status["state"] != "uploading":
        raise ApiError(409, "bad_state", f"This document is already {status['state']}.")
    source = _source_key(owner, collection, doc_id, status["ext"])
    received = bucket().size(source)
    if not received:
        raise ApiError(400, "no_file", "No file was received. Upload it to the upload address first, then start.")
    _budget_check(owner, now)
    name = worker_name()
    if not name:
        raise ApiError(503, "not_ready", "The book reader is not set up on this server.")
    if not _acquire_slot(now):
        raise ApiError(429, "busy", "Other books are being read right now. Try again in a few minutes.")
    status.update(state="queued", size=received, updated=int(now), message="")
    write_status(owner, collection, doc_id, status)
    try:
        backends.client("lambda").invoke(FunctionName=name, InvocationType="Event", Payload=json.dumps(
            {"scope": owner.scope, "owner": owner.name, "collection": collection, "doc_id": doc_id}).encode())
    except ClientError:
        _release_slot(now)
        status.update(state="uploading", message="")
        write_status(owner, collection, doc_id, status)
        raise ApiError(503, "not_ready", "Could not start the book reader. Try again in a moment.")
    return _doc_view(doc_id, status, None)


def _doc_view(doc_id, status, record):
    out = {"id": doc_id, "state": status["state"], "filename": status.get("filename"), "size": status.get("size"), "message": status.get("message", ""),
           "title": (record or {}).get("title") or status.get("filename"), "pages": (record or {}).get("pages"), "passages": (record or {}).get("passages"),
           "paged": (record or {}).get("paged"), "needs_ocr": (record or {}).get("needs_ocr") or [], "added": (record or {}).get("added")}
    return out


def documents(owner, collection, now):
    """Every document of a collection with its state. Abandoned uploads and work that never finished are cleaned up here, and their slots freed."""
    _check_name(collection)
    base = owner.base(collection)
    if not store(now).meta(base):
        raise ApiError(404, "not_found", "No such collection.")
    out = []
    for key in bucket().list(f"{base}/docs/"):
        if not key.endswith("/status.json"):
            continue
        doc_id = key.split("/")[-2]
        status = read_status(owner, collection, doc_id)
        if not status:
            continue
        age = now - int(status.get("updated", status.get("created", now)))
        if (status["state"] == "uploading" and age > STALE_UPLOAD_SECONDS) or (status["state"] in ("queued", "processing") and age > STALE_WORK_SECONDS):
            _refund(owner, status.get("reserved") or {})
            status.update(state="failed", reserved={"docs": 0, "passages": 0, "bytes": 0}, updated=int(now),
                          message="Never finished. Upload it again." if status["state"] == "uploading" else "Took too long and was stopped. Upload it again, or split the file.")
            write_status(owner, collection, doc_id, status)
        raw = bucket().get(f"{base}/docs/{doc_id}/doc.json")
        out.append({**_doc_view(doc_id, status, json.loads(raw) if raw else None), "reserved": status.get("reserved") or {}})
    return sorted(out, key=lambda d: (d.get("added") or 0, d["id"]))


def delete_document(owner, collection, doc_id, now):
    _check_name(collection)
    _check_name(doc_id, "document")
    status = read_status(owner, collection, doc_id)
    if not status:
        raise ApiError(404, "not_found", "No such document.")
    if status["state"] in ("queued", "processing") and now - int(status.get("updated", now)) <= STALE_WORK_SECONDS:
        raise ApiError(409, "busy", "This document is being read right now. Try again in a minute.")
    base = owner.base(collection)
    if bucket().size(f"{base}/docs/{doc_id}/doc.json"):
        store(now).delete_document(base, doc_id)
    else:
        bucket().delete_prefix(f"{base}/docs/{doc_id}/")
    _refund(owner, status.get("reserved") or {})
    return {"deleted": doc_id}


# ------------------------------------------------------------------ the worker's job (called by knowledge_worker.handler)

def _fail(owner, collection, doc_id, status, message, now):
    _refund(owner, status.get("reserved") or {})
    status.update(state="failed", message=message, reserved={"docs": 0, "passages": 0, "bytes": 0}, updated=int(now))
    write_status(owner, collection, doc_id, status)


def process_document(scope, owner_name, collection, doc_id, now, ocr=None):
    """Read one uploaded file into passages and vectors. Every limit is checked BEFORE the money is spent on embeddings; any failure frees what was reserved
    and leaves a message the teacher or student can read. Returns the final status."""
    if scope == "class":
        owner = library_owner()
    else:
        lock = db().get(f"slug#{owner_name}")
        item = db().get(f"tok#{lock['token']}") if lock else None
        if not item:
            return None
        owner = student_owner(item)
    status = read_status(owner, collection, doc_id)
    if not status or status["state"] != "queued":
        return status
    try:
        return _read_book(owner, collection, doc_id, status, now, ocr)
    finally:
        _release_slot(now)  # whatever happened, the reader slot taken by start() is given back


def _read_book(owner, collection, doc_id, status, now, ocr):
    status.update(state="processing", updated=int(now))
    write_status(owner, collection, doc_id, status)
    base = owner.base(collection)
    try:
        meta = store(now).meta(base)
        if not meta:
            raise ApiError(404, "not_found", "The collection was removed.")
        data = bucket().get(_source_key(owner, collection, doc_id, status["ext"]))
        prepared = ki.prepare(data or b"", status["filename"], ocr=ocr)
        passages = prepared["passages"]
        if db().counter_add(owner.counter("pass"), len(passages), limit=owner.caps["passages"], now=now) is None:
            raise ApiError(413, "quota_exceeded", f"That file makes {len(passages)} passages and your shelf allows {owner.caps['passages']} in all. Remove a book or split the file.")
        status["reserved"]["passages"] = len(passages)
        dims = meta["embedding"]["dims"]
        stored = len(data) + 2 * (len(passages) * dims * 2 + sum(len(p["text"].encode()) for p in passages))  # the source, plus the passages and vectors twice (document and index)
        if db().counter_add(owner.counter("bytes"), stored, limit=owner.caps["stored_mb"] * MB, now=now) is None:
            raise ApiError(413, "quota_exceeded", f"Your book shelf is full ({owner.caps['stored_mb']} MB). Remove a book first.")
        status["reserved"]["bytes"] = stored
        write_status(owner, collection, doc_id, status)
        result = ki.embed_prepared(prepared, meta["embedding"], cache=DbCache(now))
        month = month_id(now)
        db().counter_add(owner.counter("tok", month), result["tokens"], now=now, ttl=now + 62 * DAY)
        db().counter_add(f"kspend#{month}", result["cost_micro"], now=now, ttl=now + 62 * DAY)
        doc = {**result["doc"], "id": doc_id}
        store(now).put_document(base, doc, result["passages"], result["vectors"])
        status.update(state="ready", message="" if not result["skipped_pages"] else f"{len(result['skipped_pages'])} page(s) had no readable text (scans) and were skipped.",
                      updated=int(now))
        write_status(owner, collection, doc_id, status)
        return status
    except (ApiError, ki.IngestError, ks.StoreError) as e:
        _fail(owner, collection, doc_id, status, str(e), now)
    except em.EmbeddingError as e:
        _fail(owner, collection, doc_id, status, f"The book could not be embedded: {e}", now)
    except Exception as e:  # nothing unexpected may leave a document stuck "processing" or its reservations held
        print(f"knowledge worker error for {owner.scope}/{owner.name}/{collection}/{doc_id}: {type(e).__name__}: {e}")
        _fail(owner, collection, doc_id, status, "The book could not be read (an internal error). The teacher can see the details in the logs.", now)
    return status


# ------------------------------------------------------------------ search (the student's call)

def _resolve(owner, ref):
    if not isinstance(ref, str):
        raise ApiError(400, "bad_request", "A collection reference must be text.")
    if ref.startswith("library/"):
        return library_owner(), _check_name(ref[len("library/"):])
    return owner, _check_name(ref)


def search(item, body, now):
    """Search the student's own collections and/or the class library. body: {"query", "collections": [names or "library/<name>"], "k"}."""
    query = body.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise ApiError(400, "bad_request", f"query must be 1 to {MAX_QUERY_CHARS} characters of text.")
    k = body.get("k", 5)
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise ApiError(400, "bad_request", f"k must be a whole number from 1 to {MAX_K}.")
    owner, lim = student_owner(item), limits(item)
    period_id, resets_at = backends.period_info(lim["searches_period"], now)
    refs = body.get("collections")
    if refs is None:
        refs = [f"{n}" for n in store(now).collections(owner.scope, owner.name)] + [f"library/{n}" for n in store(now).collections("class", "library")]
    if not isinstance(refs, list) or not refs or len(refs) > MAX_SEARCH_COLLECTIONS or not all(isinstance(r, str) for r in refs):
        raise ApiError(400, "bad_request", f"collections must be a list of 1 to {MAX_SEARCH_COLLECTIONS} names (the class library's are written library/<name>); you have none to search yet.")
    targets = []
    for ref in refs:
        target_owner, name = _resolve(owner, ref)
        if not store(now).meta(target_owner.base(name)):
            raise ApiError(404, "not_found", f"No such collection: {ref}.")
        targets.append((ref, target_owner, name))
    if db().counter_add(owner.counter("search", period_id), 1, limit=lim["searches_limit"], now=now,
                        ttl=(resets_at + DAY) if resets_at else None) is None:
        raise ApiError(429, "allowance_used", "The search allowance is used up for now. It resets automatically, or ask your teacher.")
    cache, hits, per, tokens, cost = DbCache(now), [], {}, 0, 0
    for ref, target_owner, name in targets:
        found = store(now).search(target_owner.base(name), query, k=k, cache=cache)
        per[ref] = {"confident": found["confident"], "version": found["version"], "documents": len(store(now).meta(target_owner.base(name))["docs"])}
        hits += [{**h, "collection": ref} for h in found["hits"]]
        tokens, cost = tokens + found["tokens"], cost + found["cost_micro"]
    if tokens:
        month = month_id(now)
        db().counter_add(owner.counter("tok", month), tokens, now=now, ttl=now + 62 * DAY)
        db().counter_add(f"kspend#{month}", cost, now=now, ttl=now + 62 * DAY)
    hits.sort(key=lambda h: -(h["vector"] if h["vector"] is not None else 0.0))
    return {"hits": hits[:k], "confident": any(v["confident"] for v in per.values()), "collections": per, "tokens": tokens}


# ------------------------------------------------------------------ the teacher's views

def overview(now):
    """For the teacher: the class library and this month's spending against the ceiling (never what a student stored)."""
    month = month_id(now)
    return {"available": available(), "month": month, "spent_micro": _count(f"kspend#{month}"), "ceiling_micro": ceiling_micro(),
            "library": {"collections": list_collections(library_owner(), now), "limits": dict(LIBRARY),
                        "usage": {"documents": _count(library_owner().counter("doc")), "passages": _count(library_owner().counter("pass")),
                                  "stored_bytes": _count(library_owner().counter("bytes"))}}}


def clear(item, now):
    """Teacher: remove every collection of one student (their books, vectors and counters)."""
    owner = student_owner(item)
    names = store(now).collections(owner.scope, owner.name)
    for name in names:
        delete_collection(owner, name, now)
    return {"removed": names}


def portal_call(fn, *args):
    """Run a knowledge operation for the portal: every error becomes a PortalError with the same status and message the API would have sent."""
    try:
        return fn(*args)
    except (ApiError, ks.StoreError, ki.IngestError) as e:
        raise PortalError(e.status, str(e))
    except em.EmbeddingError as e:
        raise PortalError(502, f"Embedding failed: {e}")


LIB = "/api/admin/knowledge/library/collections"


def admin_route(method, path, body, now):
    """The teacher's class library, behind the teacher login (portal.py checks it). Returns (status, body) or None when the path is not a knowledge route."""
    owner = library_owner()
    if (method, path) == ("GET", "/api/admin/knowledge"):
        return 200, overview(now)
    if path == LIB and method == "POST":
        return 201, portal_call(create_collection, owner, body.get("name"), str(body.get("title") or "")[:100], now)
    match = re.fullmatch(re.escape(LIB) + r"/([^/]+)", path)
    if match and method == "DELETE":
        return 200, portal_call(delete_collection, owner, match.group(1), now)
    match = re.fullmatch(re.escape(LIB) + r"/([^/]+)/documents", path)
    if match and method == "GET":
        return 200, {"documents": [{k: v for k, v in d.items() if k != "reserved"} for d in portal_call(documents, owner, match.group(1), now)]}
    if match and method == "POST":
        return 200, portal_call(upload_url, owner, match.group(1), body.get("filename"), body.get("size"), now)
    match = re.fullmatch(re.escape(LIB) + r"/([^/]+)/documents/([^/]+)/start", path)
    if match and method == "POST":
        return 200, portal_call(start, owner, match.group(1), match.group(2), now)
    match = re.fullmatch(re.escape(LIB) + r"/([^/]+)/documents/([^/]+)", path)
    if match and method == "DELETE":
        return 200, portal_call(delete_document, owner, match.group(1), match.group(2), now)
    return None
