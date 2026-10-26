"""Custom domains: teacher switch, student setup state machine, removal, uniqueness, cache invalidation, sweeper."""
import json
import os
import sys
import time

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", TENANT_DISTRIBUTION_ID="EMT",
                  ROUTING_ENDPOINT="route.cloudfront.net")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import app  # noqa: E402
import domains  # noqa: E402
import portal  # noqa: E402
from fakes import FakeS3, FakeTenantCloudFront  # noqa: E402

site_url = app.site_url
salt = b"saltsaltsaltsalt"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"2" * 32)})
cf = domains._client = FakeTenantCloudFront()
app.cloudfront = cf
app.s3 = FakeS3()
now = int(time.time())


def call(method, path, body=None, session=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=now)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]


def new_student(name, days=30):
    _, made = call("POST", "/api/admin/tokens", {"days": days, "student_name": name}, admin)
    session = call("POST", "/api/student/login", {"token": made["token"]})[1]["session"]
    return made["item"]["id"], session


def domain_of(session):
    return call("GET", "/api/student/me", session=session)[1]["token"]["custom_domain"]


def switch(token_id, enabled):
    return call("POST", f"/api/admin/tokens/{token_id}/domain-access", {"enabled": enabled}, admin)


alice_id, alice = new_student("alice")
bob_id, bob = new_student("bob")

# ---- switch off by default: the server refuses and the UI shows the owner message ----
status, body = call("PUT", "/api/student/domain", {"domain": "www.alice.com"}, alice)
assert status == 403 and body["error"] == domains.NOT_ENABLED == "Owner not enabled for you. Please contact site owner.", (status, body)
assert domain_of(alice) == {"available": True, "enabled": False, "domain": "", "state": "none", "message": "",
                            "cname_target": "route.cloudfront.net", "site_url": ""}

# ---- only the teacher can flip the switch ----
assert call("POST", f"/api/admin/tokens/{alice_id}/domain-access", {"enabled": True}, alice)[0] == 401
assert call("POST", f"/api/admin/tokens/{alice_id}/domain-access", {"enabled": "yes"}, admin)[0] == 400
status, body = switch(alice_id, True)
assert status == 200 and body["token"]["custom_domain"]["enabled"] is True
listed = {t["slug"]: t for t in call("GET", "/api/admin/tokens", session=admin)[1]["tokens"]}
assert listed["alice"]["custom_domain"]["enabled"] is True and listed["bob"]["custom_domain"]["enabled"] is False

# ---- validation ----
for bad in ["", "nodots", "example.org", "a b.com", "d1.cloudfront.net", "-x.foo.com", "foo.example.com", "example.com", "alice.com"]:
    assert call("PUT", "/api/student/domain", {"domain": bad}, alice)[0] == 400, bad
assert domains.validate_domain(" WWW.Alice.COM. ") == "www.alice.com"

# ---- DNS not visible yet -> waiting_dns, then one domain per student ----
status, body = call("PUT", "/api/student/domain", {"domain": "WWW.alice.com"}, alice)
c = body["token"]["custom_domain"]
assert status == 200 and c["state"] == "waiting_dns" and c["domain"] == "www.alice.com" and "DNS" in c["message"], body
assert call("PUT", "/api/student/domain", {"domain": "www.other.com"}, alice)[0] == 409
assert call("GET", "/api/student/domain/status", session=alice)[1]["token"]["custom_domain"]["state"] == "waiting_dns"

# ---- the same domain cannot be claimed twice ----
switch(bob_id, True)
status, body = call("PUT", "/api/student/domain", {"domain": "www.alice.com"}, bob)
assert status == 409 and "already used" in body["error"]

# ---- CNAME appears -> tenant -> certificate -> applied -> live ----
cf.dns_ok = True
state = lambda: call("GET", "/api/student/domain/status", session=alice)[1]["token"]["custom_domain"]  # noqa: E731
assert state()["state"] == "issuing_certificate"
tenant = cf.tenants["dt_1"]
assert tenant["Parameters"] == [{"Name": "student", "Value": "alice"}] and tenant["Name"] == "student-alice"
assert state()["state"] == "issuing_certificate", "certificate still pending"
cf.cert_status = "issued"
assert state()["state"] == "finishing"
cf.deployed = False
assert state()["state"] == "finishing", "applied but still deploying"
cf.deployed = True
c = state()
assert c["state"] == "live" and c["site_url"] == "https://www.alice.com/", c
assert portal.db().get(f"tok#{alice_id}")["tenant_id"] == "dt_1"

