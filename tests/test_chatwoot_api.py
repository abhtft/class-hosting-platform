"""The Chatwoot receiver and worker (lambda/chatwoot_api.py) run on in-memory fakes: a first delivery is accepted and handed over once, a repeat is only counted,
nothing a customer wrote is ever stored, a forged or stale call is refused and leaves no trace, a failed hand-over lets Chatwoot's retry through, the worker
handles a message once, and the DynamoDB store sends exactly the conditional writes the design relies on."""
import base64
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_api as api  # noqa: E402
import chatwoot_core as core  # noqa: E402

LOGS = []  # what the functions print: collected, and checked at the end for secrets and message text
api.log = lambda what, **details: LOGS.append(json.dumps({"chatwoot": what, **details}))

SECRET = "test-only-secret-not-a-real-one"
NOW = 1_800_000_000
EMAIL = "asha" + chr(64) + "example.org"  # built here so no address is written in the repository
TEXT = "my card number is 4111 and my name is Asha Verma"
SAMPLE = {
    "event": "message_created", "id": 501, "content": TEXT, "message_type": "incoming", "private": False,
    "sender": {"id": 9, "type": "contact", "name": "Asha Verma", "email": EMAIL},
    "conversation": {"id": 12, "inbox_id": 3, "status": "pending"}, "account": {"id": 1, "name": "Shop"}, "inbox": {"id": 3, "name": "Website"},
}


class Fakes:
    def __init__(self, secrets=None, fail_invoke=False):
        self.store = api.MemoryStore()
        self.invoked = []
        self.fail_invoke = fail_invoke
        self.secret_reads = []
        table = secrets if secrets is not None else {"shop": SECRET}
        self.deps = api.Deps(self.store, api.Secrets(self.read), self.invoke)
        self.table = table

    def read(self, name):
        self.secret_reads.append(name)
        agent = name[len(api.SECRET_PREFIX):]
        return json.dumps({"bot_secret": self.table[agent]}) if agent in self.table else None

    def invoke(self, payload):
        if self.fail_invoke:
            raise RuntimeError("throttled")
        self.invoked.append(payload)


def event(payload, agent="shop", secret=SECRET, ts=NOW, method="POST", headers=None, path=None, body=None, base64_body=False, delivery="d-1"):
    raw = body if body is not None else json.dumps(payload).encode("utf8")
    head = {"Content-Type": "application/json", "X-Chatwoot-Delivery": delivery}
    if secret is not None:
        head.update({"X-Chatwoot-Timestamp": str(ts), "X-Chatwoot-Signature": core.sign(secret, ts, raw)})
    head.update(headers or {})
    return {"rawPath": path or f"/hooks/chatwoot/{agent}", "requestContext": {"http": {"method": method}}, "headers": head,
            "body": base64.b64encode(raw).decode() if base64_body else raw.decode("utf8"), "isBase64Encoded": base64_body}


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
for secret_text in ("4111", "Asha", "Verma", EMAIL, "card number", SECRET):
    assert secret_text not in stored, f"{secret_text!r} must never be stored or handed over"
print("  ok  a first delivery is accepted, handed to the worker once, and only ids and times are stored (no text, no name, no e-mail)")

# ---- the same message again (Chatwoot's retry, or a double delivery): counted, not handed over again ----
status, body = call(f, event(SAMPLE, delivery="d-2"), now=NOW + 5)
assert (status, body) == (200, {"ok": True, "duplicate": True}) and len(f.invoked) == 1
call(f, event(SAMPLE, delivery="d-3"), now=NOW + 9)
assert f.store.items["msg#shop#501"]["duplicates"] == 2 and f.store.items["msg#shop#501"]["last_duplicate_at"] == NOW + 9 and len(f.invoked) == 1
status, _ = call(f, event({**SAMPLE, "id": 502}), now=NOW + 10)
assert status == 200 and len(f.invoked) == 2 and "msg#shop#502" in f.store.items, "another message is another item"
status, _ = call(Fakes(secrets={"shop": SECRET, "other": SECRET}), event(SAMPLE, agent="other"))
assert status == 200
g = Fakes(secrets={"shop": SECRET, "other": SECRET})
call(g, event(SAMPLE)), call(g, event(SAMPLE, agent="other"))
assert set(g.store.items) == {"msg#shop#501", "msg#other#501"}, "the same message id in two agents is two items"
print("  ok  a repeat is only counted, a different message and another agent's message with the same id are separate")

