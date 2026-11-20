"""The Chatwoot receiver and worker (lambda/chatwoot_api.py) run on in-memory fakes: a first delivery is accepted and handed over once, a repeat is only counted,
nothing a customer wrote is ever stored, a forged or stale call is refused and leaves no trace, a failed hand-over lets Chatwoot's retry through, the worker
posts exactly one reply per message (never a second one, whatever fails), refuses another account's message, records every failure with a code, never leaks the
token, and the DynamoDB store sends exactly the conditional writes the design relies on. The real HTTP client is tested in test_chatwoot_http.py."""
import base64
import json
import os
import sys
import time

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_api as api  # noqa: E402
import chatwoot_core as core  # noqa: E402
import chatwoot_handover as ho  # noqa: E402
import chatwoot_memory as mem  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_runtime as rt  # noqa: E402

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
LIVE_CONFIG = {"instructions": "Speak like a friendly shopkeeper.", "language": "en", "collections": ["faq"], "fallback_text": "Sorry, a person will help you soon.", "max_answer_chars": 300, "mode": "rag"}
HIT = {"doc": "faq-doc", "title": "Faq handbook", "page": 1, "page_end": 1, "section": "Opening hours", "text": "Our shop is open from nine to six.", "vector": 0.9, "collection": "faq"}
SAMPLE = {
    "event": "message_created", "id": 501, "content": TEXT, "message_type": "incoming", "private": False,
    "sender": {"id": 9, "type": "contact", "name": "Asha Verma", "email": EMAIL},
    "conversation": {"id": 12, "inbox_id": 3, "status": "pending"}, "account": {"id": 1, "name": "Shop"}, "inbox": {"id": 3, "name": "Website"},
}


def register(store, agent="shop", tenant="acme", account=1, inboxes=(3,), enabled=True, tenant_enabled=True):
    """What the registry holds for an agent: its tenant and the one account and the inboxes it may serve (the message in SAMPLE is account 1, inbox 3)."""
    store.claim(reg.tenant_key(tenant), {"tenant_id": tenant, "enabled": tenant_enabled, "key_hash": "x", "agents": [agent], "rev": 1})
    store.claim(reg.agent_key(agent), {"tenant_id": tenant, "agent_id": agent, "account_id": account, "inbox_ids": list(inboxes), "enabled": enabled, "live_version": 1, "versions": 1, "rev": 1})
    store.claim(reg.version_key(agent, 1), {"agent_id": agent, "tenant_id": tenant, "version": 1, "config": dict(LIVE_CONFIG), "published_at": 1})


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
        self.searches, self.llm_calls = [], []
        self.found = {"hits": [HIT], "confident": True, "tokens": 20, "cost_micro": 3, "missing": []}  # what the shelves return
        self.llm_answer = {"text": "We open at nine and close at six.", "model": "m", "tokens": 100, "cost_micro": 40}  # what the model returns
        self.search_error = self.llm_error = None
        self.runtime = rt.make_runtime(self.search_shelves, self.ask)
        self.mem = mem.MemoryOnly()  # the conversation memory's own table
        if registered:
            register(self.store)
        self.deps = api.Deps(self.store, api.Secrets(self.read), self.invoke, registry=reg.Registry(self.store, ttl=0))
        self.wdeps = api.Deps(self.store, api.Secrets(self.read, api.credentials_of), http=self.post, registry=reg.Registry(self.store, ttl=0), runtime=self.runtime, memory=self.mem)

    def read(self, name):
        self.secret_reads.append(name)
        agent = name[len(api.SECRET_PREFIX):]
        return json.dumps({"bot_secret": self.table[agent], **self.extra}) if agent in self.table else None

    def invoke(self, payload):
        if self.fail_invoke:
            raise RuntimeError("throttled")
        self.invoked.append(payload)

    def search_shelves(self, tenant, collections, query):
        self.searches.append((tenant, list(collections), query))
        if self.search_error:
            raise self.search_error
        return self.found

    def ask(self, messages, max_tokens):
        self.llm_calls.append((messages, max_tokens))
        if self.llm_error:
            raise self.llm_error
        return self.llm_answer

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
assert len(f.invoked) == 1 and f.invoked[0] == {"agent_id": "shop", "delivery_id": "d-1", "text": TEXT, "channel": "", "message_id": 501, "conversation_id": 12, "account_id": 1, "inbox_id": 3}, "the text reaches the worker in the event only"
item = f.store.items["msg#shop#501"]
assert item["status"] == "received" and item["received_at"] == NOW and item["ttl"] == NOW + api.DEDUPE_TTL_DAYS * 86400 and item["delivery_id"] == "d-1"
stored = json.dumps(f.store.items)  # the table; the event to the worker carries the text on purpose and is not stored anywhere
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

    def load(self, name):
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
f.answers = [(200, b'{"id": 9001, "content": "We open at nine and close at six.", "sender": {"name": "Bot"}}')]
assert run_worker(f, job) == {"replied": True}
assert len(f.requests) == 1
url, headers, body, timeout = f.requests[0]
assert url == BASE + "/api/v1/accounts/1/conversations/12/messages", url
assert headers["Authorization"] == "Bearer " + TOKEN and "api_access_token" not in headers and timeout == api.HTTP_TIMEOUT_SECONDS
assert json.loads(body) == {"content": "We open at nine and close at six.", "message_type": "outgoing", "private": False}
item = f.store.items["msg#shop#501"]
assert item["status"] == "replied" and item["reply_id"] == 9001 and item["started_at"] == NOW + 1 and item["finished_at"] == NOW + 1
stored = json.dumps(f.store.items)
for secret_text in (TOKEN, SECRET, "4111", "Asha", "Verma", EMAIL, "Bot", "chat.example.org"):
    assert secret_text not in stored, f"{secret_text!r} must not be stored after a reply"
