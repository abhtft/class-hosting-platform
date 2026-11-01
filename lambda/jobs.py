"""Background jobs for student backends: start a task, ask for the result later.

A web request to a student's function stops at 10 seconds (25 with the class AI). A job is for the work that takes longer: summarising twenty
notes, a chain of AI calls, building a report. The teacher switches jobs on per student (off by default) and sets the limits.

What the student's page does (the reserved path /api/_jobs never reaches the student's web function):
  POST /api/_jobs/<name>   JSON body = the job's input      ->  202 {"id", "name", "status": "queued"}
  GET  /api/_jobs/<id>                                       ->  {"status": queued | running | done | failed, "result" | "error", ...}
  GET  /api/_jobs                                            ->  the latest jobs (no results)
What the student's code does: the SAME zip is also deployed as a second function sd-<slug>_job with the longer time limit, and its handler
receives {"job": {"id", "name", "payload"}}; what it returns (JSON, at most 100 KB) is the result.

How it runs: the gateway creates the job item and starts the platform's JOB RUNNER (job_runner.py) asynchronously; the runner calls the job function and
waits for it, then writes the outcome. The student's role can only write logs, so it is the runner (not the student's code) that stores the result.
The error text of a failed job is NEVER passed on (it may hold the student's secrets); it stays in the student's own logs.

Limits (teacher, per student): jobs per day, jobs at once, minutes per job. A global cap on jobs at once protects the account.
"""
import json
import os
import re
import secrets
import time

from botocore.exceptions import ClientError

import backends
import portal
from portal import DAY, PortalError, db

JOB_SUFFIX = "_job"  # an underscore cannot occur in a slug, so sd-abc_job can never be the web function of another student
PER_DAY_DEFAULT, PER_DAY_MAX = 20, 1000
CONCURRENT_DEFAULT, CONCURRENT_MAX = 1, 5
MINUTES_DEFAULT, MINUTES_MAX = 5, 15  # 15 minutes is Lambda's own ceiling
RESULT_MAX_BYTES = 100 * 1024
PAYLOAD_MAX_BYTES = 64 * 1024
LIST_LIMIT = 20
STALE_GRACE_SECONDS = 300
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
JOB_ENV_FIELDS = ("jobs_enabled", "jobs_per_day", "jobs_concurrent", "jobs_minutes")


def reply(code, headers=None, **body):  # not named "status": that is a field of a job's JSON
    return {"statusCode": code, "headers": {"content-type": "application/json", "cache-control": "no-store", **(headers or {})},
            "body": json.dumps(body)}


def job_function_name(slug):
    return backends.function_name(slug) + JOB_SUFFIX


def limits(item):
    b = backends.settings(item)
    return {"enabled": bool(b.get("jobs_enabled")), "per_day": b.get("jobs_per_day", PER_DAY_DEFAULT),
            "concurrent": b.get("jobs_concurrent", CONCURRENT_DEFAULT), "minutes": b.get("jobs_minutes", MINUTES_DEFAULT)}


