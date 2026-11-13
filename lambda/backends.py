"""Student backends: one Lambda function per student, called through the gateway (gateway.py).

State lives on the token item (item["backend"]); nothing here ever stores or returns environment-variable values.

  teacher:  enabled (on/off), runs_limit, runs_period (day | month | total)
  student:  state  none -> creating -> ready   (or failed),  runtime, function_name, env_names, error

A student uploads a zip to uploads/<slug>-backend.zip, publishes it, and polls GET /api/student/backend while the function
is created or updated (one step per call, like custom domains). Functions are named sd-<slug>, run with a sandbox role that
can only write logs, and are never deleted automatically.
"""
import hashlib
import io
import os
import posixpath
import re
import zipfile
from datetime import datetime, timezone

from botocore.exceptions import ClientError

import portal
from portal import DAY, PortalError, db
import platform_settings

NOT_ENABLED = "Owner not enabled for you. Please contact site owner."
FUNCTION_PREFIX = "sd-"
ENTRY_FILES = {  # entry file at the top of the zip -> (Lambda runtime, handler)
    "index.mjs": ("nodejs22.x", "index.handler"),
    "index.js": ("nodejs22.x", "index.handler"),
    "app.py": ("python3.12", "app.handler"),
}
MAX_ZIP_BYTES = platform_settings.mb("backend.maxZipMB")
MAX_UNZIPPED_BYTES = platform_settings.mb("backend.maxUnzippedMB")
MAX_FILES = platform_settings.value("backend.maxFiles")
MEMORY_MB = platform_settings.value("backend.memoryMB")
TIMEOUT_SECONDS = platform_settings.value("backend.timeoutSeconds")
LOG_RETENTION_DAYS = platform_settings.value("backend.logRetentionDays")
DEFAULT_RUNS = platform_settings.default("backend.runs")
MAX_RUNS = platform_settings.maximum("backend.runs")
PERIODS = ("day", "month", "total")
ENV_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ENV_RESERVED = ("AWS_", "LAMBDA_", "_")
ENV_RESERVED_NAMES = {"TZ"}
MAX_ENV_VARS = platform_settings.value("backend.maxEnvVars")
MAX_ENV_BYTES = platform_settings.value("backend.maxEnvBytes")

_clients = {}  # tests plug in fakes here


def client(name):
    if name not in _clients:
        import boto3
        if name == "s3":
            # Same settings as the site uploader in app.py: SigV4 presigned URLs. Without them boto3 can hand out legacy (SigV2)
            # URLs in us-east-1, which S3 rejects with SignatureDoesNotMatch as soon as the browser adds a Content-Type header.
            from botocore.config import Config
            _clients[name] = boto3.client("s3", region_name=os.environ.get("AWS_REGION"),
                                          config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
        elif name == "lambda-long":
            # the job runner waits for a job function for up to 15 minutes; boto's default read timeout is 60 seconds, and a retry would run the job twice
            from botocore.config import Config
            _clients[name] = boto3.client("lambda", config=Config(read_timeout=930, connect_timeout=10, retries={"max_attempts": 0}))
        else:
            _clients[name] = boto3.client(name)
    return _clients[name]


def available():
    return os.environ.get("BACKEND_ENABLED", "") == "true"


def function_name(slug):
    return FUNCTION_PREFIX + slug


def settings(item):
    return dict(item.get("backend") or {})


def _save(item, **fields):
    return db().update(item["pk"], {"backend": {**settings(item), **fields}})


# ---------- run allowance ----------
def period_info(period, now):
    """(period id, resets_at) in UTC. total never resets."""
    moment = datetime.fromtimestamp(now, timezone.utc)
    if period == "day":
        return moment.strftime("%Y-%m-%d"), int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()) + DAY
    if period == "month":
        year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
        return moment.strftime("%Y-%m"), int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    return "total", None


def usage(item, now):
    b = settings(item)
    period, limit = b.get("runs_period", "month"), b.get("runs_limit", DEFAULT_RUNS)
    period_id, resets_at = period_info(period, now)
    used = int((db().get(f"runs#{item['slug']}#{period_id}") or {}).get("count", 0))
    return {"period": period, "used": used, "limit": limit, "resets_at": resets_at}


