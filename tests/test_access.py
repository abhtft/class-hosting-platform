"""Tests for lambda/access.py: the student's home, access requests with the teacher's decision, and personal tokens for paired computers
(phases 2 to 4 of the student identity plan). In-memory store, fake clock, no network.

The sign-in with Google itself is covered by test_google_signin.py; here the students already have a Google session (made the same way the
platform makes it), and what is checked is what they can see and ask for, what the teacher can answer, and what a paired computer can do."""
import hashlib
import json
import os
import sys

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b",
                  DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
import access  # noqa: E402
import identity  # noqa: E402
import portal  # noqa: E402

DAY = portal.DAY
T0 = 1_800_000_000
AT = chr(64)
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("correct horse", salt)),
    "secret": portal.b64(b"0" * 32),
})
db = portal.db
ips = [0]


def addr(local, domain):
    return local + AT + domain


def call(method, path, body=None, session=None, now=T0, ip=None):
    ips[0] += 1
    ip = ip or f"10.2.{ips[0] // 250}.{ips[0] % 250}"
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": ip}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"})[1]["session"]
identity.set_rules({"mode": "request", "domains": ["school.edu"], "portal_url": "https://abc.cloudfront.net"}, T0)


def student(sub, email, name):
    identity.upsert_user(sub, email, name, T0)
    return portal.sign_session("user", sub, 12 * 3600, T0)


ann = student("sub-ann", addr("ann", "school.edu"), "Ann Lee")        # on the domain list: a member
bob = student("sub-bob", addr("bob", "school.edu"), "Bob Roy")
zed = student("sub-zed", addr("zed", "elsewhere.org"), "Zed Kay")      # nobody listed him: he may only ask
SITE = {"kind": "new_site", "days": 30, "note": "For the science fair"}

# ---------------------------------------------------------------- the home
status, home = call("GET", "/api/student/google/home", session=ann)
assert status == 200 and home["level"] == "member" and home["sites"] == [] and home["projects"] == [] and home["requests"] == []
status, zhome = call("GET", "/api/student/google/home", session=zed)
assert zhome["level"] == "requester" and zhome["projects"] == [], "a stranger sees no class projects"
assert call("GET", "/api/student/google/home")[0] == 401 and call("GET", "/api/student/google/home", session="junk")[0] == 401
assert call("GET", "/api/student/google/home", session=ann, now=T0 + 13 * 3600)[0] == 401
print("  ok  the home: a member sees sites, class projects and requests; a stranger sees only their own requests; no session, no home")

# ---------------------------------------------------------------- asking for a site
for bad, fragment in (({"kind": "x"}, "kind"), ({"kind": "new_site"}, "days"), ({"kind": "new_site", "days": 0}, "days"),
                      ({"kind": "new_site", "days": 99999}, "days"), ({"kind": "new_site", "days": True}, "days"),
                      ({**SITE, "slug": "Bad Name!"}, "site name"), ({**SITE, "slug": "api"}, "site name"),
                      ({**SITE, "note": 5}, "note"), ({**SITE, "features": []}, "features"),
                      ({**SITE, "features": {"root": {"enabled": True}}}, "Unknown feature"),
                      ({**SITE, "features": {"ai": {"api_key": "x"}}}, "not something"),
                      ({**SITE, "features": {"ai": {"limit_usd": -1}}}, "not allowed"),
                      ({**SITE, "features": {"ai": {"model": "a b; drop"}}}, "not allowed"),
                      ({**SITE, "features": {"ai": {"limit_usd": float("nan")}}}, "not allowed"),
                      ({**SITE, "features": {"ai": "yes"}}, "must be an object")):
    status, data = call("POST", "/api/student/google/requests", bad, zed)
    assert status == 400 and fragment in data["error"], (bad, status, data)
want = {**SITE, "slug": "zed-lab", "features": {"backend": {"enabled": True, "runs_limit": 500, "runs_period": "day"}, "ai": {"enabled": True, "limit_usd": 1.5}}}
status, data = call("POST", "/api/student/google/requests", want, zed)
assert status == 201 and data["request"]["state"] == "pending" and data["request"]["requested"]["features"]["ai"]["limit_usd"] == 1.5
zed_request = data["request"]["id"]
assert "requester" not in data["request"], "a student's own view carries no account details of others"
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot"}, zed)[0] == 403, "a stranger cannot ask to join a project"
# the limit on waiting requests
for i in range(2):
    assert call("POST", "/api/student/google/requests", SITE, zed)[0] == 201
