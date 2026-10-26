"""class_storage: save data and files from your backend with the class storage (Python 3.12, standard library only).

    import class_storage as storage

    storage.put("scores", "alice", {"points": 3})           # save a record: any JSON up to 64 KB
    storage.get("scores", "alice", default=None)            # read it back (default when it does not exist)
    storage.items("scores")                                  # [{"id": "alice", "value": {...}}, ...] (first 100; pass after=<last id> for more)
    storage.delete("scores", "alice")

    storage.files.put("photo.png", image_bytes, "image/png")  # save a file (up to 5 MB) from your function
    storage.files.upload_url("photo.png", 20000, "image/png") # or let the BROWSER send it: returns {"url", "fields"} for a form POST
    storage.files.list(); storage.files.url("photo.png"); storage.files.delete("photo.png")

On the platform it talks to the class storage with the key the platform gives your function (CLASS_STORAGE_URL, CLASS_STORAGE_KEY): your
teacher switches it on and sets the size limits. On your computer (npm run dev) it keeps everything in the folder .class-storage/ (git-ignored),
so you can build and test without a connection. Every problem raises StorageError with a plain message, a .kind and a .status:

    not_enabled    the teacher has not switched storage on for you          allowance_used  the operations allowance is used up for now
    quota_exceeded the database or file space is full                       too_large       a value or file is over its size limit
    token_inactive your token expired or was revoked                        bad_request     a name, id or size is not allowed
    unavailable    the storage service did not answer (try again)           bad_key         the platform key was refused (tell your teacher)

The key and your data are never logged by this helper.
"""
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MAX_VALUE_BYTES = 64 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 8
MESSAGES = {
    "not_enabled": "Class storage is not switched on for you. Ask your teacher.",
    "allowance_used": "The storage allowance is used up for now. It resets by itself, or ask your teacher.",
    "quota_exceeded": "Your storage is full. Delete something first, or ask your teacher for more space.",
    "unavailable": "The storage service did not answer. Try again in a moment.",
}


class StorageError(Exception):
    def __init__(self, kind, message=None, status=0):
        super().__init__(message or MESSAGES.get(kind) or kind)
        self.kind, self.status = kind, status


def _remote():
    url, key = os.environ.get("CLASS_STORAGE_URL", ""), os.environ.get("CLASS_STORAGE_KEY", "")
    if url and key:
        return url.rstrip("/"), key
    if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):  # on the platform but not switched on: do not pretend with local files
        raise StorageError("not_enabled")
    return None  # on your computer: the local folder