def view(item, now):
    if not available():
        return {"available": False}
    import jobs
    b = settings(item)
    return {
        "jobs": jobs.view(item, now),
        "available": True, "enabled": bool(b.get("enabled")), "state": b.get("state", "none"), "runtime": b.get("runtime", ""),
        "deployed_at": b.get("deployed_at"), "env_names": b.get("env_names", []), "error": b.get("error", ""),
        "limits": {"memory_mb": MEMORY_MB, "timeout_seconds": TIMEOUT_SECONDS, "zip_mb": MAX_ZIP_BYTES // 1048576},
        "usage": usage(item, now) if b.get("enabled") or b.get("state", "none") != "none" else None,
    }


# ---------- teacher ----------
def set_backend(item, body, now):
    """Teacher: switch on/off and set the run allowance. OFF stops serving immediately (the gateway checks it)."""
    if not available():
        raise PortalError(503, "Student backends are not set up on this server.")
    b = settings(item)
    changes = {}
    if "runs_limit" in body:
        limit = body["runs_limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_RUNS:
            raise PortalError(400, f"runs_limit must be a whole number from 0 to {MAX_RUNS}.")
        changes["runs_limit"] = limit
    if "runs_period" in body:
        if body["runs_period"] not in PERIODS:
            raise PortalError(400, "runs_period must be day, month or total.")
        changes["runs_period"] = body["runs_period"]
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise PortalError(400, "enabled must be true or false.")
        changes["enabled"] = body["enabled"]
    import jobs
    job_changes = jobs.parse_settings(body)
    changes.update(job_changes)
    if changes.get("enabled") and "runs_limit" not in b and "runs_limit" not in changes:
        changes["runs_limit"] = DEFAULT_RUNS  # first time on: a sensible default allowance
        changes.setdefault("runs_period", "month")
    item = _save(item, **changes)
    if "enabled" in changes and settings(item).get("function_name"):
        _apply_concurrency(settings(item)["function_name"], changes["enabled"])
    if job_changes:  # create, resize or refuse the second function that runs the jobs
        item = jobs.sync_function(item)
    return item


def _apply_concurrency(name, enabled):
    """Belt and braces for OFF: ask AWS itself to refuse invocations (reserved concurrency 0). Best effort."""
    try:
        reserved = platform_settings.value("backend.reservedConcurrency")
        if not enabled:
            client("lambda").put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=0)
        elif reserved > 0:
            client("lambda").put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=reserved)
        else:
            client("lambda").delete_function_concurrency(FunctionName=name)
    except ClientError as e:
        print(f"could not update reserved concurrency for {name}: {e}")


# ---------- student: upload and publish ----------
def _require_can_deploy(item, now):
    if not available():
        raise PortalError(503, "Student backends are not set up on this server.")
    if not settings(item).get("enabled"):
        raise PortalError(403, NOT_ENABLED)
    if portal.status_of(item, now) != "active":
        raise PortalError(403, "You can deploy a backend while your token is active.")


def upload_url(item, now):
    _require_can_deploy(item, now)
    url = client("s3").generate_presigned_url(
        "put_object", Params={"Bucket": os.environ["BUCKET"], "Key": f"uploads/{item['slug']}-backend.zip"}, ExpiresIn=600)
    return {"uploadUrl": url, "maxBytes": MAX_ZIP_BYTES}


