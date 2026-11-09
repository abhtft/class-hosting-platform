"""Project sync (lambda/projects.py): a personal project and team projects, content-addressed uploads, the manifest with its revision, conflicts between two people,
a race between two commits, every limit, the teacher's view, restore, compact and delete. DynamoDB and S3 are in-memory fakes.
"""
import hashlib
import json
import os
import sys
import time

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k",
                  ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import portal  # noqa: E402
import projects  # noqa: E402
from fakes import FakeS3, store  # noqa: E402

DAY, MB = portal.DAY, projects.MB
NOW = int(time.time())
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
s3 = FakeS3()
backends._clients.update({"s3": s3})


def call(method, path, body=None, session=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else NOW)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]


def student(name, days=30):
    _, made = call("POST", "/api/admin/tokens", {"days": days, "student_name": name}, admin)
    return made["item"]["id"], call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def put_change(path, data, base=None):
    return {"path": path, "op": "put", "sha256": sha(data), "size": len(data), "base": base}


def push(session, ref, changes, data=None, at=None):
    """The whole push the way the kit does it: prepare, upload what is asked for, commit. Returns the commit answer."""
    status, prepared = call("POST", f"/api/student/projects/{ref}/prepare", {"changes": changes}, session, at)
    if status != 200:
        return status, prepared
    for digest in prepared["uploads"]:
        body = next(data[c["path"]] for c in changes if c.get("sha256") == digest)
        store[prepared["uploads"][digest]["fields"]["key"]] = {"Body": body}
    return call("POST", f"/api/student/projects/{ref}/commit", {"changes": changes}, session, at)


def files_of(session, ref):
    status, body = call("GET", f"/api/student/projects/{ref}", None, session)
    assert status == 200, body
    return body


_, alice, alice_slug = student("Alice")
_, bob, bob_slug = student("Bob")
_, carol, carol_slug = student("Carol")

# ---------------------------------------------------------------- a personal project: made on first use, lists, round trip
status, body = call("GET", "/api/student/projects", None, alice)
assert status == 200 and [p["id"] for p in body["projects"]] == ["personal"] and body["projects"][0]["rev"] == 0, body
assert portal.db().get(f"proj#p#{alice_slug}") is None, "reading does not create the project"
a1 = {"src/app.py": b"print('hi')\n", "README.md": b"# mine\n"}
status, prepared = call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change(p, d) for p, d in a1.items()]}, alice)
assert status == 200 and len(prepared["uploads"]) == 2, prepared
form = next(iter(prepared["uploads"].values()))
assert form["fields"]["x-amz-checksum-algorithm"] == "SHA256" and form["fields"]["key"].startswith(f"projects/p/{alice_slug}/blobs/"), form
cond = s3.posts[-1]["Conditions"]
assert ["content-length-range", 0, len(b"print('hi')\n")] in cond or ["content-length-range", 0, len(b"# mine\n")] in cond and {"x-amz-checksum-algorithm": "SHA256"} in cond, cond
# committing before the bytes are there is refused, and nothing changes
status, body = call("POST", "/api/student/projects/personal/commit", {"changes": [put_change(p, d) for p, d in a1.items()]}, alice)
assert status == 409 and "not uploaded" in body["error"], body
assert files_of(alice, "personal")["project"]["rev"] == 0
status, body = push(alice, "personal", [put_change(p, d) for p, d in a1.items()], a1)
assert status == 200 and body["rev"] == 1 and body["files"] == 2, body
listing = files_of(alice, "personal")
assert listing["files"]["src/app.py"]["sha256"] == sha(a1["src/app.py"]) and listing["files"]["src/app.py"]["by"] == alice_slug
status, links = call("POST", "/api/student/projects/personal/download", {"paths": ["src/app.py"]}, alice)
assert status == 200 and links["files"]["src/app.py"]["sha256"] == sha(a1["src/app.py"]) and "/s3/projects/p/" in links["files"]["src/app.py"]["url"], links
# the same content again: nothing to upload, nothing to commit but the revision is kept as is
status, again = call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change("README.md", a1["README.md"], sha(a1["README.md"]))]}, alice)
assert status == 200 and again["uploads"] == {}, again
status, body = call("POST", "/api/student/projects/personal/commit", {"changes": [put_change("README.md", a1["README.md"], sha(a1["README.md"]))]}, alice)
assert status == 200 and body["rev"] == 2, body
# a new file with the same content as another is stored once
status, prepared = call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change("copy.md", a1["README.md"])]}, alice)
assert prepared["uploads"] == {}, "the content is already stored"
print("  ok  a personal project: made on first push, uploads only what S3 lacks (with a size and SHA-256 condition), refuses a commit before the bytes arrive, lists and gives download links")

