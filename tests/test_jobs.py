"""Background jobs (lambda/jobs.py): the teacher's switch and limits, the second function that runs the jobs, start/read through the gateway, the runner,
failures that never leak the student's error text, the limits (per day, at once, global), stale jobs, and removal.

Real Python handlers from the uploaded zip are executed by FakeLambda, so the student contract ({"job": ...} in, a JSON result out) is tested for real.
"""
import io
import json
import os
import sys
import time
import zipfile

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true",
                  SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t", GATEWAY_CACHE_SECONDS="0", GATEWAY_NEGATIVE_CACHE_SECONDS="0",
                  INFLIGHT_GLOBAL="3", INFLIGHT_STUDENT="2", PER_IP_PER_MINUTE="1000", JOB_RUNNER_FUNCTION="runner", INFLIGHT_JOBS_GLOBAL="2")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import gateway  # noqa: E402
import job_runner  # noqa: E402
import jobs  # noqa: E402
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
backends._clients.update({"lambda": lam, "lambda-long": lam, "logs": logs, "s3": s3})
now = int(time.time())
db = portal.db

pending = []  # the runner is started asynchronously by AWS; here the test decides when it runs
lam.platform["runner"] = lambda event: pending.append(event)

APP = '''
import json, os
def handler(event, context):
    if "job" in event:
        job = event["job"]
        print("working on", job["name"])
        if job["name"] == "boom":
            raise RuntimeError("secret-token-123 leaked into the message")
        if job["name"] == "big":
            return {"blob": "x" * 200000}
        if job["name"] == "text":
            return "plain text result"
        if job["name"] == "nothing":
            return None
        return {"echo": job["payload"], "name": job["name"], "id": job["id"], "greeting": os.environ.get("GREETING")}
    return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"web": True})}
'''


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def call(method, path, body=None, session=None, at=None, query=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}}, "queryStringParameters": query,
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]


def new_student(name):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    return made["item"]["id"], call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def teacher(tid, **body):
    return call("POST", f"/api/admin/tokens/{tid}/backend", body, admin)


def backend_of(session, at=None):
    return call("GET", "/api/student/me", session=session, at=at)[1]["token"]["backend"]


def gw(path, method="GET", body=None, at=None, secret="s3cr3t", ip="9.9.9.9"):
    head = {"x-viewer-ip": ip}
    if secret is not None:
        head["x-origin-secret"] = secret
    event = {"rawPath": path, "rawQueryString": "", "headers": head, "body": body, "requestContext": {"http": {"method": method, "sourceIp": "10.0.0.1"}}}
    out = gateway.handler(event, None, now=at if at is not None else now)
    return out["statusCode"], json.loads(out["body"]), out["headers"]


def deploy(session, slug, files=None):
    assert call("POST", "/api/student/backend/upload-url", session=session)[0] == 200
    store[f"uploads/{slug}-backend.zip"] = {"Body": zip_bytes(files or {"app.py": APP})}
    status, body = call("POST", "/api/student/backend/publish", session=session)
    assert status == 200, body
    for _ in range(5):
        state = call("GET", "/api/student/backend", session=session)[1]["token"]["backend"]["state"]
        if state == "ready":
            return state
    return state


def start(slug, name, payload=None, at=None, ip="9.9.9.9"):
    return gw(f"/b/{slug}/_jobs/{name}", "POST", None if payload is None else json.dumps(payload), at=at, ip=ip)


def read(slug, job_id, at=None):
    return gw(f"/b/{slug}/_jobs/{job_id}", at=at)


def run_pending(at=None):
    ran = []
    while pending:
        event = pending.pop(0)
        job_runner.handler(event) if at is None else jobs.run(event["slug"], event["id"], at)
        ran.append(event["id"])
    return ran


def counter(key):
    return (db().get(key) or {}).get("count", 0)


# ===================== off by default; the teacher's switch and limits =====================
dana_id, dana, _ = new_student("dana")
view = backend_of(dana)["jobs"]
assert view["enabled"] is False and view["per_day"] == 20 and view["concurrent"] == 1 and view["minutes"] == 5 and view["max"]["minutes"] == 15
teacher(dana_id, enabled=True)
for bad in ({"jobs_enabled": "yes"}, {"jobs_per_day": -1}, {"jobs_per_day": 1001}, {"jobs_per_day": "5"}, {"jobs_per_day": True}, {"jobs_concurrent": 0},
            {"jobs_concurrent": 6}, {"jobs_minutes": 0}, {"jobs_minutes": 16}, {"jobs_minutes": 2.5}):
    assert teacher(dana_id, **bad)[0] == 400, bad