assert run_worker(f, job, now=NOW + 2) == {"skipped": "already_handled"} and len(f.requests) == 1, "the same event delivered twice sends ONE reply"
print("  ok  the worker posts one public reply (the answer from the books, the bot's token as a Bearer header) and records replied with the reply's id only")

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

# ---- the answer: the live version, the books, the model and the month's spend ----
HANDOVER_TEXT = ho.DEFAULT_TEXT

def handed(f, job, reason, sentence=None, now=NOW + 1, expect_flags=(1, 1, 1)):
    """The worker hands the conversation to a person: the customer's one sentence, a private note, the status open (in this order), recorded as flags on the item."""
    assert run_worker(f, job, now) == {"replied": True}
    sent, note, opened = f.requests[-3:]
    assert json.loads(sent[2]) == {"content": sentence or LIVE_CONFIG.get("handover_text") or HANDOVER_TEXT, "message_type": "outgoing", "private": False}, "the customer gets the one fixed sentence"
    body = json.loads(note[2])
    assert body["private"] is True and body["content"].startswith("Bot hand-over: ") and len(body["content"]) <= 1500, "a private note for the team"
    assert opened[0].endswith("/api/v1/accounts/1/conversations/12/toggle_status") and json.loads(opened[2]) == {"status": "open"}, "the conversation becomes open"
    item = f.store.items["msg#shop#501"]
    assert item["status"] == "replied" and item["route"] == "handover" and item["handover"] == reason, item
    assert (item.get("handover_text"), item["handover_note"], item["handover_open"]) == expect_flags
    assert TEXT not in json.dumps({k: v for k, v in f.store.items.items() if k.startswith("msg#")}), "the customer's words are in the private note in Chatwoot, never in the item"
    return item


