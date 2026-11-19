"""People on a project (docs/STUDENT_IDENTITY_PLAN.md, project home phase 2): the teacher puts e-mail addresses on a project's card, each person pairs
their own computers, and taking one person off stops only that person. Uses the same in-memory stand-ins as test_access.py."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))
os.environ.setdefault("PORTAL_TABLE", "test")

import access  # noqa: E402
import identity  # noqa: E402
import portal  # noqa: E402

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
addr = lambda local, domain: local + AT + domain  # noqa: E731


def call(method, path, body=None, session=None, now=T0):
    ips[0] += 1
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": f"10.3.{ips[0] // 250}.{ips[0] % 250}"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "correct horse"})[1]["session"]
identity.set_rules({"mode": "request", "domains": ["school.edu"], "portal_url": "https://abc.cloudfront.net"}, T0)


def student(sub, email, name):
    identity.upsert_user(sub, email, name, T0)
    return portal.sign_session("user", sub, 12 * 3600, T0)


ann_mail, zed_mail, new_mail = addr("ann", "school.edu"), addr("zed", "elsewhere.org"), addr("newcomer", "elsewhere.org")
ann = student("sub-ann", ann_mail, "Ann Lee")        # on the domain list: a member
zed = student("sub-zed", zed_mail, "Zed Kay")        # on no list: may only ask
assert call("GET", "/api/student/google/home", session=zed)[1]["level"] == "requester"


def pair(session, site_id, secret, device):
    """A computer pairs itself the way the kit does: a hash goes out, the student types the code, the computer then signs in with its own token."""
    digest = hashlib.sha256(secret.encode()).hexdigest()
    code = call("POST", "/api/student/pair/start", {"hash": digest, "device": device})[1]["code"]
    status, data = call("POST", "/api/student/google/pair", {"code": code, "site": site_id}, session)
    return status, data


def computer(secret):
    status, data = call("POST", "/api/student/login", {"token": secret})
    return status, data.get("session")


# ---------------------------------------------------------------- a project (an old-style site with the teacher's own token)
status, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": "Robot Lab"}, admin)
assert status == 201
pid, universal = made["item"]["id"], made["token"]
assert call("GET", f"/api/admin/tokens/{pid}/people", session=admin)[1] == {"members": [], "invites": [], "limit": access.PEOPLE_PER_PROJECT}
assert call("GET", f"/api/admin/tokens/{pid}/people")[0] == 401 and call("GET", f"/api/admin/tokens/{pid}/people", session=ann)[0] == 401, "teacher only"
print("  ok  a project starts with nobody on it, and only the teacher may see or change who is on it")

# ---------------------------------------------------------------- adding people by e-mail
status, data = call("POST", f"/api/admin/tokens/{pid}/people", {"emails": f" {ann_mail.upper()}, {zed_mail}\n{new_mail}, {ann_mail}"}, admin)
assert status == 200, data
assert data["result"] == {"added": [ann_mail, zed_mail], "invited": [new_mail], "already": [], "refused": []}, data["result"]
assert sorted(m["email"] for m in data["members"]) == sorted([ann_mail, zed_mail]) and data["invites"] == [new_mail]
assert call("GET", "/api/student/google/home", session=zed)[1]["level"] == "member", "putting someone on a project lets them in"
for who in (ann, zed):
    home = call("GET", "/api/student/google/home", session=who)[1]
    assert [s["slug"] for s in home["sites"]] == [made["item"]["slug"]] and home["sites"][0]["people"] == 2
again = call("POST", f"/api/admin/tokens/{pid}/people", {"emails": [ann_mail, new_mail]}, admin)[1]["result"]
assert again["already"] == [ann_mail, new_mail] and again["added"] == again["invited"] == [], "adding twice changes nothing"
for bad in ({"emails": []}, {"emails": ["not-an-address"]}, {"emails": "a b"}, {"emails": 5}, {}, {"emails": [addr("x" * 70, "a.org")]}):
    assert call("POST", f"/api/admin/tokens/{pid}/people", bad, admin)[0] == 400, bad
assert call("POST", f"/api/admin/tokens/{'0' * 64}/people", {"emails": [ann_mail]}, admin)[0] == 404
print("  ok  addresses are added: a person who has signed in becomes a member at once (and is let in), the others are invited; twice does nothing; bad input is refused")

# ---------------------------------------------------------------- each person's own computers and tokens
tok_ann, tok_zed = "mdp_ann_computer_1", "mdp_zed_computer_1"
assert pair(ann, pid, tok_ann, "Ann's laptop")[0] == 200 and pair(zed, pid, tok_zed, "Zed's laptop")[0] == 200
ann_pc, ann_session = computer(tok_ann)
zed_pc, zed_session = computer(tok_zed)
assert ann_pc == 200 and zed_pc == 200
assert call("GET", "/api/student/me", session=ann_session)[0] == 200 and call("GET", "/api/student/me", session=zed_session)[0] == 200
assert call("POST", "/api/student/login", {"token": universal})[0] == 200, "the teacher's own token for the project is unchanged"
for i in range(access.DEVICES_PER_SITE - 1):
    assert pair(ann, pid, f"mdp_ann_extra_{i}", f"Ann {i}")[0] == 200
status, data = pair(ann, pid, "mdp_ann_one_too_many", "Too many")
assert status == 409 and "computers paired" in data["error"]
assert pair(zed, pid, "mdp_zed_computer_2", "Zed's desktop")[0] == 200, "the limit is per person, so Ann's computers do not use up Zed's"
assert {d["site"] for d in call("GET", "/api/student/google/devices", session=zed)[1]["devices"]} == {made["item"]["slug"]}
print("  ok  every person pairs their own computers and has their own token; the teacher's token still works; the limit on computers is per person")

# ---------------------------------------------------------------- taking one person off
status, data = call("POST", f"/api/admin/tokens/{pid}/people/remove", {"email": zed_mail}, admin)
assert status == 200 and [m["email"] for m in data["members"]] == [ann_mail], data
assert call("GET", "/api/student/me", session=zed_session)[0] == 401, "Zed's open session stops at the next request"
assert computer(tok_zed)[0] == 401, "and his token no longer signs in"
assert call("GET", "/api/student/me", session=ann_session)[0] == 200, "Ann is not affected"
assert call("POST", "/api/student/login", {"token": universal})[0] == 200
assert call("GET", "/api/student/google/home", session=zed)[1]["sites"] == []
assert not [c for c in db().list_prefix("cred#") if c["owner"] == "sub-zed"], "his computers are gone, not just blocked"
# removing an invitation, by e-mail; removing someone who is not there is harmless
status, data = call("POST", f"/api/admin/tokens/{pid}/people/remove", {"email": new_mail}, admin)
assert status == 200 and data["invites"] == [] and access.get_invite(new_mail) is None
assert call("POST", f"/api/admin/tokens/{pid}/people/remove", {"email": addr("nobody", "x.org")}, admin)[0] == 200
assert call("POST", f"/api/admin/tokens/{pid}/people/remove", {}, admin)[0] == 400
# put back by e-mail: he is a member again, with no token until he pairs again
assert call("POST", f"/api/admin/tokens/{pid}/people", {"emails": [zed_mail]}, admin)[1]["result"]["added"] == [zed_mail]
assert computer(tok_zed)[0] == 401, "an old token does not come back by itself"
print("  ok  taking a person off stops their computers at once and leaves everybody else (and the teacher's token) alone; invitations can be withdrawn")

# ---------------------------------------------------------------- the others on a project when one account goes
status, made2 = call("POST", "/api/admin/tokens", {"days": 30, "student_name": "Shared"}, admin)
pid2 = made2["item"]["id"]
user_ann = identity.get_user("sub-ann")
personal = access.create_site(user_ann, 30, "team-site", T0)  # a project made for Ann (no token anybody can see)
call("POST", f"/api/admin/tokens/{personal['id']}/people", {"emails": [zed_mail]}, admin)
assert pair(ann, personal["id"], "mdp_p_ann", "A")[0] == 200 and pair(zed, personal["id"], "mdp_p_zed", "Z")[0] == 200
identity.set_user_status("sub-ann", "disabled", T0)
assert computer("mdp_p_ann")[0] == 401, "Ann's account is off, so her computer stops"
assert computer("mdp_p_zed")[0] == 200, "but Zed is still on the project, so it keeps working"
identity.set_user_status("sub-ann", "active", T0)
assert call("POST", f"/api/admin/tokens/{personal['id']}/people/remove", {"sub": "sub-ann"}, admin)[0] == 200
assert db().get("tok#" + personal["id"])["owner_sub"] is None and computer("mdp_p_ann")[0] == 401 and computer("mdp_p_zed")[0] == 200
# a disabled account cannot be put on a project; the project is full at the limit
identity.set_user_status("sub-zed", "disabled", T0)
refused = call("POST", f"/api/admin/tokens/{pid2}/people", {"emails": [zed_mail]}, admin)[1]["result"]["refused"]
assert refused and "turned off" in refused[0]["reason"]
identity.set_user_status("sub-zed", "active", T0)
real = access.PEOPLE_PER_PROJECT
access.PEOPLE_PER_PROJECT = 2
out = call("POST", f"/api/admin/tokens/{pid2}/people", {"emails": [ann_mail, zed_mail, new_mail]}, admin)[1]["result"]
access.PEOPLE_PER_PROJECT = real
assert out["added"] == [ann_mail, zed_mail] and out["refused"][0]["email"] == new_mail and "full" in out["refused"][0]["reason"], out
print("  ok  a project keeps working for the people still on it; an account that is turned off cannot be added; the number of people is limited")

# ---------------------------------------------------------------- erasing an account takes it off every project
call("POST", f"/api/admin/tokens/{pid2}/people", {"emails": [new_mail]}, admin)
status, data = call("POST", "/api/admin/identity/users/sub-ann/erase", {"confirm": "sub-ann"}, admin)
assert status == 200
assert "sub-ann" not in (db().get("tok#" + pid2).get("members") or {}) and "sub-ann" not in (db().get("tok#" + pid).get("members") or {})
print("  ok  erasing an account takes it off every project it was on")

print("test_people.py: people on a project, invitations, own computers, removal pass")
