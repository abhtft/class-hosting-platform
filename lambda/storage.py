"""Class storage connector: the teacher can give a student's backend a small database and a small file area, with hard caps.

State lives on the token item (item["storage"]):
  teacher:  enabled, data_mb, files_mb, ops_limit, ops_period (day | month | total)
  platform: key (cs_<slug>.<secret>), the student's own access key

What the student's function receives (only while the connector is on): CLASS_STORAGE_URL and CLASS_STORAGE_KEY. The helper in the
student kit (class_storage.py / class_storage.mjs) turns them into put/get/items/delete and files.* calls. The key is only good for
THAT student's data: the storage API (storage_api.py) works out the owner from the key and builds every database key and S3 key
itself, so one student can never name another student's data. Nothing is shared except the table and the bucket.

  data   DynamoDB table (partition <slug>#<collection>, sort key <id>), JSON values up to 64 KB, counted in bytes and items
  files  S3 userdata/<slug>/<name>, up to 5 MB each, a total cap and a file-count cap (counted by listing, so nothing can drift)
  ops    every storage call counts against an allowance per day, month or in total (atomic, like the backend run allowance)

The teacher can also clear a student's data and files and see storage across the class (overview, clear); nothing here ever shows the
teacher what a student stored, only sizes and counts.

Cost is bounded by these caps: the table is on-demand (an idle app costs nothing), the data is small, the allowance stops loops.
"""
import json
import os
import re
import secrets
import time

import backends
import portal
from botocore.exceptions import ClientError
from portal import DAY, PortalError, db

ENV_NAMES = ("CLASS_STORAGE_URL", "CLASS_STORAGE_KEY")
PERIODS = ("day", "month", "total")
MB = 1024 * 1024
MAX_VALUE_BYTES = 64 * 1024
MAX_ITEMS = 5000
MAX_FILE_BYTES = 5 * MB
MAX_FILES = 100
MAX_COLLECTIONS = 50  # collection names a student may ever use (each is remembered so the teacher can clear them)
DEFAULT_DATA_MB, MAX_DATA_MB = 5, 100
DEFAULT_FILES_MB, MAX_FILES_MB = 50, 500
DEFAULT_OPS, MAX_OPS = 10_000, 1_000_000
DEFAULT_PERIOD = "day"
LIST_PAGE_ITEMS, LIST_PAGE_BYTES = 100, 400 * 1024  # a list answer must stay far below Lambda's 6 MB response limit
UPLOAD_SECONDS, DOWNLOAD_SECONDS = 300, 300
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")                 # collection
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")           # item id
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")          # file name (no folders)
TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
KEY_RE = re.compile(r"^cs_([a-z0-9][a-z0-9-]{0,29})\.([A-Za-z0-9_-]{20,})$")


def available():
    return os.environ.get("STORAGE_ENABLED", "") == "true" and backends.available()


def api_url():
    return os.environ.get("STORAGE_URL", "").rstrip("/")


def settings(item):
    return dict(item.get("storage") or {})


def _save(item, **fields):
    return db().update(item["pk"], {"storage": {**settings(item), **fields}})


def is_on(item):
    s = settings(item)
    return bool(s.get("enabled") and s.get("key"))


def env_for(item):
    """The variables the student's function gets, or {} when the connector is off."""
    if not (available() and is_on(item) and api_url()):
        return {}
    return {"CLASS_STORAGE_URL": api_url(), "CLASS_STORAGE_KEY": settings(item)["key"]}


def reserved_name(item, name):
    """While the connector is on these names belong to the platform, so a student cannot overwrite or delete them."""
    return available() and is_on(item) and name in ENV_NAMES


def limits(item):
    s = settings(item)
    return {"data_mb": s.get("data_mb", DEFAULT_DATA_MB), "files_mb": s.get("files_mb", DEFAULT_FILES_MB),
            "ops_limit": s.get("ops_limit", DEFAULT_OPS), "ops_period": s.get("ops_period", DEFAULT_PERIOD)}


# ---------- counters (all in the tokens table; each is one item with an atomic "count") ----------
def _count(pk):
    return int((db().get(pk) or {}).get("count", 0))


def usage(item, now):
    slug, lim = item["slug"], limits(item)
    period_id, resets_at = backends.period_info(lim["ops_period"], now)
    return {"data_bytes": max(_count(f"sbytes#{slug}"), 0), "items": max(_count(f"sitems#{slug}"), 0),
            "ops_used": _count(f"sops#{slug}#{period_id}"), "resets_at": resets_at}