MONTH = "usage#acme#" + time.strftime("%Y%m", time.gmtime(NOW + 1))
f, job = accepted()
assert run_worker(f, job) == {"replied": True}
assert f.searches == [("acme", ["faq"], TEXT)] and len(f.llm_calls) == 1, "one search of the agent's own shelves, one model call"
assert json.loads(f.requests[0][2])["content"] == "We open at nine and close at six."
item = f.store.items["msg#shop#501"]
assert (item["route"], item["confident"], item["hits"], item["shelves"], item["tokens"], item["cost_micro"], item["sources"]) == ("rag", True, 1, ["faq"], 120, 43, [["faq-doc", 1]])
assert isinstance(item["retrieval_ms"], int) and isinstance(item["llm_ms"], int) and item["status"] == "replied" and item["reply_id"] == 9001
assert f.store.usage(MONTH) == 43, "what the answer cost is counted against the business's month"
stored = json.dumps({k: v for k, v in f.store.items.items() if k.startswith(("msg#", "usage#"))})  # message items and spend counters (the registry holds the business's own configuration)
for words in ("4111", "Asha", "Verma", "nine", "close at six", "Opening hours", "friendly shopkeeper", TOKEN, SECRET, "Faq handbook"):
    assert words not in stored, f"{words!r} must not be in the table"
assert run_worker(f, job, now=NOW + 2) == {"skipped": "already_handled"} and len(f.llm_calls) == 1 and f.store.usage(MONTH) == 43, "a repeated event neither asks the model nor spends again"
print("  ok  the worker answers from the agent's live version: one search of its own shelves, one model call, the reply posted, numbers-only metrics on the item and the cost counted for the month")

f, job = accepted()
f.found = {"hits": [HIT], "confident": False, "tokens": 20, "cost_micro": 3, "missing": []}
assert run_worker(f, job) == {"replied": True}
assert json.loads(f.requests[0][2])["content"] == LIVE_CONFIG["fallback_text"] and not f.llm_calls and f.store.items["msg#shop#501"]["route"] == "fallback" and f.store.usage(MONTH) == 3, "not sure: the fallback text, no model call, only the search is counted"
f, job = accepted()
f.llm_answer = {"text": rt.NOT_SURE, "model": "m", "tokens": 100, "cost_micro": 40}
run_worker(f, job)
assert json.loads(f.requests[0][2])["content"] == LIVE_CONFIG["fallback_text"] and f.store.usage(MONTH) == 43
f, job = accepted()
run_worker(f, {k: v for k, v in job.items() if k != "text"})
assert json.loads(f.requests[0][2])["content"] == LIVE_CONFIG["fallback_text"] and not f.searches, "a message without text (an image) gets the fallback"
print("  ok  when the books are not sure, the model does not know, or there is no text, the customer gets the agent's fallback text (and only what was spent is counted)")

# the business's month
cap = api.DEFAULT_CAP_USD * 1000000
f, job = accepted()
f.store.usage_add(MONTH, cap, NOW)
handed(f, job, "cap_reached")
assert not f.searches and not f.llm_calls and len(f.requests) == 3 and f.store.usage(MONTH) == cap, "a business at its cap costs nothing more: its customer is handed to a person, not answered"
f, job = accepted()
f.store.usage_add(MONTH, cap - 1, NOW)
assert run_worker(f, job) == {"replied": True} and f.store.usage(MONTH) == cap - 1 + 43, "just under the cap is answered (one answer may go over it)"
f, job = accepted()
f.store.items["tenant#acme"]["monthly_cap_micro"] = 100
f.store.usage_add(MONTH, 100, NOW)
handed(f, job, "cap_reached")
f, job = accepted()
f.store.items["tenant#acme"]["monthly_cap_micro"] = 100000000
f.store.usage_add(MONTH, cap, NOW)
assert run_worker(f, job) == {"replied": True}, "a business's own cap replaces the default"
f, job = accepted()
f.store.items["tenant#acme"]["monthly_cap_micro"] = "lots"
f.store.usage_add(MONTH, cap, NOW)
handed(f, job, "cap_reached")
f, job = accepted()
f.store.usage_add(MONTH, cap, NOW)
assert run_worker(f, job, now=NOW + 40 * 86400) == {"replied": True}, "a new month starts a new count"
print("  ok  a business at its monthly cap is refused before anything is spent; its own cap replaces the default; a new month starts again")

