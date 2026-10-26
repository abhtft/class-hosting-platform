"""Student backends: teacher toggle + run allowance, deploy flow, gateway checks, limiter, response shaping, removal.

Real Python (and Node, when available) handlers from the uploaded zip are executed by FakeLambda, so the student
contract is tested for real.
"""
import io
import json
import os
import shutil
import sys
import threading
import time
import zipfile

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true",
                  SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t", GATEWAY_CACHE_SECONDS="0", GATEWAY_NEGATIVE_CACHE_SECONDS="0",
                  INFLIGHT_GLOBAL="3", INFLIGHT_STUDENT="2", PER_IP_PER_MINUTE="1000")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import gateway  # noqa: E402
import portal  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeS3, store  # noqa: E402

DAY = portal.DAY
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
logs, s3 = FakeLogs(), FakeS3()
lam = FakeLambda(logs)
backends._clients.update({"lambda": lam, "logs": logs, "s3": s3})
now = int(time.time())
db = portal.db

APP = '''
import json, os
def handler(event, context):
    print("hello log")
    http = event["requestContext"]["http"]
    return {"statusCode": 200, "headers": {"content-type": "application/json", "Content-Length": "999", "x-custom": "yes"},
            "body": json.dumps({"path": event["rawPath"], "query": event["rawQueryString"], "method": http["method"], "ip": http["sourceIp"],
                                "leaked": [h for h in event["headers"] if h in ("x-origin-secret", "x-viewer-ip")],
                                "greeting": os.environ.get("GREETING")})}
'''


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def call(method, path, body=None, session=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]


def new_student(name):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    return made["item"]["id"], call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def backend_of(session, at=None):
    return call("GET", "/api/student/me", session=session, at=at)[1]["token"]["backend"]


def teacher(tid, **body):
    return call("POST", f"/api/admin/tokens/{tid}/backend", body, admin)


def gw(path, method="GET", body=None, headers=None, at=None, secret="s3cr3t", ip="9.9.9.9"):
    head = {"x-viewer-ip": ip, **(headers or {})}
    if secret is not None:
        head["x-origin-secret"] = secret
    event = {"rawPath": path, "rawQueryString": path.partition("?")[2], "headers": head, "body": body,
             "requestContext": {"http": {"method": method, "sourceIp": "10.0.0.1"}}}
    event["rawPath"] = path.partition("?")[0]
    out = gateway.handler(event, None, now=at if at is not None else now)
    try:
        return out["statusCode"], json.loads(out["body"]), out["headers"]
    except ValueError:
        return out["statusCode"], out["body"], out["headers"]


def deploy(session, slug, files, polls=1):
    """Upload + publish + poll until ready."""
    lam.polls_until_active = polls
    url = call("POST", "/api/student/backend/upload-url", session=session)
    assert url[0] == 200, url
    store[f"uploads/{slug}-backend.zip"] = {"Body": zip_bytes(files)}
    status, body = call("POST", "/api/student/backend/publish", session=session)
    assert status == 200, body
    state = body["token"]["backend"]["state"]
    for _ in range(5):
        if state == "ready":
            break
        state = call("GET", "/api/student/backend", session=session)[1]["token"]["backend"]["state"]
    return state


def set_code(slug, source, name="app.py"):
    lam.functions[f"sd-{slug}"]["zip"] = zip_bytes({name: source})


def counter(key):
    return (db().get(key) or {}).get("count", 0)


# ===================== teacher switch and allowance =====================
alice_id, alice, _ = new_student("alice")
assert backend_of(alice)["available"] is True and backend_of(alice)["enabled"] is False and backend_of(alice)["usage"] is None

status, body = call("POST", "/api/student/backend/upload-url", session=alice)
assert status == 403 and body["error"] == backends.NOT_ENABLED == "Owner not enabled for you. Please contact site owner."
assert call("POST", f"/api/admin/tokens/{alice_id}/backend", {"enabled": True}, alice)[0] == 401, "students cannot flip the switch"
for bad in ({"runs_limit": -1}, {"runs_limit": "5"}, {"runs_limit": True}, {"runs_limit": 1_000_001}, {"runs_period": "week"}, {"enabled": "yes"}):
    assert teacher(alice_id, **bad)[0] == 400, bad

