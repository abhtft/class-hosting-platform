"""AI proxy Lambda: the OpenAI-compatible address a student's backend uses when the teacher chose Gemini for that student (see gemini.py).

  student function --HTTPS, Authorization: Bearer ga_<slug>.<secret>--> API Gateway (own throttle) --> this handler --> Google

  POST /v1/chat/completions    OpenAI chat format, not streamed (stream=true is refused with a clear message)
  GET  /v1/models              the one model this student may use

Every call: key check (constant time), token active, teacher switch on, provider is Gemini, the cap not yet reached. The model is always the
one the teacher set (a student cannot ask for another); output is capped; the cost of the answer is added to the student's counter.
Errors use the OpenAI shape {"error": {"message", "type", "code"}} so SDKs show something readable.
"""
import hmac
import json
import re
import time

import ai
import gemini
import portal
from portal import db

KEY_RE = re.compile(r"^ga_([a-z0-9][a-z0-9-]{0,29})\.([A-Za-z0-9_-]{20,})$")
MAX_BODY_CHARS = 1_000_000
DEFAULT_MAX_TOKENS, MAX_MAX_TOKENS = 2048, 8192
ALLOWED = ("messages", "temperature", "top_p", "stop", "response_format", "tools", "tool_choice", "reasoning_effort", "seed",
           "frequency_penalty", "presence_penalty")


class ProxyError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def reply(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def error_reply(status, code, message):
    return reply(status, {"error": {"message": message, "type": "invalid_request_error" if status < 500 else "server_error", "code": code}})


def authenticate(headers, now):
    given = headers.get("authorization") or ""
    given = given[7:].strip() if given.lower().startswith("bearer ") else ""
    match = KEY_RE.match(given)
    lock = db().get(f"slug#{match.group(1)}") if match else None
    item = db().get(f"tok#{lock['token']}") if lock else None
    stored = ai.settings(item).get("gkey", "") if item else ""
    if not (match and item and stored and hmac.compare_digest(stored.encode(), given.encode())):
        raise ProxyError(401, "invalid_api_key", "The AI key is not valid.")
    status = portal.status_of(item, now)
    if status != "active":
        raise ProxyError(403, "token_inactive", f"This site's token is {status}, so its AI is paused.")
    s = ai.settings(item)
    if not (ai.gemini_enabled() and s.get("enabled") and ai.provider(item) == "gemini"):
        raise ProxyError(403, "not_enabled", "Class AI (Gemini) is switched off for you. Ask your teacher.")
    return item


def parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf8", "replace")
    if len(raw) > MAX_BODY_CHARS:
        raise ProxyError(413, "too_large", "The request is too large.")
    try:
        body = json.loads(raw)
    except ValueError:
        raise ProxyError(400, "bad_request", "The request body must be JSON.")
    if not isinstance(body, dict):
        raise ProxyError(400, "bad_request", "The request body must be a JSON object.")
    return body


def complete(item, body, now):
    if body.get("stream"):
        raise ProxyError(400, "stream_not_supported", "Streaming is not available on the class AI. Leave stream out (or set it to false).")
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        raise ProxyError(400, "bad_request", "messages must be a non-empty list.")
    if body.get("n") not in (None, 1):
        raise ProxyError(400, "bad_request", "Only one answer per call (n=1) is available.")
    used, limit = ai.gemini_used_micro(item, now), ai.limit_micro(item)
    if used >= limit:
        raise ProxyError(429, "insufficient_quota", "Your AI allowance is used up for now. It resets automatically, or ask your teacher.")
    model = ai.model_of(item)
    sent = {k: body[k] for k in ALLOWED if k in body}
    wanted = body.get("max_completion_tokens", body.get("max_tokens"))
    sent["max_tokens"] = min(int(wanted), MAX_MAX_TOKENS) if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted > 0 else DEFAULT_MAX_TOKENS
    sent["model"] = model
    try:
        answer = gemini.chat(sent, timeout=ai.student_timeout() - 3)
    except gemini.GeminiError as e:
        # Tokens may have been used even when Google answers late; an error carries no usage, so nothing is counted.
        raise ProxyError(429 if e.status == 429 else 502 if e.status >= 500 else e.status, "provider_error", str(e))
    ai.add_gemini_usage(item, gemini.cost_micro(model, answer.get("usage")), now)
    return answer


def handler(event, context=None, now=None):
    now = int(now if now is not None else time.time())
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"].rstrip("/") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    try:
        item = authenticate(headers, now)
        if (method, path) == ("POST", "/v1/chat/completions"):
            return reply(200, complete(item, parse_body(event), now))
        if (method, path) == ("GET", "/v1/models"):
            return reply(200, {"object": "list", "data": [{"id": ai.model_of(item), "object": "model", "owned_by": "google"}]})
        raise ProxyError(404, "not_found", "Unknown route.")
    except ProxyError as e:
        return error_reply(e.status, e.code, str(e))
    except portal.PortalError as e:
        return error_reply(e.status, "error", str(e))