# the live version
f, job = accepted()
f.store.items["agent#shop"]["live_version"] = 0
fails(f, job, "no_live_version")
assert not f.searches and not f.requests
f, job = accepted()
f.store.forget("agent#shop#v1")
handed(f, job, "bad_config")
for change in ({"collections": []}, {"fallback_text": ""}, {"language": "fr"}, {"mode": "robot"}):
    f, job = accepted()
    f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, **change}
    handed(f, job, "bad_config")
    assert not f.searches and not f.llm_calls, change
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = "not an object"
handed(f, job, "bad_config")
print("  ok  an agent with no live version stays silent and spends nothing; a missing or unusable configuration cannot answer, so its customer is handed to a person (the default sentence)")

# failures while answering
f, job = accepted()
f.search_error = RuntimeError("an error that mentions 4111 and Asha")
item = handed(f, job, "retrieval_error", sentence=LIVE_CONFIG.get("handover_text") or ho.DEFAULT_TEXT)
assert item["error"] == "RuntimeError" and "4111" not in json.dumps(f.store.items) and len(f.requests) == 3 and f.store.usage(MONTH) == 0
f, job = accepted()
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": ["faq"]}
handed(f, job, "no_shelf")
f, job = accepted()
f.llm_error = RuntimeError("the model is down")
handed(f, job, "llm_error")
assert f.store.usage(MONTH) == 3 and len(f.requests) == 3, "the search was paid for even though the model failed; the customer is handed to a person, never left in silence"
f, job = accepted()
f.answers = [(500, b"oops")]
fails(f, job, "http_5xx")
item = f.store.items["msg#shop#501"]
assert item["route"] == "rag" and item["cost_micro"] == 43 and f.store.usage(MONTH) == 43, "a reply that Chatwoot refused was still paid for: it is recorded and counted"


class NoCounter(api.MemoryStore):
    def usage_add(self, key, micro, now):
        raise RuntimeError("down")


f, job = accepted()
f.store.__class__ = NoCounter
assert run_worker(f, job) == {"replied": True}, "a counter that cannot be written does not stop the reply"
print("  ok  a search error, a missing shelf, a model error and a refused reply are recorded with a code and numbers; what was spent is counted; a counter that cannot be written does not stop a reply")

# the agent route inside the worker: mode auto, and the books are not sure
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "mode": "auto"}
f.found = {"hits": [HIT], "confident": False, "tokens": 20, "cost_micro": 3, "missing": []}
f.llm_answer = {"text": json.dumps({"final": "The agent answer."}), "model": "m", "tokens": 30, "cost_micro": 40}
assert run_worker(f, job) == {"replied": True}
assert json.loads(f.requests[0][2])["content"] == "The agent answer."
item = f.store.items["msg#shop#501"]
assert (item["route"], item["agent_steps"], item["agent_status"], item["confident"], item["cost_micro"]) == ("agent", 1, "done", False, 43) and f.store.usage(MONTH) == 43
assert "agent answer" not in json.dumps({k: v for k, v in f.store.items.items() if k.startswith(("msg#", "usage#"))}), "the answer is sent, never stored"
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "mode": "auto"}
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
f.llm_error = RuntimeError("down")
handed(f, job, "llm_error")
f, job = accepted()
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
assert run_worker(f, job) == {"replied": True} and f.store.items["msg#shop#501"]["route"] == "fallback" and not f.llm_calls, "mode rag never starts the agent"
print("  ok  in mode auto a message the books are not sure about is answered by the agent loop, counted and recorded as numbers; in mode rag the fallback is used")

# ---- conversation memory: the turns of a conversation, kept briefly in their own table, never anywhere else ----
HOUR = 3600
f, job = accepted()
assert run_worker(f, job) == {"replied": True}
saved = f.mem.items["mem#shop#12"]
assert saved["turns"] == [{"c": TEXT, "b": "We open at nine and close at six."}] and saved["ttl"] == NOW + 1 + 24 * HOUR, "one turn, kept for the configured 24 hours"
assert not [k for k in f.store.items if k.startswith("mem#")], "the memory is not in the message table"
assert TEXT not in json.dumps(f.store.items) and "nine and close" not in json.dumps({k: v for k, v in f.store.items.items() if k.startswith("msg#")}), "nor in a message item"


