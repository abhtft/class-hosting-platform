"""Chatwoot Agent Bot adapter, the AWS part (step T1: receive, verify, deduplicate, answer 200 at once, hand over, record). No AI and no call back to
Chatwoot yet. Two Lambda functions in template.yaml, both created only when ChatwootEnabled is 'true':

  Chatwoot --POST /hooks/chatwoot/{agentId}--> ChatwootApi (its own throttle) --> handler()    the receiver
                                                                                     |  Lambda Event invoke
                                                                                     v
                                                                                   worker()    the worker (records the outcome; T2 replies here)

The receiver: reads the bot secret of the agent (Secrets Manager, name chatwoot/<agentId>, field bot_secret), checks the signature (chatwoot_core.verify),
ignores everything the bot must not answer (chatwoot_core.classify), writes ONE item per message with a conditional write (the second delivery of a message
finds it and is only counted), hands the message to the worker asynchronously and answers 200. Chatwoot waits 5 seconds and retries only on 429 and 500, so
a failure to hand over answers 500 (after removing the item, so the retry is accepted) and everything else answers fast.

What is stored: ids, status and times of a message, never its text, the customer's name or any other personal data. Items expire by themselves (ttl).
What is logged: names and reasons, never a secret, a signature or a message."""
import base64
import json
import os
import re
import time

import chatwoot_core as core
import platform_settings

MAX_BODY_BYTES = platform_settings.kb("chatwoot.maxBodyKB")
DEDUPE_TTL_DAYS = platform_settings.value("chatwoot.dedupeTtlDays")
SECRET_CACHE_SECONDS = platform_settings.value("chatwoot.secretCacheSeconds")
SECRET_PREFIX = "chatwoot/"  # the secret of agent <id> is named chatwoot/<id>; the template lets the functions read exactly this prefix
ROUTE = re.compile(r"/hooks/chatwoot/([^/]+)")
CONDITION_FAILED = "ConditionalCheckFailedException"


def log(what, **details):
    print(json.dumps({"chatwoot": what, **details}))


def reply(status, **body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


# ---------- storage: one small table, string key "pk" = msg#<agent id>#<message id> ----------

class DynamoStore:
    def __init__(self, table):
        self.table = table

    def _conditional(self, **args):
        """Run an update or put whose condition may fail: True when it was applied, False when the condition was not met."""
        from botocore.exceptions import ClientError
        try:
            args.pop("kind")(**args)
        except ClientError as error:
            if error.response["Error"]["Code"] == CONDITION_FAILED:
                return False
            raise
        return True

    def claim(self, key, item):
        """Write the message item only if there is none yet. True for the first delivery, False for a repeat."""
        return self._conditional(kind=self.table.put_item, Item={**item, "pk": key}, ConditionExpression="attribute_not_exists(pk)")

    def note_duplicate(self, key, now):
        self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="ADD #d :one SET #t = :now", ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames={"#d": "duplicates", "#t": "last_duplicate_at"}, ExpressionAttributeValues={":one": 1, ":now": now})

    def forget(self, key):
        self.table.delete_item(Key={"pk": key})

    def begin(self, key, now):
        """Claim the message for the worker: only one worker run may move it from received to processing."""
        return self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="SET #s = :processing, #t = :now", ConditionExpression="#s = :received",
                                 ExpressionAttributeNames={"#s": "status", "#t": "started_at"},
                                 ExpressionAttributeValues={":processing": "processing", ":received": "received", ":now": now})

    def finish(self, key, status, now):
        self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="SET #s = :status, #t = :now", ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames={"#s": "status", "#t": "finished_at"}, ExpressionAttributeValues={":status": status, ":now": now})


class MemoryStore:
    """Same interface as DynamoStore; used by tests."""

    def __init__(self):
        self.items = {}

    def claim(self, key, item):
        if key in self.items:
            return False
        self.items[key] = json.loads(json.dumps({**item, "pk": key}))
        return True

    def note_duplicate(self, key, now):
        if key in self.items:
            self.items[key]["duplicates"] = self.items[key].get("duplicates", 0) + 1
            self.items[key]["last_duplicate_at"] = now

    def forget(self, key):
        self.items.pop(key, None)

    def begin(self, key, now):
        item = self.items.get(key)
        if not item or item.get("status") != "received":
            return False
        item["status"], item["started_at"] = "processing", now
        return True

    def finish(self, key, status, now):
        if key in self.items:
            self.items[key]["status"], self.items[key]["finished_at"] = status, now


# ---------- the bot secret ----------

def bot_secret_of(raw):
    """The bot secret inside a secret's text ({"bot_secret": "..."}), or None when the text is not that."""
    try:
        data = json.loads(raw or "")
    except ValueError:
        return None
    value = data.get("bot_secret") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


