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

A message is served only for an agent that is registered (chatwoot_registry: its tenant, the one Chatwoot account and the inboxes it may serve, all enabled); the
receiver asks first (answered 200 `ignored: not_mapped`, nothing stored) and the worker asks again just before it replies.

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
import chatwoot_handover as handover
import chatwoot_memory as memory
import chatwoot_registry as registry
import chatwoot_runtime as engine
import platform_settings
from chatwoot_store import CONDITION_FAILED, DynamoStore, MemoryStore  # noqa: F401  (the table lives in chatwoot_store.py; these names stay importable from here)

MAX_BODY_BYTES = platform_settings.kb("chatwoot.maxBodyKB")
DEDUPE_TTL_DAYS = platform_settings.value("chatwoot.dedupeTtlDays")
SECRET_CACHE_SECONDS = platform_settings.value("chatwoot.secretCacheSeconds")
HTTP_TIMEOUT_SECONDS = platform_settings.value("chatwoot.httpTimeoutSeconds")
DEFAULT_CAP_USD = platform_settings.value("chatwoot.defaultMonthlyCapUsd")  # what one business may spend a month on answers, unless its tenant item says otherwise
BASE_URL = platform_settings.value("chatwoot.baseUrl")  # the one Chatwoot address the worker may call (a template parameter)
AUTH_STYLE = platform_settings.value("chatwoot.authStyle")  # bearer, or api_access_token for a Chatwoot older than 4.19
SECRET_PREFIX = "chatwoot/"  # the secret of agent <id> is named chatwoot/<id>; the template lets the functions read exactly this prefix
ROUTE = re.compile(r"/hooks/chatwoot/([^/]+)")


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
    def __init__(self, store, secrets, invoke=None, http=None, registry=None, runtime=None, memory=None):
        self.store, self.secrets, self.invoke, self.http, self.registry, self.runtime, self.memory = store, secrets, invoke, http, registry, runtime, memory


_receiver_deps = None
_worker_deps = None


def default_deps():
    """The receiver's real dependencies, built once per warm container: it reads bot secrets and starts the worker."""
    global _receiver_deps
    if _receiver_deps is None:
        import boto3
        store = DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"]))
        _receiver_deps = Deps(store, Secrets(read_secret, bot_secret_of), invoke_worker, registry=registry.Registry(store))
    return _receiver_deps


def default_worker_deps():
    """The worker's real dependencies: it reads tokens and account ids, and calls Chatwoot."""
    global _worker_deps
    if _worker_deps is None:
        import boto3
        store = DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"]))
        table = os.environ.get("CHATWOOT_MEMORY_TABLE")  # the conversation memory's own table; without it the worker simply has no memory
        _worker_deps = Deps(store, Secrets(read_secret, credentials_of), http=post_json, registry=registry.Registry(store, ttl=0),
                             runtime=engine.real_runtime(),  # ttl 0: asks the table every time; the runtime searches the shelves and asks the model
                             memory=memory.DynamoMemory(boto3.resource("dynamodb").Table(table)) if table else None)
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
    payload = core.parse(body)
    fields, ignored = core.classify(payload)
    if ignored:
        return reply(200, ok=True, ignored=ignored)
    try:
        unmapped = deps.registry.allows(agent_id, fields["account_id"], fields["inbox_id"])
    except Exception as error:
        log("registry_unavailable", agent=agent_id, error=type(error).__name__)
        return reply(500, error="try_again")
    if unmapped:  # not registered, disabled, or another account or inbox: acknowledged so Chatwoot does not retry, and nothing is kept
        log("ignored", agent=agent_id, reason=unmapped)
        return reply(200, ok=True, ignored=unmapped)
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
        deps.invoke({"agent_id": agent_id, "delivery_id": delivery, "text": core.message_text(payload), "channel": core.channel_of(payload), **fields})  # the text goes to the worker in this event only
    except Exception as error:  # remove the item so Chatwoot's retry is accepted instead of being taken for a repeat
        deps.store.forget(key)
        log("hand_over_failed", agent=agent_id, error=type(error).__name__)
        return reply(500, error="try_again")
    return reply(200, ok=True, accepted=True)


# ---------- the worker ----------

class Refused(Exception):
    """The message gets no reply. `reason` is a short code, `metrics` are numbers and names only (what had been spent when it stopped)."""

    def __init__(self, reason, metrics=None, config=None):
        super().__init__(reason)
        self.reason, self.metrics, self.config = reason, metrics or {}, config


def failed(reason, metrics=None):
    return {"status": "failed", "reason": reason, "metrics": metrics or {}}


def usage_key(tenant_id, now):
    return f"usage#{tenant_id}#{time.strftime('%Y%m', time.gmtime(now))}"


def monthly_cap_micro(tenant):
    cap = tenant.get("monthly_cap_micro")
    return cap if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0 else DEFAULT_CAP_USD * 1000000


def spend(deps, key, metrics, now):
    """Count what an answer cost (the search embedding and the model) against the business's month. A counter that cannot be written is logged, not fatal."""
    micro = int(metrics.get("cost_micro") or 0)
    if micro > 0:
        try:
            deps.store.usage_add(key, micro, now)
        except Exception as error:
            log("usage_unavailable", error=type(error).__name__)