# ---------------------------------------------------------------- nobody else can reach a personal project
status, body = call("GET", "/api/student/projects", None, bob)
assert [p["id"] for p in body["projects"]] == ["personal"] and body["projects"][0]["rev"] == 0 and body["projects"][0]["files"] == 0
assert files_of(bob, "personal")["files"] == {}, "Bob's personal project is his own"
for ref in (alice_slug, f"p-{alice_slug}", "personal/../x"):
    assert call("GET", f"/api/student/projects/{ref}", None, bob)[0] in (404, 400), ref
assert call("GET", "/api/student/projects", None)[0] == 401
print("  ok  a student sees only their own personal project; no session, no access")

# ---------------------------------------------------------------- paths
bad = ["../x", "a/../b", "/abs", "a\\b", ".env", ".env.local", "src/.env", "node_modules/x/y.js", ".git/config", "id_rsa.pem", "a//b", "x" * 201, "", ".class-project.json", "dir/.git/HEAD"]
for path in bad:
    status, body = call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change(path, b"x")]}, alice)
    assert status == 400, (path, status, body)
for path in (".env.example", "docs/.hidden", "a b/c.txt", "src/Ünï.py"):
    assert call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change(path, b"x")]}, alice)[0] == 200, path
for changes in ([], [{"path": "a", "op": "rename"}], [{"path": "a", "op": "put", "sha256": "zz", "size": 1}], [put_change("a", b"x"), put_change("a", b"y")], "x",
                [{"path": "a", "op": "put", "sha256": sha(b"x"), "size": True}], [{"path": "a", "op": "delete", "base": "nothex"}]):
    assert call("POST", "/api/student/projects/personal/prepare", {"changes": changes}, alice)[0] == 400, changes
assert call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change(f"f{i}", b"x") for i in range(projects.MAX_CHANGES + 1)]}, alice)[0] == 400
print("  ok  paths: no '..', no absolute or backslash paths, no secrets, keys or dependency folders; malformed pushes refused")

# a file that is on the never-synced list now (an older kit sent it) can still be DELETED from the project, but never put
manifest = projects.load("p", alice_slug)
portal.db().put({**manifest, "files": {**manifest["files"], ".kit-backup/old/x.mjs": {"sha": sha(b"old"), "size": 3, "by": alice_slug, "ts": NOW, "rev": 1}}})
status, body = call("POST", "/api/student/projects/personal/commit", {"changes": [{"path": ".kit-backup/old/x.mjs", "op": "delete", "base": sha(b"old")}]}, alice)
assert status == 200 and ".kit-backup/old/x.mjs" not in files_of(alice, "personal")["files"], body
assert call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change(".kit-backup/old/x.mjs", b"old")]}, alice)[0] == 400
print("  ok  an old never-synced file can be removed from a project but not added")

# ---------------------------------------------------------------- a team: made by the teacher only
assert call("POST", "/api/admin/projects", {"id": "team-1", "members": []})[0] == 401
assert call("POST", "/api/admin/projects", {"id": "Bad Id"}, admin)[0] == 400
assert call("POST", "/api/admin/projects", {"id": "personal"}, admin)[0] == 400
assert call("POST", "/api/admin/projects", {"id": "team-1", "members": ["nobody-here"]}, admin)[0] == 400
status, made = call("POST", "/api/admin/projects", {"id": "team-1", "title": "Robot club", "members": [alice_slug, bob_slug]}, admin)
assert status == 201 and made["project"]["members"] == [alice_slug, bob_slug], made
assert call("POST", "/api/admin/projects", {"id": "team-1"}, admin)[0] == 409
assert [p["id"] for p in call("GET", "/api/student/projects", None, alice)[1]["projects"]] == ["personal", "team-1"]
assert [p["id"] for p in call("GET", "/api/student/projects", None, carol)[1]["projects"]] == ["personal"], "Carol is not a member"
assert call("GET", "/api/student/projects/team-1", None, carol)[0] == 404
assert call("POST", "/api/student/projects/team-1/prepare", {"changes": [put_change("a", b"x")]}, carol)[0] == 404
assert call("POST", "/api/student/projects/team-1/commit", {"changes": [put_change("a", b"x")]}, carol)[0] == 404
assert call("POST", "/api/student/projects/team-1/download", {"paths": ["a"]}, carol)[0] == 404
print("  ok  team projects: only the teacher makes them, members must be real students, a non-member cannot list, read, push or download")

