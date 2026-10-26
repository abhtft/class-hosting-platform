"""Student deploy through the portal: app.handler + portal.py against in-memory S3/CloudFront/DynamoDB."""
import io
import json
import os
import sys
import time
import zipfile

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b",
                  DISTRIBUTION_ID="E1", DISTRIBUTION_DOMAIN="d.cloudfront.net", ROOT_DOMAIN="example.com", KEYS_PARAM="/k")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import app  # noqa: E402
import portal  # noqa: E402
from fakes import FakeCloudFront, FakeS3, FakeSSM, invalidations, store  # noqa: E402

app.s3, app.ssm, app.cloudfront = FakeS3(), FakeSSM(), FakeCloudFront()  # FakeSSM knows legacy student "alice"
salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("correct horse", salt)),
    "secret": portal.b64(b"1" * 32),
})


def call(method, path, body=None, session=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}}, "routeKey": "ANY /api/{proxy+}",
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = app.handler(event, None)
    return result["statusCode"], json.loads(result["body"])


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def upload(session, files):
    status, body = call("POST", "/api/student/upload-url", session=session)
    assert status == 200, body
    store[body["uploadUrl"].split("/s3/", 1)[1]] = {"Body": make_zip(files)}
    return call("POST", "/api/student/publish", session=session)


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"})[1]["session"]
_, created = call("POST", "/api/admin/tokens", {"days": 7, "student_name": "Carol"}, admin)
assert created["item"]["slug"] == "carol"
login = call("POST", "/api/student/login", {"token": created["token"]})[1]
student = login["session"]
assert login["token"]["last_deploy_at"] is None

# deploy writes only to the token's own folder, records last_deploy_at, clears the cache
status, body = upload(student, {"index.html": "<h1>hi</h1>", "assets/a.js": "1"})
assert status == 200 and body["url"] == "https://carol.example.com/" and body["files"] == 2, body
assert sorted(k for k in store if k.startswith("sites/")) == ["sites/carol/assets/a.js", "sites/carol/index.html"]
assert invalidations[-1] == ["/sites/carol/*", "/carol/*"]
me = call("GET", "/api/student/me", session=student)[1]["token"]
assert abs(me["last_deploy_at"] - time.time()) < 5

# redeploy replaces the site; a bad zip is a 400 and leaves the site alone
status, _ = upload(student, {"index.html": "<h1>v2</h1>"})
assert status == 200 and sorted(k for k in store if k.startswith("sites/")) == ["sites/carol/index.html"]
status, body = upload(student, {"readme.txt": "no index"})
assert status == 400 and "index.html" in body["error"]
assert store["sites/carol/index.html"]["Body"] == b"<h1>v2</h1>"

# no session / teacher session / garbage cannot deploy
assert call("POST", "/api/student/upload-url")[0] == 401
assert call("POST", "/api/student/upload-url", session=admin)[0] == 401
assert call("POST", "/api/student/publish", session="junk")[0] == 401

# expired token: read-only, deploy refused with a clear message, site untouched
tid = created["item"]["id"]
portal.db().update(f"tok#{tid}", {"expires_at": int(time.time()) - 60})
status, body = call("POST", "/api/student/upload-url", session=student)
assert status == 403 and "expired" in body["error"]
assert call("POST", "/api/student/publish", session=student)[0] == 403
assert call("GET", "/api/student/me", session=student)[1]["token"]["status"] == "expired"

# revoked token: session is dead
call("POST", f"/api/admin/tokens/{tid}/revoke", {}, admin)
assert call("POST", "/api/student/upload-url", session=student)[0] == 401

# names used by CLI students (SSM key map) can't be claimed by the portal
assert call("POST", "/api/admin/tokens", {"days": 1, "slug": "alice"}, admin)[0] == 409
assert call("POST", "/api/admin/tokens", {"days": 1, "student_name": "Alice"}, admin)[1]["item"]["slug"].startswith("alice-")

# restoring an archived site (used when a regeneration is approved)
store["archive/carol/index.html"] = {"Body": b"<h1>old</h1>"}
store["archive/carol/assets/a.js"] = {"Body": b"2"}
store.pop("sites/carol/index.html", None)
app.restore_site("carol")
assert store["sites/carol/index.html"]["Body"] == b"<h1>old</h1>" and store["sites/carol/assets/a.js"]["Body"] == b"2"
assert not [k for k in store if k.startswith("archive/")] and invalidations[-1] == ["/sites/carol/*", "/carol/*"]