def recall(event, deps, now):
    """The remembered turns of this conversation, or [] (no memory, none yet, expired, or the memory table could not be read: an answer never waits for it)."""
    if deps.memory is None:
        return []
    try:
        return memory.load(deps.memory, event["agent_id"], event["conversation_id"], now)
    except Exception as error:
        log("memory_unavailable", error=type(error).__name__)
        return []


def remember(event, deps, text, metrics, now):
    """Keep this turn after the reply has been posted. The customer's words go to the memory table only, never into a log line, a message item or a metric."""
    if deps.memory is None:
        return
    try:
        memory.remember(deps.memory, event["agent_id"], event["conversation_id"], event.get("text") or "", text, metrics.get("route") == "fallback", now, handover="handover" in metrics)
    except Exception as error:
        log("memory_unavailable", error=type(error).__name__)


def compose(event, agent, tenant, deps, now, history):
    """The text to send: (reply, metrics, hand-over reason or None). Reads the agent's live version, checks the business's monthly spend, applies the hand-over rules to
    the customer's message and otherwise lets the runtime answer from the books. When a reason is returned the text is the agent's hand-over sentence. Raises Refused
    with a reason code when there is nothing to send."""
    live = agent.get("live_version") or 0
    if not live:
        raise Refused("no_live_version")
    version = deps.store.get(registry.version_key(event["agent_id"], live))
    try:
        config = engine.clean_config((version or {}).get("config"))
    except engine.ConfigError:
        raise Refused("bad_config") from None
    key = usage_key(tenant["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        raise Refused("cap_reached", config=config)
    text = event.get("text") if isinstance(event.get("text"), str) else ""
    context = {"channel": event.get("channel") if isinstance(event.get("channel"), str) and re.fullmatch(r"[a-z_]{1,40}", event["channel"]) else "", "inbox_id": event["inbox_id"]}
    reason = handover.decide(text, history, config["handover_on"])
    if reason:  # a person is asked for, a complaint, a warm lead: no model call, nothing to search
        return config["handover_text"], {"route": "handover"}, reason
    try:
        made = engine.respond(config, text, tenant["tenant_id"], deps.runtime, history, context)
    except engine.RuntimeFailure as failure:
        spend(deps, key, failure.metrics, now)
        raise Refused(failure.reason, failure.metrics, config) from None
    spend(deps, key, made["metrics"], now)
    if made["route"] == "fallback" and "stuck" in config["handover_on"] and memory.stuck(history, 1):  # the second fallback in a row: the bot is not getting anywhere
        return config["handover_text"], made["metrics"], "stuck"
    return made["text"], made["metrics"], None


def hand_over(event, deps, base, credentials, reason, sentence, history, text, send_text):
    """Hand the conversation to a person with Chatwoot's own means, three steps that each run even if another failed: the customer's one sentence (when `send_text`),
    a private note for the team, and the status `open`. Returns ((kind, detail) of the customer's sentence, the flags for the message item)."""
    token, ids = credentials["api_token"], (event["account_id"], event["conversation_id"])

    def call(request, read):
        url, headers, body = request
        try:
            status, raw = deps.http(url, headers, body, HTTP_TIMEOUT_SECONDS)
        except TimeoutFailure:
            return "failed", "timeout"
        except NetworkFailure:
            return "failed", "network"
        return read(status, raw)
    flags, sent = {"handover": reason}, ("replied", None)
    if send_text:
        sent = call(core.reply_request(base, AUTH_STYLE, token, *ids, sentence or handover.DEFAULT_TEXT), core.reply_result)
        flags["handover_text"] = int(sent[0] == "replied")
    note = call(core.reply_request(base, AUTH_STYLE, token, *ids, handover.note_text(reason, history, text), private=True), core.reply_result)
    flags["handover_note"] = int(note[0] == "replied")
    opened = call(core.status_request(base, AUTH_STYLE, token, *ids, "open"), core.status_result)
    flags["handover_open"] = int(opened[0] == "ok")
    log("handover", agent=event["agent_id"], message=event.get("message_id"), reason=reason, text=flags.get("handover_text"), note=flags["handover_note"], opened=flags["handover_open"])
    if not (flags["handover_note"] and flags["handover_open"] and sent[0] == "replied"):  # something did not go through: visible in the log and on the item, by code only
        log("handover_incomplete", agent=event["agent_id"], message=event.get("message_id"), reason=reason, text=sent[1] if sent[0] == "failed" else None,
            note=note[1] if note[0] == "failed" else None, opened=opened[1] if opened[0] == "failed" else None)
    return sent, flags


def hand_over_outcome(event, deps, ready, reason, sentence, history, metrics, now):
    """The worker's outcome for a message that goes to a person: replied when the customer's sentence was posted, failed (with its code) when it was not."""
    (kind, detail), flags = hand_over(event, deps, ready["base"], ready["credentials"], reason, sentence, history, event.get("text") or "", True)
    metrics = {**metrics, **flags, "route": "handover"}
    if kind != "replied":
        return failed(detail, metrics)
    remember(event, deps, sentence or handover.DEFAULT_TEXT, metrics, now)
    return {"status": "replied", "reply_id": detail, "metrics": metrics}


def prepare(event, deps):
    """What a reply or a hand-over needs: {"base", "agent", "tenant", "credentials"}, or a failed() outcome when the message must not be served (every refusal here happens
    before anything is spent or sent)."""
    try:
        agent, tenant = deps.registry.load(event["agent_id"])  # fresh: no cache
        unmapped = registry.check_mapping(agent, tenant, event["account_id"], event["inbox_id"])
    except Exception as error:
        log("registry_unavailable", agent=event["agent_id"], error=type(error).__name__)
        return failed("registry_unavailable")
    if unmapped:  # switched off or changed since the message was accepted
        return failed(unmapped)
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
    return {"base": base, "agent": agent, "tenant": tenant, "credentials": credentials}


def answer(event, deps, now=None):
    """What the worker does with an accepted message: answer from the books and post the reply, or hand the conversation to a person. Returns {"status": "replied",
    "reply_id": n or None, "metrics": {...}} or {"status": "failed", "reason": code, "metrics": {...}}. Every refusal before the answer is made happens before anything is
    spent or sent."""
    now = int(now if now is not None else time.time())
    ready = prepare(event, deps)
    if ready.get("status") == "failed":
        return ready
    history = recall(event, deps, now)
    try:
        text, metrics, reason = compose(event, ready["agent"], ready["tenant"], deps, now, history)
    except Refused as refusal:
        if refusal.reason not in handover.FAILURE_REASONS:  # no live version: the agent is meant to be silent; the rest of the refusals are not the customer's concern
            return failed(refusal.reason, refusal.metrics)
        sentence = (refusal.config or {}).get("handover_text")  # an answer that could not be made: the customer must not be left in silence
        return hand_over_outcome(event, deps, ready, refusal.reason, sentence, history, refusal.metrics, now)
    if reason:
        return hand_over_outcome(event, deps, ready, reason, text, history, metrics, now)
    url, headers, body = core.reply_request(ready["base"], AUTH_STYLE, ready["credentials"]["api_token"], event["account_id"], event["conversation_id"], text)
    try:
        status, raw = deps.http(url, headers, body, HTTP_TIMEOUT_SECONDS)
    except TimeoutFailure:
        return failed("timeout", metrics)
    except NetworkFailure:
        return failed("network", metrics)
    kind, detail = core.reply_result(status, raw)
    if kind != "replied":
        return failed(detail, metrics)
    remember(event, deps, text, metrics, now)
    return {"status": "replied", "reply_id": detail, "metrics": metrics}


def sweep(event, deps, now):
    """A message item the worker never finished (found by chatwoot_sweeper.py): hand its conversation to a person with a private note and `open`, and no sentence to the
    customer (it is not known whether the reply was delivered)."""
    ids = [event.get(name) for name in ("message_id", "conversation_id", "account_id", "inbox_id")]
    agent_id = event.get("agent_id")
    if not core.AGENT_ID.fullmatch(str(agent_id)) or any(not isinstance(i, int) or isinstance(i, bool) or i < 1 for i in ids):
        log("bad_event")
        return {"skipped": "bad_event"}
    key = f"msg#{agent_id}#{event['message_id']}"
    ready = prepare(event, deps)
    if ready.get("status") == "failed":  # the hand-over cannot be made (switched off, no token...): the item says so
        deps.store.finish(key, "failed", now, {"reason": "stuck", "handover": "not_made", "handover_why": ready["reason"]})
        log("sweep_not_handed_over", agent=agent_id, message=event["message_id"], reason=ready["reason"])
        return {"skipped": ready["reason"]}
    _, flags = hand_over(event, deps, ready["base"], ready["credentials"], "stuck_item", "", recall(event, deps, now), "", False)
    deps.store.finish(key, "failed", now, {"reason": "stuck", **flags})
    return {"swept": True}


def worker(event, context=None, now=None, deps=None):
    now = int(now if now is not None else time.time())
    deps = deps or default_worker_deps()
    if event.get("kind") == "sweep":  # from the sweeper: a stuck message item, not a customer message
        return sweep(event, deps, now)
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
        outcome = answer(event, deps, now)
    except Exception as error:  # a bug: record it, and let the error show
        deps.store.finish(key, "failed", now, {"reason": "error"})
        log("failed", agent=agent_id, message=message_id, reason="error", error=type(error).__name__)
        raise
    if outcome["status"] == "replied":
        deps.store.finish(key, "replied", now, {**outcome["metrics"], **({"reply_id": outcome["reply_id"]} if outcome["reply_id"] else {})})
        log("replied", agent=agent_id, message=message_id)
        return {"replied": True}
    deps.store.finish(key, "failed", now, {**outcome["metrics"], "reason": outcome["reason"]})
    log("failed", agent=agent_id, message=message_id, reason=outcome["reason"])
    raise ReplyFailed(outcome["reason"])
