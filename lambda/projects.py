"""Project sync: a project's source files, shared through the platform's own bucket instead of GitHub (docs/PROJECT_SYNC.md).

  - A student has a PERSONAL project (the same code on two computers) and may be a member of TEAM projects the teacher makes.
  - Files are stored by content (projects/<p|t>/<name>/blobs/<sha256>), so a file is uploaded once and nothing is ever overwritten in S3.
  - One item in the tokens table is the project's manifest (path -> sha256, size, author, time) and a revision number. A commit replaces the manifest only if
    nobody committed in between (a conditional write), so two people never silently overwrite each other: a path someone else changed since the sender
    last synced is reported as a conflict and the commit is refused (409). The sender pulls, decides, and pushes again.
  - Every commit also writes a small history item (who, when, which paths), so the teacher can see what happened and put a file back.

The student's browser or computer talks to this through the portal API with the student's login (never a key); file bytes go straight to and from S3 with
short-lived upload forms (checked by S3 for size and SHA-256) and download links. This module never reads a file's bytes.
"""
import base64
import re
import time

import backends
import portal
from portal import PortalError, db
import platform_settings

MB = 1024 * 1024
MAX_FILES = platform_settings.value("projects.maxFiles")
MAX_FILE_BYTES = platform_settings.mb("projects.maxFileMB")
MAX_LIVE_BYTES = platform_settings.mb("projects.maxLiveMB")          # what the project holds now
MAX_STORED_BYTES = platform_settings.mb("projects.maxStoredMB")       # everything ever uploaded to it (old versions stay until the teacher compacts it)
MAX_CHANGES = platform_settings.value("projects.maxChanges")                 # paths in one push
MAX_MEMBERS = platform_settings.value("projects.maxMembers")
HISTORY_DAYS = platform_settings.value("projects.historyDays")
UPLOAD_SECONDS = platform_settings.value("projects.uploadSeconds")
DOWNLOAD_SECONDS = platform_settings.value("projects.downloadSeconds")
COMMIT_TRIES = platform_settings.value("projects.commitTries")

TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCKED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".class-platform", ".idea", ".vscode-test", ".kit-backup", ".class-storage", ".class-knowledge"}
BLOCKED_NAME = re.compile(r"^(\.env(\.(?!example$|sample$|template$).*)?|\.class-project\.json|\.class-token|.*\.(pem|key|p12|pfx))$", re.I)


def available():
    return True  # part of the portal: no extra AWS resources, nothing runs while idle


# ---------- names ----------
def clean_path(value, deleting=False):
    """A project path: relative, forward slashes, no '..', and never a secret or a dependency folder. Raises PortalError(400). Deleting a path that is now on the
    never-synced list is allowed (older versions of the kit sent such files, and they must be removable)."""
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or value != value.strip() or "\\" in value or value.startswith("/") or re.search(r"[\x00-\x1f\x7f]", value):
        raise PortalError(400, f"Not a valid project path: {str(value)[:60]!r}.")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", "..") or len(part) > 100:
            raise PortalError(400, f"Not a valid project path: {value[:60]!r}.")
    if not deleting and (any(p in BLOCKED_DIRS for p in parts[:-1]) or parts[-1] in BLOCKED_DIRS or BLOCKED_NAME.match(parts[-1])):
        raise PortalError(400, f"{value} is never synced (secrets, dependencies and editor folders stay on your computer).")
    return value


def team_name(value):
    if not isinstance(value, str) or not TEAM_RE.match(value) or value in ("personal", "me", "new"):
        raise PortalError(400, "A project id uses lowercase letters, digits and - (2 to 40 characters), and is not 'personal', 'me' or 'new'.")
    return value


def key_of(kind, name):
    return f"proj#{kind}#{name}"


def blob_key(kind, name, sha):
    return f"projects/{kind}/{name}/blobs/{sha}"


def public_id(item):
    return "personal" if item["kind"] == "p" else item["name"]


def _s3():
    return backends.client("s3")


def _bucket():
    import os
    return os.environ["BUCKET"]


# ---------- the manifest ----------
def _new(kind, name, now, title="", members=()):
    return {"pk": key_of(kind, name), "kind": kind, "name": name, "title": title, "members": list(members), "rev": 0, "files": {}, "bytes": 0,
            "stored": 0, "frozen": False, "created_at": now, "updated_at": now}


