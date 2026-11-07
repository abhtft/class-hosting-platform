"""Compute worker: one small EC2 machine that is OFF by default, started and stopped from the teacher page, and optionally started once a day.
See docs/COMPUTE_WORKER_PLAN.md.

  start(mode)   mode "run": the machine boots, runs the task runner, writes a report to S3 and shuts itself down.
                mode "keep": it stays on (for the teacher to look around through Systems Manager) until the time limit.
  stop()        stops it now.
  tick()        every 15 minutes (the control function): the WATCHDOG stops a machine that runs without a lease or past its limit, and the
                daily SCHEDULE starts it once in the chosen hour. A hung job can therefore never run for a month.

State is small and lives in the tokens table under keys that start with `worker#`:
  worker#settings   {daily_enabled, hour_utc, max_minutes}
  worker#lease      {started, until, mode, by}: why the machine is on, and when it must be off
  worker#last_day   the day the daily start last ran
  worker#min#<YYYY-MM>   minutes the machine ran this month (an estimate: the cost line on the teacher page)
The machine may only be started and stopped by this module (permission limited to that one instance), and it is never given student code to run.
"""
import json
import os
import time

import portal
from portal import DAY, PortalError

MODES = ("run", "keep")
HOUR_CHOICES = tuple(range(24))
DEFAULT_MAX_MINUTES = 60
MAX_MINUTES = 360
# On-demand price per hour in us-east-1 (an ESTIMATE shown to the teacher; the AWS bill is the truth) and the disk per GB-month.
PRICES = {"c7g.large": 0.0725, "c7g.xlarge": 0.145, "c7g.2xlarge": 0.29, "t4g.medium": 0.0336, "t4g.large": 0.0672}
DISK_PER_GB_MONTH = 0.08
RESULTS_KEY = "worker/results/latest.json"
MODE_KEY = "worker/control/mode.json"

_clients = {}


def client(name):
    if name not in _clients:
        import boto3
        _clients[name] = boto3.client(name)
    return _clients[name]


def instance_id():
    return os.environ.get("WORKER_INSTANCE_ID", "")


def available():
    return bool(instance_id())


def bucket():
    return os.environ["BUCKET"]


def _db():
    return portal.db()


# ---------------------------------------------------------------- settings and lease
def settings():
    raw = _db().get("worker#settings") or {}
    cap = int(os.environ.get("WORKER_MAX_MINUTES") or DEFAULT_MAX_MINUTES)
    minutes = raw.get("max_minutes")
    minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and 15 <= minutes <= MAX_MINUTES else cap
    hour = raw.get("hour_utc")
    return {"daily_enabled": raw.get("daily_enabled") is True, "hour_utc": hour if hour in HOUR_CHOICES else 3, "max_minutes": minutes}


def set_settings(body):
    if not available():
        raise PortalError(409, "The compute worker is not installed on this platform.")
    if not isinstance(body, dict):
        raise PortalError(400, "Send a JSON object.")
    changes = {}
    if "daily_enabled" in body:
        if not isinstance(body["daily_enabled"], bool):
            raise PortalError(400, "daily_enabled must be true or false.")
        changes["daily_enabled"] = body["daily_enabled"]
    if "hour_utc" in body:
        if isinstance(body["hour_utc"], bool) or body["hour_utc"] not in HOUR_CHOICES:
            raise PortalError(400, "hour_utc must be a whole number from 0 to 23 (UTC).")
        changes["hour_utc"] = body["hour_utc"]
    if "max_minutes" in body:
        v = body["max_minutes"]
        if isinstance(v, bool) or not isinstance(v, int) or not 15 <= v <= MAX_MINUTES:
            raise PortalError(400, f"max_minutes must be a whole number from 15 to {MAX_MINUTES}.")
        changes["max_minutes"] = v
    if not changes:
        raise PortalError(400, "Nothing to change.")
    _db().put({"pk": "worker#settings", **settings(), **changes})
    return settings()


def _lease():
    item = _db().get("worker#lease")
    return {k: item[k] for k in ("started", "until", "mode", "by") if k in item} if item else None


# ---------------------------------------------------------------- the machine
def machine():
    """{"state": pending|running|stopping|stopped|..., "launched": epoch seconds or None}."""
    found = client("ec2").describe_instances(InstanceIds=[instance_id()])
    inst = found["Reservations"][0]["Instances"][0]
    launched = inst.get("LaunchTime")
    return {"state": inst["State"]["Name"], "launched": int(launched.timestamp()) if launched else None, "type": inst.get("InstanceType")}


def _write_mode(mode, now):
    client("s3").put_object(Bucket=bucket(), Key=MODE_KEY, Body=json.dumps({"mode": mode, "at": int(now)}).encode(), ContentType="application/json")