status, data = call("POST", "/api/student/google/requests", SITE, zed)
assert status == 429 and str(access.PENDING_PER_STUDENT) in data["error"]
# another student cannot withdraw or see it
other = call("GET", "/api/student/google/home", session=ann)[1]
assert other["requests"] == []
assert call("POST", f"/api/student/google/requests/{zed_request}/withdraw", {}, ann)[0] == 404
extra = call("GET", "/api/student/google/home", session=zed)[1]["requests"]
spare = [r["id"] for r in extra if r["id"] != zed_request][0]
assert call("POST", f"/api/student/google/requests/{spare}/withdraw", {}, zed)[1]["request"]["state"] == "withdrawn"
assert call("POST", f"/api/student/google/requests/{spare}/withdraw", {}, zed)[0] == 409
assert call("POST", "/api/student/google/requests/zzzzzzzzzzzz/withdraw", {}, zed)[0] == 404
# a day's limit
for r in extra:
    if r["id"] not in (zed_request, spare):
        call("POST", f"/api/student/google/requests/{r['id']}/withdraw", {}, zed)
print("  ok  asking for a site: every odd value refused, the student's waiting-request limit, withdrawing is only for the owner")

# ---------------------------------------------------------------- the teacher's inbox and decisions
assert call("GET", "/api/admin/access-requests")[0] == 401
assert call("GET", "/api/admin/access-requests", session=zed)[0] == 401
status, inbox = call("GET", "/api/admin/access-requests", session=admin)
assert status == 200 and inbox["open"] == 1 and inbox["requests"][0]["requester"]["email"] == addr("zed", "elsewhere.org")
assert inbox["requests"][0]["id"] == zed_request
for bad in ({}, {"action": "maybe"}, {"action": "modify"}, {"action": "accept", "note": 7}):
    assert call("POST", f"/api/admin/access-requests/{zed_request}/decide", bad, admin)[0] == 400, bad
assert call("POST", "/api/admin/access-requests/000000000000/decide", {"action": "accept"}, admin)[0] == 404
assert call("POST", f"/api/admin/access-requests/{zed_request}/decide", {"action": "accept"}, zed)[0] == 401

# the platform here has no backend or AI connector: granting those fails plainly, the request goes back to waiting, and the site made is kept
status, data = call("POST", f"/api/admin/access-requests/{zed_request}/decide", {"action": "accept"}, admin)
assert status in (400, 503) and "backend" in data["error"], data
after = access.get_request(zed_request)
assert after["state"] == "pending" and after["error"] and after["site_id"], "back to waiting, with the reason and the site it made"
first_site = after["site_id"]
assert len(identity.tokens_of("sub-zed")) == 1
# the teacher gives something else instead: same site, no second one
status, data = call("POST", f"/api/admin/access-requests/{zed_request}/decide",
                    {"action": "modify", "granted": {"days": 10, "slug": "zed-lab", "features": {}}, "note": "Start small"}, admin)
assert status == 200 and data["request"]["state"] == "approved" and data["request"]["granted"]["days"] == 10 and data["request"]["teacher_note"] == "Start small"
assert [t["id"] for t in identity.tokens_of("sub-zed")] == [first_site], "asking again never makes a second site"
site = db().get("tok#" + first_site)
assert site["kind"] == "personal" and site["owner_sub"] == "sub-zed" and site["slug"] == "zed-lab" and site["revoked"] is False
assert site["expires_at"] == T0 + 30 * DAY or site["expires_at"] == T0 + 10 * DAY
assert db().get("usr#sub-zed")["granted"] is True
assert call("POST", f"/api/admin/access-requests/{zed_request}/decide", {"action": "accept"}, admin)[0] == 409, "decided once"
status, zhome = call("GET", "/api/student/google/home", session=zed)
assert zhome["level"] == "member" and [s["slug"] for s in zhome["sites"]] == ["zed-lab"] and zhome["sites"][0]["kind"] == "personal"
mine_now = next(r for r in zhome["requests"] if r["id"] == zed_request)
assert mine_now["state"] == "approved" and mine_now["granted"]["days"] == 10
assert "owner_sub" not in json.dumps(zhome)
# the teacher's own token list shows it, marked personal
tokens = call("GET", "/api/admin/tokens", session=admin)[1]["tokens"]
assert [t["kind"] for t in tokens if t["slug"] == "zed-lab"] == ["personal"] and [t["linked"] for t in tokens if t["slug"] == "zed-lab"] == [True]
print("  ok  the teacher's decision: a failed grant goes back to waiting with the reason and keeps the site, a changed grant finishes it, once only; an approved stranger becomes a member")

