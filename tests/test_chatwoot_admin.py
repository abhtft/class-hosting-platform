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
import chatwoot_core as core  # noqa: E402
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

# ---- Studio's dry run: validated here, the tenant is the key's, the worker answers ----
seen = []


def fake_worker(payload):
    seen.append(payload)
    return {"result": {"route": "rag", "text": "An answer.", "used": "version 1", "explain": []}}


def test_call(body, key="acme", invoke=fake_worker, name="shop"):
    event = {"rawPath": f"/admin/agents/{name}/test", "requestContext": {"http": {"method": "POST"}}, "headers": {"Authorization": "Bearer " + store.keys[key]}, "body": json.dumps(body), "isBase64Encoded": False}
    out = admin.handler(event, None, now=NOW, store=store, invoke=invoke)
    return out["statusCode"], json.loads(out["body"])


assert test_call({"text": "hello", "version": 1}) == (200, {"route": "rag", "text": "An answer.", "used": "version 1", "explain": []})
assert seen[-1] == {"kind": "test", "tenant_id": "acme", "agent_id": "shop", "text": "hello", "history": [], "version": 1}, "the tenant is the key's, never the body's"
status, _ = test_call({"text": "hello", "config": {"collections": ["faq"]}, "history": [{"c": "a", "b": "b"}, {"c": 5}, "junk"], "channel": "whatsapp", "inbox_id": 3})
assert status == 200 and seen[-1]["config"] == {"collections": ["faq"]} and seen[-1]["history"] == [{"c": "a", "b": "b"}] and seen[-1]["channel"] == "whatsapp" and seen[-1]["inbox_id"] == 3, "odd history turns are dropped"
before = len(seen)
for bad in ({"version": 1}, {"text": "", "version": 1}, {"text": "  ", "version": 1}, {"text": 5, "version": 1}, {"text": "x"}, {"text": "x", "version": 1, "config": {}}, {"text": "x", "version": 0}, {"text": "x", "version": "1"},
            {"text": "x", "version": True}, {"text": "x", "version": 9999999}, {"text": "x", "config": "text"}, {"text": "x", "config": []}, {"text": "x", "config": {"k": "y" * 300000}},
            {"text": "x", "version": 1, "history": "a"}, {"text": "x", "version": 1, "history": [0] * 51}, {"text": "x", "version": 1, "channel": "WhatsApp"}, {"text": "x", "version": 1, "channel": 5},
            {"text": "x", "version": 1, "inbox_id": 0}, {"text": "x", "version": 1, "inbox_id": "3"}, {"text": "x", "version": 1, "tenant_id": "beta"}, {"text": "x", "version": 1, "extra": 1}):
    status, body = test_call(bad)
    assert status in (400, 413) and body["error"] in ("bad_request", "too_large"), (bad, status, body)
assert len(seen) == before, "a bad request never reaches the worker"
assert test_call({"text": "x", "version": 1}, name="nobody") == (404, {"error": "not_found", "message": "No such agent."}) and test_call({"text": "x", "version": 1}, key="beta")[0] == 404, "another business's agent is not found"
assert test_call({"text": "x" * 5000, "version": 1})[0] == 200 and len(seen[-1]["text"]) == core.MAX_QUESTION_CHARS, "long text is cut"
event = {"rawPath": "/admin/agents/shop/test", "requestContext": {"http": {"method": "GET"}}, "headers": {"Authorization": "Bearer " + store.keys["acme"]}, "body": "", "isBase64Encoded": False}
assert admin.handler(event, None, now=NOW, store=store, invoke=fake_worker)["statusCode"] == 405
for answer, expect in (({"error": "bad_config", "message": "collections must list 1 to 5 shelf names."}, (400, "bad_config")), ({"error": "version_not_found"}, (404, "version_not_found")), ({"error": "cap_reached"}, (429, "cap_reached")),
                       ({"error": "not_found"}, (404, "not_found")), ({"error": "registry_unavailable"}, (503, "registry_unavailable")), ({"error": "something new"}, (502, "test_failed")), ({"nonsense": 1}, (502, "test_failed")), ("text", (502, "test_failed")), (None, (502, "test_failed"))):
    status, body = test_call({"text": "x", "version": 1}, invoke=lambda payload, answer=answer: answer)
    assert (status, body["error"]) == expect, (answer, status, body)
