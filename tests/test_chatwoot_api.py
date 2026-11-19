"""The Chatwoot receiver and worker (lambda/chatwoot_api.py) run on in-memory fakes: a first delivery is accepted and handed over once, a repeat is only counted,
nothing a customer wrote is ever stored, a forged or stale call is refused and leaves no trace, a failed hand-over lets Chatwoot's retry through, the worker
posts exactly one reply per message (never a second one, whatever fails), refuses another account's message, records every failure with a code, never leaks the
token, and the DynamoDB store sends exactly the conditional writes the design relies on. The real HTTP client is tested in test_chatwoot_http.py."""
import base64
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_api as api  # noqa: E402
import chatwoot_core as core  # noqa: E402
import chatwoot_registry as reg  # noqa: E402

LOGS = []  # what the functions print: collected, and checked at the end for secrets and message text
api.log = lambda what, **details: LOGS.append(json.dumps({"chatwoot": what, **details}))

SECRET = "test-only-secret-not-a-real-one"
TOKEN = "test-only-token-abc123XYZ"
BASE = "https://chat.example.org"
api.BASE_URL = BASE
api.AUTH_STYLE = "bearer"
NOW = 1_800_000_000
EMAIL = "asha" + chr(64) + "example.org"  # built here so no address is written in the repository
TEXT = "my card number is 4111 and my name is Asha Verma"
SAMPLE = {
    "event": "message_created", "id": 501, "content": TEXT, "message_type": "incoming", "private": False,
    "sender": {"id": 9, "type": "contact", "name": "Asha Verma", "email": EMAIL},
    "conversation": {"id": 12, "inbox_id": 3, "status": "pending"}, "account": {"id": 1, "name": "Shop"}, "inbox": {"id": 3, "name": "Website"},
}


def register(store, agent="shop", tenant="acme", account=1, inboxes=(3,), enabled=True, tenant_enabled=True):
    """What the registry holds for an agent: its tenant and the one account and the inboxes it may serve (the message in SAMPLE is account 1, inbox 3)."""
    store.claim(reg.tenant_key(tenant), {"tenant_id": tenant, "enabled": tenant_enabled, "key_hash": "x", "agents": [agent], "rev": 1})
    store.claim(reg.agent_key(agent), {"tenant_id": tenant, "agent_id": agent, "account_id": account, "inbox_ids": list(inboxes), "enabled": enabled, "live_version": 0, "versions": 0, "rev": 1})


class Fakes:
    """In-memory stand-ins: the table, Secrets Manager (a bot secret, the bot's token and the account id per agent), the worker invoke and the call to Chatwoot."""

    def __init__(self, secrets=None, fail_invoke=False, extra=None, registered=True):
        self.store = api.MemoryStore()
        self.invoked = []
        self.fail_invoke = fail_invoke
        self.secret_reads = []
        self.table = secrets if secrets is not None else {"shop": SECRET}
        self.extra = extra if extra is not None else {"api_token": TOKEN, "account_id": 1}
        self.requests = []
        self.answers = []  # what the next calls to Chatwoot return: (status, body bytes) or an exception to raise
        if registered:
            register(self.store)
        self.deps = api.Deps(self.store, api.Secrets(self.read), self.invoke, registry=reg.Registry(self.store, ttl=0))
        self.wdeps = api.Deps(self.store, api.Secrets(self.read, api.credentials_of), http=self.post, registry=reg.Registry(self.store, ttl=0))

    def read(self, name):
        self.secret_reads.append(name)
        agent = name[len(api.SECRET_PREFIX):]
        return json.dumps({"bot_secret": self.table[agent], **self.extra}) if agent in self.table else None

    def invoke(self, payload):
        if self.fail_invoke:
            raise RuntimeError("throttled")
        self.invoked.append(payload)

    def post(self, url, headers, body, timeout):
        self.requests.append((url, headers, body, timeout))
        answer = self.answers.pop(0) if self.answers else (200, b'{"id": 9001}')
        if isinstance(answer, Exception):
            raise answer
        return answer