assert call("POST", f"/api/admin/tokens/{dana_id}/backend", {"jobs_enabled": True}, dana)[0] == 401, "a student cannot switch jobs on"
assert deploy(dana, "dana") == "ready"
assert "sd-dana_job" not in lam.functions, "no job function while jobs are off"
status, body, _ = start("dana", "report", {"n": 1})
assert status == 403 and body["error"] == "jobs_off"
print("  ok  off by default: no second function, the gateway refuses, invalid limits are rejected, only the teacher can switch it")

# ===================== switching on creates the job function with the longer limit =====================
status, body = teacher(dana_id, jobs_enabled=True, jobs_minutes=3)
assert status == 200 and body["token"]["backend"]["jobs"]["enabled"] is True and body["token"]["backend"]["jobs"]["ready"] is True
web, job = lam.functions["sd-dana"], lam.functions["sd-dana_job"]
assert job["config"]["Timeout"] == 180 and web["config"]["Timeout"] == 10, "the web function keeps its hard limit; only the job function is long"
assert job["config"]["Runtime"] == web["config"]["Runtime"] and job["config"]["Handler"] == web["config"]["Handler"] and job["config"]["Role"] == web["config"]["Role"]
assert job["tags"]["kind"] == "job" and job["zip"] == web["zip"] and logs.retention["/aws/lambda/sd-dana_job"] == 7
assert teacher(dana_id, jobs_minutes=7)[0] == 200 and lam.functions["sd-dana_job"]["config"]["Timeout"] == 420, "the time limit follows the teacher's setting"
teacher(dana_id, jobs_minutes=3)
print("  ok  switching on creates sd-<slug>_job from the same code with the longer time limit; the web function keeps 10 s")

# ===================== start, wait, read =====================
t = now
status, body, _ = start("dana", "report", {"notes": [1, 2, 3]}, at=t)
assert status == 202 and body["status"] == "queued" and len(body["id"]) == 32 and body["name"] == "report", body
job_id = body["id"]
assert lam.calls[-1] == ("invoke_platform", "runner", "Event"), "the runner is started asynchronously"
assert not any(c[0] == "invoke" and c[1] == "sd-dana" for c in lam.calls), "a job never reaches the web function"
status, body, _ = read("dana", job_id, at=t + 1)
assert status == 200 and body["status"] == "queued" and "result" not in body
assert counter("jobsrun#dana") == 1 and counter("jobsrun#global") == 1, "the slots are held while the job is queued or running"
run_pending(at=t + 2)
status, body, _ = read("dana", job_id, at=t + 3)
assert status == 200 and body["status"] == "done" and body["result"]["echo"] == {"notes": [1, 2, 3]} and body["result"]["name"] == "report" and body["result"]["id"] == job_id, body
assert body["started"] == t + 2 and body["finished"] == t + 2
assert counter("jobsrun#dana") == 0 and counter("jobsrun#global") == 0, "the slots are released when the job ends"
item = db().get(f"job#dana#{job_id}")
assert item["ttl"] == t + 2 + DAY, "a finished job is kept for one day"
print("  ok  start -> 202 queued -> runner -> done with the student's return value; slots held then released; the web function is never called")

# results of other shapes
for name, expected in (("text", "plain text result"), ("nothing", None)):
    jid = start("dana", name, at=t + 10)[1]["id"]
    run_pending(at=t + 11)
    body = read("dana", jid, at=t + 12)[1]
    assert body["status"] == "done" and body["result"] == expected, (name, body)
jid = start("dana", "echo", at=t + 20)[1]["id"]
run_pending(at=t + 21)
assert read("dana", jid, at=t + 22)[1]["result"]["echo"] is None, "no body means no input"
listing = gw("/b/dana/_jobs", at=t + 30)
assert listing[0] == 200 and len(listing[1]["jobs"]) == 4 and listing[1]["jobs"][0]["created"] >= listing[1]["jobs"][-1]["created"] and all("result" not in j for j in listing[1]["jobs"])
print("  ok  a string, null and a missing input work; the list is newest first and carries no results")

