"""Unit tests for lambda/portal.py (tokens, logins, sessions) with an in-memory store and a fake clock."""
import base64
import json
import os
import sys

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b",
                  DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
import portal  # noqa: E402

DAY = portal.DAY
T0 = 1_800_000_000
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731

salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("correct horse", salt)),
    "secret": portal.b64(b"0" * 32),
})


def call(method, path, body=None, session=None, now=T0, ip="1.1.1.1"):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": ip}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=now)
    return result["statusCode"], json.loads(result["body"])


def teacher_session():
    status, body = call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"})
    assert status == 200, body
    return body["session"]


def create(session, **body):
    status, data = call("POST", "/api/admin/tokens", body, session)
    return status, data


# ---- teacher login ----
status, body = call("POST", "/api/teacher/login", {"username": "teacher", "password": "nope"})
assert status == 401
status, body = call("POST", "/api/teacher/login", {"username": "other", "password": "correct horse"})
assert status == 401, "wrong username rejected"
admin = teacher_session()

# lockout after 5 failures from one IP, other IPs unaffected, success clears the counter
for _ in range(5):
    call("POST", "/api/teacher/login", {"username": "teacher", "password": "bad"}, ip="9.9.9.9")
assert call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"}, ip="9.9.9.9")[0] == 429
assert call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"}, ip="9.9.9.9",
            now=T0 + 16 * 60)[0] == 200, "lockout ends after 15 minutes"
assert call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"}, ip="8.8.8.8")[0] == 200

# behind CloudFront the viewer IP comes from x-viewer-ip, trusted only with the secret origin header
os.environ["ORIGIN_SECRET"] = "s3cr3t"
via_cf = lambda ip, secret="s3cr3t": {"headers": {"x-viewer-ip": ip, "x-origin-secret": secret}, "requestContext": {"http": {"sourceIp": "10.0.0.1"}}}  # noqa: E731
assert portal.client_ip(via_cf("7.7.7.7")) == "7.7.7.7"
assert portal.client_ip(via_cf("7.7.7.7", secret="wrong")) == "10.0.0.1", "forged header without the secret is ignored"
assert portal.client_ip({"headers": {"x-viewer-ip": "7.7.7.7"}, "requestContext": {"http": {"sourceIp": "10.0.0.1"}}}) == "10.0.0.1"
del os.environ["ORIGIN_SECRET"]
assert portal.client_ip(via_cf("7.7.7.7")) == "10.0.0.1", "no secret configured: never trust the header"

# ---- admin routes need a teacher session ----
assert call("GET", "/api/admin/tokens")[0] == 401
assert call("GET", "/api/admin/tokens", session="garbage")[0] == 401
assert call("GET", "/api/admin/tokens", session=admin, now=T0 + 9 * 3600)[0] == 401, "teacher session expires after 8h"

# ---- create token: slug precedence teacher > student name > random ----
status, data = create(admin, days=7, student_name="Alice Smith", slug="alice-site")
assert status == 201 and data["item"]["slug"] == "alice-site", data
assert data["token"].startswith("mds_") and len(data["token"]) >= 20
alice_token = data["token"]
assert data["item"]["status"] == "active" and data["item"]["expires_at"] == T0 + 7 * DAY
assert data["item"]["url"] == "https://alice-site.example.com/"
token_stored = portal.db().get("tok#" + portal.token_hash(alice_token))
assert token_stored
assert alice_token not in json.dumps(token_stored), "plaintext token must not be stored"

assert create(admin, days=7, student_name="Bob Jones")[1]["item"]["slug"] == "bob-jones"
assert create(admin, days=7, student_name="Bob Jones")[1]["item"]["slug"].startswith("bob-jones-"), "name clash gets a suffix"
slug = create(admin, days=7)[1]["item"]["slug"]
assert slug.startswith("s-") and portal.SLUG_RE.match(slug)
assert create(admin, days=7, student_name="!!!")[1]["item"]["slug"].startswith("s-"), "unusable name falls back to random"

status, body = create(admin, days=7, slug="alice-site")
assert status == 409, "explicit slug that is taken is an error"
assert create(admin, days=7, slug="deploy")[0] == 400, "reserved"
assert create(admin, days=7, slug="Bad_Name")[0] == 400
assert create(admin, days=0)[0] == 400 and create(admin, days=366)[0] == 400 and create(admin, days="7")[0] == 400
assert create(admin, student_name="x")[0] == 400