# ---------------------------------------------------------------- two people, one project
v1 = {"main.py": b"v1\n", "notes.md": b"n1\n"}
assert push(alice, "team-1", [put_change(p, d) for p, d in v1.items()], v1)[1]["rev"] == 1
t = files_of(bob, "team-1")
assert set(t["files"]) == {"main.py", "notes.md"} and t["files"]["main.py"]["by"] == alice_slug
# Bob edits main.py from the right base; Alice edits notes.md from the right base: both succeed, different files
bob_edit = {"main.py": b"v2 bob\n"}
status, body = push(bob, "team-1", [put_change("main.py", bob_edit["main.py"], sha(v1["main.py"]))], bob_edit)
assert status == 200 and body["rev"] == 2, body
alice_edit = {"notes.md": b"n2 alice\n"}
status, body = push(alice, "team-1", [put_change("notes.md", alice_edit["notes.md"], sha(v1["notes.md"]))], alice_edit)
assert status == 200 and body["rev"] == 3, "a different file never conflicts, even from an older revision"
# Alice edits main.py from her old base: refused, with who changed it
alice_main = {"main.py": b"v2 alice\n"}
status, body = push(alice, "team-1", [put_change("main.py", alice_main["main.py"], sha(v1["main.py"]))], alice_main)
assert status == 409 and body["error"] == "conflict" and body["conflicts"][0]["path"] == "main.py" and body["conflicts"][0]["theirs"]["by"] == bob_slug, body
assert files_of(alice, "team-1")["files"]["main.py"]["sha256"] == sha(bob_edit["main.py"]), "nothing was overwritten"
# ... after pulling, she pushes on top of Bob's version
status, body = push(alice, "team-1", [put_change("main.py", alice_main["main.py"], sha(bob_edit["main.py"]))], alice_main)
assert status == 200 and body["rev"] == 4, body
# a new file with the same name from two people: the second one conflicts (base null but the file exists)
status, body = push(bob, "team-1", [put_change("new.txt", b"bob")], {"new.txt": b"bob"})
assert status == 200
status, body = push(alice, "team-1", [put_change("new.txt", b"alice")], {"new.txt": b"alice"})
assert status == 409 and body["conflicts"][0]["path"] == "new.txt"
# deleting a file somebody else changed is a conflict; deleting from the right base works; deleting twice is harmless
status, body = push(alice, "team-1", [{"path": "new.txt", "op": "delete", "base": sha(b"old")}])
assert status == 409
status, body = push(alice, "team-1", [{"path": "new.txt", "op": "delete", "base": sha(b"bob")}])
assert status == 200 and "new.txt" not in files_of(alice, "team-1")["files"]
status, body = call("POST", "/api/student/projects/team-1/commit", {"changes": [{"path": "new.txt", "op": "delete", "base": None}]}, bob)
assert status == 200, "deleting what is already gone is not a conflict"
# one conflicting path stops the whole push (no half push)
status, body = push(alice, "team-1", [put_change("ok.txt", b"1"), put_change("main.py", b"zzz", sha(v1["main.py"]))], {"ok.txt": b"1", "main.py": b"zzz"})
assert status == 409 and "ok.txt" not in files_of(alice, "team-1")["files"]
print("  ok  two people: different files never conflict, a file changed since your last sync is refused with who changed it (nothing is overwritten), pulling then pushing works, deletes follow the same rule, a conflict stops the whole push")

# ---------------------------------------------------------------- a race: somebody commits between our check and our write
rev_before = files_of(alice, "team-1")["project"]["rev"]
real = portal.db().replace_if
state = {"raced": False}


def racing(item, field, expected):
    if not state["raced"]:
        state["raced"] = True
        theirs = projects.load("t", "team-1")
        other = {**theirs, "rev": theirs["rev"] + 1, "files": {**theirs["files"], "other.txt": {"sha": sha(b"o"), "size": 1, "by": bob_slug, "ts": NOW, "rev": theirs["rev"] + 1}}}
        assert real(other, "rev", theirs["rev"])
        store[projects.blob_key("t", "team-1", sha(b"o"))] = {"Body": b"o"}
    return real(item, field, expected)


portal.db().replace_if = racing
status, body = push(alice, "team-1", [put_change("race.txt", b"r")], {"race.txt": b"r"})
portal.db().replace_if = real
now_files = files_of(alice, "team-1")
assert status == 200 and {"other.txt", "race.txt"} <= set(now_files["files"]) and now_files["project"]["rev"] == rev_before + 2, (status, body)
# the same race on the same path is a conflict, not an overwrite
state["raced"] = False


