"""The Studio-facing admin API of the Chatwoot adapter (lambda/chatwoot_admin.py) on an in-memory table: only the holder of a business's current service key gets in
(and a wrong, unknown, rotated or switched-off key all look the same), an agent is created, updated and switched off only by its own business, versions are
immutable and published in order, the live version is chosen from versions that exist, one business can neither see nor touch another's agents, concurrent writers
lose cleanly instead of overwriting, and nothing secret is ever in a response or a log."""
import base64
import json
import os
import re
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_store as cs  # noqa: E402

NOW = 1_800_000_000
LOGS = []
admin.log = lambda what, **details: LOGS.append(json.dumps({"chatwoot_admin": what, **details}))
ALL_KEYS = []  # every key made here, to prove none of them leaks


def make_tenant(store, tenant_id, enabled=True):
    """A tenant with a fresh service key. The keys of a table are kept on the table object, so another table's 'acme' has its own key."""
    key, key_hash = reg.new_service_key(tenant_id)
    store.claim(reg.tenant_key(tenant_id), {"tenant_id": tenant_id, "name": tenant_id.title(), "key_hash": key_hash, "enabled": enabled, "agents": [], "rev": 1, "created_at": NOW})
    if not hasattr(store, "keys"):
        store.keys = {}
    store.keys[tenant_id] = key
    ALL_KEYS.append(key)
    return key


def call(store, method, path, body=None, key="acme", raw=None, headers=None, base64_body=False):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    head = {"Content-Type": "application/json"}
    token = getattr(store, "keys", {}).get(key, key) if key else None
    if token:
        head["Authorization"] = "Bearer " + token
    head.update(headers or {})
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": head, "body": base64.b64encode(data).decode() if base64_body else data.decode(), "isBase64Encoded": base64_body}
    out = admin.handler(event, None, now=NOW, store=store)
    return out["statusCode"], json.loads(out["body"])


store = cs.MemoryStore()
KEYS = {}
KEYS["acme"] = make_tenant(store, "acme")
KEYS["beta"] = make_tenant(store, "beta")
KEYS["gone"] = make_tenant(store, "gone", enabled=False)
SETTINGS = {"account_id": 1, "inbox_ids": [3, 4], "type": "chatbot", "purpose": "sales"}

# ---- who gets in ----
refusals = []
refusals.append(call(store, "GET", "/admin/agents", key=None))
refusals.append(call(store, "GET", "/admin/agents", key="cs_acme.abcdefghijklmnopqrstuvwxyz0123456789"))
refusals.append(call(store, "GET", "/admin/agents", key="cs_nobody.abcdefghijklmnopqrstuvwxyz0123456789"))
refusals.append(call(store, "GET", "/admin/agents", key="garbage"))
refusals.append(call(store, "GET", "/admin/agents", key=None, headers={"Authorization": "Basic " + KEYS["acme"]}))
refusals.append(call(store, "GET", "/admin/agents", key="gone"))
refusals.append(call(store, "GET", "/admin/agents", key=None, headers={"Authorization": "Bearer cs_acme." + "x" * 70}))
refusals.append(call(store, "GET", "/admin/agents", key=None, headers={"authorization": KEYS["acme"]}))
assert all(r == (401, refusals[0][1]) for r in refusals) and refusals[0][1]["error"] == "unauthorized", refusals
# the right secret of another tenant must not open this tenant
acme_secret = KEYS["acme"].split(".", 1)[1]
assert call(store, "GET", "/admin/agents", key=None, headers={"Authorization": f"Bearer cs_beta.{acme_secret}"})[0] == 401
assert call(store, "GET", "/admin/agents", key="acme")[0] == 200 and call(store, "GET", "/admin/agents", key=None, headers={"authorization": "bearer " + KEYS["acme"]})[0] == 200, "the scheme word is not case sensitive"
assert call(store, "GET", "/admin/agents/", key="acme")[0] == 200, "a trailing slash is the same address"
print("  ok  a missing, malformed, wrong-secret, unknown-tenant, other-tenant's, switched-off or wrong-scheme key is the same 401; the right key gets in")

# ---- create ----
status, body = call(store, "PUT", "/admin/agents/shop", SETTINGS)
assert status == 201 and body == {"name": "shop", "account_id": 1, "inbox_ids": [3, 4], "enabled": True, "type": "chatbot", "purpose": "sales", "live_version": 0, "versions": 0, "rev": 1,
                                  "created_at": NOW, "updated_at": NOW}, body