# ---- list ----
status, body = call("GET", "/api/admin/tokens", session=admin)
assert status == 200 and len(body["tokens"]) == 5
assert "token" not in body["tokens"][0] and "pk" not in body["tokens"][0]

# ---- student login and session ----
assert call("POST", "/api/student/login", {"token": "mds_wrong"})[0] == 401
assert call("POST", "/api/student/login", {})[0] == 401
status, body = call("POST", "/api/student/login", {"token": f"  {alice_token}\n"})
assert status == 200 and body["token"]["slug"] == "alice-site" and body["token"]["can_deploy"] is True
student = body["session"]
status, body = call("GET", "/api/student/me", session=student)
assert status == 200 and body["token"]["status"] == "active"

# role separation: neither session works on the other role's routes
assert call("GET", "/api/admin/tokens", session=student)[0] == 401, "student session cannot call admin routes"
assert call("GET", "/api/student/me", session=admin)[0] == 401, "teacher session is not a student session"
tampered = student[:-2] + ("AA" if not student.endswith("AA") else "BB")
assert call("GET", "/api/student/me", session=tampered)[0] == 401
assert call("GET", "/api/student/me", session=student, now=T0 + 13 * 3600)[0] == 401, "student session expires after 12h"

# ---- lifecycle: active -> expired (read-only, 3 days) -> offline ----
expires = T0 + 7 * DAY
status, body = call("POST", "/api/student/login", {"token": alice_token}, now=expires + 1)
assert status == 200 and body["token"]["status"] == "expired" and body["token"]["can_deploy"] is False
assert body["token"]["offline_at"] == expires + 3 * DAY
status, body = call("POST", "/api/student/login", {"token": alice_token}, now=expires + 3 * DAY + 1)
assert status == 200 and body["token"]["status"] == "offline", "can still log in to request regeneration"

# ---- extend ----
tid = portal.token_hash(alice_token)
status, body = call("POST", f"/api/admin/tokens/{tid}/extend", {"days": 10}, portal.sign_session("teacher", "teacher", 3600, T0 + DAY), now=T0 + DAY)
assert status == 200 and body["token"]["expires_at"] == expires + 10 * DAY, "extend adds to the current expiry"
now_late = expires + 12 * DAY
status, body = call("POST", f"/api/admin/tokens/{tid}/extend", {"days": 10}, portal.sign_session("teacher", "teacher", 3600, now_late), now_late)
assert body["token"]["expires_at"] == now_late + 10 * DAY, "extend of a lapsed token counts from now"
assert body["token"]["status"] == "active"
assert call("POST", f"/api/admin/tokens/{tid}/extend", {"days": 0}, admin)[0] == 400
assert call("POST", f"/api/admin/tokens/{'0' * 64}/extend", {"days": 5}, admin)[0] == 404

# ---- revoke ----
status, body = call("POST", f"/api/admin/tokens/{tid}/revoke", {}, admin)
assert status == 200 and body["token"]["status"] == "revoked"
assert call("POST", "/api/student/login", {"token": alice_token})[0] == 401, "revoked token cannot log in"
assert call("GET", "/api/student/me", session=student)[0] == 401, "existing student session dies on revoke"
assert call("POST", f"/api/admin/tokens/{tid}/extend", {"days": 5}, admin)[0] == 409, "extend does not un-revoke"

# ---- regeneration: request -> approve / deny ----
_, c = create(admin, days=10, student_name="Dana")
dana_token, did = c["token"], c["item"]["id"]
dana_expires = T0 + 10 * DAY
login_at = lambda now: call("POST", "/api/student/login", {"token": dana_token}, now=now)[1]["session"]  # noqa: E731
admin_at = lambda now: portal.sign_session("teacher", "teacher", 3600, now)  # noqa: E731

early = T0 + 5 * DAY
s_early = login_at(early)
status, body = call("POST", "/api/student/regen-request", {}, s_early, now=early)
assert status == 403, "too early: only the last 3 days"
assert call("GET", "/api/student/me", session=s_early, now=early)[1]["token"]["can_request_regen"] is False