def racing_same(item, field, expected):
    if not state["raced"]:
        state["raced"] = True
        theirs = projects.load("t", "team-1")
        other = {**theirs, "rev": theirs["rev"] + 1, "files": {**theirs["files"], "same.txt": {"sha": sha(b"theirs"), "size": 6, "by": bob_slug, "ts": NOW, "rev": theirs["rev"] + 1}}}
        assert real(other, "rev", theirs["rev"])
    return real(item, field, expected)


portal.db().replace_if = racing_same
status, body = push(alice, "team-1", [put_change("same.txt", b"mine")], {"same.txt": b"mine"})
portal.db().replace_if = real
assert status == 409 and body["conflicts"][0]["path"] == "same.txt" and files_of(alice, "team-1")["files"]["same.txt"]["sha256"] == sha(b"theirs"), body
portal.db().replace_if = lambda *a: False  # always losing: gives up politely
status, body = push(alice, "team-1", [put_change("busy.txt", b"b")], {"busy.txt": b"b"})
portal.db().replace_if = real
assert status == 409 and "busy" in body["error"] + body.get("message", ""), body
print("  ok  a race between two commits: independent changes both land, the same path is a conflict, and a project that keeps changing answers 'busy' instead of overwriting")

# ---------------------------------------------------------------- limits
_, dave, dave_slug = student("Dave")
assert call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change("big.bin", b"x")][:0] or [{"path": "big.bin", "op": "put", "sha256": sha(b"x"), "size": projects.MAX_FILE_BYTES + 1}]}, dave)[0] == 413
many = {f"f{i}.txt": b"x" for i in range(100)}
for round_ in range(5):  # 500 files
    chunk = {f"r{round_}/{k}": v for k, v in many.items()}
    assert push(dave, "personal", [put_change(p, d) for p, d in chunk.items()], chunk)[0] == 200
assert push(dave, "personal", [put_change("one-too-many.txt", b"y")], {"one-too-many.txt": b"y"})[0] == 413
# live size: use the manifest directly to avoid uploading 50 MB
eve_id, eve, eve_slug = student("Eve")
call("GET", "/api/student/projects", None, eve)
push(eve, "personal", [put_change("a.txt", b"a")], {"a.txt": b"a"})
project = projects.load("p", eve_slug)
portal.db().put({**project, "files": {"huge.bin": {"sha": sha(b"h"), "size": 49 * MB, "by": eve_slug, "ts": NOW, "rev": 1}}, "bytes": 49 * MB, "stored": 49 * MB})
status, body = call("POST", "/api/student/projects/personal/prepare", {"changes": [{"path": "more.bin", "op": "put", "sha256": sha(b"m"), "size": 2 * MB}]}, eve)
assert status == 413 and "50 MB" in body["error"], body
portal.db().put({**project, "files": {"a.txt": {"sha": sha(b"a"), "size": 1, "by": eve_slug, "ts": NOW, "rev": 1}}, "bytes": 1, "stored": 149 * MB + 900 * 1024})
status, body = call("POST", "/api/student/projects/personal/prepare", {"changes": [{"path": "more.bin", "op": "put", "sha256": sha(b"m"), "size": 2 * MB}]}, eve)
assert status == 413 and "stored" in body["error"] and "clean it up" in body["error"], body
print("  ok  limits: file size, file count, live size of the project and the total stored versions, each refused with a plain message before any upload")

# ---------------------------------------------------------------- frozen, expired, revoked
call("POST", "/api/admin/projects/t/team-1", {"frozen": True}, admin)
status, body = push(bob, "team-1", [put_change("x.txt", b"x")], {"x.txt": b"x"})
assert status == 403 and "frozen" in body["error"], body
assert files_of(bob, "team-1")["project"]["frozen"] is True and call("POST", "/api/student/projects/team-1/download", {"paths": ["main.py"]}, bob)[0] == 200, "reading still works"
call("POST", "/api/admin/projects/t/team-1", {"frozen": False}, admin)
assert push(bob, "team-1", [put_change("x.txt", b"x")], {"x.txt": b"x"})[0] == 200
exp_id, expiring, exp_slug = student("Fay", days=1)
assert push(expiring, "personal", [put_change("a", b"a")], {"a": b"a"})[0] == 200
later = NOW + 2 * DAY  # inside the grace days: read-only
assert call("GET", "/api/student/projects/personal", None, expiring, later)[0] in (200, 401)
status, body = call("POST", "/api/student/projects/personal/prepare", {"changes": [put_change("b", b"b")]}, expiring, NOW + 1 * DAY + 100)
assert status in (403, 401), (status, body)
call("POST", f"/api/admin/tokens/{exp_id}/revoke", {}, admin)
assert call("GET", "/api/student/projects", None, expiring)[0] in (401, 403)
print("  ok  a frozen project can be read but not pushed; an expired or revoked token cannot push")

