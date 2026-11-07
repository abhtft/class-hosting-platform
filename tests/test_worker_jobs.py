"""Compute worker jobs (lambda/worker_jobs.py and the student and teacher routes): only a student at level 3 can use them, the upload form is limited and private to the
student, the queue file the machine reads, quotas (requests a day, open jobs, minutes a month), what the machine reports back, expiry, cancel and delete, that one student
can never reach another's job, and that nothing here ever starts the machine. S3 is a small fake."""
import json
import os
import sys

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d",
                  KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import portal  # noqa: E402
import worker  # noqa: E402
import worker_jobs as wj  # noqa: E402
from fakes import FakeS3, store  # noqa: E402

salt = b"saltsaltsaltsalt"
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)),
                                                "secret": portal.b64(b"3" * 32)})
DAY = 86400
NOW = 1_800_000_000
portal.STUDENT_SESSION_SECONDS = portal.TEACHER_SESSION_SECONDS = 30 * DAY  # the test jumps across days
s3 = FakeS3()
ec2_calls = []


class NoMachine:
    """Any call to EC2 from the jobs code is a failure: it must never start or touch the machine."""

    def __getattr__(self, name):
        ec2_calls.append(name)
        raise AssertionError(f"the jobs code called ec2.{name}")


worker._clients.update({"s3": s3, "ec2": NoMachine()})


def call(method, path, body=None, session=None, at=NOW):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}}, "headers": {"authorization": f"Bearer {session}"} if session else {},
             "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]


def new_student(name):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    return made["item"]["id"], call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


a_tid, a_session, a_slug = new_student("Asha")
b_tid, b_session, b_slug = new_student("Bela")
JOBS = "/api/student/worker/jobs"

# ---------- 1. not installed, level 0, level below 3: nothing works ----------
assert call("GET", JOBS, session=a_session)[0] == 409 and call("POST", JOBS, {"language": "python", "entry": "main.py"}, a_session)[0] == 409, "not installed"
os.environ.update(WORKER_INSTANCE_ID="i-0abc", WORKER_INSTANCE_TYPE="c7g.xlarge", WORKER_DISK_GB="30", WORKER_MAX_MINUTES="60")
for level in (0, 1, 2):
    call("POST", f"/api/admin/tokens/{a_tid}/worker-access", {"level": level}, admin)
    for method, path in (("GET", JOBS), ("POST", JOBS), ("GET", JOBS + "/aaaaaaaaaaaa"), ("POST", JOBS + "/aaaaaaaaaaaa/submit")):
        status, body = call(method, path, {"language": "python", "entry": "main.py"}, a_session)
        assert status in (403, 404) and (status == 403 or "No such" in body["error"]), (level, method, path, status, body)
assert call("POST", JOBS, {"language": "python", "entry": "main.py"}, a_session)[0] == 403, "level 2 cannot run their own code"
assert call("GET", JOBS)[0] == 401
print("  ok  not installed, level 0, 1 and 2: no student job can be made")

# ---------- 2. create, upload, submit ----------
call("POST", f"/api/admin/tokens/{a_tid}/worker-access", {"level": 3, "requests_per_day": 4, "minutes_per_month": 5, "job_minutes": 10}, admin)
call("POST", f"/api/admin/tokens/{b_tid}/worker-access", {"level": 3}, admin)
for bad in ({}, {"language": "ruby", "entry": "x.rb"}, {"language": "python"}, {"language": "python", "entry": "../etc/passwd"}, {"language": "python", "entry": "a b.py"},
            {"language": "python", "entry": "x" * 100}, {"language": "node", "entry": ".hidden"}):
    assert call("POST", JOBS, bad, a_session)[0] == 400, bad
status, made = call("POST", JOBS, {"language": "python", "entry": "main.py"}, a_session)
job = made["job"]
assert status == 201 and job["state"] == "created" and job["slug"] == a_slug and job["network"] is False and job["job_minutes"] == 10, made
upload = made["upload"]
post = s3.posts[-1]
assert post["Key"] == f"worker/jobs/{a_slug}/{job['id']}/input.zip", "the key is built from the signed-in student"
assert ["content-length-range", 1, wj.MAX_INPUT_BYTES] in post["Conditions"] and upload["max_bytes"] == wj.MAX_INPUT_BYTES
assert call("POST", f"{JOBS}/{job['id']}/submit", {}, a_session)[0] == 409, "not uploaded yet"
store[post["Key"]] = {"Body": b"PK" + b"x" * 100}
status, sub = call("POST", f"{JOBS}/{job['id']}/submit", {}, a_session, at=NOW + 10)
assert status == 200 and sub["job"]["state"] == "queued" and sub["job"]["size"] == 102 and sub["job"]["queued_at"] == NOW + 10, sub
assert call("POST", f"{JOBS}/{job['id']}/submit", {}, a_session)[0] == 409, "only once"
queue = json.loads(store[wj.QUEUE_KEY]["Body"])
assert [(q["id"], q["slug"], q["language"], q["entry"], q["minutes"], q["network"]) for q in queue["jobs"]] == [(job["id"], a_slug, "python", "main.py", 10, False)], queue
assert queue["jobs"][0]["input"] == f"worker/jobs/{a_slug}/{job['id']}/input.zip"
listing = call("GET", JOBS, session=a_session)[1]
assert [j["id"] for j in listing["jobs"]] == [job["id"]] and listing["usage"] == {"requests_today": 1, "requests_per_day": 4, "minutes_used": 0, "minutes_per_month": 5}, listing
print("  ok  create: validated, private upload form with a size limit, submit checks the upload, the queue file lists the job")