# the features go through the teacher's own functions, in a safe order, with exactly the values asked
calls = []
fake = {name: (lambda n: lambda i, b: calls.append((n, dict(b))) or i)(name) for name in access.FEATURES}
status, data = call("POST", "/api/student/google/requests", {**SITE, "slug": "bob-lab", "features": want["features"] | {"storage": {"enabled": True, "data_mb": 20}}}, bob)
rid = data["request"]["id"]
done = access.decide(rid, {"action": "accept"}, T0, site_url, appliers=fake)
assert done["state"] == "approved"
assert [c[0] for c in calls] == ["backend", "storage", "ai"], calls
assert calls[0][1] == {"enabled": True, "runs_limit": 500, "runs_period": "day"} and calls[2][1] == {"enabled": True, "limit_usd": 1.5}
# a rejection grants nothing
status, data = call("POST", "/api/student/google/requests", SITE, bob)
rid = data["request"]["id"]
status, data = call("POST", f"/api/admin/access-requests/{rid}/decide", {"action": "reject", "note": "Not this term"}, admin)
assert status == 200 and data["request"]["state"] == "rejected" and data["request"]["teacher_note"] == "Not this term"
assert len(identity.tokens_of("sub-bob")) == 1
assert call("POST", f"/api/student/google/requests/{rid}/withdraw", {}, bob)[0] == 409
# a disabled student cannot be granted anything
status, data = call("POST", "/api/student/google/requests", SITE, bob)
rid = data["request"]["id"]
identity.set_user_status("sub-bob", "disabled", T0)
assert call("POST", f"/api/admin/access-requests/{rid}/decide", {"action": "accept"}, admin)[0] == 409
assert call("GET", "/api/student/google/home", session=bob)[0] == 401
identity.set_user_status("sub-bob", "active", T0)
call("POST", f"/api/admin/access-requests/{rid}/decide", {"action": "reject"}, admin)
print("  ok  grants use the teacher's own functions in order with exactly the values asked; a rejection grants nothing; a disabled student gets nothing")

# the account limits
for i in range(access.SITES_PER_STUDENT):
    status, data = call("POST", "/api/student/google/requests", SITE, ann)
    if status != 201:
        break
    access.decide(data["request"]["id"], {"action": "accept"}, T0, site_url)
status, data = call("POST", "/api/student/google/requests", SITE, ann)
assert status == 409 and "at most" in data["error"]
assert len(identity.tokens_of("sub-ann")) == access.SITES_PER_STUDENT

# ---------------------------------------------------------------- class projects
for name, listed in (("robot", True), ("secret", False)):
    status, data = call("POST", "/api/admin/projects", {"id": name, "title": name.title(), "catalogue": listed, "members": []}, admin)
    assert status == 201 and data["project"]["catalogue"] is listed
status, home = call("GET", "/api/student/google/home", session=ann)
assert [(p["id"], p["state"]) for p in home["projects"]] == [("robot", "none")], "red: listed, not a member; the unlisted project is not shown"
many = [s["id"] for s in home["sites"]]
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "secret"}, ann)[0] == 404
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "nope"}, ann)[0] == 404
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot"}, ann)[0] == 400, "which site? (she has several)"
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot", "site": "f" * 64}, ann)[0] == 400
other_site = identity.tokens_of("sub-zed")[0]["id"]
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot", "site": other_site}, ann)[0] == 400, "not her site"
status, data = call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot", "site": many[0]}, ann)
assert status == 201
rid = data["request"]["id"]
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot", "site": many[0]}, ann)[0] == 409
assert [(p["state"], p["request"]) for p in call("GET", "/api/student/google/home", session=ann)[1]["projects"]] == [("pending", rid)]
status, data = call("POST", f"/api/admin/access-requests/{rid}/decide", {"action": "accept"}, admin)
assert status == 200 and data["request"]["state"] == "approved"
slug = db().get("tok#" + many[0])["slug"]
assert slug in db().get("proj#t#robot")["members"]
proj = call("GET", "/api/student/google/home", session=ann)[1]["projects"][0]
assert proj["state"] == "member" and proj["sites"] == [slug]
assert call("POST", "/api/student/google/requests", {"kind": "join_project", "project": "robot", "site": many[0]}, ann)[0] == 409, "already in"
# the existing project sync now works for that site (it reads the same member list)
import projects  # noqa: E402
assert projects.for_student(db().get("tok#" + many[0]), "robot", T0)["name"] == "robot"
print("  ok  class projects: listed ones show red until joined, an unlisted one stays hidden, a join needs one of the student's own sites, the teacher's yes adds the site to the member list")