assert store.get("agent#shop")["tenant_id"] == "acme" and store.get("tenant#acme")["agents"] == ["shop"] and store.get("tenant#acme")["rev"] == 2
assert call(store, "GET", "/admin/agents/shop")[1] == body and call(store, "GET", "/admin/agents")[1] == {"agents": [body]}
assert call(store, "PUT", "/admin/agents/shop", {"enabled": False, "expected_rev": 1}) [0] == 200
status, body = call(store, "PUT", "/admin/agents/shop", {**SETTINGS, "enabled": True})
assert status == 200 and body["rev"] == 3 and body["enabled"] is True, "PUT on an existing agent of the tenant is an update"
for bad, why in (({}, "required"), ({"account_id": 1}, "required"), ({"inbox_ids": [3]}, "required"), ({**SETTINGS, "account_id": "1"}, "account_id"), ({**SETTINGS, "inbox_ids": []}, "inbox_ids"),
                 ({**SETTINGS, "inbox_ids": [3, 3]}, "inbox_ids"), ({**SETTINGS, "enabled": "yes"}, "enabled"), ({**SETTINGS, "type": "Bad Type"}, "type"), ({**SETTINGS, "tenant_id": "beta"}, "Unknown field"),
                 ({**SETTINGS, "key_hash": "x"}, "Unknown field")):
    status, body = call(store, "PUT", "/admin/agents/other", bad)
    assert status == 400 and body["error"] == "bad_request" and why in body["message"], (bad, status, body)
assert store.get("agent#other") is None and store.get("tenant#acme")["agents"] == ["shop"], "a refused create leaves nothing"
for path in ("/admin/agents/Shop", "/admin/agents/" + "a" * 41, "/admin/agents/a_b", "/admin/agents/shop/extra", "/admin/other", "/admin", "/admin/agents/shop/versions", "/admin/agents/shop/versions/x"):
    assert call(store, "GET", path)[0] == 404, path
print("  ok  an agent is created with its account and inboxes, listed, read and updated by its own business; odd bodies and names are refused and leave nothing behind")

# ---- one business cannot see or touch another's agents ----
status, body = call(store, "PUT", "/admin/agents/shop", SETTINGS, key="beta")
assert status == 409 and body["error"] == "name_taken", "a name used by another business is refused (names are global: they are webhook addresses)"
for method, path, payload in (("GET", "/admin/agents/shop", None), ("DELETE", "/admin/agents/shop", None), ("PUT", "/admin/agents/shop/versions/1", {"config": {}}),
                              ("GET", "/admin/agents/shop/versions/1", None), ("POST", "/admin/agents/shop/live", {"version": 0})):
    status, body = call(store, method, path, payload, key="beta")
    assert status == 404 and body["error"] == "not_found", (method, path, status, body)
assert call(store, "GET", "/admin/agents", key="beta")[1] == {"agents": []}
assert store.get("agent#shop")["enabled"] is True and store.get("agent#shop")["tenant_id"] == "acme"
call(store, "PUT", "/admin/agents/stall", {"account_id": 5, "inbox_ids": [1]}, key="beta")
assert [a["name"] for a in call(store, "GET", "/admin/agents", key="beta")[1]["agents"]] == ["stall"] and [a["name"] for a in call(store, "GET", "/admin/agents")[1]["agents"]] == ["shop"]
print("  ok  another business's agent is a 404 on every route, a taken name is a 409, and each business lists only its own agents")

# ---- optimistic concurrency ----
current = call(store, "GET", "/admin/agents/shop")[1]
assert call(store, "PUT", "/admin/agents/shop", {"inbox_ids": [3]}  | {"expected_rev": current["rev"]})[0] == 200
status, body = call(store, "PUT", "/admin/agents/shop", {"inbox_ids": [4], "expected_rev": current["rev"]})
assert status == 409 and body["error"] == "changed" and body["rev"] == current["rev"] + 1, "a writer that read an old revision loses"
assert call(store, "GET", "/admin/agents/shop")[1]["inbox_ids"] == [3]
assert call(store, "PUT", "/admin/agents/shop", {"expected_rev": "x"})[0] == 400


class Racing(cs.MemoryStore):
    """Another caller replaces the agent between the read and the write."""

    def replace_if(self, key, item, field, expected):
        if key == "agent#shop" and not getattr(self, "raced", False):
            self.raced = True
            self.items[key]["rev"] += 1
        return super().replace_if(key, item, field, expected)


racing = Racing()
make_tenant(racing, "acme")
call(racing, "PUT", "/admin/agents/shop", SETTINGS)
racing.raced = False
status, body = call(racing, "PUT", "/admin/agents/shop", {"enabled": False})
assert status == 409 and body["error"] == "changed" and racing.items["agent#shop"]["enabled"] is True, "the loser changed nothing"
print("  ok  a caller that read an old revision, or loses a race between read and write, gets 409 and changes nothing")