assert test_call({"text": "x", "version": 1}, invoke=lambda payload: {"error": "bad_config", "message": "collections must list 1 to 5 shelf names."})[1]["message"] == "collections must list 1 to 5 shelf names."
def broken(payload):
    raise RuntimeError("the worker failed with 4111 and Asha")


status, body = test_call({"text": "my card is 4111", "version": 1}, invoke=broken)
assert status == 500 and body["error"] == "try_again" and "4111" not in json.dumps(body) and "4111" not in "".join(LOGS), "a failing worker is a plain 500; the tester's words are in no response or log"
print("  ok  the test route validates its body, takes the tenant from the key, cuts the text, never reaches the worker with a bad request, shows another business's agent as missing, and maps the worker's errors")

# ---- the test suite routes: save, read, start a run (asynchronous), read a run and the state ----
started = []


def suite_call(method, path, body=None, key="acme", invoke=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"Authorization": "Bearer " + store.keys[key]}, "body": json.dumps(body) if body is not None else "", "isBase64Encoded": False}
    out = admin.handler(event, None, now=NOW, store=store, invoke=invoke or started.append)
    return out["statusCode"], json.loads(out["body"])


CASE = {"id": "price", "title": "Price", "turns": ["kitna lagega?"], "checks": {"route": "rag", "contains_all": ["2,000"]}}
assert suite_call("GET", "/admin/agents/shop/suite") == (200, {"rev": 0, "cases": [], "updated_at": None})
assert suite_call("POST", "/admin/agents/shop/suite/runs", {"version": 1}) == (409, {"error": "no_suite", "message": "This agent has no test suite yet. Add cases first."})
status, saved = suite_call("PUT", "/admin/agents/shop/suite", {"cases": [CASE]})
assert status == 201 and saved["rev"] == 1 and saved["cases"][0]["severity"] == "must" and saved["cases"][0]["channel"] == "whatsapp" and saved["updated_at"] == NOW, "the cases come back tidied, with the defaults filled in"
assert suite_call("GET", "/admin/agents/shop/suite")[1]["rev"] == 1 and store.items["suite#shop"]["tenant_id"] == "acme"
status, again = suite_call("PUT", "/admin/agents/shop/suite", {"cases": [CASE, {**CASE, "id": "hours"}], "expected_rev": 1})
assert status == 200 and again["rev"] == 2 and len(again["cases"]) == 2
status, body = suite_call("PUT", "/admin/agents/shop/suite", {"cases": [CASE], "expected_rev": 1})
assert status == 409 and body["error"] == "changed" and body["rev"] == 2, "a stale editor cannot overwrite a newer suite"
for bad, why in (({"cases": []}, "1 to"), ({"cases": "x"}, "1 to"), ({"cases": [{"id": "Bad"}]}, "id must"), ({"cases": [CASE, CASE]}, "Two cases"), ({"cases": [CASE], "extra": 1}, "Unknown field"), ({}, "1 to"), ({"cases": [{**CASE, "turns": []}]}, "turns")):
    status, body = suite_call("PUT", "/admin/agents/shop/suite", bad)
    assert status == 400 and why.lower() in body["message"].lower(), (bad, status, body)
assert suite_call("PUT", "/admin/agents/nobody/suite", {"cases": [CASE]})[0] == 404 and suite_call("GET", "/admin/agents/shop/suite", key="beta")[0] == 404, "another business's agent is not found"
assert suite_call("GET", "/admin/agents/shop/suite/state") == (200, {"runs": [], "versions": {}, "latest": None, "suite_rev": 2, "cases": 2})

