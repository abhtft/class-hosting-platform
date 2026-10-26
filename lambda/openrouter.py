"""OpenRouter key management for the class AI connector (standard library only).

The platform holds ONE management key (Secrets Manager). With it, each student gets their own capped, revocable key:
  POST   /keys           create   {name, limit, limit_reset}      -> {"data": {"hash", "limit", ...}, "key": "sk-or-..."}
  GET    /keys/{hash}    read     limit, limit_remaining, usage, disabled
  PATCH  /keys/{hash}    update   limit, limit_reset, disabled    (expires_at cannot be changed after creation, so it is not used)
  DELETE /keys/{hash}

The management key is never returned, logged or put in a student's function; error messages are built from OpenRouter's own
"error.message" only. Tests plug a fake into `transport`.
"""
import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("AI_BASE_URL", "https://openrouter.ai/api/v1")
PERIOD_RESET = {"day": "daily", "month": "monthly", "total": None}
KEY_CACHE_SECONDS = 300

transport = None  # tests: fake(method, path, token, body) -> (status, json)
_secret = {"value": None, "at": 0.0}


class OpenRouterError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def management_key():
    if _secret["value"] is None or time.time() - _secret["at"] > KEY_CACHE_SECONDS:
        import boto3
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["AI_SECRET_NAME"])["SecretString"]
        _secret["value"], _secret["at"] = json.loads(raw)["OPENROUTER_MANAGEMENT_KEY"], time.time()
    return _secret["value"]


def _call(method, path, body=None):
    token = management_key()
    if transport is not None:
        status, data = transport(method, path, token, body)
    else:
        request = urllib.request.Request(
            BASE_URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "student-deploy/1"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status, text = response.status, response.read().decode().strip()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode().strip()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OpenRouterError(502, f"Could not reach OpenRouter ({type(e).__name__}).")
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
    if status >= 400:
        message = ((data or {}).get("error") or {}).get("message") if isinstance(data, dict) else None
        raise OpenRouterError(status, message or f"OpenRouter answered HTTP {status}.")
    return data


def create_key(name, limit_usd, period):
    """-> {"hash", "key", "limit"}. The key value is returned exactly once by OpenRouter."""
    body = {"name": name, "limit": limit_usd, "limit_reset": PERIOD_RESET[period]}
    data = _call("POST", "/keys", body)
    return {"hash": data["data"]["hash"], "key": data["key"], "limit": data["data"].get("limit")}


def update_key(key_hash, **fields):
    """Accepts limit (USD), period (day|month|total) and disabled."""
    body = {}
    if "limit_usd" in fields:
        body["limit"] = fields["limit_usd"]
    if "period" in fields:
        body["limit_reset"] = PERIOD_RESET[fields["period"]]
    if "disabled" in fields:
        body["disabled"] = bool(fields["disabled"])
    return _call("PATCH", f"/keys/{key_hash}", body)["data"]


def get_key(key_hash):
    return _call("GET", f"/keys/{key_hash}")["data"]


def delete_key(key_hash):
    try:
        _call("DELETE", f"/keys/{key_hash}")
    except OpenRouterError as e:
        if e.status != 404:
            raise