def event(payload, agent="shop", secret=SECRET, ts=NOW, method="POST", headers=None, path=None, body=None, base64_body=False, delivery="d-1"):
    raw = body if body is not None else json.dumps(payload).encode("utf8")
    head = {"Content-Type": "application/json", "X-Chatwoot-Delivery": delivery}
    if secret is not None:
        head.update({"X-Chatwoot-Timestamp": str(ts), "X-Chatwoot-Signature": core.sign(secret, ts, raw)})
    head.update(headers or {})
    return {"rawPath": path or f"/hooks/chatwoot/{agent}", "requestContext": {"http": {"method": method}}, "headers": head,
            "body": base64.b64encode(raw).decode() if base64_body else raw.decode("utf8"), "isBase64Encoded": base64_body}


def messages(fakes):
    """The message items in the table (the registry items are not messages)."""
    return [key for key in fakes.store.items if key.startswith("msg#")]


def call(fakes, ev, now=NOW):
    out = api.handler(ev, None, now=now, deps=fakes.deps)
    return out["statusCode"], json.loads(out["body"])


# ---- a first delivery: 200 at once, one item, one hand-over, and nothing the customer wrote is kept ----
f = Fakes()
status, body = call(f, event(SAMPLE))
assert (status, body) == (200, {"ok": True, "accepted": True}), (status, body)
assert len(f.invoked) == 1 and f.invoked[0] == {"agent_id": "shop", "delivery_id": "d-1", "message_id": 501, "conversation_id": 12, "account_id": 1, "inbox_id": 3}
item = f.store.items["msg#shop#501"]
assert item["status"] == "received" and item["received_at"] == NOW and item["ttl"] == NOW + api.DEDUPE_TTL_DAYS * 86400 and item["delivery_id"] == "d-1"
stored = json.dumps(f.store.items) + json.dumps(f.invoked)
for secret_text in ("4111", "Asha", "Verma", EMAIL, "card number", SECRET, TOKEN):
    assert secret_text not in stored, f"{secret_text!r} must never be stored or handed over"
assert all(value == SECRET for _, value in f.deps.secrets.cache.values()), "the receiver keeps only the bot secret from the secret"
assert TOKEN not in repr(f.deps.secrets.cache), "the receiver never holds the bot's token"
print("  ok  a first delivery is accepted, handed to the worker once, and only ids and times are stored (no text, no name, no e-mail)")

# ---- the same message again (Chatwoot's retry, or a double delivery): counted, not handed over again ----
status, body = call(f, event(SAMPLE, delivery="d-2"), now=NOW + 5)
assert (status, body) == (200, {"ok": True, "duplicate": True}) and len(f.invoked) == 1
call(f, event(SAMPLE, delivery="d-3"), now=NOW + 9)
assert f.store.items["msg#shop#501"]["duplicates"] == 2 and f.store.items["msg#shop#501"]["last_duplicate_at"] == NOW + 9 and len(f.invoked) == 1
status, _ = call(f, event({**SAMPLE, "id": 502}), now=NOW + 10)
assert status == 200 and len(f.invoked) == 2 and "msg#shop#502" in f.store.items, "another message is another item"
g = Fakes(secrets={"shop": SECRET, "other": SECRET})
register(g.store, agent="other")
assert call(g, event(SAMPLE))[1]["accepted"] and call(g, event(SAMPLE, agent="other"))[1]["accepted"]
assert {k for k in g.store.items if k.startswith("msg#")} == {"msg#shop#501", "msg#other#501"}, "the same message id in two agents is two items"
print("  ok  a repeat is only counted, a different message and another agent's message with the same id are separate")

# ---- calls that are not from Chatwoot leave nothing behind ----
def refused(ev, expect=401, note=""):
    f = Fakes()
    status, body = call(f, ev)
    assert status == expect and not f.invoked and not messages(f), (note, status, body)
    return body


refused(event(SAMPLE, secret="forged"), note="wrong secret")
refused(event(SAMPLE, secret=None), note="no signature headers")
refused(event(SAMPLE, ts=NOW - core.TOLERANCE_SECONDS - 1), note="old timestamp")
refused(event(SAMPLE, headers={"X-Chatwoot-Signature": "sha256=00"}), note="bad signature")
refused(event(SAMPLE, agent="nobody"), note="an agent without a secret")
assert refused(event(SAMPLE, secret="forged")) == refused(event(SAMPLE, agent="nobody")), "a wrong signature and an unknown agent answer alike"
body = json.dumps(SAMPLE).encode()
ev = event(SAMPLE)
ev["body"] = body.decode() + " "
refused(ev, note="body changed after signing")
refused(event(SAMPLE, base64_body=True, secret="forged"), note="base64 body, wrong secret")
f = Fakes()
assert call(f, event(SAMPLE, base64_body=True))[0] == 200 and len(f.invoked) == 1, "a base64 encoded body is signed over its decoded bytes"
print("  ok  a forged, unsigned, old or unknown-agent call is answered 401 (the same for the last two), with nothing stored or handed over")