# ---- switching off ----
status, body = call(store, "DELETE", "/admin/agents/stall", key="beta")
assert status == 200 and body["enabled"] is False and store.get("agent#stall") and store.get("tenant#beta")["agents"] == ["stall"], "switched off, never deleted"
assert call(store, "DELETE", "/admin/agents/nothing")[0] == 404
print("  ok  DELETE switches an agent off and keeps everything")

# ---- limits ----
saved = reg.MAX_AGENTS
reg.MAX_AGENTS = 2
try:
    assert call(store, "PUT", "/admin/agents/second", SETTINGS)[0] == 201
    status, body = call(store, "PUT", "/admin/agents/third", SETTINGS)
    assert status == 409 and body["error"] == "agent_limit" and store.get("agent#third") is None
    assert call(store, "PUT", "/admin/agents/second", {"enabled": False})[0] == 200, "an existing agent can still be updated at the limit"
finally:
    reg.MAX_AGENTS = saved


class FailingTenantWrite(cs.MemoryStore):
    def replace_if(self, key, item, field, expected):
        return False if key.startswith("tenant#") else super().replace_if(key, item, field, expected)


broken = FailingTenantWrite()
make_tenant(broken, "acme")
status, body = call(broken, "PUT", "/admin/agents/shop", SETTINGS)
assert status == 409 and broken.get("agent#shop") is None, "an agent that could not be added to its business is taken back, never left invisible"
print("  ok  the agent limit is enforced, an update at the limit still works, and an agent that cannot be listed is taken back")

# ---- versions ----
CONFIG = {"instructions": "Be brief.", "language": "en", "temperature": 0.25, "tools": ["search", {"name": "x", "limit": 3}]}
status, body = call(store, "PUT", "/admin/agents/shop/versions/1", {"config": CONFIG})
assert status == 201 and body["version"] == 1 and body["agent"]["versions"] == 1 and body["agent"]["live_version"] == 0
assert store.get("agent#shop#v1")["config"] == CONFIG and store.get("agent#shop#v1")["tenant_id"] == "acme"
assert call(store, "GET", "/admin/agents/shop/versions/1")[1] == {"version": 1, "config": CONFIG, "published_at": NOW, "live": False}
assert call(store, "PUT", "/admin/agents/shop/versions/1", {"config": CONFIG}) == (200, {"version": 1, "unchanged": True}), "the same configuration again is a harmless repeat"
status, body = call(store, "PUT", "/admin/agents/shop/versions/1", {"config": {"instructions": "Changed."}})
assert status == 409 and body["error"] == "version_exists" and body["expected"] == 2 and store.get("agent#shop#v1")["config"] == CONFIG, "a published version never changes"
status, body = call(store, "PUT", "/admin/agents/shop/versions/3", {"config": CONFIG})
assert status == 409 and body["error"] == "out_of_order" and body["expected"] == 2 and store.get("agent#shop#v3") is None
assert call(store, "PUT", "/admin/agents/shop/versions/2", {"config": {"instructions": "Second."}})[0] == 201
for bad in ({}, {"config": []}, {"config": "x"}, {"config": {}, "extra": 1}, {"cfg": {}}):
    assert call(store, "PUT", "/admin/agents/shop/versions/3", bad)[0] == 400, bad
assert call(store, "PUT", "/admin/agents/shop/versions/0", {"config": {}})[0] == 404 and call(store, "GET", "/admin/agents/shop/versions/0")[0] == 404
assert call(store, "GET", "/admin/agents/shop/versions/9")[0] == 404 and call(store, "DELETE", "/admin/agents/shop/versions/1")[0] == 405
huge = {"config": {"text": "x" * (reg.MAX_VERSION_BYTES + 10)}}
status, body = call(store, "PUT", "/admin/agents/shop/versions/3", huge)
assert status == 400 and "KB" in body["message"] and store.get("agent#shop#v3") is None
status, _ = call(store, "PUT", "/admin/agents/shop/versions/3", {"config": {"text": "x" * (admin.MAX_BODY_BYTES + 10)}})
assert status == 413
saved = reg.MAX_VERSIONS
reg.MAX_VERSIONS = 2
try:
    status, body = call(store, "PUT", "/admin/agents/shop/versions/3", {"config": CONFIG})
    assert status == 409 and body["error"] == "version_limit"
finally:
    reg.MAX_VERSIONS = saved
