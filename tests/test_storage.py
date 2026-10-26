"""Class storage connector: the teacher switch and caps, the environment given to the student's function, and the storage API itself
(data and files): isolation between students, quotas that cannot be overshot, the allowance, lifecycle, and that no key is exposed.
DynamoDB, S3 and Lambda are the shared in-memory fakes."""
import io
import json
import os
import sys
import time
import zipfile

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true",
                  SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t",
                  STORAGE_ENABLED="true", STORAGE_URL="https://storage.example.com")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import portal  # noqa: E402
import storage  # noqa: E402
import storage_api  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeS3, store  # noqa: E402

DAY = portal.DAY
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"


class StrictStore(portal.MemoryStore):
    """DynamoDB refuses Python floats; fail loudly here instead of in production."""

    @staticmethod
    def _no_floats(value, where):
        if isinstance(value, float):
            raise TypeError(f"float {value!r} in {where}: DynamoDB needs int or Decimal")
        if isinstance(value, dict):
            for k, v in value.items():
                StrictStore._no_floats(v, f"{where}.{k}")

    def update(self, pk, fields, remove=()):
        self._no_floats(fields, pk)
        return super().update(pk, fields, remove)


portal._store = StrictStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
logs, s3, lam = FakeLogs(), FakeS3(), FakeLambda()
lam.logs = logs
backends._clients.update({"lambda": lam, "logs": logs, "s3": s3})
now = int(time.time())


def call(method, path, body=None, session=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]
EXPOSED = []


def seen(status, body):
    EXPOSED.append(json.dumps(body))
    return status, body


def new_student(name):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    tid = made["item"]["id"]
    call("POST", f"/api/admin/tokens/{tid}/backend", {"enabled": True}, admin)
    return tid, call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def st_set(tid, at=None, **body):
    return seen(*call("POST", f"/api/admin/tokens/{tid}/storage", body, admin, at=at))


def key_of(tid):
    return (portal.db().get(f"tok#{tid}").get("storage") or {}).get("key")


def api(key, method, path, body=None, query=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"Authorization": f"Bearer {key}"} if key else {},
             "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}
    result = storage_api.handler(event, now=at if at is not None else now)
    return result["statusCode"], json.loads(result["body"])


def deploy(session, slug):
    lam.polls_until_active = 1
    call("POST", "/api/student/backend/upload-url", session=session)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", "def handler(event, context):\n    return {'statusCode': 200, 'body': 'ok'}\n")
    store[f"uploads/{slug}-backend.zip"] = {"Body": buf.getvalue()}
    assert call("POST", "/api/student/backend/publish", session=session)[0] == 200
    for _ in range(5):
        if call("GET", "/api/student/backend", session=session)[1]["token"]["backend"]["state"] == "ready":
            return
    raise AssertionError("backend never became ready")


def env(slug):
    return (lam.functions[f"sd-{slug}"]["config"].get("Environment") or {}).get("Variables") or {}


# ---------- 1. the teacher switch: defaults, a key made once, never exposed ----------
a_tid, a_session, a_slug = new_student("Alice")
b_tid, b_session, b_slug = new_student("Bob")
view = call("GET", "/api/student/me", session=a_session)[1]["token"]["storage"]
assert view["available"] and view["enabled"] is False and view["usage"] is None, view
status, body = st_set(a_tid, enabled=True)
assert status == 200, body
view = body["token"]["storage"]
assert view["enabled"] and view["limits"]["data_mb"] == 5 and view["limits"]["files_mb"] == 50 and view["limits"]["ops_limit"] == 10000, view
assert view["limits"]["ops_period"] == "day" and view["env_names"] == ["CLASS_STORAGE_URL", "CLASS_STORAGE_KEY"], view
a_key = key_of(a_tid)
assert a_key.startswith(f"cs_{a_slug}.") and len(a_key) > 40
st_set(a_tid, enabled=False)
st_set(a_tid, enabled=True)
assert key_of(a_tid) == a_key, "switching off and on keeps the same key"
print("  ok  first switch-on: defaults, one key per student, kept across off/on")