# ---------- 3. one student can never reach another's job ----------
assert call("GET", f"{JOBS}/{job['id']}", session=b_session)[0] == 404
assert call("POST", f"{JOBS}/{job['id']}/cancel", {}, b_session)[0] == 404
assert call("DELETE", f"{JOBS}/{job['id']}", session=b_session)[0] == 404
assert call("GET", f"{JOBS}/{job['id']}/result", session=b_session)[0] == 404
assert call("GET", JOBS, session=b_session)[1]["jobs"] == []
assert call("GET", f"{JOBS}/not-an-id", session=a_session)[0] == 404
print("  ok  another student's job is simply not found")

# ---------- 4. cancel, quotas ----------
status, out = call("POST", f"{JOBS}/{job['id']}/cancel", {}, a_session, at=NOW + 20)
assert status == 200 and out["job"]["state"] == "cancelled" and json.loads(store[wj.QUEUE_KEY]["Body"])["jobs"] == [], "a cancelled job leaves the queue"
ids = []
for i in range(3):  # 3 more requests: 4 a day in all
    status, made = call("POST", JOBS, {"language": "node", "entry": "index.mjs"}, a_session, at=NOW + 30 + i)
    assert status == 201, (i, made)
    ids.append(made["job"]["id"])
status, body = call("POST", JOBS, {"language": "node", "entry": "index.mjs"}, a_session, at=NOW + 40)
assert status == 429 and "waiting or running" in body["error"], "at most 3 open jobs"
for i in ids:
    call("POST", f"{JOBS}/{i}/cancel", {}, a_session, at=NOW + 50)
status, body = call("POST", JOBS, {"language": "node", "entry": "index.mjs"}, a_session, at=NOW + 60)
assert status == 429 and "today" in body["error"], "4 requests a day"
status, again = call("POST", JOBS, {"language": "node", "entry": "index.mjs"}, a_session, at=NOW + DAY + 100)
assert status == 201, ("a new day, a new allowance", status, again)
call("POST", f"{JOBS}/{again['job']['id']}/cancel", {}, a_session, at=NOW + DAY + 110)
print("  ok  cancel, at most 3 open jobs, requests a day, a new day starts again")

# ---------- 5. what the machine reports back ----------
status, made = call("POST", JOBS, {"language": "python", "entry": "run.py"}, a_session, at=NOW + 2 * DAY)
j2 = made["job"]["id"]
store[f"worker/jobs/{a_slug}/{j2}/input.zip"] = {"Body": b"PK" + b"y" * 50}
call("POST", f"{JOBS}/{j2}/submit", {}, a_session, at=NOW + 2 * DAY + 1)
store[f"worker/jobs/{a_slug}/{j2}/status.json"] = {"Body": json.dumps({"state": "running", "started": NOW + 2 * DAY + 5}).encode()}
assert call("GET", f"{JOBS}/{j2}", session=a_session, at=NOW + 2 * DAY + 6)[1]["job"]["state"] == "running"
assert call("POST", f"{JOBS}/{j2}/cancel", {}, a_session)[0] == 409, "a running job stops by itself"
assert call("DELETE", f"{JOBS}/{j2}", session=a_session)[0] == 409
assert json.loads(store[wj.QUEUE_KEY]["Body"])["jobs"] == [], "a running job is not in the queue any more"
store[f"worker/jobs/{a_slug}/{j2}/status.json"] = {"Body": json.dumps({"state": "done", "started": NOW + 2 * DAY + 5, "finished": NOW + 2 * DAY + 130, "seconds": 125, "exit": 0,
                                                                     "result": True, "log_tail": "hello from the container", "secret": "ignored"}).encode()}
