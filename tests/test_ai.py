"""Class AI connector: teacher on/off + cap, the student's function environment, usage, lifecycle (off, revoke, expiry, extension),
provider failures, idempotent retries, and that no key is ever exposed. OpenRouter is a fake; Lambda is the shared fake."""
import io
import json
import os
import sys
import time
import zipfile

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true",
                  SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t",
                  AI_ENABLED="true", AI_SECRET_NAME="openrouter", AI_DEFAULT_MODEL="anthropic/claude-haiku-5.5",
                  AI_DEFAULT_LIMIT_USD="0.25", AI_STUDENT_TIMEOUT="25")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import ai  # noqa: E402
import backends  # noqa: E402
import openrouter  # noqa: E402
import portal  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeOpenRouter, FakeS3, store  # noqa: E402

DAY = portal.DAY
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"


class StrictStore(portal.MemoryStore):
    """DynamoDB refuses Python floats; the in-memory store does not. Fail loudly here instead of in production."""

    @staticmethod
    def _no_floats(value, where):
        if isinstance(value, float):
            raise TypeError(f"float {value!r} in {where}: DynamoDB needs int or Decimal")
        if isinstance(value, dict):
            for k, v in value.items():
                StrictStore._no_floats(v, f"{where}.{k}")
        if isinstance(value, list):
            for v in value:
                StrictStore._no_floats(v, where)

    def update(self, pk, fields, remove=()):
        self._no_floats(fields, pk)
        return super().update(pk, fields, remove)

    def put(self, item, if_absent=False):
        self._no_floats(item, item.get("pk"))
        return super().put(item, if_absent)


portal._store = StrictStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
logs, s3, lam, router = FakeLogs(), FakeS3(), FakeLambda(), FakeOpenRouter()
lam.logs = logs
backends._clients.update({"lambda": lam, "logs": logs, "s3": s3})
openrouter.transport = router
openrouter._secret.update(value="mgmt-secret", at=10**12)  # cached: no Secrets Manager call
now = int(time.time())
KEY_PREFIX = "sk-or-v1-fake-"


def call(method, path, body=None, session=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]
EXPOSED = []  # every response body seen, scanned for key material at the end


def seen(status, body):
    EXPOSED.append(json.dumps(body))
    return status, body


def new_student(name, backend=True):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    tid = made["item"]["id"]
    if backend:
        call("POST", f"/api/admin/tokens/{tid}/backend", {"enabled": True}, admin)
    return tid, call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def ai_set(tid, at=None, **body):
    return seen(*call("POST", f"/api/admin/tokens/{tid}/ai", body, admin, at=at))


def me(session, at=None):
    return seen(*call("GET", "/api/student/me", session=session, at=at))[1]["token"]


def deploy(session, slug, files=None):
    lam.polls_until_active = 1
    assert call("POST", "/api/student/backend/upload-url", session=session)[0] == 200
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in (files or {"app.py": "def handler(event, context):\n    return {'statusCode': 200, 'body': 'ok'}\n"}).items():
            z.writestr(name, data)
    store[f"uploads/{slug}-backend.zip"] = {"Body": buf.getvalue()}
    status, body = call("POST", "/api/student/backend/publish", session=session)
    assert status == 200, body
    for _ in range(5):
        state = call("GET", "/api/student/backend", session=session)[1]["token"]["backend"]["state"]
        if state == "ready":
            return
    raise AssertionError("backend never became ready")


def function(slug):
    return lam.functions[f"sd-{slug}"]["config"]


def env(slug):
    return (function(slug).get("Environment") or {}).get("Variables") or {}


def stored_ai(tid):
    return portal.db().get(f"tok#{tid}").get("ai") or {}


# ---------- 1. first switch-on: defaults, a capped key, nothing exposed ----------
tid, session, slug = new_student("Alice")
assert me(session)["ai"]["enabled"] is False and me(session)["ai"]["available"] is True
status, body = ai_set(tid, enabled=True)
assert status == 200, body
view = body["token"]["ai"]
assert view["enabled"] and view["limit_usd"] == 0.25 and view["period"] == "month" and view["model"] == "anthropic/claude-haiku-5.5", view
assert view["env_names"] == ["OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_MODEL"] and view["key_active"] and view["approx_calls"] == 714, view
key = router.by_name(f"student:{slug}")
assert key and key["limit"] == 0.25 and key["limit_reset"] == "monthly" and not key["disabled"], key
assert sum(1 for c in router.calls if c[:2] == ("POST", "/keys")) == 1
assert stored_ai(tid)["limit_micro"] == 250000 and stored_ai(tid)["key"] == key["key"], "the key is stored for the function, in whole micro-dollars"
assert me(session)["ai"] == view, "the student sees the same numbers"
print("  ok  first switch-on: defaults, capped monthly key, no floats stored")

