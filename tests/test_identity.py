"""Unit tests for lambda/identity.py: who may sign in with Google (phase 1a of the student identity plan).

In-memory store, fake clock, no network. Covers: the rules are validated and stored; every mode admits and refuses the right people
(exact domain match, e-mail not verified, look-alike domains, case); student identities are keyed on the account id and a disabled one stays
disabled; the teacher routes need a teacher session and a student session never reaches them."""
import json
import os
import sys

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b",
                  DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
import identity  # noqa: E402
import portal  # noqa: E402

T0 = 1_800_000_000
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("correct horse", salt)),
    "secret": portal.b64(b"0" * 32),
})


def call(method, path, body=None, session=None, now=T0):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=now)
    return result["statusCode"], json.loads(result["body"])


def raises(status, fn, *args):
    try:
        fn(*args)
    except portal.PortalError as e:
        assert e.status == status, (e.status, str(e))
        return str(e)
    raise AssertionError("expected a PortalError")


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"})[1]["session"]

# ---- default: Google sign-in is off, nothing changes for an existing platform ----
assert identity.get_rules()["mode"] == "token-only"
assert identity.admit("a\x40school.edu", True)["level"] == "denied"
assert "token" in identity.admit("a\x40school.edu", True)["reason"]

# ---- rules are validated ----
raises(400, identity.set_rules, {"mode": "everyone"}, T0)
raises(400, identity.set_rules, {"mode": "invite"}, T0)                       # an empty invite list would lock everyone out
raises(400, identity.set_rules, {"mode": "domain"}, T0)
raises(400, identity.set_rules, {"emails": ["not-an-email"]}, T0)
raises(400, identity.set_rules, {"emails": "a\x40b.co"}, T0)                      # must be a list
raises(400, identity.set_rules, {"domains": ["school.edu/evil"]}, T0)
raises(400, identity.set_rules, {"domains": ["\x40school.edu"]}, T0)
raises(400, identity.set_rules, {"domains": ["localhost"]}, T0)
raises(400, identity.set_rules, {"emails": [f"u{i}\x40school.edu" for i in range(identity.MAX_EMAILS + 1)]}, T0)
raises(400, identity.set_rules, {"domains": [f"d{i}.edu" for i in range(identity.MAX_DOMAINS + 1)]}, T0)
assert identity.get_rules()["mode"] == "token-only", "a refused change stores nothing"

# ---- invite: only the listed addresses ----
rules = identity.set_rules({"mode": "invite", "emails": [" Ann\x40School.EDU ", "ann\x40school.edu", "bob\x40gmail.com"], "domains": ["school.edu"]}, T0)
assert rules["emails"] == ["ann\x40school.edu", "bob\x40gmail.com"], "trimmed, lower-cased, no duplicates"
assert identity.admit("ANN\x40school.edu", True)["level"] == "member"
assert identity.admit("bob\x40gmail.com", True)["level"] == "member"
assert identity.admit("carl\x40school.edu", True)["level"] == "denied", "invite mode ignores the domain list"
assert identity.admit("ann\x40school.edu", False)["level"] == "denied", "an unverified address never counts"
for odd in ("true", 1, None, "yes"):
    assert identity.admit("ann\x40school.edu", odd)["level"] == "denied", odd

# ---- domain: listed addresses and the exact domain ----
identity.set_rules({"mode": "domain", "emails": ["bob\x40gmail.com"], "domains": ["school.edu"]}, T0)
assert identity.admit("carl\x40school.edu", True)["level"] == "member"
assert identity.admit("bob\x40gmail.com", True)["level"] == "member"
assert identity.admit("dan\x40gmail.com", True)["level"] == "denied"
for look_alike in ("x\x40evilschool.edu", "x\x40school.edu.evil.com", "x\x40sub.school.edu", "x\x40school.edu\x40evil.com", "school.edu", "\x40school.edu", ""):
    assert identity.admit(look_alike, True)["level"] == "denied", look_alike
assert identity.admit("a\x40b\x40school.edu", True)["level"] == "denied"

# ---- request: listed people are members, any other verified account may ask ----
identity.set_rules({"mode": "request"}, T0)
assert identity.admit("carl\x40school.edu", True)["level"] == "member"
assert identity.admit("zed\x40anywhere.org", True)["level"] == "requester"
assert identity.admit("zed\x40anywhere.org", False)["level"] == "denied"
assert identity.admit("", True)["level"] == "denied"

# ---- identities: keyed on the account id, never on the e-mail ----
raises(400, identity.user_key, "")
raises(400, identity.user_key, "../x")
raises(400, identity.user_key, "a b")
raises(400, identity.user_key, None)
u = identity.upsert_user("sub-1", "Ann\x40School.edu", "Ann\x00 Lee\n", T0)
assert u["pk"] == "usr#sub-1" and u["email"] == "ann\x40school.edu" and u["name"] == "Ann  Lee" and u["status"] == "active"
assert u["first_seen"] == T0
u = identity.upsert_user("sub-1", "ann.new\x40school.edu", "Ann", T0 + 50)
assert u["email"] == "ann.new\x40school.edu" and u["first_seen"] == T0 and u["last_seen"] == T0 + 50, "the e-mail changed, the identity did not"
assert len(identity.list_users()) == 1
identity.upsert_user("sub-2", "bob\x40gmail.com", "Bob", T0 + 60)
assert [x["sub"] for x in identity.list_users()] == ["sub-2", "sub-1"], "newest sign-in first"
identity.set_user_status("sub-1", "disabled", T0 + 100)
u = identity.upsert_user("sub-1", "ann.new\x40school.edu", "Ann", T0 + 200)
assert u["status"] == "disabled", "a sign-in never re-enables a disabled student"
raises(404, identity.set_user_status, "nobody", "disabled", T0)
raises(400, identity.set_user_status, "sub-1", "banana", T0)
identity.set_user_status("sub-1", "active", T0 + 300)
assert identity.get_user("sub-1")["status"] == "active"