status, body = teacher(alice_id, enabled=True)
b = body["token"]["backend"]
assert status == 200 and b["enabled"] is True and b["usage"] == {"period": "month", "used": 0, "limit": 5000, "resets_at": b["usage"]["resets_at"]}
assert teacher(alice_id, runs_limit=3, runs_period="day")[1]["token"]["backend"]["usage"]["limit"] == 3
assert teacher(alice_id, runs_limit=2000, runs_period="month")[1]["token"]["backend"]["usage"]["period"] == "month"

# ===================== deploy =====================
assert deploy(alice, "alice", {"app.py": APP}, polls=2) == "ready"
function = lam.functions["sd-alice"]
assert function["config"]["Role"] == "arn:aws:iam::1:role/sandbox" and function["config"]["Timeout"] == 10 and function["config"]["MemorySize"] == 256
assert function["tags"]["managed-by"] == "student-deploy" and logs.retention["/aws/lambda/sd-alice"] == 7
mid = call("POST", "/api/student/backend/publish", session=alice)[1]["token"]["backend"]["state"]
assert mid == "creating", "an update shows creating until Lambda finishes"
assert ("update_function_code", "sd-alice") in lam.calls
assert call("GET", "/api/student/backend", session=alice)[1]["token"]["backend"]["state"] in ("creating", "ready")
for _ in range(3):
    call("GET", "/api/student/backend", session=alice)
assert backend_of(alice)["state"] == "ready" and backend_of(alice)["runtime"] == "python3.12"

# ===================== gateway: happy path, hygiene =====================
status, body, headers = gw("/b/alice/hello?x=1", headers={"authorization": "Bearer student-own-auth"})
assert status == 200 and body == {"path": "/hello", "query": "x=1", "method": "GET", "ip": "9.9.9.9", "leaked": [], "greeting": None}, body
assert headers["x-custom"] == "yes" and headers["x-content-type-options"] == "nosniff" and headers["cache-control"] == "no-store"
assert "content-length" not in {k.lower() for k in headers}, "hop headers from the function are dropped"
assert gw("/b/alice")[1]["path"] == "/" and gw("/b/alice/")[1]["path"] == "/"
assert gw("/b/alice/x", method="POST", body='{"a":1}')[1]["method"] == "POST"
assert counter(f"runs#alice#{backends.period_info('month', now)[0]}") == 4
assert all(c[1] == "sd-alice" for c in lam.calls if c[0] == "invoke"), "only this student's function is ever invoked"

# direct calls and bad paths never reach the function
before = len([c for c in lam.calls if c[0] == "invoke"])
assert gw("/b/alice/hello", secret=None)[0] == 403 and gw("/b/alice/hello", secret="wrong")[0] == 403
for path in ("/b/ALICE/x", "/b/../x", "/b/", "/other", "/b/al ice/x"):
    assert gw(path)[0] == 404, path
assert gw("/b/nobody/x")[1]["error"] == "no_backend"
assert len([c for c in lam.calls if c[0] == "invoke"]) == before

# ===================== environment variables =====================
status, body = call("PUT", "/api/student/backend/env", {"name": "GREETING", "value": "s3cret-value-123"}, alice)
assert status == 200 and body["token"]["backend"]["env_names"] == ["GREETING"]
assert "s3cret-value-123" not in json.dumps(body) and "s3cret-value-123" not in json.dumps(db().items if hasattr(db(), "items") else {}), "values are never returned or stored"
assert gw("/b/alice/")[1]["greeting"] == "s3cret-value-123", "the function sees it"
for bad_name in ("1BAD", "AWS_REGION", "LAMBDA_X", "_HANDLER", "TZ", "a-b", ""):
    assert call("PUT", "/api/student/backend/env", {"name": bad_name, "value": "x"}, alice)[0] == 400, bad_name
assert call("PUT", "/api/student/backend/env", {"name": "BIG", "value": "x" * 1025}, alice)[0] == 400
assert call("DELETE", "/api/student/backend/env/GREETING", session=alice)[1]["token"]["backend"]["env_names"] == []
assert gw("/b/alice/")[1]["greeting"] is None

# ===================== logs =====================
lines = call("GET", "/api/student/backend/logs", session=alice)[1]["lines"]
assert lines and all(l["message"] == "hello log" for l in lines)