# ---------- 2. validation ----------
for bad in ({"data_mb": 0}, {"data_mb": 101}, {"data_mb": True}, {"data_mb": "5"}, {"files_mb": 501}, {"ops_limit": -1}, {"ops_limit": 2_000_000},
            {"ops_period": "week"}, {"enabled": "yes"}):
    assert st_set(a_tid, **bad)[0] == 400, bad
assert st_set(a_tid, data_mb=10, files_mb=20, ops_limit=100, ops_period="month")[0] == 200
limits = call("GET", "/api/student/me", session=a_session)[1]["token"]["storage"]["limits"]
assert (limits["data_mb"], limits["files_mb"], limits["ops_limit"], limits["ops_period"]) == (10, 20, 100, "month")
st_set(a_tid, data_mb=5, files_mb=50, ops_limit=10000, ops_period="day")
print("  ok  invalid caps are refused; valid ones are saved")

# ---------- 3. the student's function gets the two variables, and cannot overwrite them ----------
deploy(a_session, a_slug)
assert env(a_slug) == {"CLASS_STORAGE_URL": "https://storage.example.com", "CLASS_STORAGE_KEY": a_key}, env(a_slug)
for name in storage.ENV_NAMES:
    assert call("PUT", "/api/student/backend/env", {"name": name, "value": "x"}, a_session)[0] == 400
    assert call("DELETE", f"/api/student/backend/env/{name}", session=a_session)[0] == 400
assert call("PUT", "/api/student/backend/env", {"name": "GREETING", "value": "hi"}, a_session)[0] == 200
st_set(a_tid, enabled=False)
assert not any(n in env(a_slug) for n in storage.ENV_NAMES) and env(a_slug)["GREETING"] == "hi"
st_set(a_tid, enabled=True)
assert env(a_slug)["CLASS_STORAGE_KEY"] == a_key and env(a_slug)["GREETING"] == "hi"
print("  ok  the function receives URL and key while on; off removes them; other variables survive")

# Bob: switched on AFTER his function exists
deploy(b_session, b_slug)
st_set(b_tid, enabled=True)
b_key = key_of(b_tid)
assert env(b_slug)["CLASS_STORAGE_KEY"] == b_key and b_key != a_key

# ---------- 4. keys: only the right student's key opens anything ----------
assert api(None, "GET", "/v1/data/notes/x")[0] == 401
assert api("garbage", "GET", "/v1/data/notes/x")[0] == 401
assert api(a_key.split(".")[0] + "." + "A" * 32, "GET", "/v1/data/notes/x")[0] == 401, "a key with a wrong secret"
assert api(f"cs_nobody.{'a' * 30}", "GET", "/v1/data/notes/x")[0] == 401
assert api(a_key, "GET", "/v1/data/notes/x")[1]["error"] == "not_found"
print("  ok  missing, malformed, wrong-secret and unknown-student keys are refused")

# ---------- 5. data: put, get, overwrite, list, delete; students never see each other ----------
first = {"text": "hello", "n": 1, "tags": ["a"]}
assert api(a_key, "PUT", "/v1/data/notes/n1", {"value": first}) == (200, {"id": "n1", "bytes": len(json.dumps(first, separators=(",", ":")))})
status, body = api(a_key, "GET", "/v1/data/notes/n1")
assert status == 200 and body["value"] == {"text": "hello", "n": 1, "tags": ["a"]}, body
api(b_key, "PUT", "/v1/data/notes/n1", {"value": "Bob's own"})
assert api(a_key, "GET", "/v1/data/notes/n1")[1]["value"]["text"] == "hello", "same collection and id, different student"
assert api(b_key, "GET", "/v1/data/notes/n1")[1]["value"] == "Bob's own"
for n in range(2, 6):
    api(a_key, "PUT", f"/v1/data/notes/n{n}", {"value": n})
