"""Class AI with Gemini: the teacher's provider switch, the student's environment, the platform's OpenAI-compatible proxy (key check, forced
model, caps, cost including thinking tokens, lifecycle) and that no key ever leaves the server. Google and OpenRouter are fakes."""
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
import ai_proxy  # noqa: E402
import backends  # noqa: E402
import gemini  # noqa: E402
import openrouter  # noqa: E402
import portal  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeOpenRouter, FakeS3, store  # noqa: E402

site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
GOOGLE_KEY = "AQ.fake-google-key-never-visible"
PROXY = "https://proxy.example.com"

portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
logs, s3, lam, router = FakeLogs(), FakeS3(), FakeLambda(), FakeOpenRouter()
lam.logs = logs
backends._clients.update({"lambda": lam, "logs": logs, "s3": s3})
openrouter.transport = router
openrouter._secret.update(value="mgmt-secret", at=10**12)
gemini._secret.update(value=GOOGLE_KEY, at=10**12)
now = int(time.time())

google_calls, google_reply = [], {}


def fake_google(url, headers, body):
    google_calls.append((url, headers, body))
    if google_reply.get("error"):
        return google_reply["error"]
    return 200, google_reply.get("ok") or {"choices": [{"message": {"role": "assistant", "content": "Hello"}}],
                                           "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1900}}


gemini.transport = fake_google


def call(method, path, body=None, session=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


def proxy(method, path, key=None, body=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"authorization": f"Bearer {key}"} if key else {},
             "body": json.dumps(body) if body is not None else None}
    result = ai_proxy.handler(event, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]
EXPOSED = []


def seen(status, body):
    EXPOSED.append(json.dumps(body))
    return status, body


def new_student(name):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    tid = made["item"]["id"]
    call("POST", f"/api/admin/tokens/{tid}/backend", {"enabled": True}, admin)
    return tid, call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def ai_set(tid, at=None, **body):
    return seen(*call("POST", f"/api/admin/tokens/{tid}/ai", body, admin, at=at))


def stored(tid):
    return portal.db().get(f"tok#{tid}").get("ai") or {}


def deploy(session, slug):
    lam.polls_until_active = 1
    call("POST", "/api/student/backend/upload-url", session=session)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", "def handler(event, context):\n    return {'statusCode': 200, 'body': 'ok'}\n")
    store[f"uploads/{slug}-backend.zip"] = {"Body": buf.getvalue()}
    assert call("POST", "/api/student/backend/publish", session=session)[0] == 200
    for _ in range(5):
        if call("GET", "/api/student/backend", session=session)[1]["token"]["backend"]["state"] == "ready":
            return
    raise AssertionError("backend never became ready")


def env(slug):
    return (lam.functions[f"sd-{slug}"]["config"].get("Environment") or {}).get("Variables") or {}


CHAT = ("POST", "/v1/chat/completions")

# ---------- 1. without the Gemini feature the choice is not offered or accepted ----------
tid, session, slug = new_student("Alice")
view = call("GET", "/api/student/me", session=session)[1]["token"]["ai"]
assert view["provider"] == "openrouter" and view["providers"] == ["openrouter"], view
status, body = ai_set(tid, enabled=True, provider="gemini")
assert status == 503 and not [c for c in router.calls if c[:2] == ("POST", "/keys")], (status, body)
print("  ok  Gemini is refused (and nothing is created) while the feature is off")

os.environ.update(AI_GEMINI_ENABLED="true", AI_PROXY_URL=PROXY + "/")

# ---------- 2. Gemini is the default for a student who never had AI; switching a student to it ----------
tid0, session0, slug0 = new_student("Zed")
assert ai_set(tid0, enabled=True)[1]["token"]["ai"]["provider"] == "gemini" and not [c for c in router.calls if c[:2] == ("POST", "/keys")]
assert stored(tid0)["provider"] == "gemini" and stored(tid0)["gkey"].startswith("ga_")

assert ai_set(tid, enabled=True, provider="bogus")[0] == 400
assert ai_set(tid, enabled=True, provider="gemini", model="anthropic/claude-haiku-5.5")[0] == 400, "an OpenRouter model id is not a Gemini model"
deploy(session, slug)
status, body = ai_set(tid, enabled=True, provider="gemini")
assert status == 200, body
view = body["token"]["ai"]
assert view["provider"] == "gemini" and view["model"] == "gemini-3.5-flash-lite" and view["base_url"] == PROXY + "/v1", view
assert view["providers"] == ["openrouter", "gemini"] and view["gemini_models"] == ["gemini-3.5-flash-lite"] and view["key_active"], view
assert not [c for c in router.calls if c[:2] == ("POST", "/keys")], "no OpenRouter key is made for a Gemini student"
gkey = stored(tid)["gkey"]
assert gkey.startswith(f"ga_{slug}."), gkey
e = env(slug)
assert e["OPENAI_API_KEY"] == gkey and e["OPENAI_BASE_URL"] == PROXY + "/v1" and e["AI_MODEL"] == "gemini-3.5-flash-lite", e
assert GOOGLE_KEY not in json.dumps(e), "the platform's Google key is never in a student function"
print("  ok  provider=gemini: private key and proxy address in the student's function, no OpenRouter key, Google key absent")

# ---------- 3. the proxy ----------
assert proxy(*CHAT, None, {"messages": [{"role": "user", "content": "hi"}]})[0] == 401
assert proxy(*CHAT, "ga_nobody.aaaaaaaaaaaaaaaaaaaaaaaa", {"messages": []})[0] == 401
assert proxy(*CHAT, gkey[:-1] + ("A" if gkey[-1] != "A" else "B"), {"messages": [{"role": "user", "content": "hi"}]})[0] == 401, "a wrong secret"
assert not google_calls, "nothing reached Google with a bad key"
status, body = proxy(*CHAT, gkey, {"model": "gemini-3.5-flash", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 999999,
                                  "user": "x", "stream": False, "extra_junk": 1})
assert status == 200 and body["choices"][0]["message"]["content"] == "Hello", body
url, headers, sent = google_calls[-1]
assert sent["model"] == "gemini-3.5-flash-lite", "the teacher's model is forced"
assert sent["max_tokens"] == ai_proxy.MAX_MAX_TOKENS and "extra_junk" not in sent and "user" not in sent and "stream" not in sent, sent
assert headers["Authorization"] == "Bearer " + GOOGLE_KEY
print("  ok  proxy: bad keys refused before Google; model forced; output capped; unknown fields dropped")

# cost: 1000 in at $0.30/M + (1900-1000)=900 out (thinking included) at $2.50/M = 300 + 2250 micro-dollars
assert gemini.cost_micro("gemini-3.5-flash-lite", {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1900}) == 2550
assert ai.gemini_used_micro(portal.db().get(f"tok#{tid}"), now) == 2550
view = call("GET", "/api/student/me", session=session)[1]["token"]["ai"]
assert view["used_usd"] == 0.00255 and abs(view["remaining_usd"] - (0.25 - 0.00255)) < 1e-9, view
status, body = ai_set(tid)
assert body["token"]["ai"]["used_usd"] == 0.00255
print("  ok  cost counts thinking tokens (total - prompt) and shows up in the usage numbers")

# ---------- 4. refusals ----------
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}], "stream": True})[1]["error"]["code"] == "stream_not_supported"
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}], "n": 3})[0] == 400
assert proxy(*CHAT, gkey, {"messages": []})[0] == 400
assert proxy("GET", "/v1/nothing", gkey)[0] == 404
status, body = proxy("GET", "/v1/models", gkey)
assert status == 200 and [m["id"] for m in body["data"]] == ["gemini-3.5-flash-lite"]
google_reply["error"] = (429, {"error": {"message": f"quota for {GOOGLE_KEY}"}})
status, body = proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})
assert status == 429 and GOOGLE_KEY not in json.dumps(body), body
google_reply["error"] = (500, {"error": {"message": "boom"}})
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})[0] == 502
google_reply.clear()
assert ai.gemini_used_micro(portal.db().get(f"tok#{tid}"), now) == 2550, "failed calls cost nothing"
print("  ok  streaming, n>1, empty input and unknown routes are refused; provider errors are mapped and carry no key")