status, begun = suite_call("POST", "/admin/agents/shop/suite/runs", {"version": 1})
assert status == 202 and re.fullmatch(r"[0-9a-f]{12}", begun["run_id"]) and begun["total"] == 2 and begun["target"] == "version 1"
assert started[-1] == {"kind": "suite", "tenant_id": "acme", "agent_id": "shop", "version": 1, "run_id": begun["run_id"]}, "the tenant is the key's"
run_item = store.items[f"suiterun#shop#{begun['run_id']}"]
assert run_item["status"] == "queued" and run_item["tenant_id"] == "acme" and run_item["total"] == 2 and run_item["suite_rev"] == 2, "the run exists before the worker starts, so Studio can read it at once"
assert suite_call("GET", f"/admin/agents/shop/suite/runs/{begun['run_id']}")[1]["status"] == "queued" and "ttl" not in suite_call("GET", f"/admin/agents/shop/suite/runs/{begun['run_id']}")[1]
status, body = suite_call("POST", "/admin/agents/shop/suite/runs", {"version": 1})
assert status == 409 and body["error"] == "run_in_progress" and body["run_id"] == begun["run_id"], "one run at a time"
store.items["suitestate#shop"]["latest"]["status"] = "done"
assert suite_call("POST", "/admin/agents/shop/suite/runs", {"config": {"collections": ["faq"]}})[0] == 202 and started[-1]["config"] == {"collections": ["faq"]} and "version" not in started[-1]
store.items["suitestate#shop"]["latest"]["status"] = "done"
for bad in ({}, {"version": 1, "config": {}}, {"version": 0}, {"version": "1"}, {"version": True}, {"config": "x"}, {"config": []}, {"config": {"k": "y" * 300000}}, {"version": 1, "extra": 1}):
    status, body = suite_call("POST", "/admin/agents/shop/suite/runs", bad)
    assert status in (400, 413) and body["error"] in ("bad_request", "too_large"), (bad, status, body)
assert suite_call("POST", "/admin/agents/nobody/suite/runs", {"version": 1})[0] == 404 and suite_call("POST", "/admin/agents/shop/suite/runs", {"version": 1}, key="beta")[0] == 404
assert suite_call("GET", f"/admin/agents/shop/suite/runs/{begun['run_id']}", key="beta")[0] == 404 and suite_call("GET", "/admin/agents/shop/suite/runs/ffffffffffff") == (404, {"error": "not_found", "message": "No such run."})
store.items["suitestate#shop"]["latest"]["status"] = "done"


def refuses(payload):
    raise RuntimeError("could not start 4111 Asha")


before = len([k for k in store.items if k.startswith("suiterun#")])
status, body = suite_call("POST", "/admin/agents/shop/suite/runs", {"version": 1}, invoke=refuses)
assert status == 502 and body["error"] == "run_not_started" and "4111" not in json.dumps(body) and len([k for k in store.items if k.startswith("suiterun#")]) == before, "a run that could not be started leaves nothing behind"
assert store.items["suitestate#shop"]["latest"]["status"] == "done", "and the earlier state is put back"
for method, path in (("POST", "/admin/agents/shop/suite"), ("GET", "/admin/agents/shop/suite/runs"), ("POST", "/admin/agents/shop/suite/state"), ("PUT", f"/admin/agents/shop/suite/runs/{begun['run_id']}")):
    assert suite_call(method, path)[0] == 405, (method, path)
print("  ok  the suite routes save a checked suite with a revision, start one run at a time asynchronously with the tenant of the key, read a run and the state, keep businesses apart and leave nothing behind when a run cannot be started")

# ---- teach sessions: a link for a client, ask / fact / feedback, the facts apply at once, limits, privacy, tests from corrections ----
asked = []


def worker_says(text, spent=7):
    def invoke(payload):
        asked.append(payload)
        return {"result": {"route": "rag", "text": text, "spent_micro": spent, "explain": [{"title": "price-list", "page": 1, "score": 0.9, "used": True}, {"title": "other", "page": 1, "score": 0.5, "used": False}]}}
    return invoke


def teach_call(method, path, body=None, key="acme", invoke=None, query=None, at=NOW):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"Authorization": "Bearer " + store.keys[key]}, "body": json.dumps(body) if body is not None else "",
             "isBase64Encoded": False, "queryStringParameters": query}
    out = admin.handler(event, None, now=at, store=store, invoke=invoke or worker_says("Rs 2,000 a month."))
    return out["statusCode"], json.loads(out["body"])


status, made = teach_call("POST", "/admin/agents/shop/teach", {"version": 1, "hours": 1, "cap_usd": 0.5, "client_name": "  Asha "})
sid = made["sid"]
assert status == 201 and re.fullmatch(r"[0-9a-f]{12}", sid) and made["expires_at"] == NOW + 3600 and made["cap_usd"] == 0.5 and made["base"] == {"version": 1} and made["client_name"] == "Asha"
item = store.items[f"teach#shop#{sid}"]
assert item["tenant_id"] == "acme" and item["ttl"] > made["expires_at"] and item["turns"] == [] and store.items["teachidx#shop"]["sids"] == [sid]
base = f"/admin/agents/shop/teach/{sid}"
assert teach_call("POST", base + "/turn", {"kind": "ask", "text": "price?"})[1]["error"] == "not_started", "the client starts first (reads the consent line)"
status, begun = teach_call("POST", base + "/start", {"client_name": "Asha K"})
assert status == 200 and store.items[f"teach#shop#{sid}"]["consent_at"] == NOW and store.items[f"teach#shop#{sid}"]["client_name"] == "Asha K"