listed = api(a_key, "GET", "/v1/data/notes")[1]
assert [i["id"] for i in listed["items"]] == ["n1", "n2", "n3", "n4", "n5"] and listed["next"] is None, listed
page = api(a_key, "GET", "/v1/data/notes", query={"limit": "2"})[1]
assert [i["id"] for i in page["items"]] == ["n1", "n2"] and page["next"] == "n2"
rest = api(a_key, "GET", "/v1/data/notes", query={"after": page["next"]})[1]
assert [i["id"] for i in rest["items"]] == ["n3", "n4", "n5"]
assert api(a_key, "GET", "/v1/data/other")[1] == {"items": [], "next": None}
assert api(a_key, "DELETE", "/v1/data/notes/n5")[1] == {"deleted": True}
assert api(a_key, "DELETE", "/v1/data/notes/n5")[1] == {"deleted": False}
assert api(a_key, "GET", "/v1/data/notes/n5")[0] == 404
print("  ok  put/get/list/paging/delete work and every student has a private namespace")

# ---------- 6. names and sizes ----------
for path in ("/v1/data/Bad/x", "/v1/data/has space/x", "/v1/data/notes/..", "/v1/data/notes/a%2Fb", "/v1/data/notes/" + "x" * 129):
    assert api(a_key, "PUT", path, {"value": 1})[0] == 400, path
assert api(a_key, "PUT", "/v1/data/notes/x", {"nope": 1})[0] == 400
assert api(a_key, "PUT", "/v1/data/notes/x", {"value": "a" * 70000})[0] == 413
assert api(a_key, "PUT", "/v1/data/notes/x", {"value": "a" * 65000})[0] == 200, "just under 64 KB is fine"
print("  ok  collection and id names and the 64 KB value limit are enforced")

# ---------- 7. quota: bytes and items, exact accounting, no overshoot ----------
def counts(slug):
    return storage._count(f"sbytes#{slug}"), storage._count(f"sitems#{slug}")


c_tid, c_session, c_slug = new_student("Carol")
st_set(c_tid, enabled=True, data_mb=1)
c_key = key_of(c_tid)
api(c_key, "PUT", "/v1/data/box/a", {"value": "x" * 100})
bytes_a = counts(c_slug)[0]
assert counts(c_slug)[1] == 1 and bytes_a == 102 + 1, counts(c_slug)
api(c_key, "PUT", "/v1/data/box/a", {"value": "x" * 300})
assert counts(c_slug) == (302 + 1, 1), "an overwrite counts only the difference"
api(c_key, "PUT", "/v1/data/box/a", {"value": "y"})
assert counts(c_slug) == (3 + 1, 1), "shrinking gives space back"
api(c_key, "DELETE", "/v1/data/box/a")
assert counts(c_slug) == (0, 0), "delete frees everything"
filled = 0
while True:  # fill 1 MB with 60 KB values
    status, body = api(c_key, "PUT", f"/v1/data/box/f{filled}", {"value": "z" * 60000})
    if status != 200:
        break
    filled += 1
assert status == 413 and body["error"] == "quota_exceeded" and 15 <= filled <= 17, (filled, status, body)
used = counts(c_slug)[0]
assert used <= 1 * 1024 * 1024 and used + 60000 > 1024 * 1024, "stopped exactly at the cap, no overshoot"
assert counts(c_slug)[1] == filled, "a refused write leaves no trace"
storage.MAX_ITEMS = 3
d_tid, d_session, d_slug = new_student("Dave")
st_set(d_tid, enabled=True)
d_key = key_of(d_tid)
for n in range(3):
    assert api(d_key, "PUT", f"/v1/data/t/i{n}", {"value": n})[0] == 200
status, body = api(d_key, "PUT", "/v1/data/t/i3", {"value": 3})
assert status == 413 and "limit of 3 items" in body["message"]
assert counts(d_slug)[1] == 3 and api(d_key, "PUT", "/v1/data/t/i0", {"value": "overwrite is fine"})[0] == 200
storage.MAX_ITEMS = 5000
print("  ok  byte and item caps are exact; overwrite, shrink and delete keep the counters true")