# ===================== failures never show the student's own error text =====================
jid = start("dana", "boom", at=t + 40)[1]["id"]
run_pending(at=t + 41)
body = read("dana", jid, at=t + 42)[1]
assert body["status"] == "failed" and "secret-token-123" not in json.dumps(body) and "npm run logs" in body["error"], body
assert counter("jobsrun#dana") == 0, "a failed job also frees its slot"
lam.function_error = "Task timed out after 180.00 seconds"
jid = start("dana", "slowpoke", at=t + 50)[1]["id"]
run_pending(at=t + 51)
body = read("dana", jid, at=t + 52)[1]
assert body["status"] == "failed" and "3 minutes" in body["error"], body
lam.function_error = None
jid = start("dana", "big", at=t + 60)[1]["id"]
run_pending(at=t + 61)
body = read("dana", jid, at=t + 62)[1]
assert body["status"] == "failed" and "100 KB" in body["error"] and "result" not in body, body
del lam.functions["sd-dana_job"]
jid = start("dana", "report", at=t + 70)[1]["id"]
run_pending(at=t + 71)
assert "Publish your backend again" in read("dana", jid, at=t + 72)[1]["error"]
assert deploy(dana, "dana") == "ready" and "sd-dana_job" in lam.functions, "publishing again brings the job function back"
print("  ok  a crash, a timeout, a too-big result and a missing function each fail with a plain message; none shows the student's error text")