status, first = teach_call("POST", base + "/turn", {"kind": "ask", "text": "What does social media cost?"}, invoke=worker_says("Rs 2,000 a month."))
assert status == 200 and first["n"] == 1 and first["answer"] == "Rs 2,000 a month." and first["closed"] is None
assert asked[-1]["kind"] == "test" and asked[-1]["tenant_id"] == "acme" and asked[-1]["version"] == 1 and asked[-1]["facts"] == [] and asked[-1]["history"] == [] and asked[-1]["channel"] == "web_widget"
status, fixed = teach_call("POST", base + "/turn", {"kind": "feedback", "of": 1, "verdict": "wrong", "correction": "It is Rs 2,500 a month, not 2,000."})
assert status == 200 and fixed["n"] == 2
status, again = teach_call("POST", base + "/turn", {"kind": "ask", "text": "And again, the price?"}, invoke=worker_says("Rs 2,500 a month."))
assert asked[-1]["facts"] == ['When a customer asks "What does social media cost?", the right answer is: It is Rs 2,500 a month, not 2,000.'], "the correction applies at once, inside the session"
assert asked[-1]["history"] == [{"c": "What does social media cost?", "b": "Rs 2,000 a month."}]
assert teach_call("POST", base + "/turn", {"kind": "fact", "text": "We close on Sundays."})[0] == 200 and "We close on Sundays." in store.items[f"teach#shop#{sid}"]["facts"][-1]["text"]
for bad, why in (({"kind": "ask"}, "text"), ({"kind": "ask", "text": "x" * 700}, "text"), ({"kind": "other"}, "kind"), ({"kind": "feedback", "of": 1, "verdict": "meh"}, "verdict"), ({"kind": "feedback", "of": 99, "verdict": "right"}, "no such answer"),
                 ({"kind": "feedback", "of": 2, "verdict": "right"}, "no such answer"), ({"kind": "ask", "text": "x", "extra": 1}, "Unknown field")):
    status, body = teach_call("POST", base + "/turn", bad)
    assert status == 400 and why.lower() in body["message"].lower(), (bad, status, body)

owner = teach_call("GET", base)[1]
client = teach_call("GET", base, query={"view": "client"})[1]
assert owner["turns"][0]["src"] == ["price-list"] and owner["turns"][0]["spent"] == 7 and owner["spent_usd"] == 0.000014 and len(owner["facts"]) == 2 and owner["status"] == "open"
assert all(set(t) <= {"n", "t", "kind", "q", "a", "of", "verdict", "correction", "text"} for t in client["turns"]) and "facts" not in client and "cap_usd" not in client and "base" not in client, "the client sees the talk, not the books, the cost or the limits"
rows = teach_call("GET", "/admin/agents/shop/teach")[1]["sessions"]
assert len(rows) == 1 and rows[0]["sid"] == sid and rows[0]["questions"] == 2 and rows[0]["corrections"] == 1 and rows[0]["client_name"] == "Asha K"

# a correction becomes a suite case; the numbers the client wrote are the check
status, saved = teach_call("POST", base + "/turns/1/test")
assert status == 201 and saved["case"]["id"] == f"teach-{sid}-1" and saved["case"]["checks"] == {"contains_all": ["2,500", "2,000"]} and saved["case"]["severity"] == "should", saved
assert any(c["id"] == f"teach-{sid}-1" for c in teach_call("GET", "/admin/agents/shop/suite")[1]["cases"]) and teach_call("POST", base + "/turns/1/test")[0] == 201, "saving twice replaces"
assert teach_call("POST", base + "/turns/3/test")[0] == 400 and teach_call("POST", base + "/turns/77/test")[0] == 400, "only an answer the client corrected"

