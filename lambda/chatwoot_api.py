"""Chatwoot Agent Bot adapter, the AWS part (steps T1 and T2: receive, verify, deduplicate, answer 200 at once, hand over, reply). No AI yet: the reply is a
fixed placeholder (chatwoot_core.reply_text); the agent runtime replaces it later. Two Lambda functions in template.yaml, both created only when ChatwootEnabled
is 'true':

  Chatwoot --POST /hooks/chatwoot/{agentId}--> ChatwootApi (its own throttle) --> handler()    the receiver
                                                                                     |  Lambda Event invoke
                                                                                     v
                                                  worker()  --POST Create New Message--> the one Chatwoot address (ChatwootBaseUrl)

The receiver: reads the bot secret of the agent (Secrets Manager, name chatwoot/<agentId>, field bot_secret), checks the signature (chatwoot_core.verify),
ignores everything the bot must not answer (chatwoot_core.classify), writes ONE item per message with a conditional write (the second delivery of a message
finds it and is only counted), hands the message to the worker asynchronously and answers 200. Chatwoot waits 5 seconds and retries only on 429 and 500, so
a failure to hand over answers 500 (after removing the item, so the retry is accepted) and everything else answers fast.

The worker: claims the message once, reads the agent bot's token and account id from the same secret (fields api_token and account_id; the receiver never
reads them, the worker never reads bot_secret), refuses a message of another account, posts ONE reply to the conversation and records `replied` (with the
reply's id) or `failed` (with a short reason). There is no automatic retry: Chatwoot does not de-duplicate messages, so a second POST after an unknown outcome
could answer a customer twice. A failure is recorded, logged and raised so it is visible; handing the conversation to a person is a later step.

What is stored: ids, status, a reason code and times of a message, never its text, the customer's name, a token or any other personal data. Items expire by
themselves (ttl). What is logged: names and reason codes, never a secret, a token, a signature or a message."""
import base64
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

import chatwoot_core as core
import platform_settings

MAX_BODY_BYTES = platform_settings.kb("chatwoot.maxBodyKB")
DEDUPE_TTL_DAYS = platform_settings.value("chatwoot.dedupeTtlDays")
SECRET_CACHE_SECONDS = platform_settings.value("chatwoot.secretCacheSeconds")
HTTP_TIMEOUT_SECONDS = platform_settings.value("chatwoot.httpTimeoutSeconds")
BASE_URL = platform_settings.value("chatwoot.baseUrl")  # the one Chatwoot address the worker may call (a template parameter)
AUTH_STYLE = platform_settings.value("chatwoot.authStyle")  # bearer, or api_access_token for a Chatwoot older than 4.19
SECRET_PREFIX = "chatwoot/"  # the secret of agent <id> is named chatwoot/<id>; the template lets the functions read exactly this prefix
ROUTE = re.compile(r"/hooks/chatwoot/([^/]+)")
CONDITION_FAILED = "ConditionalCheckFailedException"


class NetworkFailure(Exception):
    """The call to Chatwoot got no answer (connection refused, DNS, TLS...)."""


class TimeoutFailure(NetworkFailure):
    """The call to Chatwoot got no answer in time. The message may or may not have been created."""


class ReplyFailed(Exception):
    """The worker could not reply. Raised after the failure is recorded, so the Lambda error is visible; the text is only the reason code."""


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

    def finish(self, key, status, now, extra=None):
        """Record the outcome (replied or failed) with a few more plain fields (the reply's id, a reason code)."""
        names, values, sets = {"#s": "status", "#t": "finished_at"}, {":status": status, ":now": now}, ["#s = :status", "#t = :now"]
        for number, (name, value) in enumerate(sorted((extra or {}).items())):
            names[f"#x{number}"], values[f":x{number}"] = name, value
            sets.append(f"#x{number} = :x{number}")
        self._conditional(kind=self.table.update_item, Key={"pk": key}, UpdateExpression="SET " + ", ".join(sets), ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames=names, ExpressionAttributeValues=values)


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

    def finish(self, key, status, now, extra=None):
        if key in self.items:
            self.items[key].update({"status": status, "finished_at": now, **(extra or {})})


# ---------- the secret of an agent: one Secrets Manager secret, two readers that each take only their own fields ----------