def view(item, now):
    if not available():
        return {"available": False}
    s, lim = settings(item), limits(item)
    out = {
        "available": True, "enabled": bool(s.get("enabled")), "limits": {**lim, "max_value_kb": MAX_VALUE_BYTES // 1024,
               "max_items": MAX_ITEMS, "max_file_mb": MAX_FILE_BYTES // MB, "max_files": MAX_FILES},
        "env_names": list(ENV_NAMES) if s.get("enabled") else [], "usage": None,
    }
    if s.get("enabled") or s.get("key"):
        out["usage"] = usage(item, now)
    return out


# ---------- teacher ----------
def _whole(body, name, low, high):
    value = body[name]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PortalError(400, f"{name} must be a whole number from {low} to {high}.")
    return value


def set_storage(item, body, now):
    """Teacher: switch on/off and set the caps. Off stops serving at once (the API checks it on every call). Safe to repeat."""
    if not available():
        raise PortalError(503, "The class storage connector is not set up on this server.")
    changes = {}
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise PortalError(400, "enabled must be true or false.")
        changes["enabled"] = body["enabled"]
    if "data_mb" in body:
        changes["data_mb"] = _whole(body, "data_mb", 1, MAX_DATA_MB)
    if "files_mb" in body:
        changes["files_mb"] = _whole(body, "files_mb", 1, MAX_FILES_MB)
    if "ops_limit" in body:
        changes["ops_limit"] = _whole(body, "ops_limit", 0, MAX_OPS)
    if "ops_period" in body:
        if body["ops_period"] not in PERIODS:
            raise PortalError(400, "ops_period must be day, month or total.")
        changes["ops_period"] = body["ops_period"]
    if not changes:
        return item
    if changes.get("enabled") and not settings(item).get("key"):
        changes["key"] = f"cs_{item['slug']}.{secrets.token_urlsafe(24)}"  # made once, kept while the student exists
    item = _save(item, **changes)
    if "enabled" in changes:
        apply_to_function(item)
    return item


def apply_to_function(item):
    """Make the student's function match the connector state (its two environment variables). A no-op until a function exists."""
    name = backends.settings(item).get("function_name")
    if not name:
        return item
    try:
        variables = backends._current_env(name)
        for var in ENV_NAMES:
            variables.pop(var, None)
        variables.update(env_for(item))
        backends.client("lambda").update_function_configuration(FunctionName=name, Environment={"Variables": variables})
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            raise PortalError(409, "The student's function is updating. Press the switch again in a few seconds; nothing is lost.")
        raise PortalError(502, f"Could not update the student's function: {e.response['Error'].get('Message', 'AWS error')}")
    return backends._save(item, env_names=sorted(variables))


def files_usage(item):
    """Count and size of the student's files (lists the folder; used for the student's own page, not for every teacher refresh)."""
    files = list_files(item)
    return {"count": len(files), "bytes": sum(f["size"] for f in files)}


# ====================== the storage API (used by storage_api.py) ======================
class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class MemoryData:
    """The data table in memory (tests and the dev portal)."""

    def __init__(self):
        self.rows = {}

    def get(self, part, key):
        row = self.rows.get((part, key))
        return dict(row) if row else None

    def put(self, part, key, row):
        old = self.rows.get((part, key))
        self.rows[(part, key)] = dict(row)
        return dict(old) if old else None

    def delete(self, part, key):
        old = self.rows.pop((part, key), None)
        return dict(old) if old else None

    def delete_partition(self, part):
        gone = [k for (p, k) in self.rows if p == part]
        for k in gone:
            del self.rows[(part, k)]
        return len(gone)

    def query(self, part, after, limit):
        keys = sorted(k for (p, k) in self.rows if p == part and (after is None or k > after))
        return [dict(self.rows[(part, k)], id=k) for k in keys[:limit]]


class DynamoData:
    def __init__(self, table_name):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)

    def get(self, part, key):
        return self.table.get_item(Key={"s": part, "id": key}).get("Item")

    def put(self, part, key, row):
        return self.table.put_item(Item={"s": part, "id": key, **row}, ReturnValues="ALL_OLD").get("Attributes")

    def delete(self, part, key):
        return self.table.delete_item(Key={"s": part, "id": key}, ReturnValues="ALL_OLD").get("Attributes")

    def delete_partition(self, part):
        """Delete every item of one partition (a student's collection). Returns how many."""
        from boto3.dynamodb.conditions import Key
        count, kwargs = 0, {"KeyConditionExpression": Key("s").eq(part), "ProjectionExpression": "#s, #i", "ExpressionAttributeNames": {"#s": "s", "#i": "id"}}
        while True:
            page = self.table.query(**kwargs)
            with self.table.batch_writer() as batch:
                for row in page.get("Items", []):
                    batch.delete_item(Key={"s": row["s"], "id": row["id"]})
                    count += 1
            if "LastEvaluatedKey" not in page:
                return count
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def query(self, part, after, limit):
        from boto3.dynamodb.conditions import Key
        args = {"KeyConditionExpression": Key("s").eq(part), "Limit": limit}
        if after is not None:
            args["ExclusiveStartKey"] = {"s": part, "id": after}
        return self.table.query(**args).get("Items", [])