# ===================== run allowance =====================
carol_id, carol, _ = new_student("carol")
teacher(carol_id, enabled=True, runs_limit=3, runs_period="month")
assert deploy(carol, "carol", {"app.py": APP}) == "ready"
for _ in range(3):
    assert gw("/b/carol/x")[0] == 200
status, body, headers = gw("/b/carol/x")
resets = backends.period_info("month", now)[1]
assert status == 429 and body["error"] == "allowance_exhausted" and body["resets_at"] == resets and int(headers["retry-after"]) >= 1, body
assert counter("inflight#global") == 0 and counter("inflight#carol") == 0, "slots are released when the allowance is exhausted"
assert backend_of(carol)["usage"]["used"] == 3
invokes = len([c for c in lam.calls if c[0] == "invoke" and c[1] == "sd-carol"])
assert invokes == 3, "an exhausted allowance never invokes the function"
next_month = resets + 60
assert gw("/b/carol/x", at=next_month)[0] == 200, "a new month starts a new count"
assert teacher(carol_id, runs_limit=10)[0] == 200
status, body, _ = gw("/b/carol/x")
assert status == 200, ("a teacher top-up applies on the next request", status, body, backend_of(carol))
teacher(carol_id, runs_limit=5)
assert gw("/b/carol/x")[0] == 200 and gw("/b/carol/x")[0] == 429, "lowering the limit is enforced too"
teacher(carol_id, runs_limit=0)
assert gw("/b/carol/x")[1]["error"] == "allowance_exhausted", "0 means none"
teacher(carol_id, runs_period="total", runs_limit=1)
status, body, headers = gw("/b/carol/x")
assert status == 200 and gw("/b/carol/x")[1]["resets_at"] is None and "retry-after" not in headers, "total never resets"

# period boundaries (UTC), computed independently of the code under test
from datetime import datetime, timezone  # noqa: E402

for stamp in (1_798_000_000, 1_790_000_000, 1_803_000_000):
    d = datetime.fromtimestamp(stamp, timezone.utc)
    next_month = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)
    assert backends.period_info("month", stamp) == (d.strftime("%Y-%m"), int(next_month.timestamp())), stamp
    assert backends.period_info("day", stamp) == (d.strftime("%Y-%m-%d"), int(d.replace(hour=0, minute=0, second=0).timestamp()) + DAY)
assert datetime.fromtimestamp(1_798_000_000, timezone.utc).month == 12, "the December-to-January rollover is covered"
assert backends.period_info("total", now) == ("total", None)

# the counter itself: parallel callers can never exceed the limit
shared = portal.MemoryStore()
wins = []
threads = [threading.Thread(target=lambda: wins.append(shared.counter_add("runs#x", 1, limit=10))) for _ in range(60)]
[t.start() for t in threads]
[t.join() for t in threads]
assert sum(1 for w in wins if w is not None) == 10 and shared.items["runs#x"]["count"] == 10
assert shared.counter_add("zero", 1, limit=0) is None and shared.counter_sub("never-used") is None

# ===================== in-flight limiter =====================
dave_id, dave, _ = new_student("dave")
teacher(dave_id, enabled=True, runs_limit=100)
assert deploy(dave, "dave", {"app.py": APP}) == "ready"
db().put({"pk": "inflight#global", "count": 3, "updated_at": now})
status, body, headers = gw("/b/dave/x")
assert status == 429 and body["error"] == "busy" and headers["retry-after"] == "1"
assert counter("runs#dave#" + backends.period_info("month", now)[0]) == 0, "a rejected request does not consume a run"
db().put({"pk": "inflight#global", "count": 0, "updated_at": now})
db().put({"pk": "inflight#dave", "count": 2, "updated_at": now})
assert gw("/b/dave/x")[1]["error"] == "busy" and counter("inflight#global") == 0, "the global slot is released when the student's own cap rejects"
db().put({"pk": "inflight#dave", "count": 0, "updated_at": now})
# a leaked counter (crash between increment and decrement) heals itself after the grace period
db().put({"pk": "inflight#global", "count": 3, "updated_at": now - 120})
assert gw("/b/dave/x")[0] == 200 and counter("inflight#global") == 0
db().put({"pk": "inflight#global", "count": 3, "updated_at": now - 5})
assert gw("/b/dave/x")[0] == 429, "a recent counter is NOT treated as leaked"
db().put({"pk": "inflight#global", "count": 0, "updated_at": now})

