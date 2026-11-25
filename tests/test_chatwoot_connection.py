"""Connecting an agent to Chatwoot from Studio (lambda/chatwoot_connection.py and the /connection routes of chatwoot_admin.py): the three values are checked for their shape,
kept in the agent's secret (a dictionary stands in for Secrets Manager), never returned, never logged, a field left empty keeps what is stored, an agent of another business
looks like no agent, and a vault that fails is a plain 503 that says nothing about the vault."""
import base64
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_api as api  # noqa: E402
import chatwoot_connection as conn  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_store as cs  # noqa: E402

NOW = 1_800_000_000
LOGS = []
admin.log = lambda what, **details: LOGS.append(json.dumps({"chatwoot_admin": what, **details}))
FIRST = "test-value-one-one-one"
SECOND = "test-value-two-two-two"


class FakeVault:
    """Secrets Manager as a dictionary: name -> text."""

    def __init__(self):
        self.items, self.fail = {}, False

    def read(self, name):
        if self.fail:
            raise RuntimeError("AccessDenied arn:aws:secretsmanager:us-east-1:123:secret:" + name)
        return self.items.get(name)

    def write(self, name, text):
        if self.fail:
            raise RuntimeError("AccessDenied arn:aws:secretsmanager:us-east-1:123:secret:" + name)
        self.items[name] = text


store, vault = cs.MemoryStore(), FakeVault()
keys = {}
for tenant_id in ("acme", "beta"):
    key, key_hash = reg.new_service_key(tenant_id)
    store.claim(reg.tenant_key(tenant_id), {"tenant_id": tenant_id, "name": tenant_id, "key_hash": key_hash, "enabled": True, "agents": [], "rev": 1, "created_at": NOW})
    keys[tenant_id] = key


def call(method, path, body=None, tenant="acme"):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"authorization": "Bearer " + keys[tenant]},
             "body": json.dumps(body) if body is not None else "", "isBase64Encoded": False}
    out = admin.handler(event, None, now=NOW, store=store, vault=vault)
    return out["statusCode"], json.loads(out["body"])


assert call("PUT", "/admin/agents/mds", {"account_id": 2, "inbox_ids": [16]})[0] == 201
assert call("PUT", "/admin/agents/other", {"account_id": 2, "inbox_ids": [5]}, tenant="beta")[0] == 201

# ---- the card before anything is typed
status, body = call("GET", "/admin/agents/mds/connection")
assert status == 200 and body["bot_secret"] == "missing" and body["api_token"] == "missing" and body["account_id"] is None and body["agent_account_id"] == 2, body
assert body["connected"] is False and "bot secret" in body["problem"] and "bot access token" in body["problem"] and body["changed_at"] is None
assert body["secret_cache_seconds"] == api.SECRET_CACHE_SECONDS
print("  ok  before anything is typed the card says what is missing, in plain words")

# ---- shape checks: the usual wrong pastes, none of them echoed back
before = dict(vault.items)
for bad in ({}, {"by": "me"}, {"bot_secret": ""}, {"bot_secret": "   "}, {"bot_secret": "short"}, {"bot_secret": "has a space inside"}, {"bot_secret": "line\nbreak12345"}, {"bot_secret": "x" * 513}, {"bot_secret": 12345678},
            {"api_token": "tab\there12345"}, {"account_id": 0}, {"account_id": -3}, {"account_id": "abc"}, {"account_id": True}, {"account_id": 1.5}, {"account_id": 10 ** 12},
            {"bot_secret": FIRST, "surprise": 1}, [FIRST], "text"):
    status, body = call("PUT", "/admin/agents/mds/connection", bad)
    assert status == 400 and body["error"] == "bad_request", (bad, status, body)
    assert FIRST not in json.dumps(body) and "has a space inside" not in json.dumps(body), "a refusal names the field, never what was typed"
assert vault.items == before, "a refused request writes nothing"
print("  ok  a missing, blank, too short, spaced, multi-line, oversized, non-text or unknown value is refused by field name, nothing is written, nothing typed is echoed")

# ---- the first save: a pasted trailing space or line break is trimmed; the account number defaults to the agent's
status, body = call("PUT", "/admin/agents/mds/connection", {"bot_secret": FIRST + " \n", "api_token": " " + SECOND, "by": "The Owner"})
assert status == 200 and body["connected"] is True and body["bot_secret"] == "set" and body["api_token"] == "set" and body["account_id"] == 2 and body["problem"] is None, body
assert body["changed_at"] == NOW and body["changed_by"] == "The Owner"
stored = json.loads(vault.items["chatwoot/mds"])
assert stored == {"bot_secret": FIRST, "api_token": SECOND, "account_id": 2}, "the secret the receiver and the worker read"
assert api.bot_secret_of(vault.items["chatwoot/mds"]) == FIRST and api.credentials_of(vault.items["chatwoot/mds"]) == {"api_token": SECOND, "account_id": 2}, "and they read it as they always did"
print("  ok  the first save makes the secret the receiver and the worker already read (trimmed values, the agent's own account number), and the card says connected")