_data = []


def data():
    if not _data:
        _data.append(DynamoData(os.environ["DATA_TABLE"]) if os.environ.get("DATA_TABLE") else MemoryData())
    return _data[0]


def authenticate(headers, now):
    """The student behind a request's key, or an ApiError. The key names the slug; the secret part must match, in constant time."""
    import hmac
    given = (headers.get("authorization") or "")
    given = given[7:].strip() if given.lower().startswith("bearer ") else ""
    match = KEY_RE.match(given)
    lock = db().get(f"slug#{match.group(1)}") if match else None
    item = db().get(f"tok#{lock['token']}") if lock else None
    stored = settings(item).get("key", "") if item else ""
    if not (match and item and stored and hmac.compare_digest(stored.encode(), given.encode())):
        raise ApiError(401, "bad_key", "The storage key is not valid.")
    status = portal.status_of(item, now)
    if status != "active":
        raise ApiError(403, "token_inactive", f"This site's token is {status}, so its storage is paused.")
    if not (available() and settings(item).get("enabled")):
        raise ApiError(403, "not_enabled", "Class storage is switched off for you. Ask your teacher.")
    return item


def charge(item, now):
    """One operation against the allowance (atomic: no overshoot)."""
    lim = limits(item)
    period_id, resets_at = backends.period_info(lim["ops_period"], now)
    ttl = resets_at + DAY if resets_at else None
    if db().counter_add(f"sops#{item['slug']}#{period_id}", 1, limit=lim["ops_limit"], ttl=ttl, now=now) is None:
        raise ApiError(429, "allowance_used", "The storage allowance is used up for now. It resets automatically, or ask your teacher.")


def _check(pattern, value, what):
    if not isinstance(value, str) or not pattern.match(value):
        raise ApiError(400, "bad_request", f"Invalid {what}.")
    return value


REMEMBER_SECONDS = 60  # a container trusts its own memory for this long, so a list deleted elsewhere is rebuilt within a minute
_known = {}  # (slug, collection) -> until when this container may skip the check


def remember_collection(slug, collection, now):
    """Keep a list of the collection names a student has used, so clearing can find every partition without scanning the data table.
    One small item per name, created once; at most MAX_COLLECTIONS names per student."""
    if _known.get((slug, collection), 0) > now:
        return
    if db().put({"pk": f"scoll#{slug}#{collection}", "updated_at": int(now)}, if_absent=True):
        if db().counter_add(f"scollcount#{slug}", 1, limit=MAX_COLLECTIONS, now=now) is None:
            db().delete(f"scoll#{slug}#{collection}")
            raise ApiError(413, "quota_exceeded", f"You can use up to {MAX_COLLECTIONS} different collection names.")
    _known[(slug, collection)] = int(now) + REMEMBER_SECONDS