# per-visitor rate limit
os.environ["PER_IP_PER_MINUTE"] = "2"
assert [gw("/b/dave/x", ip="5.5.5.5", at=now)[0] for _ in range(3)] == [200, 200, 429]
assert gw("/b/dave/x", ip="6.6.6.6")[0] == 200, "another visitor is unaffected"
assert gw("/b/dave/x", ip="5.5.5.5", at=now + 60)[0] == 200, "the next minute starts fresh"
os.environ["PER_IP_PER_MINUTE"] = "1000"

# ===================== lifecycle and the toggle =====================
erin_id, erin, _ = new_student("erin")
teacher(erin_id, enabled=True, runs_limit=50)
assert deploy(erin, "erin", {"app.py": APP}) == "ready"
used = lambda: counter("runs#erin#" + backends.period_info("month", now)[0])  # noqa: E731
assert gw("/b/erin/x")[0] == 200 and used() == 1
tok = f"tok#{erin_id}"
db().update(tok, {"expires_at": now - 60})
assert gw("/b/erin/x")[1]["error"] == "token_expired" and gw("/b/erin/x")[0] == 403
db().update(tok, {"expires_at": now - 4 * DAY})
assert gw("/b/erin/x")[1]["error"] == "site_offline" and gw("/b/erin/x")[0] == 503
db().update(tok, {"expires_at": now + DAY, "archived": True})
assert gw("/b/erin/x")[0] == 503, "an archived site's backend is offline too"
db().update(tok, {"archived": False, "revoked": True})
assert gw("/b/erin/x")[0] == 403
db().update(tok, {"revoked": False})
assert used() == 1, "blocked requests never consume a run"

status, body = teacher(erin_id, enabled=False)
assert body["token"]["backend"]["enabled"] is False and ("put_function_concurrency", "sd-erin", 0) in lam.calls
status, body, _ = gw("/b/erin/x")
assert status == 503 and body["error"] == "backend_off" and used() == 1
assert call("POST", "/api/student/backend/upload-url", session=erin)[0] == 403, "OFF refuses deploys"
assert call("POST", "/api/student/backend/publish", session=erin)[0] == 403
assert call("PUT", "/api/student/backend/env", {"name": "A", "value": "b"}, erin)[0] == 403
teacher(erin_id, enabled=True)
assert gw("/b/erin/x")[0] == 200 and ("delete_function_concurrency", "sd-erin") in lam.calls, "ON restores service"
lam.concurrency_error = True
assert teacher(erin_id, enabled=False)[0] == 200 and gw("/b/erin/x")[1]["error"] == "backend_off", "toggle works even if AWS refuses the reservation"
lam.concurrency_error = False
teacher(erin_id, enabled=True)

# ===================== deploy validation =====================
frank_id, frank, _ = new_student("frank")
teacher(frank_id, enabled=True)


def publish_expect(files, fragment, size=None):
    body_bytes = zip_bytes(files) if size is None else b"x" * size
    store["uploads/frank-backend.zip"] = {"Body": body_bytes}
    status, body = call("POST", "/api/student/backend/publish", session=frank)
    assert status == 400 and fragment in body["error"], (status, body)


publish_expect({"readme.txt": "x"}, "No entry file")
publish_expect({"app.py": "x", "index.mjs": "x"}, "only one entry file")
publish_expect({"app.py": "x", ".env": "SECRET=1"}, "Remove .env")
publish_expect({"app.py": "x", "sub/.env.local": "S=1"}, "Remove sub/.env.local")
publish_expect({"app.py": "x", "../evil.py": "x"}, "Unsafe path")
publish_expect({"app.py": "x", **{f"f{i}.py": "x" for i in range(501)}}, "Too many files")
publish_expect({}, "limit is 5 MB", size=6 * 1024 * 1024)  # oversize zip
assert "uploads/frank-backend.zip" not in store, "an oversize upload is deleted"
store["uploads/frank-backend.zip"] = {"Body": b"not a zip"}
assert call("POST", "/api/student/backend/publish", session=frank)[1]["error"] == "Upload is not a valid zip file."
store.pop("uploads/frank-backend.zip", None)
assert call("POST", "/api/student/backend/publish", session=frank)[1]["error"].startswith("No upload found")
link = zipfile.ZipInfo("app.py")
link.external_attr = 0o120777 << 16
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr(link, "/etc/passwd")
store["uploads/frank-backend.zip"] = {"Body": buf.getvalue()}
assert "Symbolic links" in call("POST", "/api/student/backend/publish", session=frank)[1]["error"]
assert "sd-frank" not in lam.functions, "nothing is created from a rejected zip"

