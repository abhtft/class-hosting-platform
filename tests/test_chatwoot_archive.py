"""Archiving an agent (lambda/chatwoot_archive.py and the admin route): the owner archives instead of deleting; an archived agent is switched off, can be restored for the archive days, and then
the purge removes exactly its own items and its entry in the business's list, never another agent's or a message."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_api as api  # noqa: E402
import chatwoot_archive as archive  # noqa: E402
import chatwoot_registry as reg  # noqa: E402

admin.log = lambda what, **details: None
archive.log = lambda what, **details: None
NOW = 1_800_000_000
DAY = 86400
DAYS = archive.ARCHIVE_DAYS


def fresh():
    store = api.MemoryStore()
    tenant = {"tenant_id": "acme", "enabled": True, "agents": ["shop", "shopper", "lib", "front"], "rev": 1}
    store.claim(reg.tenant_key("acme"), tenant)
    for name, kind in (("shop", None), ("shopper", None), ("lib", "department"), ("front", "orchestrator")):
        store.claim(reg.agent_key(name), {"tenant_id": "acme", "agent_id": name, "account_id": 1, "inbox_ids": [3] if kind != "department" else [], "enabled": True, "live_version": 1, "versions": 2, "rev": 1, **({"type": kind} if kind else {})})
        for n in (1, 2):
            store.claim(reg.version_key(name, n), {"agent_id": name, "tenant_id": "acme", "version": n, "config": {"collections": ["faq"]}})
    return store, tenant


def put(store, tenant, name, body, at=NOW):
    try:
        return admin.put_agent(store, tenant, name, body, at)
    except reg.Invalid as error:
        return 400, {"error": "bad_request", "message": str(error)}


# ---- archive and restore
store, tenant = fresh()
status, shown = put(store, tenant, "shop", {"archived": True})
assert status == 200 and shown["enabled"] is False and shown["archived_at"] == NOW, shown
assert put(store, tenant, "shop", {"enabled": True})[0] == 409, "an archived agent is not switched on: restore it first"
assert put(store, tenant, "shop", {"enabled": True, "archived": False})[1].get("archived_at") is None
status, shown = put(store, tenant, "shop", {"archived": False})
assert status == 200 and "archived_at" not in shown and shown["enabled"] is True, "restored with the same setting it had before (the last call switched it on)"
put(store, tenant, "shop", {"archived": True})
status, shown = put(store, tenant, "shop", {"archived": False})
assert "archived_at" not in shown and shown["enabled"] is False, "restored: still switched off until the owner switches it on"
assert put(store, tenant, "newone", {"account_id": 1, "inbox_ids": [3], "archived": True})[0] == 400, "a new agent cannot be archived"
for bad in ("yes", 1, None):
    assert put(store, tenant, "shop", {"archived": bad})[0] == 400, bad
print("  ok  an agent is archived (switched off, dated), cannot be switched on while archived, and is restored switched off")

# a department a live orchestrator lists is not archived
store.items[reg.version_key("front", 1)]["config"] = {"collections": ["faq"], "departments": [{"id": "library", "agent": "lib"}, {"id": "x", "agent": "other"}]}
status, body = put(store, tenant, "lib", {"archived": True})
assert status == 409 and body["error"] == "in_use" and body["agents"] == ["front"]
store.items[reg.agent_key("front")]["live_version"] = 2
assert put(store, tenant, "lib", {"archived": True})[0] == 200, "only the LIVE version of the orchestrator counts"
print("  ok  a department that a live orchestrator lists is not archived, naming the orchestrator")

# ---- the purge
store, tenant = fresh()
for key in ("suite#shop", "suite#routing#shop", "suitestate#shop", "suitestate#shop#central", "suiterun#shop#aaaa", "suiterun#shop#bbbb", "teach#shop#s1", "teachidx#shop", "repeat#shop#v1", "repeatstate#shop"):
    store.items[key] = {"agent_id": "shop"}
for key in ("suite#shopper", "suite#routing#shopper", "suiterun#shopper#cccc", "teach#shopper#s1", "repeat#shopper#v1", "msg#shop#501", "mem#shop#7", "media#acme", "books#acme", "usage#acme#202611"):
    store.items[key] = {"keep": True}
put(store, tenant, "shop", {"archived": True}, at=NOW)
tenant = store.get(reg.tenant_key("acme"))
assert archive.purge(store, NOW + DAYS * DAY - 1) == {"purged": [], "skipped": 0} and reg.agent_key("shop") in store.items, "not before the days are over"
assert archive.purge(store, NOW + DAYS * DAY) == {"purged": ["shop"], "skipped": 0}
left = set(store.items)
assert not [k for k in left if k.startswith(("agent#shop#", "suiterun#shop#", "teach#shop#", "repeat#shop#")) or k in ("agent#shop", "suite#shop", "suite#routing#shop", "suitestate#shop", "suitestate#shop#central", "teachidx#shop", "repeatstate#shop")], sorted(left)
for key in ("suite#shopper", "suite#routing#shopper", "suiterun#shopper#cccc", "teach#shopper#s1", "repeat#shopper#v1", "msg#shop#501", "mem#shop#7", "media#acme", "books#acme", "usage#acme#202611", "agent#shopper", "agent#shopper#v1", "agent#lib", "agent#front"):
    assert key in left, key
assert store.get(reg.tenant_key("acme"))["agents"] == ["shopper", "lib", "front"], "its name leaves the business's list; the others stay"
assert archive.purge(store, NOW + 400 * DAY) == {"purged": [], "skipped": 0}, "nothing archived any more: nothing to do"
print("  ok  after the archive days the agent's own items and its name in the list go; another agent with a similar name, messages, memory, media, books and spend are untouched")

# a restored agent is never purged; an unarchived one never; an agent still used or of an unknown business is skipped, not removed
store, tenant = fresh()
put(store, tenant, "shop", {"archived": True})
put(store, tenant, "shop", {"archived": False}, at=NOW + 5)
assert archive.purge(store, NOW + 100 * DAY)["purged"] == [] and reg.agent_key("shop") in store.items
put(store, store.get(reg.tenant_key("acme")), "lib", {"archived": True})
store.items[reg.version_key("front", 2)]["config"] = {"collections": ["faq"], "departments": [{"id": "l", "agent": "lib"}]}
assert store.items[reg.agent_key("front")]["live_version"] == 1
store.items[reg.agent_key("front")]["live_version"] = 2  # it started listing it after it was archived
out = archive.purge(store, NOW + 100 * DAY)
assert out == {"purged": [], "skipped": 1} and reg.agent_key("lib") in store.items, "in use: skipped, tried again tomorrow"
store.items[reg.agent_key("lib")]["tenant_id"] = "ghost"
assert archive.purge(store, NOW + 100 * DAY)["skipped"] == 1 and reg.agent_key("lib") in store.items, "an unknown business: nothing is removed on a guess"
assert archive.due({"archived_at": True}, NOW) is False and archive.due({"archived_at": "x"}, NOW) is False and archive.due({}, NOW) is False
assert archive.handler(now=NOW + 100 * DAY, store=store) == {"purged": [], "skipped": 1}
print("  ok  a restored agent is never removed; one a live orchestrator now lists, or of an unknown business, is skipped and kept; odd archive dates count as not archived")


# ---- the display name: what people see; the technical name never changes
store, tenant = fresh()
status, shown = put(store, tenant, "shop", {"display_name": "  Library desk  "})
assert status == 200 and shown["display_name"] == "Library desk" and shown["name"] == "shop" and "display_name" not in admin.view(store.items[reg.agent_key("shopper")])
assert put(store, tenant, "shop", {"enabled": False})[1]["display_name"] == "Library desk", "another change keeps it"
status, shown = put(store, tenant, "shop", {"display_name": ""})
assert status == 200 and "display_name" not in shown and "display_name" not in store.items[reg.agent_key("shop")], "empty clears it"
assert put(store, tenant, "shop", {"display_name": None})[0] == 200
for bad in (5, "x" * 61, "line\nbreak", ["a"]):
    assert put(store, tenant, "shop", {"display_name": bad})[0] == 400, bad
assert put(store, tenant, "brand-new", {"account_id": 1, "inbox_ids": [3], "display_name": "Front desk"})[1]["display_name"] == "Front desk", "can be set when the agent is made"
print("  ok  a display name is set, kept through other changes, cleared when empty, limited to 60 characters without line breaks; the technical name never changes")
