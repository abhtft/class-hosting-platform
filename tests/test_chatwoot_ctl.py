"""The teacher's local command (tools/chatwoot_ctl.py) on an in-memory table: it creates a tenant and shows its service key only once, the key then opens the admin API
and no other door, a rotated key kills the old one, disabling a tenant stops its key and its agents, agents are created and changed with the same validation as the
admin API, listing and showing never print a key or a hash, and bad input is explained with a non-zero exit code."""
import importlib.util
import json
import os
import re
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_store as cs  # noqa: E402

spec = importlib.util.spec_from_file_location("chatwoot_ctl", os.path.join(root, "tools", "chatwoot_ctl.py"))
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

NOW = 1_800_000_000
store = cs.MemoryStore()
KEY = re.compile(r"cs_[a-z0-9-]+\.[A-Za-z0-9_-]{32,64}")


def run(*argv, store=store):
    lines = []
    try:
        code = ctl.main(list(argv), store=store, out=lines.append, now=NOW)
    except SystemExit as error:  # argparse exits on a usage error
        code = error.code
    return code, "\n".join(lines)


def api(key, method="GET", path="/admin/agents", body=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"authorization": "Bearer " + key}, "body": json.dumps(body) if body is not None else ""}
    out = admin.handler(event, None, now=NOW, store=store)
    return out["statusCode"], json.loads(out["body"])


# ---- create a tenant: the key is shown once and works ----
code, text = run("create-tenant", "acme", "--name", "Acme Shop")
assert code == 0 and len(KEY.findall(text)) == 1 and "only its hash is stored" in text
key = KEY.findall(text)[0]
tenant = store.get("tenant#acme")
assert tenant["enabled"] is True and tenant["agents"] == [] and tenant["rev"] == 1 and tenant["name"] == "Acme Shop"
assert tenant["key_hash"] == reg.hash_secret(key.split(".", 1)[1]) and key.split(".", 1)[1] not in json.dumps(store.items), "only the hash is stored"
assert api(key)[0] == 200, "the printed key opens the admin API"
code, text = run("create-tenant", "acme")
assert code == 1 and "already exists" in text and not KEY.search(text), "a second create changes nothing and shows no key"
assert api(key)[0] == 200
for bad in ("Acme", "a" * 31, "-a", "a b", ""):
    code, text = run("create-tenant", bad)
    assert code == 2 and not KEY.search(text) and store.get(f"tenant#{bad}") is None, repr(bad)
print("  ok  create-tenant prints the service key once, stores only its hash, the key opens the admin API, and a repeat or a bad id changes nothing")

# ---- rotate: the old key dies ----
code, text = run("rotate-key", "acme")
new_key = KEY.findall(text)[0]
assert code == 0 and new_key != key and "no longer works" in text
assert api(key)[0] == 401 and api(new_key)[0] == 200
assert run("rotate-key", "nobody")[0] == 1 and run("rotate-key", "Bad Id")[0] == 1
assert store.get("tenant#acme")["rev"] == 2
print("  ok  rotate-key makes a new key and the old one stops at once")

# ---- disable and enable ----
code, text = run("disable-tenant", "acme")
assert code == 0 and "disabled" in text and api(new_key)[0] == 401, "a disabled tenant's key is dead"
code, text = run("enable-tenant", "acme")
assert code == 0 and "enabled" in text and api(new_key)[0] == 200
assert run("disable-tenant", "nobody")[0] == 1 and run("enable-tenant", "nobody")[0] == 1
print("  ok  disable-tenant kills the key until enable-tenant")

# ---- agents ----
assert run("agent-set", "shop", "--tenant", "nobody", "--account", "1", "--inboxes", "3")[0] == 1
code, text = run("agent-set", "shop", "--tenant", "acme", "--account", "1", "--inboxes", "3,4", "--type", "chatbot", "--purpose", "sales")
shown = json.loads(text)
assert code == 0 and shown["name"] == "shop" and shown["inbox_ids"] == [3, 4] and shown["enabled"] is True and store.get("tenant#acme")["agents"] == ["shop"]
assert api(new_key, path="/admin/agents/shop")[0] == 200 and [a["name"] for a in api(new_key)[1]["agents"]] == ["shop"], "the admin API sees the agent the command made"
code, text = run("agent-set", "shop", "--tenant", "acme", "--enabled", "false")
assert code == 0 and json.loads(text)["enabled"] is False and json.loads(text)["inbox_ids"] == [3, 4], "an update changes only what is given"
for argv, why in ((("agent-set", "shop", "--tenant", "acme", "--inboxes", "3,x"), "comma-separated"), (("agent-set", "shop", "--tenant", "acme", "--enabled", "maybe"), "true or false"),
                  (("agent-set", "shop", "--tenant", "acme", "--inboxes", "3,3"), "inbox_ids"), (("agent-set", "shop", "--tenant", "acme", "--account", "0"), "account_id"),
                  (("agent-set", "shop", "--tenant", "acme", "--type", "Bad Type"), "type"), (("agent-set", "newone", "--tenant", "acme"), "required")):
    code, text = run(*argv)
    assert code == 2 and why in text, (argv, code, text)
assert store.get("agent#newone") is None
run("create-tenant", "beta")
code, text = run("agent-set", "shop", "--tenant", "beta", "--account", "1", "--inboxes", "3")
assert code == 1 and json.loads(text)["error"] == "name_taken", "a name of another business is refused here too"
code, text = run("show-agent", "shop")
assert code == 0 and json.loads(text)["tenant"] == "acme" and run("show-agent", "nothing")[0] == 1
print("  ok  agent-set creates and changes an agent with the admin API's own validation, refuses a name of another business, and show-agent reads it")

# ---- list and show never print a key or a hash ----
code, text = run("list")
assert code == 0 and "acme  enabled  agents: shop" in text and "beta  enabled  agents: -" in text
everything = text + run("show-agent", "shop")[1] + run("list")[1]
for item in store.items.values():
    if item.get("key_hash"):
        assert item["key_hash"] not in everything
assert not KEY.search(everything)
code, text = run("list", store=cs.MemoryStore())
assert code == 0 and "No tenants" in text
print("  ok  list and show-agent never print a key or a hash")

# ---- usage ----
for argv in ((), ("nonsense",), ("create-tenant",), ("agent-set", "shop")):
    code, text = run(*argv)
    assert code not in (0, None), argv
saved_table = os.environ.pop("CHATWOOT_TABLE", None)
try:
    messages = []
    assert ctl.main(["list"], store=None, out=messages.append, now=NOW) == 2 and "--table" in messages[0], "without a table name the command explains and stops"
finally:
    if saved_table is not None:
        os.environ["CHATWOOT_TABLE"] = saved_table
print("  ok  a missing command, a missing argument or a missing table name stops with a message and a non-zero code")

print("test_chatwoot_ctl.py: the teacher's local command")