# ---------- 8. a failed write gives the reserved space back ----------
class Broken(storage.MemoryData):
    def put(self, *args):
        raise RuntimeError("table down")


good = storage._data[0] if storage._data else storage.data()
storage._data[:] = [Broken()]
before = counts(d_slug)
try:
    api(d_key, "PUT", "/v1/data/t/new", {"value": "x" * 500})
    raise AssertionError("expected the failure to surface")
except RuntimeError:
    pass
assert counts(d_slug) == before, "bytes and items reserved for a write that failed are returned"
storage._data[:] = [good]
print("  ok  a write that fails after reserving space rolls the counters back")

# ---------- 8b. a list answer stays far below Lambda's response limit ----------
g_tid, g_session, g_slug = new_student("Gus")
st_set(g_tid, enabled=True)
g_key = key_of(g_tid)
for n in range(10):
    assert api(g_key, "PUT", f"/v1/data/big/r{n}", {"value": "q" * 60000})[0] == 200
first = api(g_key, "GET", "/v1/data/big")[1]
assert 1 <= len(first["items"]) < 10 and first["next"] == first["items"][-1]["id"], "a page stops at about 400 KB and says where to continue"
seen_ids, after = [], None
while True:
    page = api(g_key, "GET", "/v1/data/big", query={"after": after} if after else None)[1]
    seen_ids += [i["id"] for i in page["items"]]
    after = page["next"]
    if not after:
        break
assert seen_ids == sorted(f"r{n}" for n in range(10)), "following next gives every record exactly once"
print("  ok  a list answer is cut at about 400 KB and the next page continues exactly where it stopped")

# ---------- 9. the allowance ----------
e_tid, e_session, e_slug = new_student("Erin")
st_set(e_tid, enabled=True, ops_limit=3, ops_period="day")
e_key = key_of(e_tid)
assert [api(e_key, "GET", "/v1/data/x/y")[0] for _ in range(3)] == [404, 404, 404]
status, body = api(e_key, "GET", "/v1/data/x/y")
assert status == 429 and body["error"] == "allowance_used"
tomorrow = now + 2 * DAY
assert api(e_key, "GET", "/v1/data/x/y", at=tomorrow)[0] == 404, "the daily allowance resets"
usage = call("GET", "/api/student/me", session=e_session)[1]["token"]["storage"]["usage"]
assert usage["ops_used"] == 3 and usage["data_bytes"] == 0, usage
assert st_set(e_tid, ops_limit=0)[0] == 200 and api(e_key, "GET", "/v1/data/x/y", at=tomorrow + 1)[0] == 429, "0 means no operations"
print("  ok  every call counts, the allowance cannot be overshot and resets with its period")

# ---------- 10. lifecycle: off, revoke, expiry ----------
f_tid, f_session, f_slug = new_student("Fay")
st_set(f_tid, enabled=True)
f_key = key_of(f_tid)
assert api(f_key, "PUT", "/v1/data/n/a", {"value": 1})[0] == 200
st_set(f_tid, enabled=False)
status, body = api(f_key, "GET", "/v1/data/n/a")
assert status == 403 and body["error"] == "not_enabled"
st_set(f_tid, enabled=True)
assert api(f_key, "GET", "/v1/data/n/a")[1]["value"] == 1, "the data is kept while it is off"
assert api(f_key, "GET", "/v1/data/n/a", at=now + 31 * DAY)[1]["error"] == "token_inactive", "an expired token pauses storage"
call("POST", f"/api/admin/tokens/{f_tid}/revoke", {}, admin)
assert api(f_key, "GET", "/v1/data/n/a")[0] == 403
print("  ok  off, expiry and revoke stop storage at once; data stays")