def inspect_zip(archive):
    """Validate the zip WITHOUT running anything. Returns (runtime, handler)."""
    names, total = [], 0
    for info in archive.infolist():
        if info.is_dir() or "__MACOSX/" in info.filename or info.filename.endswith((".DS_Store", "Thumbs.db")):
            continue
        raw = info.filename.replace("\\", "/")
        path = posixpath.normpath(raw)
        if raw.startswith("/") or path.startswith("../") or path in (".", ".."):
            raise PortalError(400, f"Unsafe path in zip: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise PortalError(400, f"Symbolic links are not allowed: {info.filename}")
        if posixpath.basename(path).startswith(".env"):
            raise PortalError(400, f"Remove {path} from the zip. Set secrets as environment variables in the portal instead.")
        names.append(path)
        total += info.file_size
    if len(names) > MAX_FILES:
        raise PortalError(400, f"Too many files ({len(names)}); the limit is {MAX_FILES}.")
    if total > MAX_UNZIPPED_BYTES:
        raise PortalError(400, f"The backend is {total // 1048576} MB unzipped; the limit is {MAX_UNZIPPED_BYTES // 1048576} MB.")
    found = [entry for entry in ENTRY_FILES if entry in names]
    if not found:
        raise PortalError(400, "No entry file at the top of the zip. Add index.mjs (Node 22) or app.py (Python 3.12) exporting a handler.")
    if len({ENTRY_FILES[entry][0] for entry in found}) > 1:
        raise PortalError(400, "Include only one entry file: index.mjs or app.py, not both.")
    return ENTRY_FILES[found[0]]


def publish(item, now):
    _require_can_deploy(item, now)
    bucket, slug = os.environ["BUCKET"], item["slug"]
    key = f"uploads/{slug}-backend.zip"
    s3 = client("s3")
    try:
        size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            raise PortalError(400, "No upload found. Upload your backend zip first.")
        raise
    if size > MAX_ZIP_BYTES:
        s3.delete_object(Bucket=bucket, Key=key)
        raise PortalError(400, f"The zip is {size // 1048576} MB; the limit is {MAX_ZIP_BYTES // 1048576} MB.")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        runtime, handler = inspect_zip(zipfile.ZipFile(io.BytesIO(body)))
    except zipfile.BadZipFile:
        raise PortalError(400, "Upload is not a valid zip file.")

    b, name, lam = settings(item), function_name(slug), client("lambda")
    import ai
    import knowledge
    import storage
    # the connectors (class AI, class storage, class knowledge), when the teacher has switched them on for this student
    import auth
    ai_vars = {**ai.env_for(item), **storage.env_for(item), **knowledge.env_for(item), **auth.env_for(item)}
    try:
        if b.get("function_name"):
            if b.get("runtime") and b["runtime"] != runtime:
                raise PortalError(400, "You switched runtime. Remove the backend first, then upload again.")
            lam.update_function_code(FunctionName=name, S3Bucket=bucket, S3Key=key)
        else:
            lam.create_function(
                FunctionName=name, Runtime=runtime, Role=os.environ["SANDBOX_ROLE_ARN"], Handler=handler,
                Code={"S3Bucket": bucket, "S3Key": key}, MemorySize=MEMORY_MB, Timeout=ai.timeout_for(item),
                Description=f"Student backend for {slug}", Tags={"managed-by": "student-deploy", "slug": slug},
                **({"Environment": {"Variables": ai_vars}} if ai_vars else {}))
            _prepare_function(name, b.get("enabled", False))
    except ClientError as e:
        raise PortalError(502, f"Could not deploy your backend: {e.response['Error'].get('Message', 'AWS error')}")
    extra = {"env_names": sorted(ai_vars)} if ai_vars and not b.get("function_name") else {}
    item = _save(item, state="creating", runtime=runtime, handler=handler, function_name=name,
                 pending_sha=hashlib.sha256(body).hexdigest(), error="", **extra)
    import jobs
    return jobs.sync_function(item, code_changed=True)  # the same code also goes to the job function when jobs are on


def _prepare_function(name, enabled):
    """Log retention (otherwise logs never expire) and the concurrency setting for a brand-new function."""
    try:
        client("logs").create_log_group(logGroupName=f"/aws/lambda/{name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            print(f"could not create log group for {name}: {e}")
    try:
        client("logs").put_retention_policy(logGroupName=f"/aws/lambda/{name}", retentionInDays=LOG_RETENTION_DAYS)
    except ClientError as e:
        print(f"could not set log retention for {name}: {e}")
    if platform_settings.value("backend.reservedConcurrency") > 0 or not enabled:
        _apply_concurrency(name, enabled)


def advance(item, now):
    """Poll step: move creating -> ready (or failed) by asking Lambda. Safe to call repeatedly."""
    b = settings(item)
    if b.get("state") != "creating":
        return item
    try:
        config = client("lambda").get_function(FunctionName=b["function_name"])["Configuration"]
    except ClientError as e:
        raise PortalError(502, f"Could not check your backend: {e.response['Error'].get('Message', 'AWS error')}")
    state, update = config.get("State"), config.get("LastUpdateStatus")
    if state == "Failed" or update == "Failed":
        return _save(item, state="failed", error=config.get("StateReason") or config.get("LastUpdateStatusReason") or "Deployment failed.")
    if state == "Active" and update in (None, "Successful"):
        # a function made before the limits changed keeps its old memory and time until now: bring both in line with today's settings (once the code update has
        # finished: Lambda refuses two updates at the same moment), and stay "creating" until the next poll sees that change finish too
        import ai
        want = {"MemorySize": MEMORY_MB, "Timeout": ai.timeout_for(item)}
        if any(config.get(k) is not None and config[k] != v for k, v in want.items()):
            try:
                client("lambda").update_function_configuration(FunctionName=b["function_name"], **want)
                return item
            except ClientError as e:
                print(f"could not bring the limits of {b['function_name']} up to date: {e}")  # not fatal: the backend still works with its old limits
        return _save(item, state="ready", deployed_at=now, code_sha256=b.get("pending_sha", ""), error="")
    return item


# ---------- student: environment variables, logs, removal ----------
def _require_ready(item):
    b = settings(item)
    if not b.get("function_name") or b.get("state") not in ("ready", "failed"):
        raise PortalError(409, "Your backend is not ready yet. Wait until it shows Ready.")
    return b["function_name"]


def _write_env(item, name, variables):
    try:
        client("lambda").update_function_configuration(FunctionName=name, Environment={"Variables": variables})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            raise PortalError(409, "Your function is still updating. Try again in a few seconds.")
        raise PortalError(502, f"Could not save the variable: {e.response['Error'].get('Message', 'AWS error')}")
    import jobs
    jobs.mirror_env(item, variables)
    return _save(item, env_names=sorted(variables))


def _current_env(name):
    config = client("lambda").get_function_configuration(FunctionName=name)
    return dict((config.get("Environment") or {}).get("Variables") or {})


def set_env(item, var, value, now):
    _require_can_deploy(item, now)
    name = _require_ready(item)
    var, value = str(var or ""), str(value if value is not None else "")
    import ai
    import knowledge
    import storage
    if ai.reserved_name(item, var) or storage.reserved_name(item, var) or knowledge.reserved_name(item, var):
        raise PortalError(400, f"{var} is set by a class connector (AI, storage or knowledge) while it is on for you. Ask your teacher to switch it off to use your own.")
    import auth
    if auth.reserved_name(item, var):
        raise PortalError(400, f"{var} is set by the platform's sign-in. Turn sign-in on or off instead (npm run signin).")
    if not ENV_NAME_RE.match(var) or var.startswith(ENV_RESERVED) or var in ENV_RESERVED_NAMES:
        raise PortalError(400, "Use a name with letters, digits and underscores, starting with a letter (not AWS_*, LAMBDA_* or TZ).")
    if len(value) > 1024:
        raise PortalError(400, "A value can be up to 1024 characters.")
    variables = _current_env(name)
    variables[var] = value
    if len(variables) > MAX_ENV_VARS or sum(len(k) + len(v) for k, v in variables.items()) > MAX_ENV_BYTES:
        raise PortalError(400, f"Too many or too large: at most {MAX_ENV_VARS} variables and {MAX_ENV_BYTES} bytes in total.")
    return _write_env(item, name, variables)


def delete_env(item, var, now):
    _require_can_deploy(item, now)
    name = _require_ready(item)
    import ai
    import knowledge
    import storage
    if ai.reserved_name(item, var) or storage.reserved_name(item, var) or knowledge.reserved_name(item, var):
        raise PortalError(400, f"{var} is set by a class connector (AI, storage or knowledge) and cannot be removed while it is on for you.")
    import auth
    if auth.reserved_name(item, var):
        raise PortalError(400, f"{var} is set by the platform's sign-in. Turn sign-in off instead (npm run signin -- off).")
    variables = _current_env(name)
    variables.pop(var, None)
    return _write_env(item, name, variables)


LOG_LIMIT_DEFAULT = platform_settings.default("backend.logLines")
LOG_LIMIT_MAX = platform_settings.maximum("backend.logLines")
LOG_MINUTES_DEFAULT = platform_settings.default("backend.logMinutes")
LOG_MINUTES_MAX = platform_settings.maximum("backend.logMinutes")
LOG_PAGE = platform_settings.value("backend.logPageEvents")
LOG_PAGES = platform_settings.value("backend.logPages")  # up to LOG_PAGE x LOG_PAGES events are read per request, then the newest ones are kept
LOG_CUT = platform_settings.value("backend.logCutChars")  # a long message keeps its END (the real error is at the end of a traceback)
LOG_MIN_SECRET = platform_settings.value("backend.logMinSecretChars")  # an environment variable value shorter than this is not masked (too many false hits)
# CloudWatch filter syntax: any of these words (case matters). The current Python and Node runtimes report a timeout ONLY as `REPORT ... Status: timeout`
# (seen on a real account: there is no "Task timed out" line), so the Status words are what finds it; the older wording stays for older runtimes.
LOG_ERRORS_PATTERN = '?ERROR ?Error ?Exception ?Traceback ?Unhandled ?"Task timed out" ?"Runtime.OutOfMemory" ?"Status: timeout" ?"Status: error"'


def _clamp(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _log_noise(message):
    """START/END/INIT_START lines say nothing; a REPORT line matters only when the run failed (it then carries a Status)."""
    return message.startswith(("START RequestId", "END RequestId", "INIT_START")) or (message.startswith("REPORT RequestId") and "Status:" not in message)


def _mask(message, secrets):
    for secret in secrets:
        message = message.replace(secret, "***")
    return message


def logs(item, now, minutes=LOG_MINUTES_DEFAULT, limit=LOG_LIMIT_DEFAULT, errors=False):
    """The NEWEST log lines of the student's function (oldest first), without the START/END noise, with the end of long messages kept and the
    values of the student's own environment variables masked, so a pasted log never carries a secret that was printed by mistake."""
    minutes = _clamp(minutes, LOG_MINUTES_DEFAULT, 1, LOG_MINUTES_MAX)
    limit = _clamp(limit, LOG_LIMIT_DEFAULT, 1, LOG_LIMIT_MAX)
    name = settings(item).get("function_name")
    result = {"lines": [], "minutes": minutes, "truncated": False}
    if not name:  # the groups come from the student's own record, never from the request
        return result
    job = settings(item).get("job_function_name")  # background jobs write to their own group: shown together, each line marked [job]
    kept = []
    for group, is_job in ((f"/aws/lambda/{name}", False), *(((f"/aws/lambda/{job}", True),) if job else ())):
        arguments = {"logGroupName": group, "limit": LOG_PAGE, "startTime": (now - minutes * 60) * 1000}
        if errors:
            arguments["filterPattern"] = LOG_ERRORS_PATTERN
        try:
            for _ in range(LOG_PAGES):
                page = client("logs").filter_log_events(**arguments)
                kept = sorted(kept + [{**e, "job": is_job} for e in page["events"] if not _log_noise(e["message"])], key=lambda e: e["timestamp"])[-limit:]
                if not page.get("nextToken"):
                    break
                arguments["nextToken"] = page["nextToken"]
            else:
                result["truncated"] = True  # more than LOG_PAGES pages: only the newest of what was read is shown
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":  # a group that does not exist (yet) simply has no lines
                raise
    try:
        secrets = sorted({v for v in _current_env(name).values() if len(v) >= LOG_MIN_SECRET}, key=len, reverse=True)
    except ClientError:
        secrets = []
    for e in kept:
        message = _mask(e["message"].rstrip(), secrets)
        if len(message) > LOG_CUT:
            message = "[...cut] " + message[-LOG_CUT:]
        result["lines"].append({"t": e["timestamp"] // 1000, "message": ("[job] " + message) if e["job"] else message})
    return result


def remove(item, now):
    """Explicit removal by the student. Keeps the teacher's settings (on/off and allowance)."""
    b = settings(item)
    name = b.get("function_name")
    import jobs
    jobs.remove_function(item)
    if name:
        try:
            client("lambda").delete_function(FunctionName=name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise PortalError(502, f"Could not remove your backend: {e.response['Error'].get('Message', 'AWS error')}")
        try:
            client("logs").delete_log_group(logGroupName=f"/aws/lambda/{name}")
        except ClientError:
            pass
    keep = {k: v for k, v in b.items() if k in ("enabled", "runs_limit", "runs_period", "jobs_enabled", "jobs_per_day", "jobs_concurrent", "jobs_minutes")}
    return db().update(item["pk"], {"backend": {**keep, "state": "none"}})