# ---- the address, the method and the size ----
for path in ("/hooks/chatwoot", "/hooks/chatwoot/", "/hooks/chatwoot/Shop", "/hooks/chatwoot/shop/extra", "/hooks/other/shop", "/hooks/chatwoot/shop%0A", "/", "/hooks/chatwoot/" + "a" * 41):
    refused(event(SAMPLE, path=path), 404, path)
refused(event(SAMPLE, method="GET"), 405)
refused(event(SAMPLE, method="PUT"), 405)
saved = api.MAX_BODY_BYTES
api.MAX_BODY_BYTES = 100
try:
    refused(event(SAMPLE), 413)
finally:
    api.MAX_BODY_BYTES = saved
big = {**SAMPLE, "content": "x" * (saved - 700)}
assert call(Fakes(), event(big))[0] == 200, "a message just under the limit passes"
assert refused(event({**SAMPLE, "content": "x" * (saved + 10)}), 413)
assert call(Fakes(), event(SAMPLE, path="/hooks/chatwoot/shop/"))[0] == 200, "a trailing slash is the same address"
print("  ok  only POST /hooks/chatwoot/<agent name> exists; other paths 404, other methods 405, a body over the limit 413")

# ---- messages the bot must not answer are acknowledged (so Chatwoot does not retry) but never stored or handed over ----
for change, why in (({"message_type": "outgoing"}, "not_incoming"), ({"private": True}, "private"), ({"event": "message_updated"}, "event_ignored"),
                    ({"sender": {"type": "agent_bot", "id": 4}}, "from_bot"), ({"conversation": {"id": 12, "status": "open"}}, "not_pending"), ({"id": "x"}, "bad_payload")):
    f = Fakes()
    status, body = call(f, event({**SAMPLE, **change}))
    assert (status, body) == (200, {"ok": True, "ignored": why}) and not f.invoked and not messages(f), (change, status, body)
f = Fakes()
assert call(f, event(None, body=b"not json"))[1] == {"ok": True, "ignored": "not_json"}, "a signed body that is not JSON is ignored, not a crash"
print("  ok  the bot's own replies, agents' replies, private notes, other events and taken-over conversations are acknowledged and ignored")

# ---- only a registered agent is served: its tenant, account and inbox are checked before anything is stored ----
for label, build in (
        ("the agent is not registered", lambda f: f.store.forget("agent#shop")),
        ("the agent is switched off", lambda f: f.store.items["agent#shop"].update(enabled=False)),
        ("its tenant is switched off", lambda f: f.store.items["tenant#acme"].update(enabled=False)),
        ("its tenant does not exist", lambda f: f.store.forget("tenant#acme")),
        ("it belongs to another tenant than the one found", lambda f: f.store.items["agent#shop"].update(tenant_id="other")),
        ("the message is from another Chatwoot account", lambda f: f.store.items["agent#shop"].update(account_id=2)),
        ("the message is from an inbox the agent does not serve", lambda f: f.store.items["agent#shop"].update(inbox_ids=[4, 5])),
        ("the agent serves no inbox", lambda f: f.store.items["agent#shop"].update(inbox_ids=[])),
        ("enabled is text, not true", lambda f: f.store.items["agent#shop"].update(enabled="true"))):
    f = Fakes()
    build(f)
    status, body = call(f, event(SAMPLE))
    assert (status, body) == (200, {"ok": True, "ignored": "not_mapped"}), (label, status, body)
    assert not f.invoked and not [k for k in f.store.items if k.startswith("msg#")], label