# ---- calls that are not from Chatwoot leave nothing behind ----
def refused(ev, expect=401, note=""):
    f = Fakes()
    status, body = call(f, ev)
    assert status == expect and not f.invoked and not f.store.items, (note, status, body)
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
    assert (status, body) == (200, {"ok": True, "ignored": why}) and not f.invoked and not f.store.items, (change, status, body)
f = Fakes()
assert call(f, event(None, body=b"not json"))[1] == {"ok": True, "ignored": "not_json"}, "a signed body that is not JSON is ignored, not a crash"
print("  ok  the bot's own replies, agents' replies, private notes, other events and taken-over conversations are acknowledged and ignored")

# ---- the hand-over fails: 500 and no item, so Chatwoot's retry is accepted (it retries only 429 and 500) ----
f = Fakes(fail_invoke=True)
status, body = call(f, event(SAMPLE))
assert (status, body) == (500, {"error": "try_again"}) and not f.store.items
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
assert call(f, event(SAMPLE))[0] == 500 and not f.invoked and not f.store.items


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

# ---- the worker: handles a message once ----
f = Fakes()
call(f, event(SAMPLE))
job = f.invoked[0]
assert api.worker(job, None, now=NOW + 1, deps=f.deps) == {"processed": True}
item = f.store.items["msg#shop#501"]
assert item["status"] == "processed" and item["started_at"] == NOW + 1 and item["finished_at"] == NOW + 1
assert api.worker(job, None, now=NOW + 2, deps=f.deps) == {"skipped": "already_handled"} and item["started_at"] == NOW + 1, "a second event for the same message does nothing"
for bad in ({}, {"agent_id": "shop"}, {"agent_id": "Shop", "message_id": 1}, {"agent_id": "shop", "message_id": "501"}, {"agent_id": "shop", "message_id": True}):
    assert api.worker(bad, None, deps=f.deps) == {"skipped": "bad_event"}, bad
assert api.worker({"agent_id": "shop", "message_id": 999}, None, deps=f.deps) == {"skipped": "already_handled"}, "a message with no item is not invented"
assert "msg#shop#999" not in f.store.items

original = api.answer
api.answer = lambda event, deps: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    g = Fakes()
    call(g, event(SAMPLE))
    try:
        api.worker(g.invoked[0], None, now=NOW + 1, deps=g.deps)
        raise AssertionError("the worker must raise so the failure is visible")
    except RuntimeError:
        pass
    assert g.store.items["msg#shop#501"]["status"] == "failed" and g.store.items["msg#shop#501"]["finished_at"] == NOW + 1
    assert api.worker(g.invoked[0], None, now=NOW + 2, deps=g.deps) == {"skipped": "already_handled"}, "a failed message is not run twice by itself"
finally:
    api.answer = original
print("  ok  the worker claims a message once, records processed or failed, and ignores a repeat, a bad event and a message with no item")


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
store.finish("msg#a#7", "processed", NOW + 7)
assert table.items["msg#a#7"]["status"] == "processed" and table.items["msg#a#7"]["finished_at"] == NOW + 7
store.finish("msg#a#gone", "processed", NOW)
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
for leak in (SECRET, "forged", "sha256=", "4111", "Asha", "Verma", EMAIL, "card number", "bot_secret"):
    assert leak not in logged, f"{leak!r} must never be logged"
print("  ok  the log lines hold names, reasons and ids only: no secret, no signature, no message text, no name")

print("test_chatwoot_api.py: receiver, worker, secret and store")