# ---------------------------------------------------------------- personal tokens: pairing a computer
site_id = many[0]
secret = "mdp_" + "k" * 40
digest = hashlib.sha256(secret.encode()).hexdigest()
status, started = call("POST", "/api/student/pair/start", {"hash": digest, "device": "Ann's laptop"})
assert status == 200 and len(started["code"]) == 9 and started["code"][4] == "-", started
code = started["code"]
assert call("POST", "/api/student/pair/start", {"hash": "nothex"})[0] == 400
assert call("POST", "/api/student/pair/poll", {"code": code, "hash": digest})[1] == {"state": "pending"}
assert call("POST", "/api/student/pair/poll", {"code": code, "hash": "0" * 64})[1] == {"state": "unknown"}, "a guess at the code alone learns nothing"
assert call("POST", "/api/student/pair/poll", {"code": "ABCD-EFGH", "hash": digest})[1] == {"state": "unknown"}
assert call("POST", "/api/student/login", {"token": secret})[0] == 401, "not yet approved: no sign-in"
assert call("GET", f"/api/student/google/pair/{code}", session=ann)[1]["device"] == "Ann's laptop"
assert call("GET", f"/api/student/google/pair/{code}")[0] == 401
# approving needs the signed-in student, the right code and one of their own sites
assert call("POST", "/api/student/google/pair", {"code": code, "site": site_id}, bob)[0] == 400, "Bob has no such site"
assert call("POST", "/api/student/google/pair", {"code": "AAAA-AAAA", "site": site_id}, ann)[0] == 404
assert call("POST", "/api/student/google/pair", {"code": "short", "site": site_id}, ann)[0] == 400
assert call("POST", "/api/student/google/pair", {"code": code}, ann)[0] == 400, "which site?"
assert call("POST", "/api/student/google/pair", {"code": code, "site": site_id}, ann, now=T0 + 11 * 60)[0] == 404, "the code expires"
status, data = call("POST", "/api/student/google/pair", {"code": code.lower().replace("-", " "), "site": site_id}, ann)
assert status == 200 and data["site"] == slug and data["expires_at"] == T0 + access.PERSONAL_DAYS * DAY
assert call("POST", "/api/student/google/pair", {"code": code, "site": site_id}, ann)[0] == 409, "a code is used once"
assert call("POST", "/api/student/pair/poll", {"code": code, "hash": digest})[1] == {"state": "approved"}
# the computer signs in with its own token and has the site's powers
status, login = call("POST", "/api/student/login", {"token": secret})
assert status == 200 and login["token"]["slug"] == slug
personal = login["session"]
assert call("GET", "/api/student/me", session=personal)[1]["token"]["slug"] == slug
assert "." in portal.verify_session("student", {"headers": {"authorization": f"Bearer {personal}"}}, T0), "the session names the paired computer"
assert call("GET", "/api/student/projects", session=personal)[0] in (200, 503), "project sync sees the site"
assert call("POST", "/api/student/login", {"token": secret + "x"})[0] == 401
# the person can see and sign out their computers
devices = call("GET", "/api/student/google/devices", session=ann)[1]["devices"]
assert len(devices) == 1 and devices[0]["device"] == "Ann's laptop" and devices[0]["site"] == slug and not devices[0]["expired"]
assert call("GET", "/api/student/google/devices", session=bob)[1]["devices"] == [], "another student's computers are not visible"
cid = devices[0]["id"]
assert call("POST", f"/api/student/google/devices/{cid}/revoke", {}, bob)[0] == 404, "nor can they be signed out by someone else"
assert call("POST", f"/api/student/google/devices/{'0' * 32}/revoke", {}, ann)[0] == 404
assert call("POST", f"/api/student/google/devices/{cid}/revoke", {}, ann)[0] == 200
assert call("GET", "/api/student/me", session=personal)[0] == 401, "signing a computer out stops its open session at once"
assert call("POST", "/api/student/login", {"token": secret})[0] == 401
print("  ok  pairing a computer: the code is typed by the signed-in student, used once, expires; the computer never sends its token; its session stops the moment it is signed out")

