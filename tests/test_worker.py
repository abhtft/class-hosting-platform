"""Compute worker (lambda/worker.py, the teacher routes in portal.py): start and stop, the lease, the watchdog that stops a machine nobody asked for or
that runs too long, the daily schedule, the month's minutes and cost estimate, settings validation, and that nothing exists while the feature is off.
EC2 and S3 are small fakes."""
import datetime
import io
import json
import os
import sys

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d",
                  KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import portal  # noqa: E402
import worker  # noqa: E402

salt = b"saltsaltsaltsalt"
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)),
                                                "secret": portal.b64(b"3" * 32)})
DAY = 86400
NOW = 1_800_000_000 - (1_800_000_000 % DAY) + 3 * 3600 + 120  # 03:02 UTC on some day


class FakeEC2:
    def __init__(self):
        self.state, self.calls = "stopped", []
        self.launched = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

    def describe_instances(self, InstanceIds):
        assert InstanceIds == ["i-0abc"], InstanceIds
        return {"Reservations": [{"Instances": [{"State": {"Name": self.state}, "LaunchTime": self.launched, "InstanceType": "c7g.xlarge"}]}]}

    def start_instances(self, InstanceIds):
        assert InstanceIds == ["i-0abc"]
        self.calls.append("start")
        self.state = "pending"

    def stop_instances(self, InstanceIds):
        assert InstanceIds == ["i-0abc"]
        self.calls.append("stop")
        self.state = "stopped"


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


ec2, s3 = FakeEC2(), FakeS3()
worker._clients.update({"ec2": ec2, "s3": s3})


def call(method, path, body=None, session=None, at=NOW):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}}, "headers": {"authorization": f"Bearer {session}"} if session else {},
             "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]

# ---------- 1. not installed: nothing exists, nothing is touched ----------
assert not worker.available() and worker.tick(NOW) == "not installed"
assert call("GET", "/api/admin/worker", session=admin) == (200, {"available": False})
assert call("POST", "/api/admin/worker/start", {}, admin)[0] == 409 and call("POST", "/api/admin/worker/stop", {}, admin)[0] == 409
assert call("POST", "/api/admin/worker/settings", {"daily_enabled": True}, admin)[0] == 409
assert ec2.calls == []
print("  ok  not installed: the page is told so, nothing is started or touched")

os.environ.update(WORKER_INSTANCE_ID="i-0abc", WORKER_INSTANCE_TYPE="c7g.xlarge", WORKER_DISK_GB="30", WORKER_MAX_MINUTES="60")
assert call("GET", "/api/admin/worker")[0] == 401 and call("POST", "/api/admin/worker/start", {})[0] == 401, "only the teacher"

# ---------- 2. start and stop ----------
status, view = call("GET", "/api/admin/worker", session=admin)
assert status == 200 and view["state"] == "stopped" and view["instance_type"] == "c7g.xlarge" and view["disk_gb"] == 30 and view["lease"] is None, view
assert view["settings"] == {"daily_enabled": False, "hour_utc": 3, "max_minutes": 60, "monthly_cap_minutes": 2400} and view["month"]["disk_usd"] == 2.4 and view["month"]["minutes"] == 0 and view["last"] is None
status, view = call("POST", "/api/admin/worker/start", {"mode": "run"}, admin)
assert status == 200 and ec2.calls == ["start"] and view["lease"]["mode"] == "run" and view["lease"]["until"] == NOW + 3600, view
assert json.loads(s3.objects[worker.MODE_KEY])["mode"] == "run"
assert call("POST", "/api/admin/worker/start", {"mode": "run"}, admin)[0] == 409, "already on"
ec2.state = "running"
assert call("POST", "/api/admin/worker/start", {"mode": "weird"}, admin)[0] in (400, 409)
status, view = call("GET", "/api/admin/worker", session=admin, at=NOW + 600)
assert view["state"] == "running" and view["month"]["minutes"] == 10, "the run in progress counts"
status, view = call("POST", "/api/admin/worker/stop", {}, admin, at=NOW + 1200)
assert status == 200 and ec2.calls == ["start", "stop"] and view["lease"] is None and view["state"] == "stopped", view
assert call("GET", "/api/admin/worker", session=admin, at=NOW + 1300)[1]["month"]["minutes"] == 20, "the finished run is added to the month"
assert call("POST", "/api/admin/worker/stop", {}, admin)[0] == 200, "stopping an off machine is harmless"
assert ec2.calls == ["start", "stop"]
print("  ok  start, already-on refusal, stop, minutes counted for the month")

# ---------- 3. the watchdog ----------
ec2.state = "running"  # someone started it outside the lease (or the lease record is lost)
assert worker.tick(NOW + 2000).startswith("stopped") and ec2.state == "stopped" and ec2.calls[-1] == "stop", "a machine with no lease is stopped"
worker.start("run", "teacher", NOW + 3000)
ec2.state = "running"
assert worker.tick(NOW + 3000 + 600) == "running", "inside the limit nothing happens"
assert worker.tick(NOW + 3000 + 3601).startswith("stopped") and ec2.state == "stopped", "past the limit it is stopped"
worker.set_settings({"max_minutes": 30})
worker.start("keep", "teacher", NOW + 5000)
assert worker.view(NOW + 5000)["lease"]["until"] == NOW + 5000 + 1800, "the limit follows the setting"
ec2.state = "running"
assert worker.tick(NOW + 5000 + 1801).startswith("stopped")
worker.set_settings({"max_minutes": 60})
print("  ok  watchdog: no lease, past the limit, and the limit setting")