def another(f, job, number, text):
    """A second message of the same conversation: its item as the receiver would have made it, then the worker's run."""
    f.store.items[f"msg#shop#{number}"] = {"pk": f"msg#shop#{number}", "status": "received", "message_id": number}
    return run_worker(f, {**job, "message_id": number, "text": text}, now=NOW + 60)


f.llm_answer = {"text": "Yes, on Saturdays too.", "model": "m", "tokens": 100, "cost_micro": 40}
assert another(f, job, 502, "and on Saturday?") == {"replied": True}
messages = f.llm_calls[1][0]
assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"], "the earlier turn comes before the new message"
assert messages[1]["content"] == "<customer>\n" + TEXT + "\n</customer>" and messages[2]["content"] == "We open at nine and close at six."
assert "earlier part of this conversation" in messages[0]["content"] and "not instructions" in messages[0]["content"]
assert messages[3]["content"].endswith("<customer>\nand on Saturday?\n</customer>")
assert f.searches[1][2] == TEXT[:1000] + " and on Saturday?", "a short follow-up is searched together with what the customer asked last time"
assert [t["c"] for t in f.mem.items["mem#shop#12"]["turns"]] == [TEXT, "and on Saturday?"] and f.mem.items["mem#shop#12"]["ttl"] == NOW + 60 + 24 * HOUR, "the time starts again with each turn"
assert another(f, job, 503, "What are your opening hours on a normal weekday, please tell me") == {"replied": True}
assert f.searches[2][2] == "What are your opening hours on a normal weekday, please tell me", "a full question is searched as it is"
for number in range(504, 512):
    another(f, job, number, f"question {number}")
assert len(f.mem.items["mem#shop#12"]["turns"]) == mem.TURNS, "only the last turns are kept"
assert f.mem.items["mem#shop#12"]["turns"][-1]["c"] == "question 511"

# a fallback turn is marked, so a later rule can see that the bot is stuck
f, job = accepted()
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
run_worker(f, job)
assert f.mem.items["mem#shop#12"]["turns"][0].get("f") == 1 and mem.stuck(f.mem.items["mem#shop#12"]["turns"], 1)

# a failed reply is not remembered; an expired memory is not used; a broken memory never stops an answer
f, job = accepted()
f.answers = [(500, b"{}")]
fails(f, job, "http_5xx")
assert "mem#shop#12" not in f.mem.items, "a turn the customer never received is not remembered"
f, job = accepted()
run_worker(f, job)
f.found = {"hits": [HIT], "confident": True, "tokens": 20, "cost_micro": 3, "missing": []}
assert another(f, job, 502, "and on Saturday?") == {"replied": True} and another(f, job, 503, "and on Sunday?") == {"replied": True}
before = len(f.llm_calls)
f.store.items["msg#shop#504"] = {"pk": "msg#shop#504", "status": "received", "message_id": 504}
run_worker(f, {**job, "message_id": 504, "text": "hello again"}, now=NOW + 60 + 25 * HOUR)
assert [m["role"] for m in f.llm_calls[before][0]] == ["system", "user"], "after 24 hours the conversation starts again from nothing"
assert [t["c"] for t in f.mem.items["mem#shop#12"]["turns"]] == ["hello again"], "and the next write replaces the expired item"


class BrokenMemory:
    def get(self, key):
        raise RuntimeError("down 4111 Asha")

    def put(self, key, item):
        raise RuntimeError("down 4111 Asha")