# ---- switching the teacher switch off later does not break a live domain ----
switch(alice_id, False)
assert state()["state"] == "live"
switch(alice_id, True)

# ---- publishing clears the custom domain's own cache; a failure there never fails the deploy ----
app.invalidate("alice", "dt_1")
assert cf.invalidations == [("dt_1", ["/*"])] and cf.main_invalidations[-1] == ["/sites/alice/*", "/alice/*"]
app.invalidate("alice")
assert len(cf.invalidations) == 1, "no tenant, no tenant invalidation"
real = cf.create_invalidation_for_distribution_tenant
cf.create_invalidation_for_distribution_tenant = lambda **kw: (_ for _ in ()).throw(RuntimeError("throttled"))
app.invalidate("alice", "dt_1")
cf.create_invalidation_for_distribution_tenant = real

# ---- removal: disable first, delete once deployed, then the name is free again ----
cf.deployed = False
status, body = call("DELETE", "/api/student/domain", session=alice)
assert body["token"]["custom_domain"]["state"] == "removing" and cf.tenants["dt_1"]["Enabled"] is False
assert state()["state"] == "removing"
cf.deployed = True
assert state()["state"] == "none" and "dt_1" not in cf.tenants
assert domain_of(alice)["domain"] == "" and portal.db().get("domain#www.alice.com") is None
assert call("PUT", "/api/student/domain", {"domain": "www.alice.com"}, bob)[1]["token"]["custom_domain"]["state"] == "issuing_certificate", "freed name can be claimed"

# ---- removing before DNS ever worked just clears it ----
cf.dns_ok = False
carol_id, carol = new_student("carol")
switch(carol_id, True)
assert call("PUT", "/api/student/domain", {"domain": "www.carol.com"}, carol)[1]["token"]["custom_domain"]["state"] == "waiting_dns"
assert call("DELETE", "/api/student/domain", session=carol)[1]["token"]["custom_domain"]["state"] == "none"

# ---- CloudFront refusing a domain for another reason is reported (not hidden as "waiting") and nothing is left saved ----
cf.refuse_with = ("InvalidArgument", "The domain is already associated with another distribution")
status, body = call("PUT", "/api/student/domain", {"domain": "www.carol.com"}, carol)
assert status == 400 and "already associated" in body["error"], body
assert domain_of(carol)["domain"] == "" and portal.db().get("domain#www.carol.com") is None, "rolled back, free to try another name"
cf.refuse_with = None

# ---- a token that is not active cannot start a domain ----
portal.db().update(f"tok#{carol_id}", {"expires_at": now - 60})
assert call("PUT", "/api/student/domain", {"domain": "www.carol.com"}, carol)[0] == 403

# ---- the sweeper finishes removals the student never came back to complete ----
cf.dns_ok = True
bob_tenant_id = portal.db().get(f"tok#{bob_id}")["tenant_id"]  # created when bob claimed the freed name above
cf.deployed = False
assert call("DELETE", "/api/student/domain", session=bob)[1]["token"]["custom_domain"]["state"] == "removing"
assert portal.sweep(now)["domains_removed"] == []
cf.deployed = True
assert portal.sweep(now)["domains_removed"] == ["bob"] and bob_tenant_id not in cf.tenants

# ---- feature not configured on the server ----
os.environ["TENANT_DISTRIBUTION_ID"] = ""
assert domain_of(alice)["available"] is False
assert call("PUT", "/api/student/domain", {"domain": "www.alice.com"}, alice)[0] == 503
os.environ["TENANT_DISTRIBUTION_ID"] = "EMT"

# ---- a revoked token's session is dead for domain routes too ----
call("POST", f"/api/admin/tokens/{alice_id}/revoke", {}, admin)
assert call("GET", "/api/student/domain/status", session=alice)[0] == 401

print("test_domains.py: all passed")