f = Fakes(registered=False)
assert call(f, event(SAMPLE))[1] == {"ok": True, "ignored": "not_mapped"} and not f.invoked, "an agent with a secret but no registration is not served"
f = Fakes()
f.store.items["agent#shop"]["inbox_ids"] = [1, 2, 3, 9]
assert call(f, event(SAMPLE))[1] == {"ok": True, "accepted": True}, "one of several inboxes is enough"
f = Fakes()
register(f.store, agent="other", tenant="acme", account=1, inboxes=(3,))
f.table["other"] = SECRET
assert call(f, event(SAMPLE, agent="other"))[1] == {"ok": True, "accepted": True} and "msg#other#501" in f.store.items, "a second agent of the same tenant"
print("  ok  an unregistered agent, a switched-off agent or tenant, another account or inbox are acknowledged with ignored:not_mapped and nothing is stored or handed over")


class DownRegistry:
    def allows(self, *args):
        raise RuntimeError("down")


f = Fakes()
f.deps.registry = DownRegistry()
assert call(f, event(SAMPLE))[0] == 500 and not f.invoked and not [k for k in f.store.items if k.startswith("msg#")], "a failing registry answers 500 so Chatwoot retries"
print("  ok  a registry that cannot be read answers 500, never a guess")

# a refusal never reveals a reason: a forged call is 401 whether or not the agent is registered
f = Fakes(registered=False)
assert call(f, event(SAMPLE, secret="forged"))[0] == 401

# ---- the hand-over fails: 500 and no item, so Chatwoot's retry is accepted (it retries only 429 and 500) ----
f = Fakes(fail_invoke=True)
status, body = call(f, event(SAMPLE))
assert (status, body) == (500, {"error": "try_again"}) and not messages(f)
f.fail_invoke = False
status, body = call(f, event(SAMPLE), now=NOW + 3)
assert (status, body) == (200, {"ok": True, "accepted": True}) and len(f.invoked) == 1 and "msg#shop#501" in f.store.items
print("  ok  when the worker cannot be started the answer is 500 and the retry is accepted, not taken for a repeat")


# ---- Secrets Manager or the table is down: 500 so Chatwoot retries; nothing else ----
class Boom:
    def bot_secret(self, agent):
        raise RuntimeError("down")


f = Fakes()
f.deps.secrets = Boom()
assert call(f, event(SAMPLE))[0] == 500 and not f.invoked and not messages(f)


class BrokenStore(api.MemoryStore):
    def claim(self, key, item):
        raise RuntimeError("down")


f = Fakes()
f.deps.store = BrokenStore()
assert call(f, event(SAMPLE))[0] == 500 and not f.invoked
print("  ok  a failing secret store or table answers 500, never 200 for a message that was not recorded")

# ---- the secret: read as chatwoot/<agent>, field bot_secret, remembered for a while, a missing one never remembered ----
clock = [1000.0]
reads = []
texts = {"chatwoot/shop": json.dumps({"bot_secret": "s1", "api_token": "t"})}
secrets = api.Secrets(lambda name: (reads.append(name), texts.get(name))[1], clock=lambda: clock[0])
assert secrets.bot_secret("shop") == "s1" and secrets.bot_secret("shop") == "s1" and reads == ["chatwoot/shop"], "remembered"
clock[0] += api.SECRET_CACHE_SECONDS + 1
texts["chatwoot/shop"] = json.dumps({"bot_secret": "s2"})
assert secrets.bot_secret("shop") == "s2" and len(reads) == 2, "read again after the cache time, so a rotated secret takes effect"
assert secrets.bot_secret("new") is None and secrets.bot_secret("new") is None and reads.count("chatwoot/new") == 2, "a missing secret is not remembered"
texts["chatwoot/new"] = json.dumps({"bot_secret": "n1"})
assert secrets.bot_secret("new") == "n1", "a secret created a moment ago works at once"
for bad in (None, "", "not json", "[]", "{}", '{"bot_secret": ""}', '{"bot_secret": 5}', '{"api_token": "x"}'):
    assert api.bot_secret_of(bad) is None, bad
assert api.bot_secret_of('{"bot_secret": "ok"}') == "ok"
print("  ok  the secret is chatwoot/<agent> with a bot_secret field, remembered briefly, a missing or unusable one refused and never remembered")

# ---- the worker: one reply per message ----
def accepted(**kw):
    f = Fakes(**kw)
    assert call(f, event(SAMPLE))[0] == 200
    return f, f.invoked[0]


def run_worker(f, job, now=NOW + 1):
    return api.worker(job, None, now=now, deps=f.wdeps)