# ===================== limits =====================
bob_id, bob, _ = new_student("bob")
teacher(bob_id, enabled=True, jobs_enabled=True, jobs_per_day=2, jobs_concurrent=1)
deploy(bob, "bob")
day_start = (t // DAY) * DAY + 3600
first = start("bob", "a", at=day_start)
assert first[0] == 202
busy = start("bob", "b", at=day_start + 1)
assert busy[0] == 429 and busy[1]["error"] == "jobs_busy", "one at a time"
run_pending(at=day_start + 2)
assert start("bob", "b", at=day_start + 3)[0] == 202
run_pending(at=day_start + 4)
status, body, headers = start("bob", "c", at=day_start + 5)
assert status == 429 and body["error"] == "jobs_allowance_exhausted" and "retry-after" in headers, "two a day"
assert counter("jobsrun#bob") == 0, "a refused start leaves no slot taken"
assert start("bob", "c", at=day_start + DAY)[0] == 202, "the allowance resets the next day"
run_pending(at=day_start + DAY + 1)
print("  ok  one job at a time, jobs per day (with retry-after), a refusal never leaves a slot taken, the allowance resets daily")

# the platform-wide cap protects the account (new accounts may have only 10 concurrent executions)
cy_id, cy, _ = new_student("cy")
di_id, di, _ = new_student("di")
for sid, sess, slug in ((cy_id, cy, "cy"), (di_id, di, "di")):
    teacher(sid, enabled=True, jobs_enabled=True, jobs_concurrent=2)
    deploy(sess, slug)
os.environ["INFLIGHT_JOBS_GLOBAL"] = "2"
g = now + 5 * DAY
assert start("cy", "a", at=g)[0] == 202 and start("cy", "b", at=g)[0] == 202
refused = start("di", "a", at=g)
assert refused[0] == 429 and refused[1]["error"] == "jobs_busy" and counter("jobsrun#di") == 0, "the global cap is full: the second student waits"
run_pending(at=g + 1)
assert start("di", "a", at=g + 2)[0] == 202
run_pending(at=g + 3)
# a slot leaked by a crashed runner heals after the job's own time limit (not after a minute)
db().put({"pk": "jobsrun#cy", "count": 2, "updated_at": g})
assert start("cy", "c", at=g + 100)[0] == 429, "a minute later the slots are still honoured: a job may legitimately run for minutes"
assert start("cy", "c", at=g + 5 * 60 + 100)[0] == 429, "still honoured just before the job's time limit (5 minutes by default) plus two minutes"
assert start("cy", "c", at=g + 5 * 60 + 121)[0] == 202, "after the job's time limit plus two minutes the stuck slots heal"
run_pending(at=g + 400)
print("  ok  a global cap on jobs at once protects the account; leaked slots heal after the job's time limit")

# ===================== input checks =====================
for name in ("Report", "a b", "x" * 41, "-x", ".."):
    assert start("dana", name, at=g + 500)[0:2][0] == 400, name
assert start("dana", "ok", None, at=g + 500)[0] == 202
run_pending(at=g + 501)
big = gw("/b/dana/_jobs/ok", "POST", json.dumps({"text": "x" * 70000}), at=g + 502)
assert big[0] == 413 and big[1]["error"] == "payload_too_large"
assert gw("/b/dana/_jobs/ok", "POST", "{not json", at=g + 503)[0:2][0] == 400
assert counter("jobsrun#dana") == 0
assert gw("/b/dana/_jobs/ok", "DELETE", at=g + 504)[0] == 405
assert gw("/b/dana/_jobs/not-an-id", at=g + 505)[1]["error"] == "bad_job_id"
assert gw(f"/b/dana/_jobs/{'0' * 32}", at=g + 506)[0] == 404
assert gw("/b/dana/_jobs/ok", "POST", "{}", secret="wrong", at=g + 507)[0] == 403, "the origin secret is still required"
print("  ok  bad names, a too-big or malformed input, wrong methods, bad and unknown ids and a missing origin secret are refused without taking a slot")

# ===================== one student cannot read another's jobs =====================
jid = start("cy", "private", {"secret": 1}, at=g + 600)[1]["id"]
run_pending(at=g + 601)
assert read("cy", jid, at=g + 602)[0] == 200
assert read("di", jid, at=g + 602)[0] == 404, "a job id works only under its own site"
print("  ok  a job id is only readable through the site that started it")

# ===================== stale jobs, idempotent runner =====================
stale = start("cy", "stuck", at=g + 700)[1]["id"]
status, body, _ = read("cy", stale, at=g + 700 + 5 * 60 + 300 + 1)
assert body["status"] == "failed" and "did not finish" in body["error"], "a job nobody finished is not shown as running for ever"
pending.clear()
jid = start("di", "twice", at=g + 800)[1]["id"]
event = pending[-1]
run_pending(at=g + 801)
before = db().get(f"job#di#{jid}")
assert jobs.run(event["slug"], event["id"], g + 900) is None and db().get(f"job#di#{jid}") == before and counter("jobsrun#di") == 0, "a second run changes nothing and releases nothing twice"
print("  ok  a job nobody finished is shown as failed; running the same job twice does nothing")

# ===================== switching off, environment copy, removal =====================
assert teacher(dana_id, jobs_enabled=False)[0] == 200 and lam.functions["sd-dana_job"]["concurrency"] == 0, "off: AWS itself refuses the job function"
assert start("dana", "x", at=g + 1000)[1]["error"] == "jobs_off"
teacher(dana_id, jobs_enabled=True)
assert lam.functions["sd-dana_job"]["concurrency"] is None, "back on: the refusal is lifted"
assert call("PUT", "/api/student/backend/env", {"name": "GREETING", "value": "hello"}, dana)[0] == 200
assert lam.functions["sd-dana_job"]["config"]["Environment"]["Variables"]["GREETING"] == "hello", "the student's variables reach the job function too"
jid = start("dana", "env", at=g + 1100)[1]["id"]
run_pending(at=g + 1101)
assert read("dana", jid, at=g + 1102)[1]["result"]["greeting"] == "hello"
teacher(dana_id, enabled=False)
assert gw("/b/dana/_jobs/x", "POST", "{}", at=g + 1200)[1]["error"] == "backend_off", "the backend switch also stops new jobs"
teacher(dana_id, enabled=True)
assert call("DELETE", "/api/student/backend", session=dana)[0] == 200
assert "sd-dana" not in lam.functions and "sd-dana_job" not in lam.functions and "/aws/lambda/sd-dana_job" not in logs.groups, "removing the backend removes both functions and the job log group"
kept = backend_of(dana)["jobs"]
assert kept["enabled"] is True and kept["minutes"] == 3 and kept["ready"] is False, "the teacher's job settings survive a removal"
print("  ok  off refuses at AWS too, the student's variables are copied, the backend switch also stops jobs, removal deletes both functions and keeps the settings")

# ===================== the job function never exists for a student who has no web function yet =====================
eve_id, eve, _ = new_student("eve")
teacher(eve_id, enabled=True, jobs_enabled=True)
assert "sd-eve_job" not in lam.functions
assert deploy(eve, "eve") == "ready" and "sd-eve_job" in lam.functions, "enabled before the first deploy: the first publish creates both"
print("  ok  enabled before the first deploy: the first publish creates both functions")

print("test_jobs.py: all passed")