def _request(method, path, body=None, query=None):
    base, key = _remote()
    url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "class-storage/1"}
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            try:
                info = json.load(e)
            except ValueError:
                info = {}
            if e.code >= 500 and attempt == 1:
                continue
            kind = info.get("error") or ("unavailable" if e.code >= 500 else "bad_request")
            raise StorageError(kind, info.get("message"), e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise StorageError("unavailable") from None
    raise StorageError("unavailable")


def _check(pattern, value, what):
    if not isinstance(value, str) or not pattern.match(value):
        raise StorageError("bad_request", f"Invalid {what}.")
    return value


# ---------- the local folder (npm run dev) ----------
def _folder():
    path = os.path.abspath(os.environ.get("CLASS_STORAGE_DIR") or ".class-storage")
    os.makedirs(path, exist_ok=True)
    return path


def _record_path(collection, key):
    return os.path.join(_folder(), "data", _check(NAME_RE, collection, "collection (lowercase letters, digits, - and _)"), _check(ID_RE, key, "id") + ".json")


# ---------- data ----------
def put(collection, key, value):
    _check(NAME_RE, collection, "collection (lowercase letters, digits, - and _)")
    _check(ID_RE, key, "id (letters, digits, . - _)")
    if _remote():
        return _request("PUT", f"/v1/data/{collection}/{key}", {"value": value})
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode("utf8")) > MAX_VALUE_BYTES:
        raise StorageError("too_large", f"A value can be up to {MAX_VALUE_BYTES // 1024} KB.")
    path = _record_path(collection, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        f.write(encoded)
    return {"id": key, "bytes": len(encoded.encode("utf8"))}


def get(collection, key, default=None):
    _check(NAME_RE, collection, "collection")
    _check(ID_RE, key, "id")
    if _remote():
        try:
            return _request("GET", f"/v1/data/{collection}/{key}")["value"]
        except StorageError as e:
            if e.kind == "not_found":
                return default
            raise
    path = _record_path(collection, key)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf8") as f:
        return json.load(f)


def items(collection, limit=100, after=None):
    """Up to `limit` records (at most 100), ordered by id. With more records, pass after=<id of the last one> for the next page."""
    _check(NAME_RE, collection, "collection")
    limit = max(1, min(int(limit), 100))
    if _remote():
        query = {"limit": limit, **({"after": after} if after else {})}
        return _request("GET", f"/v1/data/{collection}", query=query)["items"]
    folder = os.path.join(_folder(), "data", collection)
    ids = sorted(n[:-5] for n in os.listdir(folder)) if os.path.isdir(folder) else []
    return [{"id": i, "value": get(collection, i)} for i in ids if after is None or i > after][:limit]


def delete(collection, key):
    _check(NAME_RE, collection, "collection")
    _check(ID_RE, key, "id")
    if _remote():
        return _request("DELETE", f"/v1/data/{collection}/{key}")["deleted"]
    path = _record_path(collection, key)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ---------- files ----------
class _Files:
    def upload_url(self, name, size, content_type="application/octet-stream"):
        """A one-time upload form target. The browser (or your function) POSTs a multipart form to url with every field in `fields`, file last."""
        _check(FILE_RE, name, "file name (letters, digits, . - _, no folders)")
        if _remote():
            return _request("POST", "/v1/files/upload-url", {"name": name, "size": size, "content_type": content_type})
        return {"url": "", "fields": {}, "max_bytes": size, "local": True}  # on your computer there is nothing to upload to: use files.put

    def put(self, name, data, content_type=None):
        """Save bytes as a file (up to 5 MB). Counts against your file space."""
        _check(FILE_RE, name, "file name (letters, digits, . - _, no folders)")
        data = data.encode() if isinstance(data, str) else bytes(data)
        if len(data) > MAX_FILE_BYTES:
            raise StorageError("too_large", f"A file can be up to {MAX_FILE_BYTES // 1048576} MB.")
        content_type = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not _remote():
            path = os.path.join(_folder(), "files", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            return {"name": name, "size": len(data)}
        target = self.upload_url(name, len(data), content_type)
        boundary = uuid.uuid4().hex
        parts = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in target["fields"].items())
        parts += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
        body = parts + data + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(target["url"], data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS * 2).close()
        except (urllib.error.URLError, TimeoutError, OSError):
            raise StorageError("unavailable", "The file could not be uploaded. Try again.") from None
        return {"name": name, "size": len(data)}

    def list(self):
        if _remote():
            return _request("GET", "/v1/files")["files"]
        folder = os.path.join(_folder(), "files")
        names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        return [{"name": n, "size": os.path.getsize(os.path.join(folder, n))} for n in names]

    def url(self, name):
        """A link that downloads the file for 5 minutes (works in <img src> and for fetch). On your computer: the local path."""
        _check(FILE_RE, name, "file name")
        if _remote():
            return _request("GET", "/v1/files/" + urllib.parse.quote(name))["url"]
        path = os.path.join(_folder(), "files", name)
        if not os.path.exists(path):
            raise StorageError("not_found", "No such file.", 404)
        return "file:///" + path.replace(os.sep, "/").lstrip("/")

    def delete(self, name):
        _check(FILE_RE, name, "file name")
        if _remote():
            return _request("DELETE", "/v1/files/" + urllib.parse.quote(name))["deleted"]
        path = os.path.join(_folder(), "files", name)
        if os.path.exists(path):
            os.remove(path)
        return True


files = _Files()