# a computer's sign-in ends with its pairing, and with the account
secret2 = "mdp_" + "j" * 40
d2 = hashlib.sha256(secret2.encode()).hexdigest()
code2 = call("POST", "/api/student/pair/start", {"hash": d2, "device": "Lab PC"})[1]["code"]
assert call("POST", "/api/student/google/pair", {"code": code2, "site": site_id}, ann)[0] == 200
later = T0 + access.PERSONAL_DAYS * DAY
assert call("POST", "/api/student/login", {"token": secret2}, now=later - 10)[0] == 200
assert call("POST", "/api/student/login", {"token": secret2}, now=later + 1)[0] == 401, "the pairing ends after the personal token days"
session_short = call("POST", "/api/student/login", {"token": secret2}, now=later - 100)[1]["session"]
assert call("GET", "/api/student/me", session=session_short, now=later - 50)[0] == 200
assert call("GET", "/api/student/me", session=session_short, now=later + 5)[0] == 401, "a session never outlives the pairing"
open_session = call("POST", "/api/student/login", {"token": secret2})[1]["session"]
identity.set_user_status("sub-ann", "disabled", T0)
assert call("GET", "/api/student/me", session=open_session)[0] == 401, "turning the account off stops its computers at once"
assert call("POST", "/api/student/login", {"token": secret2})[0] == 401
identity.set_user_status("sub-ann", "active", T0)
assert call("GET", "/api/student/me", session=open_session)[0] == 200
# Google sign-in turned off: personal tokens stop, the teacher's own tokens do not
identity.set_rules({"mode": "token-only"}, T0)
assert call("GET", "/api/student/me", session=open_session)[0] == 401 and call("POST", "/api/student/login", {"token": secret2})[0] == 401
assert call("POST", "/api/student/pair/start", {"hash": d2})[0] == 403
identity.set_rules({"mode": "request"}, T0)
assert call("GET", "/api/student/me", session=open_session)[0] == 200
# a revoked site stops its computers; the cap on computers per site
status, token_made = call("POST", "/api/admin/tokens", {"days": 5, "student_name": "Old Style"}, admin)
assert call("POST", "/api/student/login", {"token": token_made["token"]})[0] == 200, "the teacher's own tokens are untouched"
for i in range(access.DEVICES_PER_SITE):
    h = hashlib.sha256(f"mdp_{i}".encode()).hexdigest()
    c = call("POST", "/api/student/pair/start", {"hash": h, "device": f"PC {i}"})[1]["code"]
    status, data = call("POST", "/api/student/google/pair", {"code": c, "site": site_id}, ann)
    if status != 200:
        assert status == 409 and "computers paired" in data["error"] and "Sign one out" in data["error"], data
        break
else:
    c = call("POST", "/api/student/pair/start", {"hash": "a" * 64, "device": "One too many"})[1]["code"]
    assert call("POST", "/api/student/google/pair", {"code": c, "site": site_id}, ann)[0] == 409
out = call("POST", "/api/student/google/sign-out-everywhere", {}, ann)[1]
assert out["revoked"] >= access.DEVICES_PER_SITE and call("GET", "/api/student/google/devices", session=ann)[1]["devices"] == []
assert call("GET", "/api/student/me", session=open_session)[0] == 401
db().update("tok#" + site_id, {"revoked": True})
c = call("POST", "/api/student/pair/start", {"hash": "b" * 64})[1]["code"]
assert call("POST", "/api/student/google/pair", {"code": c, "site": site_id}, ann)[0] == 409, "a revoked site cannot get a computer"
db().update("tok#" + site_id, {"revoked": False})
print("  ok  a computer's sign-in ends with its pairing, the account being turned off or Google sign-in being off, and a revoked site; the number of computers is limited; sign out everywhere")