status, got = call("GET", f"{JOBS}/{j2}", session=a_session, at=NOW + 2 * DAY + 140)
done = got["job"]
assert done["state"] == "done" and done["seconds"] == 125 and done["exit"] == 0 and done["has_result"] and done["log"] == "hello from the container" and "secret" not in json.dumps(got), done
assert call("GET", JOBS, session=a_session, at=NOW + 2 * DAY + 150)[1]["usage"]["minutes_used"] == 3, "125 seconds are 3 minutes, counted once"
call("GET", f"{JOBS}/{j2}", session=a_session, at=NOW + 2 * DAY + 160)
assert call("GET", JOBS, session=a_session, at=NOW + 2 * DAY + 170)[1]["usage"]["minutes_used"] == 3, "never counted twice"
status, url = call("GET", f"{JOBS}/{j2}/result", session=a_session)
assert status == 200 and url["url"].endswith(f"worker/jobs/{a_slug}/{j2}/result.zip") and url["expires_in"] == 300, url
# the month's minutes (5) are used up after another long job
status, made = call("POST", JOBS, {"language": "python", "entry": "run.py"}, a_session, at=NOW + 2 * DAY + 200)
j3 = made["job"]["id"]
store[f"worker/jobs/{a_slug}/{j3}/input.zip"] = {"Body": b"PK" + b"z" * 50}
call("POST", f"{JOBS}/{j3}/submit", {}, a_session, at=NOW + 2 * DAY + 201)
store[f"worker/jobs/{a_slug}/{j3}/status.json"] = {"Body": json.dumps({"state": "failed", "seconds": 130, "exit": 1, "result": False, "log_tail": "boom"}).encode()}
failed = call("GET", f"{JOBS}/{j3}", session=a_session, at=NOW + 2 * DAY + 300)[1]["job"]
assert failed["state"] == "failed" and failed["exit"] == 1 and not failed.get("has_result")
assert call("GET", f"{JOBS}/{j3}/result", session=a_session)[0] == 409
status, body = call("POST", JOBS, {"language": "python", "entry": "run.py"}, a_session, at=NOW + 2 * DAY + 400)
assert status == 429 and "minutes for this month" in body["error"], (status, body)
print("  ok  the machine's report: running, done and failed, minutes counted once, a result link, the month's minutes limit new jobs")

# ---------- 6. expiry, the teacher's queue, cancel and delete ----------
status, made = call("POST", JOBS, {"language": "node", "entry": "x.mjs"}, b_session, at=NOW + 3 * DAY)
jb = made["job"]["id"]
store[f"worker/jobs/{b_slug}/{jb}/input.zip"] = {"Body": b"PK" + b"q" * 10}
call("POST", f"{JOBS}/{jb}/submit", {}, b_session, at=NOW + 3 * DAY + 1)
queue = call("GET", "/api/admin/worker/jobs", session=admin, at=NOW + 3 * DAY + 5)[1]
assert queue["available"] and [j["id"] for j in queue["jobs"] if j["state"] == "queued"] == [jb], queue
assert {j["slug"] for j in queue["jobs"]} <= {a_slug, b_slug} and all("log" not in j for j in queue["jobs"])
assert call("GET", "/api/admin/worker/jobs")[0] == 401 and call("GET", "/api/admin/worker/jobs", session=a_session)[0] == 401, "teacher only"
status, out = call("POST", f"/api/admin/worker/jobs/{b_slug}/{jb}/cancel", {}, admin, at=NOW + 3 * DAY + 6)
assert status == 200 and out["job"]["state"] == "cancelled" and "teacher" in out["job"]["message"]
assert call("DELETE", f"/api/admin/worker/jobs/{b_slug}/{jb}", session=admin)[0] == 200
assert call("GET", f"{JOBS}/{jb}", session=b_session)[0] == 404 and f"worker/jobs/{b_slug}/{jb}/input.zip" not in store, "delete removes the record and the files"
assert call("DELETE", f"/api/admin/worker/jobs/{b_slug}/{jb}", session=admin)[0] == 404
status, made = call("POST", JOBS, {"language": "node", "entry": "old.mjs"}, b_session, at=NOW + 4 * DAY)
jo = made["job"]["id"]
store[f"worker/jobs/{b_slug}/{jo}/input.zip"] = {"Body": b"PK" + b"w" * 10}
call("POST", f"{JOBS}/{jo}/submit", {}, b_session, at=NOW + 4 * DAY + 1)
old = call("GET", f"{JOBS}/{jo}", session=b_session, at=NOW + 4 * DAY + 8 * DAY)[1]["job"]
assert old["state"] == "expired" and json.loads(store[wj.QUEUE_KEY]["Body"])["jobs"] == [], old
print("  ok  teacher queue (teacher only), cancel and delete, a job that waits more than 7 days expires")

# ---------- 7. nothing here ever starts the machine ----------
assert ec2_calls == [], ec2_calls
call("POST", f"/api/admin/tokens/{a_tid}/worker-access", {"level": 1}, admin)
assert call("GET", JOBS, session=a_session)[0] == 403, "lowering the level closes the door"
print("  ok  the machine is never touched by the jobs code; a lower level closes the student's door")