def bot_secret_of(raw):
    """The bot secret inside a secret's text ({"bot_secret": "..."}), or None when the text is not that. What the receiver reads: nothing else."""
    try:
        data = json.loads(raw or "")
    except ValueError:
        return None
    value = data.get("bot_secret") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def credentials_of(raw):
    """{"api_token": text or None, "account_id": whole number or None} from a secret's text, or None when the text is not a JSON object. What the worker
    reads: it never takes the bot secret."""
    try:
        data = json.loads(raw or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    account = data.get("account_id")
    if isinstance(account, str) and account.isdigit():
        account = int(account)
    return {"api_token": data.get("api_token") if isinstance(data.get("api_token"), str) else None,
            "account_id": account if isinstance(account, int) and not isinstance(account, bool) and account > 0 else None}


class Secrets:
    """Looks an agent's secret up and remembers what `parse` took from it for a few minutes (Secrets Manager costs a little per call). A missing or unusable
    secret is never remembered, so a secret created a moment ago works at once."""

    def __init__(self, read, parse=bot_secret_of, clock=time.time):
        self.read, self.parse, self.clock, self.cache = read, parse, clock, {}

    def get(self, agent_id):
        now = self.clock()
        hit = self.cache.get(agent_id)
        if hit and hit[0] > now:
            return hit[1]
        value = self.parse(self.read(SECRET_PREFIX + agent_id))
        if value and SECRET_CACHE_SECONDS > 0:
            self.cache[agent_id] = (now + SECRET_CACHE_SECONDS, value)
        return value

    bot_secret = get  # the receiver's name for it
    credentials = get  # the worker's name for it


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an answer like any other here: the token must never follow it to another address


def post_json(url, headers, body, timeout):
    """POST and return (status, first bytes of the answer). Redirects are not followed. Raises TimeoutFailure or NetworkFailure when there is no answer."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, response.read(65536)
    except urllib.error.HTTPError as error:  # Chatwoot answered with a status that is not a success (a redirect included)
        try:
            return error.code, error.read(65536)
        except OSError:
            return error.code, b""
    except (socket.timeout, TimeoutError):
        raise TimeoutFailure("timeout") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise TimeoutFailure("timeout") from None
        raise NetworkFailure(type(error.reason).__name__) from None
    except OSError as error:
        raise NetworkFailure(type(error).__name__) from None


class Deps:
    def __init__(self, store, secrets, invoke=None, http=None):
        self.store, self.secrets, self.invoke, self.http = store, secrets, invoke, http


_receiver_deps = None
_worker_deps = None


def default_deps():
    """The receiver's real dependencies, built once per warm container: it reads bot secrets and starts the worker."""
    global _receiver_deps
    if _receiver_deps is None:
        import boto3
        _receiver_deps = Deps(DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"])), Secrets(read_secret, bot_secret_of), invoke_worker)
    return _receiver_deps


def default_worker_deps():
    """The worker's real dependencies: it reads tokens and account ids, and calls Chatwoot."""
    global _worker_deps
    if _worker_deps is None:
        import boto3
        _worker_deps = Deps(DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"])), Secrets(read_secret, credentials_of), http=post_json)
    return _worker_deps


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

def failed(reason):
    return {"status": "failed", "reason": reason}


def answer(event, deps):
    """What the worker does with an accepted message: post the placeholder reply. Returns {"status": "replied", "reply_id": n or None} or
    {"status": "failed", "reason": code}. Every refusal below happens before anything is sent."""
    base = core.base_url_of(BASE_URL)
    if not base:
        return failed("bad_base_url")
    if AUTH_STYLE not in core.AUTH_STYLES:
        return failed("bad_setting")
    try:
        credentials = deps.secrets.credentials(event["agent_id"])
    except Exception as error:
        log("secret_unavailable", agent=event["agent_id"], error=type(error).__name__)
        return failed("secret_unavailable")
    if not credentials:
        return failed("no_secret")
    if not credentials["api_token"]:
        return failed("no_token")
    if not core.token_ok(credentials["api_token"]) or not credentials["account_id"]:
        return failed("bad_secret")
    if credentials["account_id"] != event["account_id"]:  # the bot token may write in any conversation of its account: only this agent's own account is allowed
        return failed("account_mismatch")
    url, headers, body = core.reply_request(base, AUTH_STYLE, credentials["api_token"], event["account_id"], event["conversation_id"], core.reply_text(event["message_id"]))
    try:
        status, raw = deps.http(url, headers, body, HTTP_TIMEOUT_SECONDS)
    except TimeoutFailure:
        return failed("timeout")
    except NetworkFailure:
        return failed("network")
    kind, detail = core.reply_result(status, raw)
    return {"status": "replied", "reply_id": detail} if kind == "replied" else failed(detail)


def worker(event, context=None, now=None, deps=None):
    now = int(now if now is not None else time.time())
    deps = deps or default_worker_deps()
    agent_id, message_id = event.get("agent_id"), event.get("message_id")
    ids = [event.get(name) for name in ("conversation_id", "account_id", "inbox_id")]
    if not core.AGENT_ID.fullmatch(str(agent_id)) or any(not isinstance(i, int) or isinstance(i, bool) or i < 1 for i in [message_id, *ids]):
        log("bad_event")
        return {"skipped": "bad_event"}
    key = f"msg#{agent_id}#{message_id}"
    if not deps.store.begin(key, now):  # a second run of the same message (Lambda may deliver an event twice) does nothing: one claim, one POST
        log("already_handled", agent=agent_id, message=message_id)
        return {"skipped": "already_handled"}
    try:
        outcome = answer(event, deps)
    except Exception as error:  # a bug: record it, and let the error show
        deps.store.finish(key, "failed", now, {"reason": "error"})
        log("failed", agent=agent_id, message=message_id, reason="error", error=type(error).__name__)
        raise
    if outcome["status"] == "replied":
        deps.store.finish(key, "replied", now, {"reply_id": outcome["reply_id"]} if outcome["reply_id"] else None)
        log("replied", agent=agent_id, message=message_id)
        return {"replied": True}
    deps.store.finish(key, "failed", now, {"reason": outcome["reason"]})
    log("failed", agent=agent_id, message=message_id, reason=outcome["reason"])
    raise ReplyFailed(outcome["reason"])