f, job = accepted()
f.wdeps.memory = BrokenMemory()
assert run_worker(f, job) == {"replied": True} and json.loads(f.requests[0][2])["content"], "a memory that is down never stops an answer"
assert [e for e in LOGS if "memory_unavailable" in e] and not [e for e in LOGS if "4111" in e], "it is logged by error type only"
f, job = accepted()
f.wdeps.memory = None
assert run_worker(f, job) == {"replied": True} and [m["role"] for m in f.llm_calls[0][0]] == ["system", "user"], "no memory table: no memory, same answers as before"
saved_seconds = mem.HOURS_SECONDS
mem.HOURS_SECONDS = 0
f, job = accepted()
assert run_worker(f, job) == {"replied": True} and not f.mem.items, "memoryHours 0: nothing is read and nothing is stored"
mem.HOURS_SECONDS = saved_seconds
print("  ok  the worker remembers each answered turn for 24 hours in its own table (never in message items, logs or metrics), shows earlier turns to the model, searches short follow-ups with the last question, and keeps working when memory is off, expired or down")

# ---- hand-over to a person: the rules, the three steps, once per conversation, and what is recorded ----
def say(f, job, text, number=501, now=NOW + 60):
    """A message of the same conversation (its item as the receiver would have made it), run by the worker."""
    f.store.items[f"msg#shop#{number}"] = {"pk": f"msg#shop#{number}", "status": "received", "message_id": number}
    return api.worker({**job, "message_id": number, "text": text}, None, now=now, deps=f.wdeps)


for words, why in (("can I talk to a person please", "person_asked"), ("mujhe insaan se baat karni hai", "person_asked"), ("मुझे किसी इंसान से बात करनी है", "person_asked"), ("please call me tomorrow", "person_asked"),
                   ("this is a fraud, I want my money back", "complaint"), ("aapne dhokha diya", "complaint"), ("आपने धोखा दिया", "complaint"), ("my number is 9876543210", "warm_lead"),
                   ("call on +91 98765 43210", "warm_lead"), ("I want to book the classroom", "warm_lead"), ("mujhe kal aana hai", "warm_lead"), ("मुझे कल आना है", "warm_lead")):
    f, job = accepted()
    run = say(f, job, words, number=501)
    item = f.store.items["msg#shop#501"]
    assert run == {"replied": True} and item["handover"] == why, (words, item.get("handover"), why)
    assert not f.searches and not f.llm_calls and len(f.requests) == 3, f"{words!r}: no search, no model call, three Chatwoot calls"
    assert words in json.loads(f.requests[1][2])["content"], "the team's private note holds what the customer wrote"
assert f.mem.items["mem#shop#12"]["turns"][-1] == {"c": "मुझे कल आना है", "b": HANDOVER_TEXT, "h": 1}, "the hand-over turn is remembered and marked"
print("  ok  a person asked for, a complaint, a mobile number or a wish to go ahead hands the conversation over without a search or a model call: sentence, private note, open")

for words in ("what is the refund policy", "do you have a book on marketing", "what is the price of content creation", "order number 123456789012", "my age is 25 and I am from Darbhanga", "interested in social media management"):
    f, job = accepted()
    assert say(f, job, words) == {"replied": True} and f.store.items["msg#shop#501"].get("route") == "rag" and "handover" not in f.store.items["msg#shop#501"], words
print("  ok  ordinary questions (a refund policy, a price, an interest in a service, an order number) are answered, not handed over")

# an agent can switch reasons off; an empty list switches the content reasons off, a failure still hands over
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "handover_on": ["complaint"]}
say(f, job, "call me")
assert f.store.items["msg#shop#501"]["route"] == "rag", "person_asked is off for this agent"
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "handover_on": []}
say(f, job, "this is a fraud")
assert f.store.items["msg#shop#501"]["route"] == "rag"
f.llm_error = RuntimeError("down")
say(f, job, "what time do you open", number=502)
assert f.store.items["msg#shop#502"]["handover"] == "llm_error", "a failure of the answer always hands over"
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "handover_text": "A person will write to you."}
say(f, job, "call me")
assert json.loads(f.requests[0][2])["content"] == "A person will write to you.", "the agent's own sentence"
for bad in ({"handover_on": ["everything"]}, {"handover_on": "all"}, {"handover_on": ["stuck", "stuck"]}, {"handover_text": ""}, {"handover_text": "x" * 600}, {"handover_text": 5}):
    f, job = accepted()
    f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, **bad}
    handed(f, job, "bad_config")