# ---- teacher routes ----
assert call("GET", "/api/admin/identity")[0] == 401
assert call("GET", "/api/admin/identity", session="garbage")[0] == 401
assert call("GET", "/api/admin/identity/users")[0] == 401
assert call("POST", "/api/admin/identity/users/sub-1/disable")[0] == 401
# a student session must not open them
status, made = call("POST", "/api/admin/tokens", {"days": 5, "student_name": "Kim"}, admin)
assert status == 201
student = call("POST", "/api/student/login", {"token": made["token"]})[1]["session"]
for method, path in (("GET", "/api/admin/identity"), ("POST", "/api/admin/identity"), ("GET", "/api/admin/identity/users"),
                     ("POST", "/api/admin/identity/users/sub-1/disable")):
    assert call(method, path, {}, student)[0] == 401, path

status, data = call("GET", "/api/admin/identity", session=admin)
assert status == 200 and data["rules"]["mode"] == "request" and data["modes"] == list(identity.MODES)
assert data["limits"] == {"emails": identity.MAX_EMAILS, "domains": identity.MAX_DOMAINS}
import auth  # noqa: E402
made_clients = []
auth.ensure_portal_client = lambda url: made_clients.append(url)  # the real one talks to Cognito; test_google_signin.py covers it
# turning Google sign-in on needs the portal address, and the sign-in client is made before the rules are stored
status, data = call("POST", "/api/admin/identity", {"mode": "domain", "domains": ["School.edu"], "emails": []}, admin)
assert status == 400 and "portal_url" in data["error"] and not made_clients and identity.get_rules()["mode"] == "request"
for bad_url in ("http://x.cloudfront.net", "https://evil.example.com", "https://abc.cloudfront.net/path", "https://x.cloudfront.net.evil.com", "javascript:alert(1)"):
    assert call("POST", "/api/admin/identity", {"mode": "domain", "domains": ["school.edu"], "portal_url": bad_url}, admin)[0] == 400, bad_url
assert not made_clients
status, data = call("POST", "/api/admin/identity", {"mode": "domain", "domains": ["School.edu"], "emails": [], "portal_url": "https://abc123.cloudfront.net/"}, admin)
assert status == 200 and data["rules"]["domains"] == ["school.edu"] and data["rules"]["mode"] == "domain"
assert data["rules"]["portal_url"] == "https://abc123.cloudfront.net" and made_clients == ["https://abc123.cloudfront.net"]
# a later change reuses the stored address; an invalid change makes no client and stores nothing
assert call("POST", "/api/admin/identity", {"mode": "invite", "emails": []}, admin)[0] == 400 and len(made_clients) == 1
assert call("POST", "/api/admin/identity", {"mode": "request"}, admin)[0] == 200 and len(made_clients) == 2
# turning it off needs no portal address and makes no client
assert call("POST", "/api/admin/identity", {"mode": "token-only"}, admin)[0] == 200 and len(made_clients) == 2
assert call("POST", "/api/admin/identity", {"mode": "domain"}, admin)[0] == 200
assert call("POST", "/api/admin/identity", {"mode": "bogus"}, admin)[0] == 400
assert call("PUT", "/api/admin/identity", {}, admin)[0] == 404
status, data = call("GET", "/api/admin/identity/users", session=admin)
assert status == 200 and [x["sub"] for x in data["users"]] == ["sub-1", "sub-2"]
assert set(data["users"][0]) == {"sub", "email", "name", "status", "first_seen", "last_seen", "approved"}, "nothing else leaves the server"
status, data = call("POST", "/api/admin/identity/users/sub-1/disable", {}, admin)
assert status == 200 and data["user"]["status"] == "disabled"
assert call("POST", "/api/admin/identity/users/sub-1/enable", {}, admin)[1]["user"]["status"] == "active"
assert call("POST", "/api/admin/identity/users/nobody/disable", {}, admin)[0] == 404
assert call("POST", "/api/admin/identity/users/a%2Fb/disable", {}, admin)[0] == 404
assert call("GET", "/api/admin/identity/nothing", session=admin)[0] == 404

# ---- the token login is untouched by any of this ----
assert call("POST", "/api/student/login", {"token": made["token"]})[0] == 200
assert call("GET", "/api/admin/tokens", session=admin)[0] == 200

print("  ok  identity: admission rules (token-only, invite, domain, request), exact domain match, unverified e-mail refused, identities keyed on the account id, disabled stays disabled, teacher-only routes")