# ---------- 5. the cap ----------
assert ai_set(tid, limit_usd=0.01)[0] == 200
google_reply["ok"] = {"choices": [{"message": {"content": "big"}}], "usage": {"prompt_tokens": 1000, "total_tokens": 4000}}
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})[0] == 200  # 2550 + 300 + 7500 > 10000 after this one
status, body = proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})
assert status == 429 and body["error"]["code"] == "insufficient_quota", body
calls_before = len(google_calls)
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})[0] == 429 and len(google_calls) == calls_before, "no Google call once the cap is reached"

print("  ok  once the cap is reached the proxy stops before calling Google")
google_reply.clear()
assert ai_set(tid, limit_usd=20)[0] == 200 and proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})[0] == 200

# ---------- 6. models ----------
assert ai_set(tid, model="gemini-3.5-flash-lite")[0] == 200 and env(slug)["AI_MODEL"] == "gemini-3.5-flash-lite"
assert ai_set(tid, model="gemini-3.5-flash")[0] == 400 and ai_set(tid, model="gemini-9")[0] == 400, "gemini-3.5-flash-lite is the only Gemini model"
assert ai.model_of(portal.db().get(f"tok#{tid}")) == "gemini-3.5-flash-lite" and gemini.models() == ["gemini-3.5-flash-lite"]
print("  ok  gemini-3.5-flash-lite is the one Gemini model")