def _month_minutes(now):
    return int((_db().get(f"worker#min#{time.strftime('%Y-%m', time.gmtime(now))}") or {}).get("count", 0))


def _close_lease(now, ended=None):
    """The machine is off: add this run to the month's minutes and drop the lease."""
    lease = _lease()
    if not lease:
        return
    minutes = max(1, int(((ended or now) - int(lease["started"])) / 60))
    month = time.strftime("%Y-%m", time.gmtime(lease["started"]))
    _db().counter_add(f"worker#min#{month}", minutes, ttl=int(now) + 70 * DAY, now=now)
    _db().delete("worker#lease")


def start(mode="run", by="teacher", now=None):
    if not available():
        raise PortalError(409, "The compute worker is not installed on this platform.")
    if mode not in MODES:
        raise PortalError(400, "mode must be run or keep.")
    now = int(now if now is not None else time.time())
    state = machine()["state"]
    if state in ("pending", "running"):
        raise PortalError(409, "The machine is already on.")
    if state in ("stopping", "shutting-down"):
        raise PortalError(409, "The machine is still stopping. Try again in a minute.")
    if state not in ("stopped",):
        raise PortalError(409, f"The machine cannot be started now (state: {state}).")
    limit = settings()["max_minutes"] * 60
    _write_mode(mode, now)
    _db().put({"pk": "worker#lease", "started": now, "until": now + limit, "mode": mode, "by": by, "ttl": now + DAY})
    client("ec2").start_instances(InstanceIds=[instance_id()])
    return view(now)


def stop(now=None):
    if not available():
        raise PortalError(409, "The compute worker is not installed on this platform.")
    now = int(now if now is not None else time.time())
    state = machine()["state"]
    if state in ("pending", "running"):
        client("ec2").stop_instances(InstanceIds=[instance_id()])
    _close_lease(now)
    return view(now)


# ---------------------------------------------------------------- the 15-minute tick: watchdog and daily schedule
def tick(now=None):
    """Returns a short text of what it did (printed in the log). Never leaves the machine running without a reason."""
    if not available():
        return "not installed"
    now = int(now if now is not None else time.time())
    m = machine()
    lease = _lease()
    cfg = settings()
    if m["state"] in ("pending", "running"):
        if lease is None or now >= int(lease["until"]):
            client("ec2").stop_instances(InstanceIds=[instance_id()])
            _close_lease(now)
            return "stopped (no lease or time limit reached)"
        return "running"
    if m["state"] == "stopped":
        if lease:
            _close_lease(now, ended=_finished_at(lease))
        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        hour = time.gmtime(now).tm_hour
        last = (_db().get("worker#last_day") or {}).get("day")
        if cfg["daily_enabled"] and hour == cfg["hour_utc"] and last != today:
            _db().put({"pk": "worker#last_day", "day": today, "ttl": now + 3 * DAY})
            try:
                start("run", by="schedule", now=now)
            except Exception as error:  # for example no capacity right now: forget today, so the next tick (within the hour) tries again
                _db().delete("worker#last_day")
                return f"daily start failed: {getattr(error, 'status', '')} {error}"[:200]
            return "started by the daily schedule"
        return "stopped"
    return m["state"]


def _finished_at(lease):
    """When the last run ended, if the runner reported it after this lease started (else None: now is used)."""
    try:
        data = last_result()
        if data and int(data.get("finished_epoch", 0)) >= int(lease["started"]):
            return int(data["finished_epoch"])
    except Exception:
        pass
    return None


def last_result():
    try:
        body = client("s3").get_object(Bucket=bucket(), Key=RESULTS_KEY)["Body"].read()
    except Exception:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- for the teacher page
def view(now=None):
    if not available():
        return {"available": False}
    now = int(now if now is not None else time.time())
    m = machine()
    kind = os.environ.get("WORKER_INSTANCE_TYPE", "") or m.get("type") or ""
    disk = int(os.environ.get("WORKER_DISK_GB") or 0)
    minutes = _month_minutes(now)
    lease = _lease()
    if lease and m["state"] in ("pending", "running"):
        minutes += max(0, int((now - int(lease["started"])) / 60))  # the run in progress
    price = PRICES.get(kind)
    compute = round(minutes / 60 * price, 2) if price else None
    last = last_result()
    return {"available": True, "state": m["state"], "instance_type": kind, "disk_gb": disk, "settings": settings(), "lease": lease,
            "last": {k: last[k] for k in ("started", "finished", "exit", "seconds", "task", "output") if k in last} if last else None,
            "month": {"minutes": minutes, "compute_usd": compute, "disk_usd": round(disk * DISK_PER_GB_MONTH, 2),
                      "note": "An estimate from list prices; the AWS bill is the truth."}}


def handler(event, context=None):
    """The control function: runs every 15 minutes."""
    out = tick()
    print(json.dumps({"worker": out}))
    return {"worker": out}