# ---------- 2. no function yet: the first publish creates it with the connector ----------
assert not any(c[0] == "update_function_configuration" for c in lam.calls), "nothing to update before a function exists"
deploy(session, slug)
assert function(slug)["Timeout"] == 25, "AI functions get the longer timeout"
assert env(slug) == {"OPENAI_API_KEY": key["key"], "OPENAI_BASE_URL": "https://openrouter.ai/api/v1", "AI_MODEL": "anthropic/claude-haiku-5.5"}, env(slug)
assert me(session)["backend"]["env_names"] == ["AI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"]
print("  ok  first deploy creates the function with the key, base URL, model and a 25 s timeout")

# ---------- 3. the student cannot touch the managed names while it is on ----------
for name in ai.AI_ENV_NAMES:
    assert call("PUT", "/api/student/backend/env", {"name": name, "value": "x"}, session)[0] == 400, name
    assert call("DELETE", f"/api/student/backend/env/{name}", session=session)[0] == 400, name
assert call("PUT", "/api/student/backend/env", {"name": "GREETING", "value": "hi"}, session)[0] == 200
assert env(slug)["OPENAI_API_KEY"] == key["key"] and env(slug)["GREETING"] == "hi"
print("  ok  managed names are protected; other variables still work")

# ---------- 4. changing the cap, period and model ----------
status, body = ai_set(tid, limit_usd=0.5, period="day", model="google/gemma-4-26b-a4b-it")
assert status == 200 and body["token"]["ai"]["limit_usd"] == 0.5 and body["token"]["ai"]["period"] == "day"
assert "approx_calls" not in body["token"]["ai"], "the 'about N calls' hint is only given for the default model"
assert (key["limit"], key["limit_reset"]) == (0.5, "daily")
assert env(slug)["AI_MODEL"] == "google/gemma-4-26b-a4b-it" and env(slug)["GREETING"] == "hi", "model changes reach the function; other variables stay"
assert ai_set(tid, model="")[1]["token"]["ai"]["model"] == "anthropic/claude-haiku-5.5" and env(slug)["AI_MODEL"] == "anthropic/claude-haiku-5.5"
assert [c for c in router.calls if c[0] == "POST"].__len__() == 1, "the same key is reused"
print("  ok  cap, period and model edits reach OpenRouter and the function")

# ---------- 5. invalid input changes nothing ----------
calls_before = len(router.calls)
for bad in ({"limit_usd": 0}, {"limit_usd": 0.001}, {"limit_usd": 25}, {"limit_usd": True}, {"limit_usd": "1"}, {"period": "week"},
            {"enabled": "yes"}, {"model": "has space"}, {"model": "../etc"}):
    status, body = ai_set(tid, **bad)
    assert status == 400, (bad, status, body)
assert len(router.calls) == calls_before
print("  ok  invalid input is refused before OpenRouter is called")

# ---------- 6. usage, cached between refreshes ----------
router.spend(key["hash"], 0.1)
t0 = now + 1000
used = call("GET", "/api/student/ai", session=session, at=t0)[1]["token"]["ai"]
assert used["used_usd"] == 0.1 and abs(used["remaining_usd"] - 0.4) < 1e-9, used
gets = lambda: sum(1 for c in router.calls if c[0] == "GET")  # noqa: E731
before = gets()
call("GET", "/api/student/ai", session=session, at=t0 + 5)
assert gets() == before, "asked again within 20 s: served from the cache"
router.spend(key["hash"], 0.05)
again = call("GET", "/api/student/ai", session=session, at=t0 + 30)[1]["token"]["ai"]
assert abs(again["used_usd"] - 0.15) < 1e-9 and gets() == before + 1, again
assert abs(ai_set(tid)[1]["token"]["ai"]["used_usd"] - 0.15) < 1e-9, "an empty teacher request refreshes"
print("  ok  usage comes from OpenRouter and is cached for 20 s")

# ---------- 7. switching off disables the key first, then cleans the function ----------
status, body = ai_set(tid, enabled=False)
assert status == 200 and body["token"]["ai"]["enabled"] is False and not body["token"]["ai"]["key_active"]
assert key["disabled"] is True
assert not any(n in env(slug) for n in ai.AI_ENV_NAMES) and env(slug)["GREETING"] == "hi" and function(slug)["Timeout"] == 10
assert call("PUT", "/api/student/backend/env", {"name": "OPENAI_API_KEY", "value": "my-own"}, session)[0] == 200, "own key allowed again"
print("  ok  switch-off: key disabled, variables removed, timeout back to 10 s, the student may use their own key")

# ---------- 8. switching on again reuses the same key ----------
call("DELETE", "/api/student/backend/env/OPENAI_API_KEY", session=session)
assert ai_set(tid, enabled=True)[0] == 200
assert key["disabled"] is False and env(slug)["OPENAI_API_KEY"] == key["key"] and len([1 for c in router.calls if c[:2] == ("POST", "/keys")]) == 1
print("  ok  switching on again re-enables the same key")

# ---------- 9. lifecycle: revoke, offline, expiry (sweeper), extension ----------
tid2, session2, slug2 = new_student("Bob")
ai_set(tid2, enabled=True)
bob_key = router.by_name(f"student:{slug2}")
assert call("POST", f"/api/admin/tokens/{tid2}/revoke", {}, admin)[0] == 200 and bob_key["disabled"] is True, "revoke disables the key at once"