def fails(f, job, reason, now=NOW + 1):
    try:
        run_worker(f, job, now)
    except api.ReplyFailed as error:
        assert str(error) == reason, (str(error), reason)
    else:
        raise AssertionError(f"the worker must raise so the failure is visible ({reason})")
    item = f.store.items["msg#shop#501"]
    assert item["status"] == "failed" and item["reason"] == reason and item["finished_at"] == now and "reply_id" not in item, item
    assert run_worker(f, job, now + 1) == {"skipped": "already_handled"}, "a failed message is not run again by itself"


f, job = accepted()
f.answers = [(200, b'{"id": 9001, "content": "Received (message 501)", "sender": {"name": "Bot"}}')]
assert run_worker(f, job) == {"replied": True}
assert len(f.requests) == 1
url, headers, body, timeout = f.requests[0]
assert url == BASE + "/api/v1/accounts/1/conversations/12/messages", url
assert headers["Authorization"] == "Bearer " + TOKEN and "api_access_token" not in headers and timeout == api.HTTP_TIMEOUT_SECONDS
assert json.loads(body) == {"content": "Received (message 501)", "message_type": "outgoing", "private": False}
item = f.store.items["msg#shop#501"]
assert item["status"] == "replied" and item["reply_id"] == 9001 and item["started_at"] == NOW + 1 and item["finished_at"] == NOW + 1
stored = json.dumps(f.store.items)
for secret_text in (TOKEN, SECRET, "4111", "Asha", "Verma", EMAIL, "Bot", "chat.example.org"):
    assert secret_text not in stored, f"{secret_text!r} must not be stored after a reply"
assert run_worker(f, job, now=NOW + 2) == {"skipped": "already_handled"} and len(f.requests) == 1, "the same event delivered twice sends ONE reply"
print("  ok  the worker posts one public reply (fixed text with the message number, the bot's token as a Bearer header) and records replied with the reply's id only")

f, job = accepted()
api.AUTH_STYLE = "api_access_token"
try:
    run_worker(f, job)
finally:
    api.AUTH_STYLE = "bearer"
headers = f.requests[0][1]
assert headers["api_access_token"] == TOKEN and "Authorization" not in headers
print("  ok  the older api_access_token header is used when the setting says so")

for status, reason in ((401, "http_401"), (403, "http_403"), (404, "http_404"), (422, "http_422"), (429, "http_429"), (500, "http_5xx"), (503, "http_5xx"), (400, "http_other"), (302, "http_other")):
    f, job = accepted()
    f.answers = [(status, b'{"error": "nope", "echo": "' + TOKEN.encode() + b'"}')]
    fails(f, job, reason)
    assert len(f.requests) == 1, "no automatic retry, whatever the answer"
f, job = accepted()
f.answers = [api.TimeoutFailure("timeout")]
fails(f, job, "timeout")
assert len(f.requests) == 1, "after a timeout the message may exist: it is never sent again"
f, job = accepted()
f.answers = [api.NetworkFailure("ConnectionRefusedError")]
fails(f, job, "network")
assert len(f.requests) == 1
print("  ok  every failure (401, 403, 404, 422, 429, 5xx, other, timeout, network) is recorded with its code, raised, and never retried")

# ---- refused before anything is sent ----
f, job = accepted(extra={"api_token": TOKEN, "account_id": 2})
fails(f, job, "account_mismatch")
assert not f.requests, "a message of another account never gets a reply"
f, job = accepted(extra={"api_token": TOKEN, "account_id": "1"})
assert run_worker(f, job) == {"replied": True}, "an account id written as text in the secret is accepted"
for extra, reason in (({"account_id": 1}, "no_token"), ({"api_token": "", "account_id": 1}, "no_token"), ({"api_token": TOKEN}, "bad_secret"), ({"api_token": TOKEN, "account_id": 0}, "bad_secret"),
                      ({"api_token": TOKEN, "account_id": True}, "bad_secret"), ({"api_token": TOKEN, "account_id": "one"}, "bad_secret"), ({"api_token": "has a space 12345", "account_id": 1}, "bad_secret"),
                      ({"api_token": "line\nbreak12345678", "account_id": 1}, "bad_secret"), ({"api_token": 12345678901, "account_id": 1}, "no_token")):
    f, job = accepted(extra=extra)
    fails(f, job, reason)
    assert not f.requests, extra