# ---------- 11. files ----------
status, body = api(a_key, "POST", "/v1/files/upload-url", {"name": "photo.png", "size": 1000, "content_type": "image/png"})
assert status == 200 and body["url"].endswith("/s3-post") and body["max_bytes"] == 1000, body
post = s3.posts[-1]
assert post["Key"] == f"userdata/{a_slug}/photo.png" and ["content-length-range", 1, 1000] in post["Conditions"] and {"Content-Type": "image/png"} in post["Conditions"], post
assert body["fields"]["key"] == f"userdata/{a_slug}/photo.png"
for name in ("../x", "a/b", "", ".hidden", "x" * 101, "sp ace"):
    assert api(a_key, "POST", "/v1/files/upload-url", {"name": name, "size": 10})[0] == 400, name
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "a.bin", "size": 0})[0] == 400
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "a.bin", "size": True})[0] == 400
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "a.bin", "size": 10, "content_type": "no slash"})[0] == 400
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "big.bin", "size": 5 * 1024 * 1024 + 1})[0] == 413
store[f"userdata/{a_slug}/photo.png"] = {"Body": b"x" * 1000}
store[f"userdata/{b_slug}/secret.txt"] = {"Body": b"bob"}
listed = api(a_key, "GET", "/v1/files")[1]["files"]
assert [(f["name"], f["size"]) for f in listed] == [("photo.png", 1000)], "only the student's own folder"
status, body = api(a_key, "GET", "/v1/files/photo.png")
assert status == 200 and body["url"].endswith(f"userdata/{a_slug}/photo.png"), body
assert api(a_key, "GET", "/v1/files/secret.txt")[0] == 404, "another student's file is not reachable by name"
assert api(a_key, "DELETE", "/v1/files/photo.png")[0] == 200 and f"userdata/{a_slug}/photo.png" not in store
assert f"userdata/{b_slug}/secret.txt" in store
print("  ok  upload links carry the owner's key path and a size limit; listing, download and delete stay inside the folder")

# ---------- 12. file caps: total size and count ----------
st_set(a_tid, files_mb=1)
store[f"userdata/{a_slug}/one.bin"] = {"Body": b"x" * (900 * 1024)}
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "two.bin", "size": 200 * 1024})[0] == 413
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "one.bin", "size": 1000 * 1024})[0] == 200, "replacing a file only counts the new size"
del store[f"userdata/{a_slug}/one.bin"]
st_set(a_tid, files_mb=500)
for n in range(storage.MAX_FILES):
    store[f"userdata/{a_slug}/f{n}.txt"] = {"Body": b"1"}
status, body = api(a_key, "POST", "/v1/files/upload-url", {"name": "extra.txt", "size": 1})
assert status == 413 and "100 files" in body["message"]
assert api(a_key, "POST", "/v1/files/upload-url", {"name": "f0.txt", "size": 5})[0] == 200, "replacing an existing name is allowed at the limit"
status, body = call("GET", "/api/student/storage", session=a_session)
assert status == 200 and body["files"] == {"count": storage.MAX_FILES, "bytes": storage.MAX_FILES}, body
print("  ok  the file space and the file count are capped; replacing a file is allowed at the cap")

# ---------- 12b. the teacher's overview and "clear": sizes and counts only, never the content ----------
h_tid, h_session, h_slug = new_student("Hana")
i_tid, i_session, i_slug = new_student("Ivo")
st_set(h_tid, enabled=True)
st_set(i_tid, enabled=True)
h_key, i_key = key_of(h_tid), key_of(i_tid)
for coll in ("alpha", "beta"):
    for n in range(3):
        assert api(h_key, "PUT", f"/v1/data/{coll}/r{n}", {"value": "v" * 100})[0] == 200