# ---- never a value out: not in any response, not in any log
for path in ("/admin/agents/mds/connection", "/admin/agents/mds", "/admin/agents"):
    text = json.dumps(call("GET", path)[1])
    assert FIRST not in text and SECOND not in text, path
assert not any(FIRST in line or SECOND in line for line in LOGS), "no value in a log line"
assert any('"connection_saved"' in line and '"bot_secret"' in line for line in LOGS), "the log names the fields changed"
print("  ok  a value is never in a response (this one, the agent, the list) or in a log line; the log names the fields that changed")

# ---- a change of one value keeps the others (unknown fields of a hand-made secret are kept, too)
vault.items["chatwoot/mds"] = json.dumps({**stored, "note": "made by hand"})
status, body = call("PUT", "/admin/agents/mds/connection", {"api_token": "test-value-new-new-new", "bot_secret": "", "account_id": None})
assert status == 200 and json.loads(vault.items["chatwoot/mds"]) == {"bot_secret": FIRST, "api_token": "test-value-new-new-new", "account_id": 2, "note": "made by hand"}
status, body = call("PUT", "/admin/agents/mds/connection", {"account_id": "5"})
assert status == 200 and json.loads(vault.items["chatwoot/mds"])["account_id"] == 5 and call("GET", "/admin/agents/mds")[1]["account_id"] == 5, "the account number is written on the agent too (the receiver checks every message against it)"
assert body["connected"] is True and body["agent_account_id"] == 5
print("  ok  changing one value keeps the others (and unknown fields); the account number is saved on the agent as well")

# ---- a secret made by hand shows in the card; a mismatch of the account number is said plainly
vault.items["chatwoot/mds"] = json.dumps({"bot_secret": FIRST, "api_token": SECOND, "account_id": 9})
status, body = call("GET", "/admin/agents/mds/connection")
assert body["connected"] is False and "differ" in body["problem"], body
vault.items["chatwoot/mds"] = "not json"
assert call("GET", "/admin/agents/mds/connection")[1]["bot_secret"] == "missing", "an unreadable secret counts as missing"
print("  ok  a secret made by hand shows in the card; an account number that differs from the agent's, or an unreadable secret, is said plainly")

# ---- other businesses and unknown agents
assert call("GET", "/admin/agents/mds/connection", tenant="beta")[0] == 404 and call("PUT", "/admin/agents/mds/connection", {"bot_secret": FIRST}, tenant="beta")[0] == 404
assert "chatwoot/other" not in vault.items and call("GET", "/admin/agents/nobody/connection")[0] == 404
assert call("DELETE", "/admin/agents/mds/connection")[0] == 405
print("  ok  an agent of another business looks like no agent (nothing is written); a wrong method is refused")

# ---- a vault that fails: a plain 503, no detail
vault.fail = True
for method, body_in in (("GET", None), ("PUT", {"bot_secret": FIRST})):
    status, body = call(method, "/admin/agents/mds/connection", body_in)
    assert status == 503 and body["error"] == "vault_unavailable" and "arn" not in json.dumps(body).lower() and "AccessDenied" not in json.dumps(body), body
assert any('"vault_failed"' in line for line in LOGS) and not any("arn:aws" in line or "AccessDenied" in line for line in LOGS), "the log keeps the error type only"
vault.fail = False
print("  ok  a vault that fails is a plain 503 and the log keeps the error type only")


# ---- the real vault: creates the secret the first time, puts a value after, copes with a race
class FakeClient:
    def __init__(self):
        self.items, self.calls, self.race = {}, [], False

    class Error(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}

    def get_secret_value(self, SecretId):
        if SecretId not in self.items:
            raise self.Error("ResourceNotFoundException")
        return {"SecretString": self.items[SecretId]}

    def put_secret_value(self, SecretId, SecretString):
        self.calls.append("put")
        if SecretId not in self.items:
            raise self.Error("ResourceNotFoundException")
        self.items[SecretId] = SecretString

    def create_secret(self, Name, SecretString, Description):
        self.calls.append("create")
        if self.race:
            self.items[Name] = "made by another call"
            raise self.Error("ResourceExistsException")
        self.items[Name] = SecretString


client = FakeClient()
real = conn.SecretsVault(client)
assert real.read("chatwoot/a") is None
real.write("chatwoot/a", "one")
assert client.calls == ["put", "create"] and client.items["chatwoot/a"] == "one", "created the first time"
real.write("chatwoot/a", "two")
assert client.items["chatwoot/a"] == "two" and client.calls[-1] == "put"
client.race = True
real.write("chatwoot/b", "mine")
assert client.items["chatwoot/b"] == "mine", "a secret made at the same moment gets the value"


class Broken(FakeClient):
    def get_secret_value(self, SecretId):
        raise self.Error("AccessDeniedException")


try:
    conn.SecretsVault(Broken()).read("chatwoot/a")
    raise SystemExit("an access error must not look like a missing secret")
except FakeClient.Error:
    pass
print("  ok  the real vault creates the secret the first time, puts a value after, copes with a race, and an access error is not taken for a missing secret")
print("test_chatwoot_connection.py: all passed")
