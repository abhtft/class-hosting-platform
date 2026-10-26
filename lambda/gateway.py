"""Gateway Lambda: the only way to reach a student's backend function.

  Browser -> CloudFront /api/* -> API Gateway  ANY /b/{slug}/{proxy+}  -> this handler -> student's function sd-<slug>

Checks, in order (each failure is a small JSON error, and only a run that passes all of them is counted):
  1 origin secret (direct calls to the API URL are refused)      2 path and slug            3 body size
  4 student backend exists and the token's lifecycle allows it   5 teacher toggle (ON/OFF)  6 per-visitor-IP rate limit
  7 in-flight limiter (protects the rest of the AWS account)      8 run allowance (atomic)    9 invoke
Then the response is validated and shaped. See backend_wire.md section 5.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time

from botocore.exceptions import ClientError

import backends
import portal
from portal import db

SLUG_PATH = re.compile(r"^/b/([a-z0-9][a-z0-9-]{0,29})(/.*)?$")
MAX_BODY_CHARS = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
LEAK_SECONDS = 60  # an in-flight counter untouched for this long while above zero is considered leaked
STRIP_REQUEST_HEADERS = {"x-origin-secret", "x-viewer-ip"}
DROP_RESPONSE_HEADERS = {"transfer-encoding", "connection", "content-length", "keep-alive"}

_cache = {}


def setting(name, default):
    return int(os.environ.get(name, default))


def reply(status, headers=None, **body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store", **(headers or {})},
            "body": json.dumps(body)}


def handler(event, context=None, now=None):
    now = int(now if now is not None else time.time())
    started = time.time()
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    secret = os.environ.get("ORIGIN_SECRET", "")
    if not secret or not hmac.compare_digest(headers.get("x-origin-secret", "").encode(), secret.encode()):
        return reply(403, error="forbidden")

    match = SLUG_PATH.match(event.get("rawPath", ""))
    if not match:
        return reply(404, error="not_found")
    slug, rest = match.group(1), match.group(2) or "/"

    if len(event.get("body") or "") > MAX_BODY_CHARS:
        return reply(413, error="payload_too_large", message="The request body is too large.")

    item = load_token(slug, now)
    b = backends.settings(item) if item else {}
    if not item or not b.get("function_name") or b.get("state") != "ready":
        return reply(404, error="no_backend", message="This site has no backend.")
    status = portal.status_of(item, now)
    if status == "revoked":
        return reply(403, error="forbidden")
    if status == "offline":
        return reply(503, error="site_offline", message="This site is offline.")
    if status == "expired":
        return reply(403, error="token_expired", message="This site's token has expired.")
    if not b.get("enabled"):
        return reply(503, error="backend_off", message="This backend is turned off by the site owner.")

    ip = headers.get("x-viewer-ip") or (event.get("requestContext", {}).get("http", {}).get("sourceIp")) or "unknown"
    ip_key = f"ip#{slug}#{hashlib.sha1(ip.encode()).hexdigest()[:12]}#{now // 60}"
    if db().counter_add(ip_key, 1, limit=setting("PER_IP_PER_MINUTE", 120), ttl=now + 120, now=now) is None:
        return reply(429, {"retry-after": "30"}, error="too_many_requests", message="Too many requests. Please slow down.")

    slots = [("inflight#global", setting("INFLIGHT_GLOBAL", 3)), (f"inflight#{slug}", setting("INFLIGHT_STUDENT", 2))]
    taken = []
    try:
        for key, cap in slots:
            if not acquire(key, cap, now):
                return reply(429, {"retry-after": "1"}, error="busy", message="The backend is busy. Please retry in a moment.")
            taken.append(key)

        period_id, resets_at = backends.period_info(b.get("runs_period", "month"), now)
        ttl = resets_at + 7 * portal.DAY if resets_at else None
        if db().counter_add(f"runs#{slug}#{period_id}", 1, limit=b.get("runs_limit", backends.DEFAULT_RUNS), ttl=ttl, now=now) is None:
            retry = {"retry-after": str(max(resets_at - now, 1))} if resets_at else {}
            return reply(429, retry, error="allowance_exhausted", resets_at=resets_at,
                         message="This site's backend has used its allowed runs. Please contact the site owner.")

        result = invoke(b["function_name"], student_event(event, headers, rest, ip))
        print(json.dumps({"slug": slug, "status": result["statusCode"], "ms": int((time.time() - started) * 1000)}))
        return result
    finally:
        for key in taken:
            db().counter_sub(key, now=now)


def load_token(slug, now):
    """Settings are cached briefly. A site WITHOUT a ready backend is cached for much less, so a student who has just finished
    deploying is not told "no backend" for seconds afterwards (found in the first live run)."""
    cached = _cache.get(slug)
    if cached:
        ready = cached[1] is not None and backends.settings(cached[1]).get("state") == "ready"
        ttl = setting("GATEWAY_CACHE_SECONDS", 10) if ready else setting("GATEWAY_NEGATIVE_CACHE_SECONDS", 2)
        if 0 <= now - cached[0] < ttl:  # a negative age (clock skew) is never treated as fresh
            return cached[1]
    lock = db().get(f"slug#{slug}")
    item = db().get(f"tok#{lock['token']}") if lock else None
    _cache[slug] = (now, item)
    return item


def acquire(key, cap, now):
    """Take one in-flight slot. A counter stuck above zero (a crash between increment and decrement) heals itself."""
    if db().counter_add(key, 1, limit=cap, now=now) is not None:
        return True
    record = db().get(key) or {}
    if record.get("count", 0) > 0 and now - record.get("updated_at", now) > LEAK_SECONDS:
        db().put({"pk": key, "count": 0, "updated_at": now})
        return db().counter_add(key, 1, limit=cap, now=now) is not None
    return False


def student_event(event, headers, rest, ip):
    """The standard contract: an API Gateway HTTP API (payload v2) event for the student's function."""
    http = {**event.get("requestContext", {}).get("http", {}), "path": rest, "sourceIp": ip}
    return {
        "version": "2.0", "routeKey": "$default", "rawPath": rest, "rawQueryString": event.get("rawQueryString", ""),
        "headers": {k: v for k, v in headers.items() if k not in STRIP_REQUEST_HEADERS},
        "queryStringParameters": event.get("queryStringParameters"), "requestContext": {**event.get("requestContext", {}), "http": http},
        "body": event.get("body"), "isBase64Encoded": bool(event.get("isBase64Encoded")),
    }