tid3, session3, slug3 = new_student("Carol")
ai_set(tid3, enabled=True)
carol = router.by_name(f"student:{slug3}")
call("POST", f"/api/admin/tokens/{tid3}/offline", {}, admin)
assert carol["disabled"] is True, "taking the site offline disables the key"
assert call("POST", f"/api/admin/tokens/{tid3}/extend", {"days": 5}, admin)[0] == 200 and carol["disabled"] is False, "extending enables it again"

tid4, session4, slug4 = new_student("Dave")
ai_set(tid4, enabled=True)
dave = router.by_name(f"student:{slug4}")
portal.db().update(f"tok#{tid4}", {"expires_at": now - 10})  # the token expired by itself: no teacher action happens
result = portal.sweep(now)
assert dave["disabled"] is True and result.get("ai_keys_changed") == [slug4], result
assert portal.sweep(now).get("ai_keys_changed") is None, "a second sweep changes nothing"
call("POST", f"/api/admin/tokens/{tid4}/extend", {"days": 3}, admin)
assert dave["disabled"] is False
print("  ok  revoke/offline/expiry disable the key, extension enables it, the sweeper catches silent expiry")

# ---------- 10. a student who is not active gets a disabled key from the start ----------
tid5, session5, slug5 = new_student("Erin")
portal.db().update(f"tok#{tid5}", {"expires_at": now - 10})
assert ai_set(tid5, enabled=True)[0] == 200 and router.by_name(f"student:{slug5}")["disabled"] is True
print("  ok  enabling AI for an expired student creates a disabled key")

# ---------- 11. provider failures ----------
tid6, session6, slug6 = new_student("Finn")
router.fail = (500, "upstream exploded")
status, body = ai_set(tid6, enabled=True)
assert status == 502 and "upstream exploded" in body["error"] and "mgmt-secret" not in json.dumps(body), body
assert not stored_ai(tid6).get("enabled") and router.by_name(f"student:{slug6}") is None, "a failed switch-on leaves nothing half-done"
router.fail = None
assert ai_set(tid6, enabled=True)[0] == 200
router.fail = (500, "still down")
assert call("POST", f"/api/admin/tokens/{tid6}/revoke", {}, admin)[0] == 200, "a provider outage never blocks the teacher's revoke"
finn = router.by_name(f"student:{slug6}")
assert finn["disabled"] is False, "the key could not be disabled right now"
router.fail = None
assert portal.sweep(now).get("ai_keys_changed") == [slug6] and finn["disabled"] is True, "the next sweep disables it"
router.management_key = "rotated"
status, body = ai_set(tid, limit_usd=0.4)
assert status == 502 and "mgmt-secret" not in json.dumps(body) and "rotated" not in json.dumps(body), "a wrong management key never appears in a response"
router.management_key = "mgmt-secret"
print("  ok  provider failures: clear error, nothing half-done, revoke never blocked, sweeper retries, no secrets in errors")

# ---------- 12. a function that is mid-update: 409, and the retry finishes the job without a second key ----------
tid7, session7, slug7 = new_student("Gail")
deploy(session7, slug7)
lam.functions[f"sd-{slug7}"]["updating"] = True
status, body = ai_set(tid7, enabled=True)
assert status == 409 and "Press the switch again" in body["error"], body
assert not any(n in env(slug7) for n in ai.AI_ENV_NAMES)
lam.functions[f"sd-{slug7}"]["updating"] = False
assert ai_set(tid7, enabled=True)[0] == 200 and env(slug7)["OPENAI_API_KEY"] == router.by_name(f"student:{slug7}")["key"]
assert sum(1 for k in router.keys.values() if k["name"] == f"student:{slug7}") == 1, "one key, not two"
print("  ok  a function that is updating gives 409; repeating the request completes it with the same key")

# ---------- 13. removing and re-deploying the backend keeps the connector ----------
call("DELETE", "/api/student/backend", session=session)
assert me(session)["ai"]["enabled"] is True
deploy(session, slug)
assert env(slug)["OPENAI_API_KEY"] == key["key"] and function(slug)["Timeout"] == 25
print("  ok  the connector survives removing and re-creating the backend")

# ---------- 14. the connector is off on a server without it ----------
os.environ["AI_ENABLED"] = "false"
assert me(session)["ai"] == {"available": False}
assert ai_set(tid, enabled=False)[0] == 503
assert call("PUT", "/api/student/backend/env", {"name": "OPENAI_API_KEY", "value": "own"}, session)[0] == 200, "names are free when the feature is off"
os.environ["AI_ENABLED"] = "true"
print("  ok  without AI_ENABLED nothing is offered or reserved")

# ---------- 15. nothing secret ever left the API ----------
blob = "\n".join(EXPOSED)
assert KEY_PREFIX not in blob and "mgmt-secret" not in blob, "no key material in any response"
listing = call("GET", "/api/admin/tokens", session=admin)[1]
assert KEY_PREFIX not in json.dumps(listing) and '"hash"' not in json.dumps([t["ai"] for t in listing["tokens"]])
print("  ok  no key (student or management) in any API response, including the teacher's list")

print("test_ai.py: all passed")