def load(kind, name):
    return db().get(key_of(kind, name))


def for_student(item, ref, now, create=False):
    """The project a student means by `ref` ('personal' or a team id), if they may use it. A personal project is made on first use."""
    if ref in (None, "", "personal"):
        found = load("p", item["slug"])
        if found or not create:
            return found or _new("p", item["slug"], now, "My project", [item["slug"]])
        fresh = _new("p", item["slug"], now, "My project", [item["slug"]])
        db().put(fresh, if_absent=True)
        return load("p", item["slug"])
    found = load("t", team_name(ref)) if isinstance(ref, str) and TEAM_RE.match(ref) else None
    if not found or item["slug"] not in (found.get("members") or []):
        raise PortalError(404, "No such project, or you are not a member of it.")
    return found


def view(project):
    return {"id": public_id(project), "kind": "personal" if project["kind"] == "p" else "team", "title": project.get("title") or public_id(project),
            "members": project.get("members") or [], "rev": project["rev"], "files": len(project.get("files") or {}), "bytes": project.get("bytes", 0),
            "stored": project.get("stored", 0), "frozen": bool(project.get("frozen")), "updated_at": project.get("updated_at", 0),
            "limits": {"files": MAX_FILES, "file_mb": MAX_FILE_BYTES // MB, "project_mb": MAX_LIVE_BYTES // MB}}


# ---------- reading a push ----------
def _changes(body):
    raw = body.get("changes")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_CHANGES:
        raise PortalError(400, f"changes must list 1 to {MAX_CHANGES} paths.")
    out, seen = [], set()
    for change in raw:
        if not isinstance(change, dict):
            raise PortalError(400, "Each change is an object.")
        path = clean_path(change.get("path"), deleting=change.get("op") == "delete")
        if path in seen:
            raise PortalError(400, f"{path} appears twice.")
        seen.add(path)
        op = change.get("op")
        base = change.get("base")
        if op not in ("put", "delete") or not (base is None or (isinstance(base, str) and SHA_RE.match(base))):
            raise PortalError(400, f"{path}: op must be put or delete and base a SHA-256 or null.")
        entry = {"path": path, "op": op, "base": base}
        if op == "put":
            sha, size = change.get("sha256"), change.get("size")
            if not (isinstance(sha, str) and SHA_RE.match(sha)) or isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise PortalError(400, f"{path}: a put needs sha256 and size.")
            if size > MAX_FILE_BYTES:
                raise PortalError(413, f"{path} is {size // 1024} KB; a file can be up to {MAX_FILE_BYTES // MB} MB.")
            entry.update(sha=sha, size=size)
        out.append(entry)
    return out


def _conflicts(project, changes):
    """Paths somebody else changed since the sender last synced them. A change that already equals the stored state is not a conflict (nothing to do)."""
    files, found = project.get("files") or {}, []
    for c in changes:
        current = files.get(c["path"])
        current_sha = current["sha"] if current else None
        if c["base"] == current_sha:
            continue
        if (c["op"] == "put" and c.get("sha") == current_sha) or (c["op"] == "delete" and current is None):
            continue
        found.append({"path": c["path"], "theirs": ({"sha256": current["sha"], "by": current.get("by", ""), "ts": current.get("ts", 0)} if current else None)})
    return found


def _effect(project, changes):
    """The files after the changes, and the bytes that are new in storage. Raises 413 when a limit is passed."""
    files = dict(project.get("files") or {})
    added = 0
    for c in changes:
        current = files.get(c["path"])
        if c["op"] == "delete":
            files.pop(c["path"], None)
        elif not current or current["sha"] != c["sha"]:
            added += c["size"]
            files[c["path"]] = {"sha": c["sha"], "size": c["size"]}
    live = sum(f["size"] for f in files.values())
    if len(files) > MAX_FILES:
        raise PortalError(413, f"A project can hold {MAX_FILES} files. Leave out build output and dependencies.")
    if live > MAX_LIVE_BYTES:
        raise PortalError(413, f"A project can hold {MAX_LIVE_BYTES // MB} MB of files; this would make it {live // MB} MB. Leave out videos, datasets and build output.")
    if project.get("stored", 0) + added > MAX_STORED_BYTES:
        raise PortalError(413, f"This project has stored {project.get('stored', 0) // MB} MB of file versions in total (limit {MAX_STORED_BYTES // MB} MB). Ask your teacher to clean it up.")
    return files, added


def _usable(item, now, write):
    status = portal.status_of(item, now)
    if status != "active" and (write or status not in ("expired",)):
        raise PortalError(403, f"Your token is {status}, so project sync is paused. Ask your teacher or request regeneration on the portal.")


# ---------- the student's routes ----------
STUDENT = "/api/student/projects"


def student_route(item, method, path, body, now):
    """Returns (status, body), or None when the path is not one of these routes."""
    if not (path == STUDENT or path.startswith(STUDENT + "/")):
        return None
    rest = path[len(STUDENT):]
    if (method, rest) == ("GET", ""):
        _usable(item, now, False)
        mine = [view(for_student(item, "personal", now))]
        mine += [view(p) for p in sorted(db().list_prefix("proj#t#"), key=lambda p: p["name"]) if item["slug"] in (p.get("members") or [])]
        return 200, {"projects": mine}
    match = re.fullmatch(r"/([^/]+)(/.*)?", rest)
    if not match:
        return None
    ref, sub = match.group(1), match.group(2) or ""
    if (method, sub) == ("GET", ""):
        _usable(item, now, False)
        project = for_student(item, ref, now)
        return 200, {"project": view(project), "files": {k: {"sha256": v["sha"], "size": v["size"], "by": v.get("by", ""), "ts": v.get("ts", 0)} for k, v in (project.get("files") or {}).items()}}
    if (method, sub) == ("POST", "/download"):
        _usable(item, now, False)
        return 200, download(for_student(item, ref, now), body)
    if (method, sub) == ("POST", "/prepare"):
        _usable(item, now, True)
        return prepare(for_student(item, ref, now, create=True), body)
    if (method, sub) == ("POST", "/commit"):
        _usable(item, now, True)
        return commit(for_student(item, ref, now, create=True), body, item["slug"], now)
    return None


def download(project, body):
    paths = body.get("paths")
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_CHANGES:
        raise PortalError(400, f"paths must list 1 to {MAX_CHANGES} files.")
    files, urls = project.get("files") or {}, {}
    for path in paths:
        entry = files.get(clean_path(path))
        if not entry:
            raise PortalError(404, f"{path} is not in this project (any more).")
        urls[path] = {"url": _s3().generate_presigned_url("get_object", Params={"Bucket": _bucket(), "Key": blob_key(project["kind"], project["name"], entry["sha"])},
                                                           ExpiresIn=DOWNLOAD_SECONDS), "sha256": entry["sha"], "size": entry["size"]}
    return {"files": urls, "expires_in": DOWNLOAD_SECONDS}


def _exists(project, sha, size):
    try:
        head = _s3().head_object(Bucket=_bucket(), Key=blob_key(project["kind"], project["name"], sha))
    except Exception as e:  # a missing object is the normal "not uploaded yet" answer
        code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return int(head["ContentLength"]) == size


def prepare(project, body):
    """Check the push against the project as it is now and hand out upload forms for the files S3 does not have yet."""
    if project.get("frozen"):
        raise PortalError(403, "This project is frozen by your teacher; nothing can be pushed.")
    changes = _changes(body)
    conflicts = _conflicts(project, changes)
    if conflicts:
        return 409, {"error": "conflict", "message": "Someone changed these files since you last synced. Pull first.", "conflicts": conflicts, "rev": project["rev"]}
    _effect(project, changes)
    uploads = {}
    for c in changes:
        if c["op"] != "put" or c["sha"] in uploads or _exists(project, c["sha"], c["size"]):
            continue
        digest = base64.b64encode(bytes.fromhex(c["sha"])).decode()
        fields = {"x-amz-checksum-algorithm": "SHA256", "x-amz-checksum-sha256": digest}
        conditions = [{"x-amz-checksum-algorithm": "SHA256"}, {"x-amz-checksum-sha256": digest}, ["content-length-range", 0, c["size"]]]
        form = _s3().generate_presigned_post(Bucket=_bucket(), Key=blob_key(project["kind"], project["name"], c["sha"]), Fields=fields, Conditions=conditions, ExpiresIn=UPLOAD_SECONDS)
        uploads[c["sha"]] = {"url": form["url"], "fields": form["fields"]}
    return 200, {"rev": project["rev"], "uploads": uploads, "expires_in": UPLOAD_SECONDS}


def commit(project, body, author, now, label=None):
    """Apply a push if nobody changed the same paths meanwhile. Returns (status, body)."""
    changes = _changes(body)
    for attempt in range(COMMIT_TRIES):
        if project.get("frozen") and author != "teacher":
            raise PortalError(403, "This project is frozen by your teacher; nothing can be pushed.")
        conflicts = _conflicts(project, changes)
        if conflicts:
            return 409, {"error": "conflict", "message": "Someone changed these files since you last synced. Pull first.", "conflicts": conflicts, "rev": project["rev"]}
        files, added = _effect(project, changes)
        for c in changes:
            if c["op"] == "put" and (project.get("files") or {}).get(c["path"], {}).get("sha") != c["sha"] and not _exists(project, c["sha"], c["size"]):
                raise PortalError(409, f"{c['path']} was not uploaded completely. Push again.")
        stamped = dict(project.get("files") or {})
        rev = project["rev"] + 1
        for c in changes:
            if c["op"] == "delete":
                stamped.pop(c["path"], None)
            elif stamped.get(c["path"], {}).get("sha") != c["sha"]:
                stamped[c["path"]] = {"sha": c["sha"], "size": c["size"], "by": author, "ts": now, "rev": rev}
        updated = {**project, "files": stamped, "rev": rev, "bytes": sum(f["size"] for f in stamped.values()), "stored": project.get("stored", 0) + added,
                   "updated_at": now}
        if db().replace_if(updated, "rev", project["rev"]):
            _history(project, rev, author, now, changes, label)
            return 200, {"rev": rev, "files": len(stamped), "project": view(updated)}
        project = load(project["kind"], project["name"]) or project  # somebody committed first: look again, maybe their change does not touch ours
    raise PortalError(409, "The project is busy (several people pushed at the same moment). Try again.")


def _history(project, rev, author, now, changes, label):
    db().put({"pk": f"projh#{project['kind']}#{project['name']}#{rev:08d}", "rev": rev, "by": author, "ts": now, "label": label or "",
              "changes": [{"path": c["path"], "op": c["op"], "sha": c.get("sha"), "size": c.get("size")} for c in changes], "ttl": now + HISTORY_DAYS * portal.DAY})


def history(project, limit=30):
    prefix = f"projh#{project['kind']}#{project['name']}#"
    rows = sorted(db().list_prefix(prefix), key=lambda r: -r["rev"])
    return [{k: v for k, v in r.items() if k != "pk"} for r in rows[:limit]]


# ---------- the teacher's routes ----------
ADMIN = "/api/admin/projects"


def _members(value):
    if not isinstance(value, list) or len(value) > MAX_MEMBERS:
        raise PortalError(400, f"members is a list of up to {MAX_MEMBERS} student sites (their slug).")
    slugs = []
    for slug in value:
        if not isinstance(slug, str) or not portal.SLUG_RE.match(slug) or not db().get(f"slug#{slug}"):
            raise PortalError(400, f"No student site called {str(slug)[:40]!r}.")
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def _admin_project(kind, name):
    if kind not in ("p", "t"):
        raise PortalError(404, "Unknown project.")
    project = load(kind, name)
    if not project:
        raise PortalError(404, "Unknown project.")
    return project


def admin_route(method, path, body, now):
    if not (path == ADMIN or path.startswith(ADMIN + "/")):
        return None
    rest = path[len(ADMIN):]
    if (method, rest) == ("GET", ""):
        rows = [view(p) | {"ref": f"{p['kind']}/{p['name']}", "name": p["name"]} for p in db().list_prefix("proj#")]
        return 200, {"projects": sorted(rows, key=lambda r: (r["kind"], r["name"]))}
    if (method, rest) == ("POST", ""):
        name = team_name(body.get("id"))
        project = _new("t", name, now, str(body.get("title") or name)[:80], _members(body.get("members") or []))
        if not db().put(project, if_absent=True):
            raise PortalError(409, "A project with this id exists already.")
        return 201, {"project": view(project) | {"ref": f"t/{name}", "name": name}}
    match = re.fullmatch(r"/([pt])/([a-z0-9-]{1,40})(/.*)?", rest)
    if not match:
        return None
    kind, name, sub = match.group(1), match.group(2), match.group(3) or ""
    project = _admin_project(kind, name)
    if (method, sub) == ("GET", ""):
        files = project.get("files") or {}
        return 200, {"project": view(project) | {"ref": f"{kind}/{name}", "name": name},
                     "files": [{"path": p, "size": f["size"], "by": f.get("by", ""), "ts": f.get("ts", 0), "rev": f.get("rev", 0)} for p, f in sorted(files.items())],
                     "history": history(project)}
    if (method, sub) == ("POST", ""):
        fields = {}
        if "title" in body and kind == "t":
            fields["title"] = str(body["title"])[:80]
        if "members" in body and kind == "t":
            fields["members"] = _members(body["members"])
        if "frozen" in body:
            fields["frozen"] = bool(body["frozen"])
        updated = db().update(project["pk"], fields) if fields else project
        return 200, {"project": view(updated) | {"ref": f"{kind}/{name}", "name": name}}
    if (method, sub) == ("DELETE", ""):
        remove_all(project)
        return 200, {"deleted": True}
    if (method, sub) == ("POST", "/file"):
        entry = (project.get("files") or {}).get(clean_path(body.get("path")))
        if not entry:
            raise PortalError(404, "No such file.")
        return 200, download(project, {"paths": [body["path"]]})
    if (method, sub) == ("POST", "/restore"):
        return restore(project, body, now)
    if (method, sub) == ("POST", "/compact"):
        return 200, compact(project)
    return None


def restore(project, body, now):
    """Put one file back as it was at an earlier revision (a new revision; nothing is lost)."""
    path, rev = clean_path(body.get("path")), body.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        raise PortalError(400, "rev is a revision number.")
    row = db().get(f"projh#{project['kind']}#{project['name']}#{rev:08d}")
    change = next((c for c in (row or {}).get("changes", []) if c["path"] == path), None)
    if not change:
        raise PortalError(404, "That revision did not change this file (or it is older than the history is kept).")
    current = (project.get("files") or {}).get(path)
    base = current["sha"] if current else None
    if change["op"] == "delete":
        entry = {"path": path, "op": "delete", "base": base}
    else:
        entry = {"path": path, "op": "put", "base": base, "sha256": change["sha"], "size": change["size"]}
    return commit(project, {"changes": [entry]}, "teacher", now, label=f"restored {path} from revision {rev}")


def _delete_prefix(prefix):
    s3, token = _s3(), None
    while True:
        args = {"Bucket": _bucket(), "Prefix": prefix, "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        keys = [o["Key"] for o in page.get("Contents", [])]
        for key in keys:
            s3.delete_object(Bucket=_bucket(), Key=key)
        token = page.get("NextContinuationToken")
        if not token:
            return


def compact(project):
    """Delete the file versions the project no longer shows (older revisions can then not be restored). Returns what was freed."""
    keep = {f["sha"] for f in (project.get("files") or {}).values()}
    prefix = f"projects/{project['kind']}/{project['name']}/blobs/"
    s3, token, freed, removed = _s3(), None, 0, 0
    while True:
        args = {"Bucket": _bucket(), "Prefix": prefix, "MaxKeys": 1000}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        for o in page.get("Contents", []):
            if o["Key"][len(prefix):] not in keep:
                s3.delete_object(Bucket=_bucket(), Key=o["Key"])
                freed += int(o.get("Size", 0))
                removed += 1
        token = page.get("NextContinuationToken")
        if not token:
            break
    live = sum(f["size"] for f in (project.get("files") or {}).values())
    db().update(project["pk"], {"stored": live})
    return {"removed": removed, "freed_bytes": freed}


def remove_all(project):
    _delete_prefix(f"projects/{project['kind']}/{project['name']}/")
    for row in db().list_prefix(f"projh#{project['kind']}#{project['name']}#"):
        db().delete(row["pk"])
    db().delete(project["pk"])

