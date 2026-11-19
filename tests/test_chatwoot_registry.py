"""The registry of the Chatwoot adapter (lambda/chatwoot_registry.py and chatwoot_store.py): the service key (shape, hashing, constant-time check, never stored), the
validation of agent settings and version configurations, the rule that decides whether a message is served, the few-second memory of the reader, and the table
(its conditional writes, the numbers DynamoDB returns and the floats it refuses), on in-memory fakes."""
import json
import os
import sys
from decimal import Decimal

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_registry as reg  # noqa: E402
import chatwoot_store as cs  # noqa: E402

# ---- the service key ----
key, stored_hash = reg.new_service_key("acme")
assert key.startswith("cs_acme.") and len(key) > 40
tenant_id, secret = reg.parse_service_key(key)
assert tenant_id == "acme" and reg.hash_secret(secret) == stored_hash and secret not in stored_hash and key not in stored_hash, "only the hash is kept"
assert reg.new_service_key("acme")[0] != key, "every key is new"
for bad in ("", None, "cs_acme", "cs_acme.", "cs_acme.short", "acme.abcdefghijklmnopqrstuvwxyz0123456789", "cs_ACME.abcdefghijklmnopqrstuvwxyz0123456789", "cs_acme.abc def" + "x" * 30,
            "cs_acme." + "a" * 65, "cs_" + "a" * 31 + ".abcdefghijklmnopqrstuvwxyz0123456789", "cs_acme.abcdefghijklmnopqrstuvwxyz0123456789\n", "Bearer " + key):
    assert reg.parse_service_key(bad) is None, bad
tenant = {"tenant_id": "acme", "key_hash": stored_hash, "enabled": True}
assert reg.key_matches(tenant, secret) is True
assert reg.key_matches(tenant, secret + "x") is False and reg.key_matches(tenant, "") is False
assert reg.key_matches({**tenant, "enabled": False}, secret) is False, "a switched-off tenant's key is dead"
assert reg.key_matches({**tenant, "enabled": "true"}, secret) is False
assert reg.key_matches(None, secret) is False and reg.key_matches({}, secret) is False and reg.key_matches({"key_hash": 5, "enabled": True}, secret) is False, "no tenant: false, no crash"
other_key, other_hash = reg.new_service_key("acme")
assert reg.key_matches({**tenant, "key_hash": other_hash}, secret) is False, "a rotated key kills the old one"
print("  ok  a service key is cs_<tenant>.<secret>, only its hash is kept, a wrong, old, odd or switched-off key never matches and a missing tenant does not crash")

# ---- the keys of the items ----
assert reg.tenant_key("acme") == "tenant#acme" and reg.agent_key("shop") == "agent#shop" and reg.version_key("shop", 3) == "agent#shop#v3"
for text in ("acme", "a", "a-1", "0x"):
    assert reg.TENANT_ID.fullmatch(text), text
for text in ("", "-a", "A", "a_b", "a" * 31, "a b", "a\n"):
    assert not reg.TENANT_ID.fullmatch(text), repr(text)
print("  ok  item keys and tenant names are fixed in shape")


# ---- agent settings ----
def invalid(body, creating=True, why=None):
    try:
        reg.clean_agent(body, creating)
    except reg.Invalid as error:
        assert why is None or why in str(error), (body, str(error))
        return
    raise AssertionError(f"must be refused: {body}")


good = {"account_id": 1, "inbox_ids": [3, 4], "enabled": True, "type": "chatbot", "purpose": "lead_collection"}
assert reg.clean_agent(good, True) == good
assert reg.clean_agent({"enabled": False}, False) == {"enabled": False}, "an update may send only what changes"
assert reg.clean_agent({"account_id": 1, "inbox_ids": [3], "expected_rev": 2}, True) == {"account_id": 1, "inbox_ids": [3]}, "expected_rev is a condition, not a setting"
invalid({}, True, "required")
invalid({"account_id": 1}, True, "required")
invalid({"inbox_ids": [3]}, True, "required")
invalid("x"), invalid(None), invalid([1])
invalid({**good, "extra": 1}, True, "Unknown field")
for account in (0, -1, "1", 1.5, True, None, [1]):
    invalid({**good, "account_id": account}, True, "account_id")
for inboxes in ([], [0], [-2], ["3"], [3, 3], [True], "3", 3, None, list(range(1, reg.MAX_INBOXES + 2))):
    invalid({**good, "inbox_ids": inboxes}, True, "inbox_ids")