print("  ok  an agent can choose its reasons and its sentence; a bad choice is an unusable configuration; a failure of the answer hands over whatever the reasons")

# stuck: the books are not sure twice in a row
f, job = accepted()
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
say(f, job, "hello", number=501)
assert f.store.items["msg#shop#501"]["route"] == "fallback" and len(f.requests) == 1, "the first fallback is the agent's fallback text"
say(f, job, "anything else", number=502)
item = f.store.items["msg#shop#502"]
assert item["route"] == "handover" and item["handover"] == "stuck" and len(f.requests) == 4, "the second fallback in a row hands over instead"
f.found = {"hits": [HIT], "confident": True, "tokens": 20, "cost_micro": 3, "missing": []}
say(f, job, "what time do you open", number=503)
assert f.store.items["msg#shop#503"]["route"] == "rag", "after a hand-over the rules do not fire again at once"
f, job = accepted()
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
say(f, job, "hello", number=501)
f.found = {"hits": [HIT], "confident": True, "tokens": 20, "cost_micro": 3, "missing": []}
say(f, job, "what time do you open", number=502)
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
say(f, job, "and now?", number=503)
assert f.store.items["msg#shop#503"]["route"] == "fallback", "fallback, answer, fallback is not stuck"
f, job = accepted()
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "handover_on": ["person_asked"]}
f.found = {"hits": [], "confident": False, "tokens": 0, "cost_micro": 0, "missing": []}
say(f, job, "hello", number=501)
say(f, job, "anything else", number=502)
assert f.store.items["msg#shop#502"]["route"] == "fallback", "stuck can be switched off"
print("  ok  two fallbacks in a row hand the conversation over (the second one), an answer in between resets it, and a hand-over is not repeated at once")

# one step failing does not stop the others, and the item says which
f, job = accepted()
f.answers = [(200, b'{"id": 1}'), (403, b"{}"), (200, b"{}")]
say(f, job, "call me")
item = f.store.items["msg#shop#501"]
assert item["status"] == "replied" and (item["handover_text"], item["handover_note"], item["handover_open"]) == (1, 0, 1) and len(f.requests) == 3
assert [e for e in LOGS if "handover_incomplete" in e] and "403" not in json.dumps(item), "incomplete is logged with codes only"
f, job = accepted()
f.answers = [(200, b'{"id": 1}'), (200, b'{"id": 2}'), (403, b"{}")]
say(f, job, "call me")
item = f.store.items["msg#shop#501"]
assert (item["handover_text"], item["handover_note"], item["handover_open"]) == (1, 1, 0), "the private note reached the team even though the status could not be changed"
f, job = accepted()
f.answers = [(429, b"{}"), (200, b'{"id": 2}'), (200, b"{}")]
try:
    say(f, job, "call me")
    raise AssertionError("the customer's sentence failed: the message is failed, visibly")
except api.ReplyFailed as error:
    assert str(error) == "http_429"
item = f.store.items["msg#shop#501"]
assert item["status"] == "failed" and (item["handover_text"], item["handover_note"], item["handover_open"]) == (0, 1, 1), "but the team still got the note and the conversation is open"
assert "mem#shop#12" not in f.mem.items, "a hand-over sentence the customer never received is not remembered"
f, job = accepted()
f.answers = [api.TimeoutFailure("t"), api.NetworkFailure("n"), (200, b"{}")]
try:
    say(f, job, "call me")
except api.ReplyFailed as error:
    assert str(error) == "timeout"
item = f.store.items["msg#shop#501"]
assert (item["handover_text"], item["handover_note"], item["handover_open"]) == (0, 0, 1)
print("  ok  the three hand-over steps are independent: a refused note or status is recorded and logged by code, a failed sentence fails the message, the others still go")