# ---------- 7. off, revoke ----------
assert ai_set(tid, enabled=False)[0] == 200
status, body = proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})
assert status == 403 and body["error"]["code"] == "not_enabled" and "OPENAI_API_KEY" not in env(slug)
assert ai_set(tid, enabled=True)[0] == 200 and env(slug)["OPENAI_API_KEY"] == gkey, "the same key comes back"
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})[0] == 200
assert call("POST", f"/api/admin/tokens/{tid}/revoke", {}, admin)[0] == 200
assert proxy(*CHAT, gkey, {"messages": [{"role": "user", "content": "x"}]})[1]["error"]["code"] == "token_inactive"
print("  ok  switching off or revoking stops the proxy at once; switching on again restores the same key")

# ---------- 8. moving a student between providers ----------
tid2, session2, slug2 = new_student("Bob")
assert ai_set(tid2, enabled=True, provider="openrouter")[0] == 200
orkey = router.by_name(f"student:{slug2}")
assert orkey and not orkey["disabled"]
deploy(session2, slug2)
assert ai_set(tid2, enabled=True)[0] == 200 and env(slug2)["OPENAI_API_KEY"] == orkey["key"]
status, body = ai_set(tid2, provider="gemini")
assert status == 200 and orkey["disabled"] is True, "the OpenRouter key is disabled when the student moves to Gemini"
assert body["token"]["ai"]["model"] == "gemini-3.5-flash-lite", "the old model id does not carry over"
assert env(slug2)["OPENAI_BASE_URL"] == PROXY + "/v1"
ai.sweep(now)
assert orkey["disabled"] is True, "the sweeper keeps it disabled"
status, body = ai_set(tid2, provider="openrouter")
assert status == 200 and orkey["disabled"] is False and env(slug2)["OPENAI_API_KEY"] == orkey["key"] and body["token"]["ai"]["model"] == "anthropic/claude-haiku-5.5"
status, body = proxy(*CHAT, stored(tid2)["gkey"], {"messages": [{"role": "user", "content": "x"}]})
assert status == 403 and body["error"]["code"] == "not_enabled", "a Gemini key does nothing for an OpenRouter student"
print("  ok  moving between providers swaps the environment, disables/re-enables the OpenRouter key, resets the model")

# ---------- 9. nothing secret in any response ----------
blob = "\n".join(EXPOSED)
assert GOOGLE_KEY not in blob and "ga_" + slug + "." not in blob, "no key material in any teacher response"
listing = json.dumps(call("GET", "/api/admin/tokens", session=admin)[1])
assert GOOGLE_KEY not in listing and "gkey" not in listing and "ga_" + slug + "." not in listing
print("  ok  the teacher's API never shows the Google key or any student's proxy key")

os.environ.update(AI_GEMINI_ENABLED="false")
assert call("GET", "/api/student/me", session=session2)[1]["token"]["ai"]["providers"] == ["openrouter"]
print("test_ai_gemini.py: all passed")