assert api(i_key, "PUT", "/v1/data/alpha/r0", {"value": "ivo's"})[0] == 200
store[f"userdata/{h_slug}/a.bin"] = {"Body": b"x" * 400}
store[f"userdata/{h_slug}/b.bin"] = {"Body": b"y" * 100}
store[f"userdata/{i_slug}/keep.bin"] = {"Body": b"z" * 50}
status, body = call("GET", "/api/admin/storage", session=admin)
assert status == 200, body
by_slug = {r["slug"]: r for r in body["students"]}
assert h_slug in by_slug and i_slug in by_slug and by_slug[h_slug]["usage"]["items"] == 6 and by_slug[h_slug]["files"] == {"count": 2, "bytes": 500}, by_slug.get(h_slug)
assert by_slug[i_slug]["files"] == {"count": 1, "bytes": 50} and by_slug[i_slug]["usage"]["items"] == 1
assert body["totals"]["items"] >= 7 and body["totals"]["files"] >= 3 and body["totals"]["enabled"] >= 2, body["totals"]
assert "v" * 20 not in json.dumps(body) and "ivo's" not in json.dumps(body) and "cs_" not in json.dumps(body), "the overview never carries content or keys"
assert call("GET", "/api/admin/storage")[0] == 401 and call("GET", "/api/admin/storage", session=h_session)[0] == 401, "teacher only"
unused = [r["slug"] for r in body["students"] if r["slug"] == b_slug]
print("  ok  overview: per-student sizes and counts, totals, no content or key, teacher only")

# clear the data only
status, body = call("POST", f"/api/admin/tokens/{h_tid}/storage/clear", {"scope": "data"}, admin)
assert status == 200 and body["cleared"] == {"items": 6, "files": 0}, body
assert counts(h_slug) == (0, 0), "counters are back to zero"
assert api(h_key, "GET", "/v1/data/alpha/r0")[0] == 404 and api(h_key, "GET", "/v1/data/beta")[1]["items"] == []
assert [f["name"] for f in api(h_key, "GET", "/v1/files")[1]["files"]] == ["a.bin", "b.bin"], "files untouched by a data-only clear"
assert api(i_key, "GET", "/v1/data/alpha/r0")[1]["value"] == "ivo's" and f"userdata/{i_slug}/keep.bin" in store, "another student is untouched"
assert api(h_key, "PUT", "/v1/data/alpha/again", {"value": 1})[0] == 200 and counts(h_slug)[1] == 1, "the key and the switch still work after a clear"
# clear files only
status, body = call("POST", f"/api/admin/tokens/{h_tid}/storage/clear", {"scope": "files"}, admin)
assert body["cleared"] == {"items": 0, "files": 2} and api(h_key, "GET", "/v1/files")[1]["files"] == [] and api(h_key, "GET", "/v1/data/alpha/again")[0] == 200
assert f"userdata/{i_slug}/keep.bin" in store
# clear everything, also while storage is switched off and after the token is revoked (the cleanup case)
store[f"userdata/{h_slug}/c.bin"] = {"Body": b"c"}
st_set(h_tid, enabled=False)
call("POST", f"/api/admin/tokens/{h_tid}/revoke", {}, admin)
inactive = {r["slug"]: r for r in call("GET", "/api/admin/storage", session=admin)[1]["students"]}[h_slug]
assert inactive["inactive"] is True and inactive["holds_data"], "a revoked student who still holds data is flagged"
status, body = call("POST", f"/api/admin/tokens/{h_tid}/storage/clear", {"scope": "all"}, admin)
assert status == 200 and body["cleared"] == {"items": 1, "files": 1}, body
after = {r["slug"]: r for r in call("GET", "/api/admin/storage", session=admin)[1]["students"]}[h_slug]
assert after["holds_data"] is False and after["inactive"] is False and after["usage"]["data_bytes"] == 0
assert call("POST", f"/api/admin/tokens/{h_tid}/storage/clear", {"scope": "everything"}, admin)[0] == 400
assert call("POST", f"/api/admin/tokens/{h_tid}/storage/clear", {}, admin)[0] == 400
assert call("POST", f"/api/admin/tokens/{h_tid}/storage/clear", {"scope": "all"})[0] == 401
assert call("POST", f"/api/admin/tokens/{'0' * 64}/storage/clear", {"scope": "all"}, admin)[0] == 404
print("  ok  clear: data, files or both; counters reset; others untouched; works for revoked students; bad scope refused")