def invoke(function, payload):
    try:
        response = backends.client("lambda").invoke(FunctionName=function, InvocationType="RequestResponse",
                                                    Payload=json.dumps(payload).encode())
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code == "TooManyRequestsException":
            return reply(429, {"retry-after": "1"}, error="busy", message="The backend is busy. Please retry in a moment.")
        if code == "ResourceNotFoundException":
            return reply(404, error="no_backend", message="This site has no backend.")
        print(f"invoke failed for {function}: {e}")
        return reply(502, error="backend_unavailable", message="The backend is unavailable.")
    raw = response["Payload"].read()
    if response.get("FunctionError"):
        try:
            message = str(json.loads(raw).get("errorMessage", ""))
        except ValueError:
            message = ""
        print(f"function error in {function}")  # the message may hold student secrets: it stays in the student's own function logs
        if "timed out" in message.lower():
            return reply(504, error="function_timeout", message="The backend took too long to respond.")
        return reply(502, error="function_error", message="The backend returned an error.")
    return shape(raw)


def shape(raw):
    """Turn the function's return value into an HTTP response, as API Gateway does for payload v2."""
    try:
        value = json.loads(raw)
    except ValueError:
        return reply(502, error="bad_response", message="The backend returned something that is not JSON.")
    if isinstance(value, dict) and "statusCode" in value:
        status = value["statusCode"]
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            return reply(502, error="bad_response", message="The backend returned an invalid status code.")
        headers = value.get("headers") or {}
        if not isinstance(headers, dict):
            return reply(502, error="bad_response", message="The backend returned invalid headers.")
        out = {str(k).lower(): str(v) for k, v in headers.items() if str(k).lower() not in DROP_RESPONSE_HEADERS}
        body, encoded = value.get("body", ""), bool(value.get("isBase64Encoded"))
        if body is None:
            body = ""
        if not isinstance(body, str):  # a non-string body is serialized as JSON
            out.setdefault("content-type", "application/json")
            body = json.dumps(body)
    else:  # a plain value: API Gateway answers 200 with it as the body
        status, out, encoded = 200, {"content-type": "application/json"}, False
        body = value if isinstance(value, str) else json.dumps(value)
    size = len(base64.b64decode(body, validate=False)) if encoded else len(body.encode())
    if size > MAX_RESPONSE_BYTES:
        return reply(502, error="response_too_large", message="The backend's response is too large.")
    out.setdefault("x-content-type-options", "nosniff")
    out.setdefault("cache-control", "no-store")
    result = {"statusCode": status, "headers": out, "body": body, "isBase64Encoded": encoded}
    if isinstance(value, dict) and isinstance(value.get("cookies"), list):
        result["cookies"] = [str(c) for c in value["cookies"]]
    return result