def put_value(item, collection, key, value, now):
    _check(NAME_RE, collection, "collection (lowercase letters, digits, - and _, up to 40)")
    _check(ID_RE, key, "id (letters, digits, . - _, up to 128)")
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    size = len(encoded.encode("utf8"))
    if size > MAX_VALUE_BYTES:
        raise ApiError(413, "too_large", f"A value can be up to {MAX_VALUE_BYTES // 1024} KB (this one is {size // 1024 + 1} KB).")
    slug, part = item["slug"], f"{item['slug']}#{collection}"
    remember_collection(slug, collection, now)
    cost = size + len(key)
    old = data().get(part, key)
    delta = cost - int(old["n"]) if old else cost
    bytes_pk, items_pk = f"sbytes#{slug}", f"sitems#{slug}"
    took_bytes = took_item = False
    try:
        if delta > 0:
            if db().counter_add(bytes_pk, delta, limit=limits(item)["data_mb"] * MB, now=now) is None:
                raise ApiError(413, "quota_exceeded", f"Your storage is full ({limits(item)['data_mb']} MB). Delete something first.")
            took_bytes = True
        if not old:
            if db().counter_add(items_pk, 1, limit=MAX_ITEMS, now=now) is None:
                raise ApiError(413, "quota_exceeded", f"You reached the limit of {MAX_ITEMS} items. Delete some first.")
            took_item = True
        data().put(part, key, {"v": encoded, "n": cost, "u": int(now)})
    except Exception:
        if took_bytes:
            db().counter_add(bytes_pk, -delta, now=now)
        if took_item:
            db().counter_add(items_pk, -1, now=now)
        raise
    if delta < 0:
        db().counter_add(bytes_pk, delta, now=now)
    return {"id": key, "bytes": size}


def get_value(item, collection, key):
    _check(NAME_RE, collection, "collection")
    _check(ID_RE, key, "id")
    row = data().get(f"{item['slug']}#{collection}", key)
    if not row:
        raise ApiError(404, "not_found", "No such item.")
    return {"id": key, "value": json.loads(row["v"]), "updated_at": int(row["u"])}


def delete_value(item, collection, key, now):
    _check(NAME_RE, collection, "collection")
    _check(ID_RE, key, "id")
    old = data().delete(f"{item['slug']}#{collection}", key)
    if old:
        db().counter_add(f"sbytes#{item['slug']}", -int(old["n"]), now=now)
        db().counter_add(f"sitems#{item['slug']}", -1, now=now)
    return {"deleted": bool(old)}


def list_values(item, collection, after, limit):
    _check(NAME_RE, collection, "collection")
    if after is not None:
        _check(ID_RE, after, "after")
    limit = max(1, min(int(limit or LIST_PAGE_ITEMS), LIST_PAGE_ITEMS))
    rows = data().query(f"{item['slug']}#{collection}", after, limit + 1)
    out, total, more = [], 0, len(rows) > limit
    for row in rows[:limit]:
        total += int(row["n"])
        if out and total > LIST_PAGE_BYTES:
            more = True
            break
        out.append({"id": row["id"], "value": json.loads(row["v"]), "updated_at": int(row["u"])})
    return {"items": out, "next": out[-1]["id"] if (more and out) else None}


# ---------- files ----------
def _folder(item):
    return f"userdata/{item['slug']}/"


def list_files(item):
    s3, found, token = backends.client("s3"), [], None
    while True:
        args = {"Bucket": os.environ["BUCKET"], "Prefix": _folder(item), "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        found += [{"name": o["Key"][len(_folder(item)):], "size": int(o["Size"]), "updated_at": int(o["LastModified"].timestamp())
                   if hasattr(o.get("LastModified"), "timestamp") else int(o.get("LastModified") or 0)} for o in page.get("Contents", [])]
        token = page.get("NextContinuationToken")
        if not token:
            return found


def file_upload_url(item, name, size, content_type):
    _check(FILE_RE, name, "file name (letters, digits, . - _, up to 100, no folders)")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ApiError(400, "bad_request", "size must be the file size in bytes.")
    if size > MAX_FILE_BYTES:
        raise ApiError(413, "too_large", f"A file can be up to {MAX_FILE_BYTES // MB} MB.")
    content_type = (content_type or "application/octet-stream").lower()
    if not TYPE_RE.match(content_type):
        raise ApiError(400, "bad_request", "Invalid content type.")
    files = list_files(item)
    others = [f for f in files if f["name"] != name]
    if len(others) >= MAX_FILES:
        raise ApiError(413, "quota_exceeded", f"You can keep up to {MAX_FILES} files. Delete some first.")
    cap = limits(item)["files_mb"] * MB
    if sum(f["size"] for f in others) + size > cap:
        raise ApiError(413, "quota_exceeded", f"Your file space is full ({limits(item)['files_mb']} MB). Delete something first.")
    post = backends.client("s3").generate_presigned_post(
        Bucket=os.environ["BUCKET"], Key=_folder(item) + name, Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, size]], ExpiresIn=UPLOAD_SECONDS)
    return {"url": post["url"], "fields": post["fields"], "max_bytes": size, "expires_in": UPLOAD_SECONDS}


