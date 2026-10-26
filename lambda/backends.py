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

NOT_ENABLED = "Owner not enabled for you. Please contact site owner."
FUNCTION_PREFIX = "sd-"
ENTRY_FILES = {  # entry file at the top of the zip -> (Lambda runtime, handler)
    "index.mjs": ("nodejs22.x", "index.handler"),
    "index.js": ("nodejs22.x", "index.handler"),
    "app.py": ("python3.12", "app.handler"),
}
MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
MEMORY_MB = 256
TIMEOUT_SECONDS = 10
LOG_RETENTION_DAYS = 7
DEFAULT_RUNS, MAX_RUNS, PERIODS = 5000, 1_000_000, ("day", "month", "total")
ENV_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
ENV_RESERVED = ("AWS_", "LAMBDA_", "_")
ENV_RESERVED_NAMES = {"TZ"}
MAX_ENV_VARS, MAX_ENV_BYTES = 20, 4096

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
    b = settings(item)
    return {
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
    if changes.get("enabled") and "runs_limit" not in b and "runs_limit" not in changes:
        changes["runs_limit"] = DEFAULT_RUNS  # first time on: a sensible default allowance
        changes.setdefault("runs_period", "month")
    item = _save(item, **changes)
    if "enabled" in changes and settings(item).get("function_name"):
        _apply_concurrency(settings(item)["function_name"], changes["enabled"])
    return item


def _apply_concurrency(name, enabled):
    """Belt and braces for OFF: ask AWS itself to refuse invocations (reserved concurrency 0). Best effort."""
    try:
        reserved = int(os.environ.get("BACKEND_RESERVED_CONCURRENCY", "0") or 0)
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
    import storage
    # the connectors (class AI, class storage), when the teacher has switched them on for this student
    ai_vars = {**ai.env_for(item), **storage.env_for(item)}
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
    return _save(item, state="creating", runtime=runtime, handler=handler, function_name=name,
                 pending_sha=hashlib.sha256(body).hexdigest(), error="", **extra)


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
    if int(os.environ.get("BACKEND_RESERVED_CONCURRENCY", "0") or 0) > 0 or not enabled:
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
    return _save(item, env_names=sorted(variables))


def _current_env(name):
    config = client("lambda").get_function_configuration(FunctionName=name)
    return dict((config.get("Environment") or {}).get("Variables") or {})


def set_env(item, var, value, now):
    _require_can_deploy(item, now)
    name = _require_ready(item)
    var, value = str(var or ""), str(value if value is not None else "")
    import ai
    import storage
    if ai.reserved_name(item, var) or storage.reserved_name(item, var):
        raise PortalError(400, f"{var} is set by a class connector (AI or storage) while it is on for you. Ask your teacher to switch it off to use your own.")
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
    import storage
    if ai.reserved_name(item, var) or storage.reserved_name(item, var):
        raise PortalError(400, f"{var} is set by a class connector (AI or storage) and cannot be removed while it is on for you.")
    variables = _current_env(name)
    variables.pop(var, None)
    return _write_env(item, name, variables)


def logs(item, now, limit=50):
    name = settings(item).get("function_name")
    if not name:
        return []
    try:
        events = client("logs").filter_log_events(logGroupName=f"/aws/lambda/{name}", limit=limit, startTime=(now - 3600) * 1000)["events"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return []
        raise
    return [{"t": e["timestamp"] // 1000, "message": e["message"].rstrip()[:500]} for e in events][-limit:]


def remove(item, now):
    """Explicit removal by the student. Keeps the teacher's settings (on/off and allowance)."""
    b = settings(item)
    name = b.get("function_name")
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
    keep = {k: v for k, v in b.items() if k in ("enabled", "runs_limit", "runs_period")}
    return db().update(item["pk"], {"backend": {**keep, "state": "none"}})