assert reg.clean_agent({**good, "inbox_ids": list(range(1, reg.MAX_INBOXES + 1))}, True)["inbox_ids"] == list(range(1, reg.MAX_INBOXES + 1)), "exactly the limit is fine"
for flag in ("true", 1, None):
    invalid({**good, "enabled": flag}, True, "enabled")
for label in ("Chatbot", "chat bot", "", "1x", "x" * 41, 5, None, "a\n"):
    invalid({**good, "type": label}, True, "type")
    invalid({**good, "purpose": label}, True, "purpose")
for rev in (-1, "1", 1.5, True):
    invalid({**good, "expected_rev": rev}, True, "expected_rev")
print("  ok  agent settings: account, inboxes (1 to the limit, whole, different), enabled, short lowercase type and purpose; unknown fields and odd values are refused")

# ---- version configurations ----
assert reg.clean_config({"config": {"instructions": "x", "n": 1.5, "list": [1, {"a": None}]}}) == {"instructions": "x", "n": 1.5, "list": [1, {"a": None}]}
for body in (None, [], {}, {"config": []}, {"config": "x"}, {"config": {}, "extra": 1}, {"configuration": {}}):
    try:
        reg.clean_config(body)
    except reg.Invalid:
        continue
    raise AssertionError(body)
assert reg.clean_config({"config": {}}) == {}, "an empty configuration is allowed: the platform does not look inside"
fits = {"config": {"text": "x" * (reg.MAX_VERSION_BYTES - 40)}}
assert reg.clean_config(fits)
try:
    reg.clean_config({"config": {"text": "x" * (reg.MAX_VERSION_BYTES + 1)}})
    raise AssertionError("too large")
except reg.Invalid as error:
    assert "KB" in str(error)
print("  ok  a version configuration is a JSON object of limited size and nothing else")


# ---- the rule that decides whether a message is served ----
def agent(**kw):
    return {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3, 4], "enabled": True, **kw}


tenant_ok = {"tenant_id": "acme", "enabled": True}
assert reg.check_mapping(agent(), tenant_ok, 1, 3) is None and reg.check_mapping(agent(), tenant_ok, 1, 4) is None
for label, args in (("no agent", (None, tenant_ok, 1, 3)), ("no tenant", (agent(), None, 1, 3)), ("agent off", (agent(enabled=False), tenant_ok, 1, 3)),
                    ("tenant off", (agent(), {**tenant_ok, "enabled": False}, 1, 3)), ("other account", (agent(), tenant_ok, 2, 3)), ("other inbox", (agent(), tenant_ok, 1, 5)),
                    ("other tenant", (agent(tenant_id="other"), tenant_ok, 1, 3)), ("no inboxes", (agent(inbox_ids=[]), tenant_ok, 1, 3)), ("missing inboxes", (agent(inbox_ids=None), tenant_ok, 1, 3)),
                    ("enabled as text", (agent(enabled="yes"), tenant_ok, 1, 3)), ("account as text", (agent(account_id="1"), tenant_ok, 1, 3)), ("not dicts", ("x", "y", 1, 3))):
    assert reg.check_mapping(*args) == "not_mapped", label
print("  ok  a message is served only for an enabled agent of an enabled tenant, from its own account and one of its inboxes; every other case gives the same reason")


# ---- the reader remembers for a few seconds ----
class Counting(cs.MemoryStore):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, key):
        self.reads.append(key)
        return super().get(key)


store = Counting()
store.claim("tenant#acme", {"tenant_id": "acme", "enabled": True, "rev": 1})
store.claim("agent#shop", agent(rev=1))
clock = [100.0]
registry = reg.Registry(store, ttl=15, clock=lambda: clock[0])
assert registry.allows("shop", 1, 3) is None and registry.allows("shop", 1, 3) is None and registry.allows("shop", 1, 9) == "not_mapped"
assert store.reads == ["agent#shop", "tenant#acme"], "two reads once, then the memory"
store.items["agent#shop"]["enabled"] = False
assert registry.allows("shop", 1, 3) is None, "a switch-off is seen after the cache time, not before"
clock[0] += 16
assert registry.allows("shop", 1, 3) == "not_mapped" and len(store.reads) == 4
assert registry.allows("nobody", 1, 3) == "not_mapped" and registry.allows("nobody", 1, 3) == "not_mapped" and store.reads.count("agent#nobody") == 1, "a missing agent is remembered for the same few seconds"
fresh = reg.Registry(store, ttl=0)
before = len(store.reads)
fresh.allows("shop", 1, 3), fresh.allows("shop", 1, 3)
assert len(store.reads) == before + 4 and not fresh.cache, "ttl 0 reads every time and remembers nothing"
broken = Counting()
broken.claim("agent#x", {"tenant_id": 5})
assert reg.Registry(broken, ttl=0).allows("x", 1, 1) == "not_mapped", "an agent whose tenant name is not text is not served, and nothing crashes"
print("  ok  the reader reads the agent and its tenant, remembers them for the cache time (a missing agent too), and reads every time with ttl 0")