# creation failure is shown, then a new upload recovers
lam.fail_create = "Resource limit exceeded"
assert deploy(frank, "frank", {"app.py": APP}) != "ready"
failed = backend_of(frank)
assert failed["state"] == "failed" and "Resource limit" in failed["error"]
lam.fail_create = None
assert deploy(frank, "frank", {"app.py": APP}) == "ready" and backend_of(frank)["error"] == ""
# switching runtime needs a clean slate
store["uploads/frank-backend.zip"] = {"Body": zip_bytes({"index.mjs": "export const handler = async () => ({})"})}
assert "switched runtime" in call("POST", "/api/student/backend/publish", session=frank)[1]["error"]

# ===================== response shaping =====================
gina_id, gina, _ = new_student("gina")
teacher(gina_id, enabled=True)
assert deploy(gina, "gina", {"app.py": APP}) == "ready"


def respond(source):
    set_code("gina", source)
    return gw("/b/gina/x")


status, body, headers = respond("def handler(e, c):\n    return {'hello': 'world'}")
assert status == 200 and body == {"hello": "world"} and headers["content-type"] == "application/json", "a plain value becomes a 200 JSON body"
assert respond("def handler(e, c):\n    return 'plain text'")[1] == "plain text"
assert respond("def handler(e, c):\n    return {'statusCode': 201, 'headers': {'Set-Cookie': 'a=b'}, 'body': {'k': 1}}")[:2] == (201, {"k": 1}), "a non-string body is serialized as JSON"
assert respond("def handler(e, c):\n    return {'statusCode': 'ok', 'body': ''}")[1]["error"] == "bad_response"
assert respond("def handler(e, c):\n    return {'statusCode': 99, 'body': ''}")[0] == 502
assert respond("def handler(e, c):\n    return {'statusCode': 200, 'headers': 'nope'}")[0] == 502
status, body, _ = respond("def handler(e, c):\n    return {'statusCode': 200, 'body': 'x' * (1024 * 1024 + 1)}")
assert status == 502 and body["error"] == "response_too_large"
status, body, _ = respond("def handler(e, c):\n    raise RuntimeError('db password is hunter2')")
assert status == 502 and body["error"] == "function_error" and "hunter2" not in json.dumps(body), "crash details never reach the visitor"
lam.function_error = "Task timed out after 10.00 seconds"
assert gw("/b/gina/x")[:2] == (504, {"error": "function_timeout", "message": "The backend took too long to respond."})
lam.function_error = None
lam.invoke_error = ("TooManyRequestsException", "Rate Exceeded.")
assert gw("/b/gina/x")[1]["error"] == "busy"
lam.invoke_error = ("ResourceNotFoundException", "gone")
assert gw("/b/gina/x")[1]["error"] == "no_backend"
lam.invoke_error = ("ServiceException", "boom")
assert gw("/b/gina/x")[1]["error"] == "backend_unavailable"
lam.invoke_error = None
assert gw("/b/gina/x", body="x" * (1024 * 1024 + 1), method="POST")[0] == 413
assert counter("inflight#global") == 0, "slots are always released, whatever happened"

# ===================== removal =====================
assert call("DELETE", "/api/student/backend", session=gina)[1]["token"]["backend"]["state"] == "none"
assert "sd-gina" not in lam.functions and "/aws/lambda/sd-gina" not in logs.groups
kept = backend_of(gina)
assert kept["enabled"] is True and kept["usage"]["limit"] == 5000, "the teacher's settings survive removal"
assert gw("/b/gina/x")[1]["error"] == "no_backend"
assert call("DELETE", "/api/student/backend", session=gina)[0] == 200, "removing twice is harmless"

# ===================== server not configured =====================
os.environ["BACKEND_ENABLED"] = "false"
assert backend_of(alice) == {"available": False}
assert call("POST", "/api/student/backend/upload-url", session=alice)[0] == 503 and teacher(alice_id, enabled=False)[0] == 503
os.environ["BACKEND_ENABLED"] = "true"