print("  ok  versions are published in order, never change, repeat harmlessly, keep any JSON (floats included), and are limited in size and number")

# ---- a call that stopped half way is carried on, not blocked ----
store.claim("agent#shop#v3", {"agent_id": "shop", "tenant_id": "acme", "version": 3, "config": CONFIG, "published_at": NOW - 5})  # written, but the pointer never moved
status, body = call(store, "PUT", "/admin/agents/shop/versions/3", {"config": CONFIG})
assert status == 201 and body["agent"]["versions"] == 3, "the same configuration completes the half-done publish"
store.claim("agent#shop#v4", {"agent_id": "shop", "tenant_id": "acme", "version": 4, "config": {"instructions": "orphan"}, "published_at": NOW})
assert call(store, "PUT", "/admin/agents/shop/versions/4", {"config": CONFIG})[1]["error"] == "version_exists", "a different configuration never replaces a leftover"
print("  ok  a publish that stopped before moving the pointer is completed by the same request, and a leftover is never overwritten")

# ---- the live version ----
assert call(store, "POST", "/admin/agents/shop/live", {"version": 2}) [1]["live_version"] == 2
assert call(store, "GET", "/admin/agents/shop/versions/2")[1]["live"] is True and call(store, "GET", "/admin/agents/shop/versions/1")[1]["live"] is False
assert call(store, "POST", "/admin/agents/shop/live", {"version": 0})[1]["live_version"] == 0, "0 means no live version"
for bad in ({}, {"version": -1}, {"version": "2"}, {"version": True}, {"version": 1.5}, {"version": 1, "x": 1}, {"v": 1}):
    assert call(store, "POST", "/admin/agents/shop/live", bad)[0] == 400, bad
assert call(store, "POST", "/admin/agents/shop/live", {"version": 9})[0] == 404, "only a version that exists"
assert call(store, "GET", "/admin/agents/shop/live")[0] == 405
print("  ok  the live version is chosen from versions that exist; 0 means none")

# ---- request shape ----
for raw, why in ((b"not json", "JSON"), (b"[1]", "object"), (b"\xff\xfe", "JSON")):
    status, body = call(store, "PUT", "/admin/agents/shop", raw=raw, base64_body=True)
    assert status == 400 and why in body["message"], (raw, status, body)
assert call(store, "PUT", "/admin/agents/shop", {"enabled": True}, base64_body=True)[0] == 200, "a base64 body is decoded"
assert call(store, "PATCH", "/admin/agents/shop")[0] == 405 and call(store, "POST", "/admin/agents")[0] == 405 and call(store, "GET", "/admin/agents/shop/live")[0] == 405
print("  ok  a body that is not a JSON object is a 400, base64 is decoded, other methods are 405")


# ---- the table is down ----
class Down(cs.MemoryStore):
    def get(self, key):
        raise RuntimeError("down")


status, body = call(Down(), "GET", "/admin/agents", key=reg.new_service_key("acme")[0])
assert (status, body["error"]) == (500, "try_again")
status, body = call(store, "GET", "/admin/agents")
assert status == 200


class DownLater(cs.MemoryStore):
    armed = False

    def claim(self, key, item):
        if self.armed:
            raise RuntimeError("down")
        return super().claim(key, item)


later = DownLater()
make_tenant(later, "acme")
later.armed = True
assert call(later, "PUT", "/admin/agents/shop", SETTINGS)[0] == 500
print("  ok  a table that cannot be read or written answers 500, never a guess")

# ---- nothing secret is returned or logged ----
everything = json.dumps([call(store, "GET", "/admin/agents"), call(store, "GET", "/admin/agents/shop"), refusals]) + "\n".join(LOGS)
for key in ALL_KEYS:
    secret = key.split(".", 1)[1]
    assert secret not in everything and key not in everything and reg.hash_secret(secret) not in everything, key[:8]
for item in store.items.values():
    assert not any(key.split(".", 1)[1] in json.dumps(item) for key in ALL_KEYS), "only hashes are in the table"
assert "key_hash" not in everything and not re.search(r"cs_[a-z0-9-]+\.[A-Za-z0-9_-]{32,}", everything), "no key, hash or key-shaped text in a response or a log"
assert LOGS and all(json.loads(line)["chatwoot_admin"] in ("agent_created", "agent_updated", "version_published", "live_version_set", "failed", "store_unavailable") for line in LOGS)
print("  ok  no service key, its hash or a bearer header is in any response or log line, and the table holds only hashes")

print("test_chatwoot_admin.py: service-key access, agents, versions, tenant isolation, concurrency and secrecy")