f, job = accepted()
f.table.pop("shop")
fails(f, job, "no_secret")
assert not f.requests
f, job = accepted()


class DownSecrets:
    def credentials(self, agent):
        raise RuntimeError("down")


f.wdeps.secrets = DownSecrets()
fails(f, job, "secret_unavailable")
assert not f.requests
for base in ("", "http://chat.example.org", "https://chat.example.org/api", "https://user:pw" + chr(64) + "chat.example.org"):
    f, job = accepted()
    api.BASE_URL = base
    try:
        fails(f, job, "bad_base_url")
    finally:
        api.BASE_URL = BASE
    assert not f.requests, base
f, job = accepted()
api.AUTH_STYLE = "basic"
try:
    fails(f, job, "bad_setting")
finally:
    api.AUTH_STYLE = "bearer"
assert not f.requests
print("  ok  another account's message, a missing, empty or odd token or account id, a missing secret, a down secret store, a wrong address and a wrong header setting all fail before anything is sent")

# ---- the worker asks the registry again, fresh: a switch-off or a change after the message was accepted stops the reply ----
for label, build in (("agent switched off", lambda f: f.store.items["agent#shop"].update(enabled=False)), ("tenant switched off", lambda f: f.store.items["tenant#acme"].update(enabled=False)),
                     ("agent removed", lambda f: f.store.forget("agent#shop")), ("account changed", lambda f: f.store.items["agent#shop"].update(account_id=7)),
                     ("inbox removed", lambda f: f.store.items["agent#shop"].update(inbox_ids=[8]))):
    f, job = accepted()
    build(f)
    fails(f, job, "not_mapped")
    assert not f.requests, label
f, job = accepted()
f.wdeps.registry = DownRegistry()
fails(f, job, "registry_unavailable")
assert not f.requests
f, job = accepted()
worker_registry = reg.Registry(f.store, ttl=0)
worker_registry.load("shop")
assert not worker_registry.cache, "a worker registry with ttl 0 remembers nothing"
print("  ok  the worker asks the registry again just before replying: a message accepted earlier is not answered after its agent was switched off or changed")

# ---- the worker only takes what the secret says it takes: never the bot secret ----
f, job = accepted()
run_worker(f, job)
assert all("bot_secret" not in value for _, value in f.wdeps.secrets.cache.values()) and SECRET not in repr(f.wdeps.secrets.cache), "the worker never holds the bot secret"
print("  ok  the worker's copy of the secret holds the token and the account id and not the bot secret")

# ---- a bug and a bad event ----
for bad in ({}, {"agent_id": "shop"}, {"agent_id": "Shop", "message_id": 1, "conversation_id": 1, "account_id": 1, "inbox_id": 1},
            {"agent_id": "shop", "message_id": "501", "conversation_id": 12, "account_id": 1, "inbox_id": 3},
            {"agent_id": "shop", "message_id": True, "conversation_id": 12, "account_id": 1, "inbox_id": 3},
            {"agent_id": "shop", "message_id": 501, "conversation_id": 0, "account_id": 1, "inbox_id": 3},
            {"agent_id": "shop", "message_id": 501, "conversation_id": 12, "account_id": "1", "inbox_id": 3}):
    f = Fakes()
    assert api.worker(bad, None, deps=f.wdeps) == {"skipped": "bad_event"} and not f.requests, bad
f, job = accepted()
missing = dict(job, message_id=999)
assert run_worker(f, missing) == {"skipped": "already_handled"} and "msg#shop#999" not in f.store.items and not f.requests, "a message with no item is not invented and not answered"

original = api.answer
api.answer = lambda event, deps: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    f, job = accepted()
    try:
        run_worker(f, job)
        raise AssertionError("the worker must raise so the failure is visible")
    except RuntimeError:
        pass
    assert f.store.items["msg#shop#501"]["status"] == "failed" and f.store.items["msg#shop#501"]["reason"] == "error" and not f.requests
    assert run_worker(f, job, now=NOW + 2) == {"skipped": "already_handled"}
finally:
    api.answer = original
f, job = accepted()
f.answers = [ValueError("an unexpected error from the client")]
try:
    run_worker(f, job)
    raise AssertionError
except ValueError:
    pass
assert f.store.items["msg#shop#501"]["reason"] == "error" and len(f.requests) == 1
print("  ok  a bad event, a message with no item and an unexpected error are handled without a second reply and the error stays visible")