# ===================== Node runtime (same contract) =====================
if shutil.which("node"):
    hank_id, hank, _ = new_student("hank")
    teacher(hank_id, enabled=True)
    NODE = ("export const handler = async (event) => ({ statusCode: 200, headers: { 'content-type': 'application/json' },"
            " body: JSON.stringify({ path: event.rawPath, ip: event.requestContext.http.sourceIp, greeting: process.env.GREETING }) });")
    assert deploy(hank, "hank", {"index.mjs": NODE}) == "ready" and backend_of(hank)["runtime"] == "nodejs22.x"
    call("PUT", "/api/student/backend/env", {"name": "GREETING", "value": "hi from node"}, hank)
    status, body, _ = gw("/b/hank/ping")
    assert status == 200 and body == {"path": "/ping", "ip": "9.9.9.9", "greeting": "hi from node"}, body
    print("node runtime checked")

# ===================== the starter templates satisfy the contract =====================
template_root = os.path.join(here, "..", "student", "backend-template")
for flavor, runtime in (("python", "python3.12"), ("node", "nodejs22.x")):
    if flavor == "node" and not shutil.which("node"):
        continue
    folder = os.path.join(template_root, flavor)
    files = {n: open(os.path.join(folder, n), encoding="utf8").read() for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))}
    sid, session, slug = new_student(f"tmpl-{flavor}")
    teacher(sid, enabled=True)
    assert deploy(session, slug, files) == "ready" and backend_of(session)["runtime"] == runtime, flavor
    assert gw(f"/b/{slug}/health")[:2] == (200, {"ok": True}), flavor
    assert gw(f"/b/{slug}/hello?name=Sam")[1] == {"message": "Hello, Sam!"} and gw(f"/b/{slug}/hello")[1] == {"message": "Hello, world!"}, flavor
    assert gw(f"/b/{slug}/echo", method="POST", body='{"a": 1}')[1] == {"youSent": {"a": 1}}, flavor
    assert gw(f"/b/{slug}/echo", method="POST", body="{oops")[0] == 400 and gw(f"/b/{slug}/nope")[0] == 404, flavor
    assert any("hello requested for Sam" in l["message"] for l in call("GET", "/api/student/backend/logs", session=session)[1]["lines"]), f"{flavor}: console output reaches the student's logs"
    print(f"starter template {flavor} checked")

# ===================== caching: a freshly deployed backend is never hidden behind a stale "no backend" =====================
os.environ["GATEWAY_CACHE_SECONDS"], os.environ["GATEWAY_NEGATIVE_CACHE_SECONDS"] = "10", "2"
gateway._cache.clear()
ivy_id, ivy, _ = new_student("ivy")
teacher(ivy_id, enabled=True)
t0 = now + 1000
assert gw("/b/ivy/x", at=t0)[1]["error"] == "no_backend"
assert deploy(ivy, "ivy", {"app.py": APP}) == "ready"
assert gw("/b/ivy/x", at=t0 + 1)[1]["error"] == "no_backend", "a negative answer is cached only briefly (within 2 s)"
assert gw("/b/ivy/x", at=t0 + 3)[0] == 200, "after 2 s the new backend is served (the first live run showed 10 s of stale no_backend)"
teacher(ivy_id, runs_limit=7)  # a settings change is picked up within the 10 s positive cache
assert gw("/b/ivy/x", at=t0 + 5)[0] == 200
assert gw("/b/ivy/x", at=t0 + 14)[0] == 200
os.environ["GATEWAY_CACHE_SECONDS"], os.environ["GATEWAY_NEGATIVE_CACHE_SECONDS"] = "0", "0"
gateway._cache.clear()

# ===================== the REAL S3 client hands out SigV4 presigned URLs =====================
# (fakes cannot show this: the first live run failed with SignatureDoesNotMatch because the default client signs with SigV2)
fake_s3 = backends._clients.pop("s3")
try:
    real = backends.client("s3")
    assert real.meta.config.signature_version == "s3v4"
    url = real.generate_presigned_url("put_object", Params={"Bucket": "student-bucket", "Key": "uploads/x-backend.zip"}, ExpiresIn=600)
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url and "AWSAccessKeyId" not in url, url
    assert url.startswith("https://student-bucket.s3."), "virtual-hosted addressing, like the site uploader"
finally:
    backends._clients["s3"] = fake_s3

print("test_backend.py: all passed")