# limits: cap, expiry, finish, revoke, another business, other agents
status, capped = teach_call("POST", "/admin/agents/shop/teach", {"version": 1, "cap_usd": 0.1})
sid2 = capped["sid"]
teach_call("POST", f"/admin/agents/shop/teach/{sid2}/start", {})
status, body = teach_call("POST", f"/admin/agents/shop/teach/{sid2}/turn", {"kind": "ask", "text": "q"}, invoke=worker_says("a", spent=100_000))
assert status == 200 and body["closed"] == "cap_reached"
status, body = teach_call("POST", f"/admin/agents/shop/teach/{sid2}/turn", {"kind": "ask", "text": "q"})
assert status == 429 and body["error"] == "cap_reached" and body["closed"] == "cap_reached", "a session that spent its cap takes no more questions"
status, body = teach_call("POST", base + "/turn", {"kind": "ask", "text": "q"}, at=NOW + 3601)
assert status == 410 and body["error"] == "expired" and teach_call("GET", base, at=NOW + 3601)[1]["closed"] == "expired"
assert teach_call("POST", base + "/finish")[1]["status"] == "finished" and teach_call("POST", base + "/turn", {"kind": "ask", "text": "q"})[1]["error"] == "finished"
status, third = teach_call("POST", "/admin/agents/shop/teach", {"version": 1})
assert teach_call("POST", f"/admin/agents/shop/teach/{third['sid']}/revoke")[1]["status"] == "revoked" and teach_call("POST", f"/admin/agents/shop/teach/{third['sid']}/start", {})[0] == 410
assert teach_call("GET", base, key="beta")[0] == 404 and teach_call("POST", base + "/turn", {"kind": "ask", "text": "q"}, key="beta")[0] == 404, "another business cannot reach the session"
assert teach_call("GET", "/admin/agents/nobody/teach")[0] == 404 and teach_call("GET", "/admin/agents/shop/teach/ffffffffffff")[0] == 404
for body, why in (({"hours": 0}, "hours"), ({"hours": 500}, "hours"), ({"cap_usd": 99}, "cap_usd"), ({"client_name": "x" * 99}, "client_name"), ({"version": 99}, "version"), ({"version": 1, "config": {}}, "not both"), ({"extra": 1}, "Unknown field"),
                  ({"config": {"collections": []}}, "collections")):
    status, answer = teach_call("POST", "/admin/agents/shop/teach", body if ("version" in body or "config" in body) else {"version": 1, **body})
    assert status == 400 and why.lower() in answer["message"].lower(), (body, status, answer)
status, draft = teach_call("POST", "/admin/agents/shop/teach", {"config": {"collections": ["faq"], "fallback_text": "Sorry.", "provider": "bedrock"}})
assert status == 201 and draft["base"] == {"config": "a draft"} and store.items[f"teach#shop#{draft['sid']}"]["base"]["config"]["provider"] == "bedrock", "a draft is tested as it is, the owner's view only says it is a draft"
for method, path in (("GET", base + "/turn"), ("POST", base), ("DELETE", "/admin/agents/shop/teach"), ("GET", base + "/finish")):
    assert teach_call(method, path)[0] == 405, (method, path)
print("  ok  a teach session: a link on a version or a draft, the client starts, asks, marks answers and corrects them (applied at once, inside the session), the client sees only the talk, the owner sees books and cost, a correction becomes a suite case, caps, expiry, finish, revoke and other businesses are handled")

# ---- nothing secret is returned or logged ----
everything = json.dumps([call(store, "GET", "/admin/agents"), call(store, "GET", "/admin/agents/shop"), refusals]) + "\n".join(LOGS)
for key in ALL_KEYS:
    secret = key.split(".", 1)[1]
    assert secret not in everything and key not in everything and reg.hash_secret(secret) not in everything, key[:8]
for item in store.items.values():
    assert not any(key.split(".", 1)[1] in json.dumps(item) for key in ALL_KEYS), "only hashes are in the table"
assert "key_hash" not in everything and not re.search(r"cs_[a-z0-9-]+\.[A-Za-z0-9_-]{32,}", everything), "no key, hash or key-shaped text in a response or a log"
assert LOGS and all(json.loads(line)["chatwoot_admin"] in ("agent_created", "agent_updated", "version_published", "live_version_set", "failed", "store_unavailable", "suite_saved", "teach_created", "suite_started", "suite_not_started") for line in LOGS)
print("  ok  no service key, its hash or a bearer header is in any response or log line, and the table holds only hashes")

print("test_chatwoot_admin.py: service-key access, agents, versions, tenant isolation, concurrency and secrecy")