# the sweeper's event: a stuck message item is handed over with a note and open, and no sentence to the customer
f, job = accepted()
f.store.items["msg#shop#501"].update({"status": "failed", "reason": "stuck", "agent_id": "shop"})
sweep_job = {"kind": "sweep", "agent_id": "shop", "message_id": 501, "conversation_id": 12, "account_id": 1, "inbox_id": 3}
assert api.worker(sweep_job, None, now=NOW + 400, deps=f.wdeps) == {"swept": True}
note, opened = f.requests
assert json.loads(note[2])["private"] is True and "may not have been delivered" in json.loads(note[2])["content"] and opened[0].endswith("/toggle_status")
item = f.store.items["msg#shop#501"]
assert item["status"] == "failed" and item["reason"] == "stuck" and item["handover"] == "stuck_item" and item["handover_note"] == 1 and item["handover_open"] == 1 and "handover_text" not in item
f, job = accepted()
f.store.items["agent#shop"]["enabled"] = False
assert api.worker(sweep_job, None, now=NOW + 400, deps=f.wdeps) == {"skipped": "not_mapped"} and not f.requests and f.store.items["msg#shop#501"]["handover"] == "not_made"
for bad in ({**sweep_job, "conversation_id": "x"}, {**sweep_job, "agent_id": "../x"}, {"kind": "sweep"}):
    assert api.worker(bad, None, now=NOW + 400, deps=f.wdeps) == {"skipped": "bad_event"}
print("  ok  a stuck item is handed over with a private note and open and no sentence to the customer; a switched-off agent or a bad event does nothing")

# the channel travels in the event (a word, not the customer's), and the worker tells the model: WhatsApp rule, inbox note, no second greeting
f = Fakes()
payload = {**SAMPLE, "conversation": {**SAMPLE["conversation"], "channel": "Channel::Whatsapp"}}
assert call(f, event(payload))[0] == 200 and f.invoked[0]["channel"] == "whatsapp"
job = f.invoked[0]
f.store.items["agent#shop#v1"]["config"] = {**LIVE_CONFIG, "inbox_notes": {"3": "This is the business's own WhatsApp number."}}
assert run_worker(f, job) == {"replied": True}
system = f.llm_calls[0][0][0]["content"]
assert "Never suggest that they WhatsApp, message or text that number" in system and "business's own WhatsApp number." in system and "already started" not in system
f.store.items["msg#shop#502"] = {"pk": "msg#shop#502", "status": "received", "message_id": 502}
api.worker({**job, "message_id": 502, "text": "and on Saturday?"}, None, now=NOW + 60, deps=f.wdeps)
assert "already started: do NOT greet" in f.llm_calls[1][0][0]["content"], "the second message of the conversation is not greeted again"
for odd in ("WHATSAPP", "whatsapp\n", "x" * 50, 5, None, "<b>"):
    f, job = accepted()
    assert run_worker(f, {**job, "channel": odd}) == {"replied": True} and "WhatsApp" not in f.llm_calls[0][0][0]["content"] and "same channel" not in f.llm_calls[0][0][0]["content"], odd
f, job = accepted()
run_worker(f, {**job, "channel": "telegram"})
assert "writing on telegram" in f.llm_calls[0][0][0]["content"]
print("  ok  the receiver passes the channel word to the worker, and the worker gives the model the channel rule, the inbox note and the no-second-greeting rule; an odd channel value is ignored")

# the receiver cuts the text and sends nothing when there is none
f = Fakes()
long_text = "word " * 1000
call(f, event({**SAMPLE, "content": long_text}))
assert len(f.invoked[0]["text"]) == core.MAX_QUESTION_CHARS
f = Fakes()
payload = {k: v for k, v in SAMPLE.items() if k != "content"}
call(f, event(payload))
assert f.invoked[0]["text"] == "", "a message without text is handed over with empty text"
assert "word" not in json.dumps(f.store.items)
print("  ok  the receiver hands the worker the customer's words cut to the limit (empty when there are none) and stores none of them")

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
api.answer = lambda event, deps, now=None: (_ for _ in ()).throw(RuntimeError("boom"))
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