warn = dana_expires - 3 * DAY + 1
s_warn = login_at(warn)
me = call("GET", "/api/student/me", session=s_warn, now=warn)[1]["token"]
assert me["can_request_regen"] is True and me["warn_at"] == dana_expires - 3 * DAY and me["status"] == "active"
assert call("POST", "/api/student/regen-request", {"note": "x" * 301}, s_warn, now=warn)[0] == 400
status, body = call("POST", "/api/student/regen-request", {"note": "need more time"}, s_warn, now=warn)
assert status == 200 and body["token"]["regen"] == {"state": "pending", "requested_at": warn, "note": "need more time"}
assert body["token"]["can_request_regen"] is False
assert call("POST", "/api/student/regen-request", {}, s_warn, now=warn)[0] == 409, "one pending request at a time"

status, body = call("GET", "/api/admin/requests", session=admin_at(warn), now=warn)
assert [r["slug"] for r in body["requests"]] == ["dana"] and body["requests"][0]["regen"]["note"] == "need more time"
assert call("GET", "/api/admin/requests", session=s_warn, now=warn)[0] == 401, "students cannot read the inbox"

# deny -> student can ask again
status, body = call("POST", f"/api/admin/tokens/{did}/deny", {"reason": "not yet"}, admin_at(warn), now=warn)
assert status == 200 and body["token"]["regen"]["state"] == "denied" and body["token"]["regen"]["reason"] == "not yet"
assert call("GET", "/api/admin/requests", session=admin_at(warn), now=warn)[1]["requests"] == []
assert call("POST", f"/api/admin/tokens/{did}/deny", {}, admin_at(warn), now=warn)[0] == 409, "nothing pending anymore"
me = call("GET", "/api/student/me", session=s_warn, now=warn)[1]["token"]
assert me["regen"]["state"] == "denied" and me["can_request_regen"] is True

# request again after expiry (read-only period), approve with a new duration counted from now
late = dana_expires + 2 * DAY
s_late = login_at(late)
assert call("POST", "/api/student/regen-request", {}, s_late, now=late)[0] == 200
assert call("POST", f"/api/admin/tokens/{did}/approve", {"days": 0}, admin_at(late), now=late)[0] == 400
status, body = call("POST", f"/api/admin/tokens/{did}/approve", {"days": 30}, admin_at(late), now=late)
assert status == 200 and body["token"]["expires_at"] == late + 30 * DAY and body["token"]["status"] == "active"
assert body["token"]["regen"]["state"] == "approved" and body["token"]["regen"]["days"] == 30
assert call("POST", f"/api/admin/tokens/{did}/approve", {"days": 5}, admin_at(late), now=late)[0] == 409

# an offline (archived) site is restored on approval
restored = []
portal.restore_site = lambda slug, tenant_id=None: restored.append(slug)
offline = late + 40 * DAY + 4 * DAY
portal.db().update(f"tok#{did}", {"archived": True})
call("POST", "/api/student/regen-request", {}, login_at(offline), now=offline)
status, body = call("POST", f"/api/admin/tokens/{did}/approve", {"days": 7}, admin_at(offline), now=offline)
assert status == 200 and restored == ["dana"] and portal.db().get(f"tok#{did}")["archived"] is False
# extend also restores
portal.db().update(f"tok#{did}", {"archived": True, "expires_at": T0})
assert call("POST", f"/api/admin/tokens/{did}/extend", {"days": 7}, admin_at(offline), now=offline)[0] == 200 and restored == ["dana", "dana"]

# a revoked token cannot request
call("POST", f"/api/admin/tokens/{did}/revoke", {}, admin_at(offline), now=offline)
assert call("POST", "/api/student/login", {"token": dana_token}, now=offline)[0] == 401

# ---- misc ----
assert call("GET", "/api/nope", session=admin)[0] == 404
assert call("GET", "/api/admin/other", session=admin)[0] == 404
bad = portal.handle({"rawPath": "/api/student/login", "requestContext": {"http": {"method": "POST"}}, "body": "{oops"}, site_url, now=T0)
assert bad["statusCode"] == 400
encoded = {"rawPath": "/api/student/login", "requestContext": {"http": {"method": "POST"}}, "isBase64Encoded": True,
           "body": base64.b64encode(json.dumps({"token": "mds_x"}).encode()).decode()}
assert portal.handle(encoded, site_url, now=T0)["statusCode"] == 401, "base64 bodies are decoded"

print("test_portal.py: all passed")