# collections are remembered (so clear finds them) and capped
storage.MAX_COLLECTIONS = 3
for n in range(2):  # Ivo already used "alpha", so two more names reach the cap of 3
    assert api(i_key, "PUT", f"/v1/data/c{n}/x", {"value": 1})[0] == 200
status, body = api(i_key, "PUT", "/v1/data/c2/x", {"value": 1})
assert status == 413 and "collection names" in body["message"], body
assert api(i_key, "PUT", "/v1/data/c0/y", {"value": 2})[0] == 200, "an existing name keeps working at the cap"
storage.MAX_COLLECTIONS = 50
cleared = call("POST", f"/api/admin/tokens/{i_tid}/storage/clear", {"scope": "data"}, admin)[1]["cleared"]
assert cleared["items"] == 4 and all(api(i_key, "GET", f"/v1/data/{c}/x")[0] == 404 for c in ("c0", "c1")) and api(i_key, "GET", "/v1/data/alpha/r0")[0] == 404, cleared
print("  ok  every collection a student used is found by clear; the number of names is capped")

# the remembered list heals: if its items are deleted behind a warm container's back, the next write after the memory expires recreates them
j_tid, j_session, j_slug = new_student("Jo")
st_set(j_tid, enabled=True)
j_key = key_of(j_tid)
assert api(j_key, "PUT", "/v1/data/memo/x", {"value": 1})[0] == 200
portal.db().delete(f"scoll#{j_slug}#memo")
portal.db().delete(f"scollcount#{j_slug}")
assert api(j_key, "PUT", "/v1/data/memo/y", {"value": 2})[0] == 200
assert portal.db().get(f"scoll#{j_slug}#memo") is None, "inside the memory window nothing is re-checked"
assert api(j_key, "PUT", "/v1/data/memo/z", {"value": 3}, at=now + storage.REMEMBER_SECONDS + 5)[0] == 200
assert portal.db().get(f"scoll#{j_slug}#memo") is not None and portal.db().get(f"scollcount#{j_slug}")["count"] == 1
assert call("POST", f"/api/admin/tokens/{j_tid}/storage/clear", {"scope": "data"}, admin)[1]["cleared"]["items"] == 3
print("  ok  the remembered collection list is rebuilt after a minute if it was deleted elsewhere")

# ---------- 13. routes and bodies ----------
assert api(a_key, "GET", "/v2/nothing")[0] == 404
assert api(a_key, "PATCH", "/v1/data/notes/n1")[0] == 404
event = {"rawPath": "/v1/data/notes/n1", "requestContext": {"http": {"method": "PUT"}}, "headers": {"authorization": f"Bearer {a_key}"}, "body": "{nope"}
assert storage_api.handler(event, now=now)["statusCode"] == 400
event["body"] = "[1]"
assert storage_api.handler(event, now=now)["statusCode"] == 400
event["body"] = json.dumps({"value": "a" * 200000})
assert storage_api.handler(event, now=now)["statusCode"] == 413
print("  ok  unknown routes, bad JSON and oversized bodies get clear errors")

# ---------- 14. off on a server without it ----------
os.environ["STORAGE_ENABLED"] = "false"
assert call("GET", "/api/student/me", session=a_session)[1]["token"]["storage"] == {"available": False}
assert st_set(a_tid, enabled=False)[0] == 503
assert api(a_key, "GET", "/v1/data/notes/n1")[0] == 403
assert call("PUT", "/api/student/backend/env", {"name": "CLASS_STORAGE_KEY", "value": "own"}, a_session)[0] == 200, "names are free when the feature is off"
os.environ["STORAGE_ENABLED"] = "true"
print("  ok  without STORAGE_ENABLED nothing is offered, served or reserved")

# ---------- 15. no key ever leaves the API ----------
blob = "\n".join(EXPOSED)
assert "cs_" not in blob, "no storage key in any teacher or student response"
listing = call("GET", "/api/admin/tokens", session=admin)[1]
assert "cs_" not in json.dumps(listing)
print("  ok  no storage key in any API response, including the teacher's list")

print("test_storage.py: all passed")