class Secrets:
    """Looks the bot secret up and remembers it for a few minutes (Secrets Manager costs a little per call). A missing or unusable secret is never
    remembered, so a secret created a moment ago works at once."""

    def __init__(self, read, clock=time.time):
        self.read, self.clock, self.cache = read, clock, {}

    def bot_secret(self, agent_id):
        now = self.clock()
        hit = self.cache.get(agent_id)
        if hit and hit[0] > now:
            return hit[1]
        secret = bot_secret_of(self.read(SECRET_PREFIX + agent_id))
        if secret and SECRET_CACHE_SECONDS > 0:
            self.cache[agent_id] = (now + SECRET_CACHE_SECONDS, secret)
        return secret


def read_secret(name):
    import boto3
    from botocore.exceptions import ClientError
    try:
        return boto3.client("secretsmanager").get_secret_value(SecretId=name).get("SecretString")
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def invoke_worker(payload):
    import boto3
    response = boto3.client("lambda").invoke(FunctionName=os.environ["CHATWOOT_WORKER_FUNCTION"], InvocationType="Event", Payload=json.dumps(payload).encode("utf8"))
    if response.get("StatusCode") != 202:
        raise RuntimeError(f"the worker was not started (status {response.get('StatusCode')})")


class Deps:
    def __init__(self, store, secrets, invoke):
        self.store, self.secrets, self.invoke = store, secrets, invoke


_deps = None


def default_deps():
    """The real dependencies, built once per warm container."""
    global _deps
    if _deps is None:
        import boto3
        _deps = Deps(DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"])), Secrets(read_secret), invoke_worker)
    return _deps


# ---------- the receiver ----------

def raw_body(event):
    body = event.get("body") or ""
    return base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf8")


def handler(event, context=None, now=None, deps=None):
    now = int(now if now is not None else time.time())
    deps = deps or default_deps()
    match = ROUTE.fullmatch((event.get("rawPath") or "").rstrip("/"))
    agent_id = match.group(1) if match else ""
    if not core.AGENT_ID.fullmatch(agent_id):
        return reply(404, error="not_found")
    if event["requestContext"]["http"]["method"] != "POST":
        return reply(405, error="method_not_allowed")
    body = raw_body(event)
    if len(body) > MAX_BODY_BYTES:
        return reply(413, error="too_large")
    headers = {name.lower(): value for name, value in (event.get("headers") or {}).items()}
    try:
        secret = deps.secrets.bot_secret(agent_id)
    except Exception as error:  # Secrets Manager is down: Chatwoot retries a 500
        log("secret_unavailable", agent=agent_id, error=type(error).__name__)
        return reply(500, error="try_again")
    if not secret:  # unknown agent and wrong signature look the same from outside: nobody can probe which agents exist
        log("refused", agent=agent_id, reason="no_secret")
        return reply(401, error="unauthorized")
    refusal = core.verify(secret, headers, body, now)
    if refusal:
        log("refused", agent=agent_id, reason=refusal)
        return reply(401, error="unauthorized")
    fields, ignored = core.classify(core.parse(body))
    if ignored:
        return reply(200, ok=True, ignored=ignored)
    key = f"msg#{agent_id}#{fields['message_id']}"
    delivery = re.sub(r"[^A-Za-z0-9-]", "", headers.get("x-chatwoot-delivery") or "")[:64]
    record = {**fields, "agent_id": agent_id, "delivery_id": delivery, "status": "received", "received_at": now, "ttl": now + DEDUPE_TTL_DAYS * 86400}
    try:
        first = deps.store.claim(key, record)
    except Exception as error:
        log("store_unavailable", agent=agent_id, error=type(error).__name__)
        return reply(500, error="try_again")
    if not first:
        deps.store.note_duplicate(key, now)
        log("duplicate", agent=agent_id, message=fields["message_id"])
        return reply(200, ok=True, duplicate=True)
    try:
        deps.invoke({"agent_id": agent_id, "delivery_id": delivery, **fields})
    except Exception as error:  # remove the item so Chatwoot's retry is accepted instead of being taken for a repeat
        deps.store.forget(key)
        log("hand_over_failed", agent=agent_id, error=type(error).__name__)
        return reply(500, error="try_again")
    return reply(200, ok=True, accepted=True)


# ---------- the worker ----------

def answer(event, deps):
    """What the worker does with an accepted message. T1: nothing (the message is only recorded). T2 replies to the conversation here."""
    return None


def worker(event, context=None, now=None, deps=None):
    now = int(now if now is not None else time.time())
    deps = deps or default_deps()
    agent_id, message_id = event.get("agent_id"), event.get("message_id")
    if not core.AGENT_ID.fullmatch(str(agent_id)) or not isinstance(message_id, int) or isinstance(message_id, bool):
        log("bad_event")
        return {"skipped": "bad_event"}
    key = f"msg#{agent_id}#{message_id}"
    if not deps.store.begin(key, now):  # a second run of the same message (Lambda may deliver an event twice) does nothing
        log("already_handled", agent=agent_id, message=message_id)
        return {"skipped": "already_handled"}
    try:
        answer(event, deps)
    except Exception as error:
        deps.store.finish(key, "failed", now)
        log("failed", agent=agent_id, message=message_id, error=type(error).__name__)
        raise
    deps.store.finish(key, "processed", now)
    return {"processed": True}