# ---------- 4. the daily schedule ----------
day = NOW - (NOW % DAY)
call("POST", "/api/admin/worker/settings", {"daily_enabled": True, "hour_utc": 3}, admin)
ec2.calls.clear()
ec2.state = "stopped"
assert worker.tick(day + 2 * 3600) == "stopped" and not ec2.calls, "not before its hour"
assert worker.tick(day + 3 * 3600 + 60) == "started by the daily schedule" and ec2.calls == ["start"]
assert worker.view(day + 3 * 3600 + 60)["lease"]["by"] == "schedule"
ec2.state = "stopped"  # the run finished by itself
assert worker.tick(day + 3 * 3600 + 900) == "stopped" and ec2.calls == ["start"], "once a day only"
assert worker.tick(day + 4 * 3600) == "stopped", "not outside its hour"
assert worker.tick(day + DAY + 3 * 3600 + 30) == "started by the daily schedule", "the next day it runs again"
ec2.state = "stopped"
call("POST", "/api/admin/worker/settings", {"daily_enabled": False}, admin)
assert worker.tick(day + 2 * DAY + 3 * 3600 + 30) == "stopped", "switched off: it does not start"
print("  ok  daily schedule: its hour only, once a day, off means off")

# ---------- 5. settings, last report, cost ----------
for bad in ({}, {"daily_enabled": "yes"}, {"hour_utc": 24}, {"hour_utc": True}, {"max_minutes": 5}, {"max_minutes": 9999}, {"max_minutes": 1.5}, "x"):
    status, _ = call("POST", "/api/admin/worker/settings", bad, admin)
    assert status == 400, bad
status, view = call("POST", "/api/admin/worker/settings", {"hour_utc": 5, "max_minutes": 120}, admin)
assert status == 200 and view["settings"]["hour_utc"] == 5 and view["settings"]["max_minutes"] == 120
s3.objects[worker.RESULTS_KEY] = json.dumps({"started": "2026-11-07T03:00:01Z", "finished": "x", "finished_epoch": NOW, "seconds": 42, "exit": 0, "task": "runner",
                                             "output": "all good", "secret": "no"}).encode()
last = call("GET", "/api/admin/worker", session=admin)[1]["last"]
assert last == {"started": "2026-11-07T03:00:01Z", "finished": "x", "seconds": 42, "exit": 0, "task": "runner", "output": "all good"}, last
s3.objects[worker.RESULTS_KEY] = b"not json"
assert call("GET", "/api/admin/worker", session=admin)[1]["last"] is None
month = call("GET", "/api/admin/worker", session=admin)[1]["month"]
assert month["compute_usd"] == round(month["minutes"] / 60 * 0.145, 2) and month["disk_usd"] == 2.4
print("  ok  settings validated, last report shown (only known fields), cost estimate")

# ---------- 6. states that cannot be started ----------
ec2.state = "stopping"
assert call("POST", "/api/admin/worker/start", {"mode": "run"}, admin)[0] == 409
ec2.state = "terminated"
assert call("POST", "/api/admin/worker/start", {"mode": "run"}, admin)[0] == 409
print("  ok  a stopping or removed machine is not started")


# ---------- 7. the class-wide monthly cap on machine minutes ----------
ec2.state = "stopped"
worker._db().delete("worker#lease")
used = worker._month_minutes(NOW)
assert used > 0
for bad in ({"monthly_cap_minutes": 10}, {"monthly_cap_minutes": 10 ** 6}, {"monthly_cap_minutes": True}, {"monthly_cap_minutes": 100.5}):
    assert call("POST", "/api/admin/worker/settings", bad, admin)[0] == 400, bad
status, view = call("POST", "/api/admin/worker/settings", {"monthly_cap_minutes": 60}, admin)
assert status == 200 and view["settings"]["monthly_cap_minutes"] == 60 and view["month"]["cap_minutes"] == 60
worker._db().counter_add("worker#min#" + __import__("time").strftime("%Y-%m", __import__("time").gmtime(NOW)), 100, now=NOW)  # push the month over the cap
ec2.calls.clear()
status, body = call("POST", "/api/admin/worker/start", {"mode": "run"}, admin)
assert status == 409 and "monthly cap" in body["error"] and ec2.calls == [], body
call("POST", "/api/admin/worker/settings", {"monthly_cap_minutes": 14400}, admin)
assert call("POST", "/api/admin/worker/start", {"mode": "run"}, admin)[0] == 200, "raising the cap lets it start again"
ec2.state = "stopped"
worker._db().delete("worker#lease")
print("  ok  class monthly cap: validated, shown, and a start is refused when it is reached")
