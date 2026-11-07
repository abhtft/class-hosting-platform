"""Compute worker jobs: a student's own script (access level 3), stored and queued by the platform, run later by the worker machine.
See docs/COMPUTE_WORKER_PLAN.md section 11.5.  This module NEVER starts the machine: only the teacher (or the daily schedule) does.

Flow
  1. create   the student asks for a job (language, entry file); the platform checks the level, the daily request count, open jobs and the month's minutes,
              and answers with a presigned upload form for ONE zip of at most MAX_INPUT_BYTES under worker/jobs/<slug>/<id>/input.zip.
  2. submit   the student says the upload is done; the platform checks the object exists and its size, marks the job "queued" and rewrites the queue
              file worker/queue/queue.json (the machine has no database access: it reads this file, nothing else).
  3. run      (later step) the machine takes jobs one at a time from queue.json, runs each in a locked-down container, and writes
              worker/jobs/<slug>/<id>/status.json and result.zip.
  4. sync     the platform reads status.json when anyone looks at the job (or the control function ticks) and updates the record, adds the minutes to the
              student's month, and closes the job.
State lives in the tokens table: wjob#<slug>#<id> (the job), wjobs#<slug> (the student's list of ids, newest first, at most MAX_LISTED),
wjr#<slug>#<day> and wjm#<slug>#<month> (counters). Everything expires by itself.
The student can only ever name their OWN jobs: every key is built from the signed-in student's slug.
"""
import json
import re
import secrets
import time

import portal
import worker
from portal import DAY, PortalError

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OPEN_JOBS = 3
MAX_LISTED = 30
QUEUE_KEY = "worker/queue/queue.json"
QUEUE_EXPIRES_DAYS = 7
LANGUAGES = ("python", "node")
OPEN = ("created", "queued", "running")
FINISHED = ("done", "failed", "cancelled", "expired")
ENTRY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
UPLOAD_SECONDS = 600
LOG_TAIL = 3000


def _db():
    return portal.db()


def _key(slug, job_id):
    return f"wjob#{slug}#{job_id}"


def _prefix(slug, job_id):
    return f"worker/jobs/{slug}/{job_id}/"


def _month(now):
    return time.strftime("%Y-%m", time.gmtime(now))