# the code reaches the open internet: it must not help a guesser
for i in range(access.identity.SIGNIN_PER_MINUTE + 2):
    status, _ = call("POST", "/api/student/pair/start", {"hash": digest}, ip="9.9.9.9")
assert status == 429, "starting pairings is limited per network address"
status, _ = call("POST", "/api/student/pair/start", {"hash": digest}, ip="9.9.9.10")
assert status == 200
assert call("GET", "/api/student/pair/start")[0] == 404

# ---------------------------------------------------------------- linking an old token, then erasing the identity
linked_session = ann
status, data = call("POST", "/api/student/google/link", {"token": token_made["token"]}, linked_session)
assert status == 200
assert call("POST", "/api/student/login", {"token": token_made["token"]})[0] == 200
status, data = call("POST", "/api/admin/identity/users/sub-ann/erase", {}, admin)
assert status == 400, "erasing needs the account id typed back"
status, data = call("POST", "/api/admin/identity/users/sub-ann/erase", {"confirm": "sub-ann"}, admin)
assert status == 200 and data["erased"]["sites_unlinked"] == access.SITES_PER_STUDENT + 1 and data["erased"]["requests"] >= 2
assert db().get("usr#sub-ann") is None and identity.tokens_of("sub-ann") == []
assert call("GET", "/api/student/google/home", session=ann)[0] == 401, "an erased account has no session"
assert call("POST", "/api/student/login", {"token": token_made["token"]})[0] == 200, "an old-style token keeps working, only unlinked"
assert db().get("tok#" + site_id)["revoked"] is True, "a site that existed only for the account is switched off, not deleted"
assert call("POST", "/api/admin/identity/users/sub-ann/erase", {"confirm": "sub-ann"}, admin)[0] == 404
assert call("POST", "/api/admin/identity/users/sub-bob/erase", {"confirm": "sub-bob"}, ann)[0] == 401
print("  ok  linking an old token, and erasing an account: its requests, computers and record go; its sites are unlinked (personal ones switched off, nothing deleted)")

# ---------------------------------------------------------------- review fixes
# a decision cut off half way (the function timed out) is put back to waiting after five minutes, and the site it made is reused
status, data = call("POST", "/api/student/google/requests", {**SITE, "slug": "bob-two"}, bob)
stuck = data["request"]["id"]
db().update("req#" + stuck, {"state": "approving", "approving_at": T0})
status, inbox = call("GET", "/api/admin/access-requests", session=admin, now=T0 + 400)
stale = next(r for r in inbox["requests"] if r["id"] == stuck)
assert stale["state"] == "pending" and "interrupted" in stale["error"]
assert call("POST", f"/api/admin/access-requests/{stuck}/decide", {"action": "reject"}, admin, now=T0 + 400)[0] == 200

# a teacher's own token that a student linked keeps working whatever the Google settings are: turning Google off brings the platform back as it was
status, legacy = call("POST", "/api/admin/tokens", {"days": 5, "student_name": "Legacy"}, admin)
assert call("POST", "/api/student/google/link", {"token": legacy["token"]}, bob)[0] == 200
identity.set_user_status("sub-bob", "disabled", T0)
assert call("POST", "/api/student/login", {"token": legacy["token"]})[0] == 200, "an account turned off does not lock out a token the teacher made"
identity.set_user_status("sub-bob", "active", T0)
identity.set_rules({"mode": "token-only"}, T0)
assert call("POST", "/api/student/login", {"token": legacy["token"]})[0] == 200
identity.set_rules({"mode": "request"}, T0)

# a computer polling patiently is never counted against the network address (a whole class may wait behind one school network)
for i in range(identity.SIGNIN_PER_MINUTE * 3):
    status, _ = call("POST", "/api/student/pair/poll", {"code": "ABCD-EFGH", "hash": "0" * 64}, ip="8.8.4.4")
    assert status == 200
print("  ok  review fixes: an interrupted decision is put back to waiting; a linked teacher token is never locked by Google settings; polling is not counted")

print("test_access.py: home, requests and decisions, class projects, paired computers, erase pass")
