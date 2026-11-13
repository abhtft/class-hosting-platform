p = r"D:\Github_Codes\class-hosting-platform-github\tests\test_worker.py"
s = open(p, encoding="utf8").read()
a = 'assert view["settings"] == {"daily_enabled": False, "hour_utc": 3, "max_minutes": 60} and'
assert a in s
s = s.replace(a, 'assert view["settings"] == {"daily_enabled": False, "hour_utc": 3, "max_minutes": 60, "monthly_cap_minutes": 2400} and', 1)
s += '''

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

# ---------- 8. per-student access (level 0 to 3, off by default) ----------
_, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": "Dana"}, admin)
tid = made["item"]["id"]
student = call("POST", "/api/student/login", {"token": made["token"]})[1]["session"]
mine = call("GET", "/api/student/me", session=student)[1]["token"]["worker_access"]
assert mine["available"] and mine["level"] == 0 and mine["level_name"] == "off" and mine["network"] is False and mine["requests_per_day"] == 5 and mine["minutes_per_month"] == 30, mine
assert call("POST", f"/api/admin/tokens/{tid}/worker-access", {"level": 1}, session=student)[0] == 401, "only the teacher changes it"
status, body = call("POST", f"/api/admin/tokens/{tid}/worker-access", {"level": 2, "requests_per_day": 3, "minutes_per_month": 15}, admin)
got = body["token"]["worker_access"]
assert status == 200 and got["level"] == 2 and got["level_name"] == "platform tasks" and got["requests_per_day"] == 3 and got["minutes_per_month"] == 15 and got["job_minutes"] == 10, got
assert call("GET", "/api/student/me", session=student)[1]["token"]["worker_access"]["level"] == 2, "the student can read their own level"
for bad in ({}, {"level": 4}, {"level": -1}, {"level": "3"}, {"level": True}, {"requests_per_day": 101}, {"minutes_per_month": -1}, {"job_minutes": 0}, {"job_minutes": 61}, {"network": "yes"}, {"requests_per_day": 1.5}):
    assert call("POST", f"/api/admin/tokens/{tid}/worker-access", bad, admin)[0] == 400, bad
assert call("POST", f"/api/admin/tokens/{tid}/worker-access", {"network": True}, admin)[0] == 400, "the internet only applies to a student's own code (level 3)"
status, body = call("POST", f"/api/admin/tokens/{tid}/worker-access", {"level": 3, "network": True, "job_minutes": 20}, admin)
got = body["token"]["worker_access"]
assert status == 200 and got["level"] == 3 and got["network"] is True and got["job_minutes"] == 20, got
status, body = call("POST", f"/api/admin/tokens/{tid}/worker-access", {"level": 1}, admin)
assert body["token"]["worker_access"]["network"] is False, "lowering the level takes the internet away"
assert worker.access({"worker_access": {"level": 9, "requests_per_day": "x", "network": True}}) == worker.ACCESS_DEFAULT, "odd stored data reads as the safe default"
assert worker.access(None)["level"] == 0
os.environ["WORKER_INSTANCE_ID"] = ""
assert call("GET", "/api/student/me", session=student)[1]["token"]["worker_access"] == {"available": False}
assert call("POST", f"/api/admin/tokens/{tid}/worker-access", {"level": 1}, admin)[0] == 409, "not installed: nothing to set"
os.environ["WORKER_INSTANCE_ID"] = "i-0abc"
assert ec2.calls == [] or ec2.calls[-1] == "start", "setting access never starts or stops the machine"
print("  ok  student access: off by default, levels 0 to 3 with quotas, validated, internet only at level 3, the student reads their own level, nothing starts the machine")
'''
open(p, "w", encoding="utf8").write(s)
print("ok")