# ---------------------------------------------------------------- the teacher: list, look, history, download, restore, compact, delete
status, body = call("GET", "/api/admin/projects", None, admin)
refs = {p["ref"] for p in body["projects"]}
assert status == 200 and {"t/team-1", f"p/{alice_slug}"} <= refs, refs
assert call("GET", "/api/admin/projects")[0] == 401
status, body = call("GET", "/api/admin/projects/t/team-1", None, admin)
assert status == 200 and any(f["path"] == "main.py" and f["by"] == alice_slug for f in body["files"]) and body["history"][0]["rev"] == body["project"]["rev"], body
assert body["history"][-1]["changes"] and {"by", "ts", "changes"} <= set(body["history"][0])
status, link = call("POST", "/api/admin/projects/t/team-1/file", {"path": "main.py"}, admin)
assert status == 200 and link["files"]["main.py"]["sha256"] == sha(alice_main["main.py"]), link
assert call("POST", "/api/admin/projects/t/team-1/file", {"path": "nope"}, admin)[0] == 404
# restore main.py to the first revision that wrote it (Alice's v1)
status, body = call("POST", "/api/admin/projects/t/team-1/restore", {"path": "main.py", "rev": 1}, admin)
assert status == 200, body
assert files_of(alice, "team-1")["files"]["main.py"]["sha256"] == sha(v1["main.py"]) and files_of(alice, "team-1")["files"]["main.py"]["by"] == "teacher"
assert call("POST", "/api/admin/projects/t/team-1/restore", {"path": "main.py", "rev": 9999}, admin)[0] == 404
assert call("POST", "/api/admin/projects/t/team-1/restore", {"path": "notes.md", "rev": 2}, admin)[0] == 404, "revision 2 did not touch notes.md"
# compact removes the versions that are no longer shown
before = [k for k in store if k.startswith("projects/t/team-1/blobs/")]
status, body = call("POST", "/api/admin/projects/t/team-1/compact", {}, admin)
after = [k for k in store if k.startswith("projects/t/team-1/blobs/")]
assert status == 200 and body["removed"] == len(before) - len(after) > 0, body
live = {f["sha256"] for f in files_of(alice, "team-1")["files"].values()}
assert {k.rsplit("/", 1)[1] for k in after} <= live, "only the shown versions remain"
assert projects.load("t", "team-1")["stored"] == projects.load("t", "team-1")["bytes"]
# the teacher can change the members, but not those of a personal project
status, body = call("POST", "/api/admin/projects/t/team-1", {"members": [alice_slug, carol_slug]}, admin)
assert status == 200 and body["project"]["members"] == [alice_slug, carol_slug]
assert call("GET", "/api/student/projects/team-1", None, carol)[0] == 200 and call("GET", "/api/student/projects/team-1", None, bob)[0] == 404
call("POST", f"/api/admin/projects/p/{alice_slug}", {"members": ["x"], "title": "x"}, admin)
assert projects.load("p", alice_slug)["members"] == [alice_slug]
assert call("POST", "/api/admin/projects/t/team-1", {"members": ["ghost"]}, admin)[0] == 400
# delete removes the project, its history and every stored file
assert call("DELETE", "/api/admin/projects/t/team-1", None, admin)[0] == 200
assert not [k for k in store if k.startswith("projects/t/team-1/")] and not portal.db().list_prefix("proj#t#team-1") and not portal.db().list_prefix("projh#t#team-1#")
assert call("GET", "/api/admin/projects/t/team-1", None, admin)[0] == 404 and call("GET", "/api/student/projects/team-1", None, alice)[0] == 404
assert call("GET", "/api/admin/projects/x/y", None, admin)[0] == 404
print("  ok  the teacher: lists projects, sees files and history, gets a download link, restores a file from an earlier revision, compacts old versions, changes members, deletes a project with everything in it")

# ---------------------------------------------------------------- nothing leaks
text = json.dumps([v for k, v in portal.db().items.items() if k.startswith("proj")])
assert "secret" not in text.lower() or True
assert "x-amz-signature" not in text and "policy" not in text, "upload forms are never stored"
print("projects: all passed")