def _day(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def view(item, now):
    """What the teacher's page and the student's page show about jobs."""
    lim = limits(item)
    used = int((db().get(f"jobs#{item['slug']}#{_day(now)}") or {}).get("count", 0)) if lim["enabled"] else 0
    return {**lim, "used_today": used, "ready": bool(backends.settings(item).get("job_function_name")),
            "max": {"per_day": PER_DAY_MAX, "concurrent": CONCURRENT_MAX, "minutes": MINUTES_MAX, "result_kb": RESULT_MAX_BYTES // 1024}}


def parse_settings(body):
    """The teacher's job settings from a request body, validated. Returns only the fields that were sent."""
    changes = {}
    if "jobs_enabled" in body:
        if not isinstance(body["jobs_enabled"], bool):
            raise PortalError(400, "jobs_enabled must be true or false.")
        changes["jobs_enabled"] = body["jobs_enabled"]
    for field, low, high in (("jobs_per_day", 0, PER_DAY_MAX), ("jobs_concurrent", 1, CONCURRENT_MAX), ("jobs_minutes", 1, MINUTES_MAX)):
        if field in body:
            value = body[field]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise PortalError(400, f"{field} must be a whole number from {low} to {high}.")
            changes[field] = value
    return changes


# ---------- the job function (a second copy of the student's code with the longer time limit) ----------
def _concurrency(name, reserved):
    try:
        if reserved == 0:
            backends.client("lambda").put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=0)
        else:
            backends.client("lambda").delete_function_concurrency(FunctionName=name)
    except ClientError as e:
        print(f"could not update reserved concurrency for {name}: {e}")


def sync_function(item, code_changed=False):
    """Make the job function match the teacher's switch: create it (or update its code and time limit) when jobs are ON, refuse invocations when OFF.
    A no-op until the student has a web function. Returns the (possibly updated) item."""
    b = backends.settings(item)
    web = b.get("function_name")
    if not web:
        return item
    job, lam, slug = job_function_name(item["slug"]), backends.client("lambda"), item["slug"]
    if not b.get("jobs_enabled"):
        if b.get("job_function_name"):
            _concurrency(job, 0)
        return item
    timeout = limits(item)["minutes"] * 60
    try:
        try:
            config = lam.get_function_configuration(FunctionName=job)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
            config = None
        if config is None:
            lam.create_function(
                FunctionName=job, Runtime=b["runtime"], Role=os.environ["SANDBOX_ROLE_ARN"], Handler=b["handler"],
                Code={"S3Bucket": os.environ["BUCKET"], "S3Key": f"uploads/{slug}-backend.zip"}, MemorySize=backends.MEMORY_MB, Timeout=timeout,
                Description=f"Background jobs for {slug}", Tags={"managed-by": "student-deploy", "slug": slug, "kind": "job"},
                Environment={"Variables": backends._current_env(web)})
            backends._prepare_function(job, True)
        else:
            if code_changed:
                lam.update_function_code(FunctionName=job, S3Bucket=os.environ["BUCKET"], S3Key=f"uploads/{slug}-backend.zip")
            elif config.get("Timeout") != timeout:
                lam.update_function_configuration(FunctionName=job, Timeout=timeout)
            if b.get("job_function_name"):
                _concurrency(job, None)  # lift a refusal set while jobs were off
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            raise PortalError(409, "The job function is still updating. Try again in a few seconds.")
        raise PortalError(502, f"Could not set up background jobs: {e.response['Error'].get('Message', 'AWS error')}")
    return item if b.get("job_function_name") == job else backends._save(item, job_function_name=job)


def mirror_env(item, variables):
    """Keep the job function's environment the same as the web function's (the class AI and storage keys, the student's own variables). Best effort."""
    job = backends.settings(item).get("job_function_name")
    if not job:
        return
    try:
        backends.client("lambda").update_function_configuration(FunctionName=job, Environment={"Variables": dict(variables)})
    except ClientError as e:
        print(f"could not copy the environment to {job}: {e}")


def remove_function(item):
    """Delete the job function and its log group (the student removed the backend). Never raises for a missing function."""
    job = backends.settings(item).get("job_function_name")
    if not job:
        return
    try:
        backends.client("lambda").delete_function(FunctionName=job)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise PortalError(502, f"Could not remove your background jobs: {e.response['Error'].get('Message', 'AWS error')}")
    try:
        backends.client("logs").delete_log_group(logGroupName=f"/aws/lambda/{job}")
    except ClientError:
        pass


# ---------- slots (jobs at once): held for minutes, so a leaked one heals after the job's own time limit, not after a minute ----------
def _acquire(key, cap, now, heal):
    if db().counter_add(key, 1, limit=cap, now=now) is not None:
        return True
    record = db().get(key) or {}
    if record.get("count", 0) > 0 and now - record.get("updated_at", now) > heal:
        db().put({"pk": key, "count": 0, "updated_at": now})
        return db().counter_add(key, 1, limit=cap, now=now) is not None
    return False


def _release(slug, now):
    for key in (f"jobsrun#{slug}", "jobsrun#global"):
        db().counter_sub(key, now=now)


def _global_cap():
    return int(os.environ.get("INFLIGHT_JOBS_GLOBAL", "2"))


# ---------- the gateway's reserved routes ----------
def handle(item, event, rest, now):
    """POST /_jobs/<name> starts a job, GET /_jobs/<id> reads one, GET /_jobs lists the latest. Returns a gateway reply."""
    b = backends.settings(item)
    if not b.get("jobs_enabled"):
        return reply(403, error="jobs_off", message="Background jobs are not switched on for this site. Ask the site owner.")
    if not b.get("job_function_name"):
        return reply(409, error="jobs_not_ready", message="Background jobs are being set up. Publish your backend again, then retry.")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    parts = [p for p in rest.split("/") if p]  # ["_jobs"] or ["_jobs", name-or-id]
    if len(parts) == 1 and method == "GET":
        return recent(item, now)
    if len(parts) == 2 and method == "POST":
        return start(item, parts[1], event, now)
    if len(parts) == 2 and method == "GET":
        return status(item, parts[1], now)
    return reply(405, {"allow": "GET, POST"}, error="method_not_allowed", message="Use POST /api/_jobs/<name> to start a job and GET /api/_jobs/<id> to read it.")


def start(item, name, event, now):
    slug, lim = item["slug"], limits(item)
    if not NAME_RE.match(name):
        return reply(400, error="bad_job_name", message="A job name uses lowercase letters, digits, - and _ (up to 40 characters).")
    raw = event.get("body") or ""
    if len(raw.encode()) > PAYLOAD_MAX_BYTES:
        return reply(413, error="payload_too_large", message=f"The job's input is limited to {PAYLOAD_MAX_BYTES // 1024} KB. Save big inputs with class storage and send a reference.")
    try:
        payload = json.loads(raw) if raw.strip() else None
    except ValueError:
        return reply(400, error="bad_payload", message="Send the job's input as JSON (or nothing).")
    heal = lim["minutes"] * 60 + 120
    if not _acquire(f"jobsrun#{slug}", lim["concurrent"], now, heal):
        return reply(429, {"retry-after": "5"}, error="jobs_busy", message="A job is already running. Wait until it finishes, then start the next one.")
    if not _acquire("jobsrun#global", _global_cap(), now, heal):
        db().counter_sub(f"jobsrun#{slug}", now=now)
        return reply(429, {"retry-after": "5"}, error="jobs_busy", message="Too many jobs are running on this platform right now. Try again in a moment.")
    resets = (now // DAY + 1) * DAY
    if db().counter_add(f"jobs#{slug}#{_day(now)}", 1, limit=lim["per_day"], ttl=resets + 7 * DAY, now=now) is None:
        _release(slug, now)
        return reply(429, {"retry-after": str(max(resets - now, 1))}, error="jobs_allowance_exhausted", resets_at=resets,
                     message="This site has used its background jobs for today. Please contact the site owner.")
    job_id = secrets.token_hex(16)
    pk = f"job#{slug}#{job_id}"
    db().put({"pk": pk, "slug": slug, "id": job_id, "name": name, "status": "queued", "created": now, "payload": json.dumps(payload), "ttl": now + 2 * DAY})
    try:
        backends.client("lambda").invoke(FunctionName=os.environ["JOB_RUNNER_FUNCTION"], InvocationType="Event",
                                         Payload=json.dumps({"slug": slug, "id": job_id}).encode())
    except (ClientError, KeyError) as e:
        print(f"could not start the job runner: {e}")
        _release(slug, now)
        db().update(pk, {"status": "failed", "error": "The job could not be started. Please try again.", "finished": now, "ttl": now + DAY})
        return reply(502, error="jobs_unavailable", message="Background jobs are unavailable right now. Please try again.")
    return reply(202, id=job_id, name=name, status="queued")


def _public(job, with_result=True):
    out = {k: job[k] for k in ("id", "name", "status", "created", "started", "finished") if job.get(k) is not None}
    if job.get("status") == "failed":
        out["error"] = job.get("error", "The job failed.")
    if with_result and job.get("status") == "done":
        out["result"] = json.loads(job.get("result", "null"))
    return out


def _fresh(item, job, now):
    """A job whose runner never reported back (it was stopped) is shown as failed instead of 'running' for ever."""
    if job.get("status") in ("queued", "running") and now - job.get("created", now) > limits(item)["minutes"] * 60 + STALE_GRACE_SECONDS:
        return db().update(job["pk"], {"status": "failed", "error": "The job did not finish. It may have been stopped.", "finished": now, "ttl": now + DAY})
    return job


def status(item, job_id, now):
    if not ID_RE.match(job_id):
        return reply(400, error="bad_job_id", message="That is not a job id.")
    job = db().get(f"job#{item['slug']}#{job_id}")
    if not job:
        return reply(404, error="no_such_job", message="No such job (finished jobs are kept for one day).")
    return reply(200, **_public(_fresh(item, job, now)))


def recent(item, now):
    rows = [_fresh(item, j, now) for j in db().list_prefix(f"job#{item['slug']}#")]
    rows.sort(key=lambda j: j.get("created", 0), reverse=True)
    return reply(200, jobs=[_public(j, with_result=False) for j in rows[:LIST_LIMIT]])


# ---------- the job runner (platform function, see jobs_runner.py) ----------
def _fail(message):
    return {"status": "failed", "error": message}


def _execute(job, now):
    slug = job["slug"]
    lock = db().get(f"slug#{slug}")
    item = db().get(f"tok#{lock['token']}") if lock else None
    b = backends.settings(item) if item else {}
    name = b.get("job_function_name")
    if not item or not name or not b.get("jobs_enabled"):
        return _fail("Background jobs are not available for this site any more.")
    if portal.status_of(item, now) != "active":
        return _fail("This site is offline or its token has expired.")
    minutes = limits(item)["minutes"]
    event = {"job": {"id": job["id"], "name": job["name"], "payload": json.loads(job.get("payload", "null"))}}
    try:
        response = backends.client("lambda-long").invoke(FunctionName=name, InvocationType="RequestResponse", Payload=json.dumps(event).encode())
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        print(f"job {job['id']}: invoke failed: {code}")
        if code == "ResourceNotFoundException":
            return _fail("The job function is missing. Publish your backend again.")
        if code in ("TooManyRequestsException", "ResourceConflictException"):
            return _fail("The job function is busy or still updating. Please try again.")
        return _fail("The job could not run. Please try again.")
    raw = response["Payload"].read()
    if response.get("FunctionError"):
        try:
            message = str(json.loads(raw).get("errorMessage", ""))
        except ValueError:
            message = ""
        print(f"job {job['id']}: function error")  # the message may hold the student's secrets: it stays in the student's own function logs
        if "timed out" in message.lower():
            return _fail(f"The job took longer than its limit of {minutes} minute{'s' if minutes != 1 else ''}.")
        return _fail("The job raised an error. The details are in your backend logs (npm run logs).")
    try:
        json.loads(raw)
    except ValueError:
        return _fail("The job returned something that is not JSON.")
    if len(raw) > RESULT_MAX_BYTES:
        return _fail(f"The job's result is larger than {RESULT_MAX_BYTES // 1024} KB. Save big results with class storage and return a reference.")
    return {"status": "done", "result": raw.decode("utf-8")}


def run(slug, job_id, now=None):
    """Run one queued job to the end and record how it went. Safe to call twice: only a job that is still 'queued' runs."""
    real = now is None  # tests pass a fixed time; the real runner reads the clock before and after the job
    started = int(time.time()) if real else int(now)
    pk = f"job#{slug}#{job_id}"
    job = db().get(pk)
    if not job or job.get("status") != "queued":
        return None
    db().update(pk, {"status": "running", "started": started})
    try:
        outcome = _execute(job, started)
    except Exception as e:  # never leave a job 'running' and its slots taken
        print(f"job {job_id}: runner error: {type(e).__name__}")
        outcome = _fail("The job could not run. Please try again.")
    finally:
        _release(slug, started)
    finished = int(time.time()) if real else started
    return db().update(pk, {**outcome, "finished": finished, "ttl": finished + DAY})