# ---- the DynamoDB store sends exactly the conditional writes the design needs ----
from botocore.exceptions import ClientError  # noqa: E402  (the tests need boto3 anyway, like the other Lambda tests)


def error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "FakeOperation")


class FakeTable:
    """Enough of a DynamoDB table to see that the right condition is sent and its failure is understood."""

    def __init__(self):
        self.items, self.calls = {}, []

    def put_item(self, Item, ConditionExpression):
        self.calls.append(("put", ConditionExpression))
        assert ConditionExpression == "attribute_not_exists(pk)"
        if Item["pk"] in self.items:
            raise error(api.CONDITION_FAILED)
        self.items[Item["pk"]] = dict(Item)

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        self.calls.append(("update", UpdateExpression, ConditionExpression))
        item = self.items.get(Key["pk"])
        names, values = ExpressionAttributeNames, ExpressionAttributeValues
        if ConditionExpression == "attribute_exists(pk)":
            ok = item is not None
        elif ConditionExpression == "#s = :received":
            ok = item is not None and item.get(names["#s"]) == values[":received"]
        else:
            raise AssertionError(ConditionExpression)
        if not ok:
            raise error(api.CONDITION_FAILED)
        if "ADD" in UpdateExpression:
            item["duplicates"] = item.get("duplicates", 0) + values[":one"]
            item[names["#t"]] = values[":now"]
        else:
            for token in UpdateExpression.replace("SET ", "").split(","):
                name, value = [part.strip() for part in token.split("=")]
                item[names[name]] = values[value]

    def delete_item(self, Key):
        self.calls.append(("delete",))
        self.items.pop(Key["pk"], None)


table = FakeTable()
store = api.DynamoStore(table)
record = {"message_id": 7, "status": "received", "ttl": NOW + 1}
assert store.claim("msg#a#7", record) is True and store.claim("msg#a#7", record) is False, "the second write of the same message loses"
store.note_duplicate("msg#a#7", NOW + 4)
store.note_duplicate("msg#a#gone", NOW + 4)  # an item that expired meanwhile: no error, nothing created
assert table.items["msg#a#7"]["duplicates"] == 1 and "msg#a#gone" not in table.items
assert store.begin("msg#a#7", NOW + 5) is True and store.begin("msg#a#7", NOW + 6) is False and table.items["msg#a#7"]["status"] == "processing"
store.finish("msg#a#7", "replied", NOW + 7, {"reply_id": 9001})
assert table.items["msg#a#7"]["status"] == "replied" and table.items["msg#a#7"]["finished_at"] == NOW + 7 and table.items["msg#a#7"]["reply_id"] == 9001
store.finish("msg#a#7", "failed", NOW + 8, {"reason": "http_401"})
assert table.items["msg#a#7"]["status"] == "failed" and table.items["msg#a#7"]["reason"] == "http_401"
store.finish("msg#a#7", "processed", NOW + 9)
store.finish("msg#a#gone", "replied", NOW, {"reply_id": 1})
store.forget("msg#a#7")
assert "msg#a#7" not in table.items
assert [c[0] for c in table.calls].count("put") == 2 and all(c[2] in ("attribute_exists(pk)", "#s = :received") for c in table.calls if c[0] == "update")


class Denied(FakeTable):
    def put_item(self, Item, ConditionExpression):
        raise error("AccessDeniedException")


try:
    api.DynamoStore(Denied()).claim("k", {})
    raise AssertionError("a real error must not be taken for a repeat")
except ClientError:
    pass
print("  ok  the DynamoDB store claims with attribute_not_exists, counts repeats, lets one worker begin, and never takes a real error for a repeat")

# ---- nothing that was logged holds a secret, a signature, a customer's words or a name ----
logged = chr(10).join(LOGS)
assert logged and "refused" in logged and "duplicate" in logged and "failed" in logged, "the functions do log what happened"
for leak in (SECRET, TOKEN, "forged", "sha256=", "4111", "Asha", "Verma", EMAIL, "card number", "bot_secret", "api_token", "Bearer", "chat.example.org"):
    assert leak not in logged, f"{leak!r} must never be logged"
print("  ok  the log lines hold names, reasons and ids only: no secret, no signature, no message text, no name")

print("test_chatwoot_api.py: receiver, worker reply, secrets and store")
