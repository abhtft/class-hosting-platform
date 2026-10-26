"""Storage API Lambda: what a student's backend talks to when the teacher has switched class storage on (see storage.py).

  student function --HTTPS, Authorization: Bearer cs_<slug>.<secret>--> API Gateway (own throttle) --> this handler

  PUT    /v1/data/{collection}/{id}      body {"value": <any JSON, up to 64 KB>}
  GET    /v1/data/{collection}/{id}
  DELETE /v1/data/{collection}/{id}
  GET    /v1/data/{collection}?after=<id>&limit=<n>          ids in order, a page at a time
  POST   /v1/files/upload-url            body {"name", "size", "content_type"?}  -> presigned S3 POST (the browser or function sends the bytes)
  GET    /v1/files                       names and sizes
  GET    /v1/files/{name}                -> a short-lived download link
  DELETE /v1/files/{name}

Every call: key check, token must be active, teacher switch must be on, then one operation off the allowance. Every error is JSON:
{"error": "<code>", "message": "..."} with codes bad_key, token_inactive, not_enabled, allowance_used, quota_exceeded, too_large, not_found, bad_request.
"""
import json
import re
import time

import portal
import storage
from storage import ApiError

DATA_ONE = re.compile(r"^/v1/data/([^/]+)/([^/]+)$")
DATA_LIST = re.compile(r"^/v1/data/([^/]+)$")
FILE_ONE = re.compile(r"^/v1/files/([^/]+)$")
MAX_BODY_CHARS = 2 * storage.MAX_VALUE_BYTES


def reply(status, **body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf8", "replace")
    if len(raw) > MAX_BODY_CHARS:
        raise ApiError(413, "too_large", f"The request is too large (a value can be up to {storage.MAX_VALUE_BYTES // 1024} KB).")
    try:
        body = json.loads(raw)
    except ValueError:
        raise ApiError(400, "bad_request", "The request body must be JSON.")
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "The request body must be a JSON object.")
    return body


def handler(event, context=None, now=None):
    now = int(now if now is not None else time.time())
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"].rstrip("/") or "/"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    query = event.get("queryStringParameters") or {}
    try:
        item = storage.authenticate(headers, now)
        storage.charge(item, now)
        one, many, file_one = DATA_ONE.match(path), DATA_LIST.match(path), FILE_ONE.match(path)
        if one and method == "PUT":
            body = parse_body(event)
            if "value" not in body:
                raise ApiError(400, "bad_request", 'The body must be {"value": ...}.')
            return reply(200, **storage.put_value(item, one.group(1), one.group(2), body["value"], now))
        if one and method == "GET":
            return reply(200, **storage.get_value(item, one.group(1), one.group(2)))
        if one and method == "DELETE":
            return reply(200, **storage.delete_value(item, one.group(1), one.group(2), now))
        if many and method == "GET":
            return reply(200, **storage.list_values(item, many.group(1), query.get("after"), query.get("limit")))
        if (method, path) == ("POST", "/v1/files/upload-url"):
            body = parse_body(event)
            return reply(200, **storage.file_upload_url(item, body.get("name"), body.get("size"), body.get("content_type")))
        if (method, path) == ("GET", "/v1/files"):
            return reply(200, files=storage.list_files(item))
        if file_one and method == "GET":
            return reply(200, **storage.file_download_url(item, file_one.group(1)))
        if file_one and method == "DELETE":
            return reply(200, **storage.file_delete(item, file_one.group(1)))
        raise ApiError(404, "not_found", "Unknown route.")
    except ApiError as e:
        return reply(e.status, error=e.code, message=str(e))
    except portal.PortalError as e:
        return reply(e.status, error="error", message=str(e))