# ---- sweeper: archive sites past expiry + 3 days grace; never delete; restorable ----
DAY = portal.DAY
now = int(time.time())
portal.archive_site, portal.restore_site = app.archive_site, app.restore_site


def new_site(name, expires_in_days, **extra):
    _, c = call("POST", "/api/admin/tokens", {"days": 7, "student_name": name}, admin)
    store[f"sites/{c['item']['slug']}/index.html"] = {"Body": name.encode()}
    portal.db().update(f"tok#{c['item']['id']}", {"expires_at": now + expires_in_days * DAY, **extra})
    return c["item"]["slug"], c["item"]["id"]


fresh, _ = new_site("fresh", 5)
grace, _ = new_site("grace", -2)                 # expired 2 days ago: still read-only, site stays up
gone, gone_id = new_site("gone", -4)             # past the 3-day grace -> archive
revoked, _ = new_site("revoked", -10, revoked=True)  # revoked tokens are left alone
store["archive/gone/old.txt"] = {"Body": b"left over from an older archive"}

result = portal.sweep(now)
assert result == {"archived": ["gone"], "failed": [], "domains_removed": []}, result
assert "sites/gone/index.html" not in store and store["archive/gone/index.html"]["Body"] == b"gone"
assert "archive/gone/old.txt" not in store, "leftovers of an older archive are replaced"
assert all(f"sites/{n}/index.html" in store for n in (fresh, grace, revoked)), "other sites untouched"
assert portal.db().get(f"tok#{gone_id}")["archived"] is True
assert portal.sweep(now) == {"archived": [], "failed": [], "domains_removed": []}, "second run does nothing"
view = portal.view(portal.db().get(f"tok#{gone_id}"), now, app.site_url)
assert view["status"] == "offline" and view["archived"] is True

# a failure on one site does not stop the others, and is retried next run
broke, _ = new_site("broke", -9)
also, _ = new_site("also", -9)
real = app.archive_site
portal.archive_site = lambda slug, tenant_id=None: (_ for _ in ()).throw(RuntimeError("boom")) if slug == "broke" else real(slug, tenant_id)
result = portal.sweep(now)
assert result["archived"] == ["also"] and result["failed"] == ["broke: boom"], result
portal.archive_site = real
assert portal.sweep(now)["archived"] == ["broke"]

# teacher can take a live site offline right now, and a regen approval / extend brings it back
live, live_id = new_site("live", 20)
call("POST", f"/api/admin/tokens/{live_id}/offline", {}, admin)
assert "sites/live/index.html" not in store and "archive/live/index.html" in store
assert call("POST", f"/api/admin/tokens/{live_id}/extend", {"days": 5}, admin)[0] == 200
assert "sites/live/index.html" in store and "archive/live/index.html" not in store
assert portal.db().get(f"tok#{live_id}")["archived"] is False

# a site the teacher took offline cannot be brought back by uploading; the student may ask for regeneration instead
call("POST", f"/api/admin/tokens/{live_id}/offline", {}, admin)
assert portal.status_of(portal.db().get(f"tok#{live_id}"), now) == "offline"
view_live = portal.view(portal.db().get(f"tok#{live_id}"), now, app.site_url)
assert view_live["can_deploy"] is False and view_live["can_request_regen"] is True
live_session = portal.sign_session("student", live_id, 3600, now)
assert call("POST", "/api/student/upload-url", session=live_session)[0] == 403, "no upload while offline"
assert "sites/live/index.html" not in store
call("POST", f"/api/admin/tokens/{live_id}/extend", {"days": 5}, admin)
assert portal.view(portal.db().get(f"tok#{live_id}"), now, app.site_url)["can_deploy"] is True

assert app.sweep({}, None) == {"archived": [], "failed": [], "domains_removed": []}  # Lambda entry point

# legacy CLI route still works unchanged
event = {"routeKey": "POST /upload-url", "headers": {"x-deploy-key": "alice.s3cret"}}
assert app.handler(event, None)["statusCode"] == 200

print("test_portal_deploy.py: all passed")