def file_download_url(item, name):
    _check(FILE_RE, name, "file name")
    try:
        backends.client("s3").head_object(Bucket=os.environ["BUCKET"], Key=_folder(item) + name)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            raise ApiError(404, "not_found", "No such file.")
        raise
    url = backends.client("s3").generate_presigned_url(
        "get_object", Params={"Bucket": os.environ["BUCKET"], "Key": _folder(item) + name}, ExpiresIn=DOWNLOAD_SECONDS)
    return {"url": url, "expires_in": DOWNLOAD_SECONDS}


def file_delete(item, name):
    _check(FILE_RE, name, "file name")
    backends.client("s3").delete_object(Bucket=os.environ["BUCKET"], Key=_folder(item) + name)
    return {"deleted": True}


# ====================== teacher tools: overview and clearing (sizes and counts only, never the content) ======================
def _folder_totals():
    """Count and size of every student's files in ONE listing of userdata/ -> {slug: {"count", "bytes"}}."""
    s3, totals, token = backends.client("s3"), {}, None
    while True:
        args = {"Bucket": os.environ["BUCKET"], "Prefix": "userdata/", "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for o in page.get("Contents", []):
            parts = o["Key"].split("/", 2)
            if len(parts) == 3 and parts[2]:
                entry = totals.setdefault(parts[1], {"count": 0, "bytes": 0})
                entry["count"] += 1
                entry["bytes"] += int(o["Size"])
        token = page.get("NextContinuationToken")
        if not token:
            return totals


def overview(now):
    """The teacher's picture of storage across the class: per student, plus totals. Students who never used it are left out."""
    if not available():
        raise PortalError(503, "The class storage connector is not set up on this server.")
    folders, students = _folder_totals(), []
    for item in sorted(db().list_prefix("tok#"), key=lambda i: i["slug"]):
        s = settings(item)
        used = usage(item, now)
        files = folders.get(item["slug"], {"count": 0, "bytes": 0})
        holds = used["data_bytes"] > 0 or files["bytes"] > 0
        if not (s.get("enabled") or s.get("key") or holds):
            continue
        status = portal.status_of(item, now)
        students.append({
            "id": item["id"], "slug": item["slug"], "student_name": item.get("student_name", ""), "status": status, "enabled": bool(s.get("enabled")),
            "limits": limits(item), "usage": used, "files": files, "holds_data": holds,
            # still holding data although the student can no longer use it: worth clearing
            "inactive": holds and (status in ("revoked", "offline") or not s.get("enabled")),
        })
    totals = {"students": len(students), "enabled": sum(1 for r in students if r["enabled"]), "inactive_with_data": sum(1 for r in students if r["inactive"]),
              "data_bytes": sum(r["usage"]["data_bytes"] for r in students), "items": sum(r["usage"]["items"] for r in students),
              "files": sum(r["files"]["count"] for r in students), "files_bytes": sum(r["files"]["bytes"] for r in students)}
    return {"students": students, "totals": totals}


def clear(item, scope, now):
    """Delete a student's stored data, files, or both. Counters go back to zero. The key, the switch and the caps stay as they are.
    Files live in a versioned bucket, so a deleted file can still be restored by the owner for 30 days (docs/END_TO_END.md, 4.5b)."""
    if not available():
        raise PortalError(503, "The class storage connector is not set up on this server.")
    if scope not in ("data", "files", "all"):
        raise PortalError(400, "scope must be data, files or all.")
    slug, done = item["slug"], {"items": 0, "files": 0}
    if scope in ("data", "all"):
        for entry in db().list_prefix(f"scoll#{slug}#"):
            done["items"] += data().delete_partition(f"{slug}#{entry['pk'].split('#', 2)[2]}")
        db().put({"pk": f"sbytes#{slug}", "count": 0, "updated_at": int(now)})
        db().put({"pk": f"sitems#{slug}", "count": 0, "updated_at": int(now)})
    if scope in ("files", "all"):
        s3 = backends.client("s3")
        for f in list_files(item):
            s3.delete_object(Bucket=os.environ["BUCKET"], Key=_folder(item) + f["name"])
            done["files"] += 1
    return done