def _day(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _s3():
    return worker.client("s3")


def _read_json(key):
    try:
        data = json.loads(_s3().get_object(Bucket=worker.bucket(), Key=key)["Body"].read())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _view(job, log=False):
    keep = ("id", "slug", "state", "language", "entry", "created_at", "queued_at", "started_at", "finished_at", "size", "job_minutes", "network", "seconds", "exit", "has_result", "message")
    out = {k: job[k] for k in keep if k in job}
    if log and job.get("log"):
        out["log"] = job["log"]
    return out


def _need_level(item):
    if not worker.available():
        raise PortalError(409, "The compute worker is not installed on this platform.")
    a = worker.access(item)
    if a["level"] < 3:
        raise PortalError(403, "Your teacher has not switched on running your own code for you. Ask your teacher.")
    return a


def _get(item, job_id):
    if not isinstance(job_id, str) or not ID_RE.match(job_id):
        raise PortalError(404, "No such job.")
    job = _db().get(_key(item["slug"], job_id))
    if not job:
        raise PortalError(404, "No such job (it may have been removed).")
    return job


def _put(job):
    _db().put(job)


def _remember(slug, job_id, now):
    listed = (_db().get(f"wjobs#{slug}") or {}).get("ids", [])
    ids = [job_id] + [i for i in listed if i != job_id]
    for gone in ids[MAX_LISTED:]:
        _delete_job(slug, gone)
    _db().put({"pk": f"wjobs#{slug}", "ids": ids[:MAX_LISTED], "ttl": now + 30 * DAY})


def _delete_job(slug, job_id):
    job = _db().get(_key(slug, job_id))
    if job and job.get("state") in OPEN:
        return False
    _db().delete(_key(slug, job_id))
    try:
        s3 = _s3()
        for name in ("input.zip", "status.json", "result.zip"):
            s3.delete_object(Bucket=worker.bucket(), Key=_prefix(slug, job_id) + name)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- the student's side
def usage(item, now):
    a = worker.access(item)
    used = int((_db().get(f"wjm#{item['slug']}#{_month(now)}") or {}).get("count", 0))
    today = int((_db().get(f"wjr#{item['slug']}#{_day(now)}") or {}).get("count", 0))
    return {"requests_today": today, "requests_per_day": a["requests_per_day"], "minutes_used": used, "minutes_per_month": a["minutes_per_month"]}


def create(item, body, now):
    a = _need_level(item)
    if not isinstance(body, dict):
        raise PortalError(400, "Send a JSON object.")
    language, entry = body.get("language"), body.get("entry")
    if language not in LANGUAGES:
        raise PortalError(400, "language must be python or node.")
    if not isinstance(entry, str) or not ENTRY_RE.match(entry):
        raise PortalError(400, "entry must be the name of the file to run, for example main.py (letters, digits, dot, dash, underscore).")
    slug = item["slug"]
    if usage(item, now)["minutes_used"] >= a["minutes_per_month"]:
        raise PortalError(429, "Your machine minutes for this month are used up. Ask your teacher for more, or wait for next month.")
    open_jobs = [j for j in _mine(item) if j["state"] in OPEN]
    if len(open_jobs) >= MAX_OPEN_JOBS:
        raise PortalError(429, f"You already have {MAX_OPEN_JOBS} jobs waiting or running. Wait for one to finish, or cancel one.")
    if _db().counter_add(f"wjr#{slug}#{_day(now)}", 1, limit=a["requests_per_day"], ttl=now + 2 * DAY, now=now) is None:
        raise PortalError(429, "You have used your job requests for today. Try again tomorrow.")
    job_id = secrets.token_hex(6)
    job = {"pk": _key(slug, job_id), "id": job_id, "slug": slug, "state": "created", "language": language, "entry": entry, "created_at": now, "job_minutes": a["job_minutes"],
           "network": a["network"], "ttl": now + QUEUE_EXPIRES_DAYS * DAY + 30 * DAY}
    _put(job)
    _remember(slug, job_id, now)
    post = _s3().generate_presigned_post(Bucket=worker.bucket(), Key=_prefix(slug, job_id) + "input.zip", Fields={"Content-Type": "application/zip"},
                                         Conditions=[{"Content-Type": "application/zip"}, ["content-length-range", 1, MAX_INPUT_BYTES]], ExpiresIn=UPLOAD_SECONDS)
    return {"job": _view(job), "upload": {"url": post["url"], "fields": post["fields"], "max_bytes": MAX_INPUT_BYTES, "expires_in": UPLOAD_SECONDS}}


def submit(item, job_id, now):
    job = _get(item, job_id)
    _need_level(item)
    if job["state"] != "created":
        raise PortalError(409, f"This job is already {job['state']}.")
    try:
        size = int(_s3().head_object(Bucket=worker.bucket(), Key=_prefix(item["slug"], job_id) + "input.zip")["ContentLength"])
    except Exception:
        raise PortalError(409, "The files have not been uploaded yet. Upload the zip first.")
    if not 0 < size <= MAX_INPUT_BYTES:
        raise PortalError(413, "The zip is empty or too large.")
    job.update(state="queued", queued_at=now, size=size)
    _put(job)
    stage_queue(now)
    return {"job": _view(job)}


def cancel(item, job_id, now):
    job = _get(item, job_id)
    if job["state"] in ("created", "queued"):
        job.update(state="cancelled", finished_at=now, message="Cancelled before it ran.")
        _put(job)
        stage_queue(now)
    elif job["state"] == "running":
        raise PortalError(409, "The job is running now. It will stop by itself at its time limit.")
    return {"job": _view(job)}


def delete(item, job_id, now):
    job = _get(item, job_id)
    if job["state"] in OPEN:
        raise PortalError(409, "Cancel the job first.")
    _delete_job(item["slug"], job_id)
    _db().put({"pk": f"wjobs#{item['slug']}", "ids": [i for i in (_db().get(f"wjobs#{item['slug']}") or {}).get("ids", []) if i != job_id], "ttl": now + 30 * DAY})
    return {"deleted": True}


def _mine(item, now=None):
    jobs = []
    for job_id in (_db().get(f"wjobs#{item['slug']}") or {}).get("ids", []):
        job = _db().get(_key(item["slug"], job_id))
        if job:
            jobs.append(job)
    return jobs


def list_mine(item, now):
    _need_level_or_view(item)
    jobs = [sync(j, now) for j in _mine(item)]
    return {"jobs": [_view(j) for j in jobs], "usage": usage(item, now)}


def _need_level_or_view(item):
    if not worker.available():
        raise PortalError(409, "The compute worker is not installed on this platform.")
    if worker.access(item)["level"] < 3:
        raise PortalError(403, "Your teacher has not switched on running your own code for you. Ask your teacher.")


def get_mine(item, job_id, now):
    _need_level_or_view(item)
    return {"job": _view(sync(_get(item, job_id), now), log=True)}


def result_url(item, job_id, now):
    _need_level_or_view(item)
    job = sync(_get(item, job_id), now)
    if job["state"] not in ("done", "failed") or not job.get("has_result"):
        raise PortalError(409, "This job has no result to download.")
    url = _s3().generate_presigned_url("get_object", Params={"Bucket": worker.bucket(), "Key": _prefix(item["slug"], job_id) + "result.zip"}, ExpiresIn=300)
    return {"url": url, "expires_in": 300}


# ---------------------------------------------------------------- the machine's side of the protocol (read by the platform)
def sync(job, now):
    """Bring a job up to date from what the machine wrote (status.json), expire a job that waited too long, and count its minutes once."""
    if job["state"] not in ("queued", "running"):
        return job
    slug, job_id = job["slug"], job["id"]
    status = _read_json(_prefix(slug, job_id) + "status.json")
    changed = False
    if status and status.get("state") in ("running", "done", "failed"):
        job["state"] = status["state"]
        for src, dst in (("started", "started_at"), ("finished", "finished_at")):
            if isinstance(status.get(src), int):
                job[dst] = status[src]
        if isinstance(status.get("seconds"), int):
            job["seconds"] = status["seconds"]
        if isinstance(status.get("exit"), int):
            job["exit"] = status["exit"]
        job["has_result"] = bool(status.get("result"))
        log = status.get("log_tail")
        if isinstance(log, str):
            job["log"] = log[-LOG_TAIL:]
        if status.get("message") and isinstance(status["message"], str):
            job["message"] = status["message"][:200]
        changed = True
        if job["state"] in ("done", "failed") and not job.get("accounted"):
            minutes = max(1, -(-int(job.get("seconds", 0)) // 60))
            _db().counter_add(f"wjm#{slug}#{_month(now)}", minutes, ttl=now + 62 * DAY, now=now)
            job["accounted"] = True
    elif job["state"] == "queued" and now - int(job.get("queued_at", now)) > QUEUE_EXPIRES_DAYS * DAY:
        job.update(state="expired", finished_at=now, message="It waited too long for the machine to be started.")
        changed = True
    if changed:
        _put(job)
        if job["state"] in FINISHED or job["state"] == "running":
            stage_queue(now)
    return job


def all_jobs():
    return [j for j in _db().list_prefix("wjob#") if j and j.get("slug") and j.get("id")]


def stage_queue(now):
    """Write the queue file the machine reads: queued jobs only, oldest first. The machine never touches the database."""
    jobs = sorted((j for j in all_jobs() if j.get("state") == "queued"), key=lambda j: j.get("queued_at", 0))
    queue = {"updated": int(now), "jobs": [{"id": j["id"], "slug": j["slug"], "language": j["language"], "entry": j["entry"], "input": _prefix(j["slug"], j["id"]) + "input.zip",
                                            "minutes": j["job_minutes"], "network": bool(j["network"]), "queued_at": j["queued_at"]} for j in jobs]}
    _s3().put_object(Bucket=worker.bucket(), Key=QUEUE_KEY, Body=json.dumps(queue).encode(), ContentType="application/json")
    return queue


# ---------------------------------------------------------------- the teacher's side
def queue_view(now):
    if not worker.available():
        return {"available": False, "jobs": []}
    jobs = [sync(j, now) for j in all_jobs() if j.get("state") in OPEN]
    recent = [j for j in all_jobs() if j.get("state") in FINISHED]
    recent.sort(key=lambda j: -int(j.get("finished_at") or j.get("created_at") or 0))
    ordered = sorted(jobs, key=lambda j: (j.get("queued_at") or j.get("created_at") or 0)) + recent[:20]
    return {"available": True, "jobs": [_view(j, log=False) for j in ordered]}


def teacher_cancel(slug, job_id, now):
    if not isinstance(slug, str) or not isinstance(job_id, str) or not ID_RE.match(job_id):
        raise PortalError(404, "No such job.")
    job = _db().get(_key(slug, job_id))
    if not job:
        raise PortalError(404, "No such job.")
    if job["state"] in ("created", "queued"):
        job.update(state="cancelled", finished_at=now, message="Cancelled by the teacher.")
        _put(job)
        stage_queue(now)
    elif job["state"] == "running":
        raise PortalError(409, "The job is running now. It stops at its time limit, or when you stop the machine.")
    return {"job": _view(job)}


def teacher_delete(slug, job_id, now):
    job = _db().get(_key(slug, job_id)) if isinstance(slug, str) and isinstance(job_id, str) and ID_RE.match(job_id) else None
    if not job:
        raise PortalError(404, "No such job.")
    if job["state"] in OPEN:
        raise PortalError(409, "Cancel the job first.")
    _delete_job(slug, job_id)
    return {"deleted": True}