# ---- the memory table: conditional writes ----
m = cs.MemoryStore()
assert m.claim("agent#a", {"rev": 1, "v": "one"}) is True and m.claim("agent#a", {"rev": 1, "v": "two"}) is False and m.get("agent#a")["v"] == "one"
assert m.replace_if("agent#a", {"rev": 2, "v": "three"}, "rev", 1) is True and m.get("agent#a") == {"rev": 2, "v": "three", "pk": "agent#a"}
assert m.replace_if("agent#a", {"rev": 3, "v": "late"}, "rev", 1) is False, "a stale writer loses"
assert m.replace_if("agent#none", {"rev": 1}, "rev", 0) is False, "no item, no replace"
m.get("agent#a")["v"] = "changed outside"
assert m.get("agent#a")["v"] == "three", "a read is a copy"
assert [i["pk"] for i in m.list_prefix("agent#")] == ["agent#a"] and m.list_prefix("tenant#") == []
print("  ok  the memory table claims once, replaces only on the expected revision, and hands out copies")

# ---- the DynamoDB table: the conditions it sends and the numbers it returns ----
from botocore.exceptions import ClientError  # noqa: E402


def error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "FakeOperation")


class FakeTable:
    def __init__(self):
        self.items, self.calls = {}, []

    def get_item(self, Key):
        self.calls.append("get")
        item = self.items.get(Key["pk"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        self.calls.append(ConditionExpression)
        current = self.items.get(Item["pk"])
        if ConditionExpression == "attribute_not_exists(pk)":
            ok = current is None
        elif ConditionExpression == "#f = :e":
            ok = current is not None and current.get(ExpressionAttributeNames["#f"]) == ExpressionAttributeValues[":e"]
        else:
            raise AssertionError(ConditionExpression)
        if not ok:
            raise error(cs.CONDITION_FAILED)
        self.items[Item["pk"]] = dict(Item)

    def scan(self, FilterExpression, ExclusiveStartKey=None):
        self.calls.append("scan")
        return {"Items": [dict(i) for k, i in sorted(self.items.items()) if k.startswith("agent#")]}


table = FakeTable()
store = cs.DynamoStore(table)
assert store.claim("agent#a", {"rev": 1, "n": 1.5, "inbox_ids": [3]}) is True
assert isinstance(table.items["agent#a"]["n"], Decimal), "a float is stored as a Decimal (DynamoDB refuses floats)"
got = store.get("agent#a")
assert got == {"rev": 1, "n": 1.5, "inbox_ids": [3], "pk": "agent#a"} and isinstance(got["rev"], int) and isinstance(got["n"], float), "numbers come back as plain numbers"
table.items["agent#a"]["rev"] = Decimal(1)
assert store.get("agent#a")["rev"] == 1 and isinstance(store.get("agent#a")["rev"], int)
assert store.get("agent#missing") is None
assert store.claim("agent#a", {"rev": 9}) is False, "the second claim loses"
assert store.replace_if("agent#a", {"rev": 2}, "rev", 1) is True and store.replace_if("agent#a", {"rev": 3}, "rev", 1) is False
assert store.replace_if("agent#none", {"rev": 1}, "rev", 0) is False
assert [i["pk"] for i in store.list_prefix("agent#")] == ["agent#a"]


class Denied(FakeTable):
    def put_item(self, Item, ConditionExpression, **kw):
        raise error("AccessDeniedException")


for call in (lambda s: s.claim("k", {}), lambda s: s.replace_if("k", {}, "rev", 1)):
    try:
        call(cs.DynamoStore(Denied()))
        raise AssertionError("a real error must not be taken for a lost race")
    except ClientError:
        pass
print("  ok  the DynamoDB table sends attribute_not_exists or a revision condition, understands a lost race, stores floats as Decimal and returns plain numbers")

print("test_chatwoot_registry.py: service key, validation, the serving rule, the reader and the table")
