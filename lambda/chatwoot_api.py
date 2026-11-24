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
import concurrent.futures
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request

import chatwoot_consistency as consistency
import chatwoot_core as core
import chatwoot_handover as handover
import chatwoot_media as media
import chatwoot_memory as memory
import chatwoot_orchestrator as orchestrator
import chatwoot_registry as registry
import chatwoot_runtime as engine
import chatwoot_suggest as suggest_rows
import chatwoot_trace
import gemini
import chatwoot_suite as suite
import chatwoot_interview as interview_topics
import chatwoot_repeat as repeat
import chatwoot_teach as teach
import platform_settings
from chatwoot_store import CONDITION_FAILED, DynamoStore, MemoryStore  # noqa: F401  (the table lives in chatwoot_store.py; these names stay importable from here)

MAX_BODY_BYTES = platform_settings.kb("chatwoot.maxBodyKB")
DEDUPE_TTL_DAYS = platform_settings.value("chatwoot.dedupeTtlDays")
SECRET_CACHE_SECONDS = platform_settings.value("chatwoot.secretCacheSeconds")
HTTP_TIMEOUT_SECONDS = platform_settings.value("chatwoot.httpTimeoutSeconds")
DEFAULT_CAP_USD = platform_settings.value("chatwoot.defaultMonthlyCapUsd")  # what one business may spend a month on answers, unless its tenant item says otherwise
BASE_URL = platform_settings.value("chatwoot.baseUrl")  # the one Chatwoot address the worker may call (a template parameter)
INTERVIEW_MAX_TOKENS = platform_settings.value("chatwoot.teachInterviewMaxTokens")  # the question or the summary is short
DISTILL_MAX_TOKENS = INTERVIEW_MAX_TOKENS * len(interview_topics.TOPICS)  # the whole list of facts is longer than one question
SUITE_PARALLEL = platform_settings.value("chatwoot.suiteParallel")  # test cases run at the same time
SUITE_RUN_DAYS = platform_settings.value("chatwoot.suiteRunDays")  # how long the record of a suite run is kept
SUITE_BUDGET_SECONDS = platform_settings.value("chatwoot.suiteBudgetSeconds")  # no new case is started after this long: the worker has 120 seconds
MEDIA_TIMEOUT_SECONDS = platform_settings.value("chatwoot.mediaSendTimeoutSeconds")  # one file uploaded into the chat
MEDIA_CAPTION_CHARS = platform_settings.value("chatwoot.mediaCaptionMaxChars")  # the answer travels as the file's caption up to this long (WhatsApp takes about 1,000)
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
    def __init__(self, store, secrets, invoke=None, http=None, registry=None, runtime=None, memory=None, files=None, traces=None):
        self.store, self.secrets, self.invoke, self.http, self.registry, self.runtime, self.memory, self.files = store, secrets, invoke, http, registry, runtime, memory, files
        self.traces = traces  # where the trace of each answered message is written (a traces store), or None: no traces


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
                             memory=memory.DynamoMemory(boto3.resource("dynamodb").Table(table)) if table else None,
                             files=media.S3Files() if os.environ.get("BUCKET") else None,  # the media files of the agents (read only)
                             traces=chatwoot_trace.traces.store() if chatwoot_trace.traces.available() and os.environ.get("TRACES_TABLE") else None)
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
        if ignored in ("not_pending", "bad_payload", "not_json"):  # the ones worth knowing about (a conversation a person already has, a payload we cannot read); the bot's own replies and other events are far too many to log
            log("not_served", agent=agent_id, reason=ignored)
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
        deps.invoke({"agent_id": agent_id, "delivery_id": delivery, "text": core.message_text(payload), "channel": core.channel_of(payload), "attachments": core.attachment_kinds(payload), **fields})  # the text goes to the worker in this event only
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
        memory.remember(deps.memory, event["agent_id"], event["conversation_id"], event.get("text") or "", text, metrics.get("route") == "fallback", now, handover="handover" in metrics,
                        department=metrics.get("department") if metrics.get("department") != "general" else None)
    except Exception as error:
        log("memory_unavailable", error=type(error).__name__)


def context_of(event):
    """What the system knows about where the message came from: the channel word (only a short lowercase word is accepted) and the inbox number."""
    channel = event.get("channel")
    kinds = [k for k in event.get("attachments") or [] if isinstance(k, str) and re.fullmatch(r"[a-z_]{1,20}", k)] if isinstance(event.get("attachments"), list) else []
    return {"channel": channel if isinstance(channel, str) and re.fullmatch(r"[a-z_]{1,40}", channel) else "", "inbox_id": event.get("inbox_id"), "facts": teach.clean_facts(event.get("facts")),
            "attachments": kinds[:5]}


def answer_text(config, text, history, context, tenant_id, runtime, explain=False):
    """(reply, metrics, hand-over reason or None) for one customer message: the hand-over rules first, then the books. The reply is the agent's hand-over sentence when a reason is
    returned. Raises engine.RuntimeFailure when the answer could not be made. The one piece of logic behind both a real answer (compose) and Studio's dry run (test), so a test
    shows what a customer would get."""
    reason = handover.decide(text, history, config["handover_on"], (context or {}).get("attachments"))
    if reason:  # a person is asked for, a complaint, a warm lead: no model call, nothing to search
        return config["handover_text"], {"route": "handover"}, reason
    made = engine.respond(config, text, tenant_id, runtime, history, context, explain)
    if made["route"] == "fallback" and "stuck" in config["handover_on"] and memory.stuck(history, 1):  # the second fallback in a row: the bot is not getting anywhere
        return config["handover_text"], made["metrics"], "stuck"
    return made["text"], made["metrics"], None


def department_config(department, tenant_id, deps):
    """The live configuration of a department's agent: the same business, type `department`, switched on, with a live version that is not itself an orchestrator. Anything else is
    engine.RuntimeFailure("department_unavailable") (the customer is handed to a person)."""
    problem = {"department": department["id"]}
    try:
        agent, tenant = deps.registry.load(department["agent"])
    except Exception as error:
        log("registry_unavailable", agent=department["agent"], error=type(error).__name__)
        raise engine.RuntimeFailure("department_unavailable", problem) from None
    ok = isinstance(agent, dict) and isinstance(tenant, dict) and agent.get("tenant_id") == tenant_id == tenant.get("tenant_id") and agent.get("type") == "department" \
        and agent.get("enabled") is True and tenant.get("enabled") is True and (agent.get("live_version") or 0) > 0
    version = deps.store.get(registry.version_key(department["agent"], agent["live_version"])) if ok else None
    try:
        config = engine.clean_config((version or {}).get("config")) if ok else None
    except engine.ConfigError:
        config = None
    if not config or config.get("departments"):
        log("department_unavailable", department=department["id"], agent=department["agent"])
        raise engine.RuntimeFailure("department_unavailable", problem)
    return config, agent["live_version"]


def route_message(config, text, history, now, deps):
    """(the route, what routing spent): the rules first (free), then, when they are not sure or the conversation is only staying where it was, the meaning match (the message against the
    departments' example questions: one embedding call) and last the small model (one short call, only when nothing else decided and there is no department to stay in). A layer that
    fails is skipped, never fatal: the next one, or the menu, takes over. Routing never reads the books of a department."""
    spent = {"tokens": 0, "cost_micro": 0}
    picked = orchestrator.route(text, config, history, now)
    if picked["layer"] not in ("sticky", "none") or not str(text or "").strip():
        return picked, spent
    stay = picked["department"]["id"] if picked["kind"] == "department" else None
    pairs, texts = orchestrator.example_texts(config)
    if texts and deps.runtime.extras is not None:
        try:
            scored = deps.runtime.extras(texts, str(text))
            spent["tokens"] += scored["tokens"]
            spent["cost_micro"] += scored["cost_micro"]
            chosen = orchestrator.meaning_pick(pairs, scored["scores"])
        except Exception as error:
            log("routing_meaning_failed", error=type(error).__name__)
            chosen = None
        if chosen and chosen != stay:  # a clear switch of topic, or a clear first choice
            return {"kind": "department", "department": next(d for d in config["departments"] if d["id"] == chosen), "layer": "meaning_switch" if stay else "meaning"}, spent
    if stay:
        return picked, spent
    try:  # nothing decided and there is no department to stay in: one short call to the cheapest model
        answer = deps.runtime.llm(orchestrator.router_messages(config, history, text), orchestrator.ROUTER_MAX_TOKENS)
        spent["tokens"] += int(answer.get("tokens") or 0)
        spent["cost_micro"] += int(answer.get("cost_micro") or 0)
        chosen = orchestrator.parse_router(answer.get("text"), config)
    except Exception as error:
        log("routing_model_failed", error=type(error).__name__)
        return picked, spent
    if chosen == "general":
        return {"kind": "none", "department": None, "layer": "router_general"}, spent
    if chosen:
        return {"kind": "department", "department": next(d for d in config["departments"] if d["id"] == chosen), "layer": "router"}, spent
    return picked, spent


def route_run(event, deps, now):
    """Where would each message of a routing table go (started by the admin function and nobody else): the guard, the rules, the meaning match and the routing model, WITHOUT asking any
    department to answer, so a run is cheap. Rows are given in the event (Studio sends the table). Returns {"result": {"rows": [{"id", "got", "layer", "ok"}], "passed", "spent_micro"}}
    or {"error": code}."""
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    config, used = dry_target(event["agent_id"], tenant, event, deps)
    if used is None:
        return config
    if not config.get("departments"):
        return {"error": "not_orchestrator"}
    try:
        rows = orchestrator.clean_rows(event.get("rows"))
    except orchestrator.Invalid:
        return {"error": "bad_event"}
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}

    def one(row):
        history = [{"c": "earlier", "b": "earlier", "d": row["in_department"], "t": now}] if row.get("in_department") else []
        reason = handover.decide(row["text"], history, config["handover_on"])
        if reason:
            return {"id": row["id"], "got": "handover", "layer": "guard", "tokens": 0, "cost_micro": 0}
        routed, spent = route_message(config, row["text"], history, now, deps)
        return {"id": row["id"], "got": orchestrator.outcome(routed, None), "layer": routed["layer"], **spent}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        done = list(pool.map(one, rows))
    cost = sum(d["cost_micro"] for d in done)
    spend(deps, key, {"cost_micro": cost}, now)
    expect = {row["id"]: row["expect"] for row in rows}
    out = [{"id": d["id"], "got": d["got"], "layer": d["layer"], "ok": d["got"] == expect[d["id"]]} for d in done]
    return {"result": {"rows": out, "passed": sum(1 for r in out if r["ok"]), "spent_micro": int(cost)}}


def orchestrate(config, text, history, context, tenant_id, deps, now, explain=False):
    """One message to an orchestrator: (reply, metrics, hand-over reason or None), like answer_text. The guard first (the hand-over rules, for the whole number), then the rules
    (menu, a reply to the menu, a keyword, the department the conversation is in), then the orchestrator's own books; when they are not sure, the menu. A chosen department answers
    with its own live version, exactly as if it were the bot of this number."""
    stay = orchestrator.current(history, now, config["sticky_minutes"])
    by_id = {d["id"]: d for d in config["departments"]}
    reason = handover.decide(text, history, config["handover_on"], (context or {}).get("attachments"))
    if reason:
        team = by_id[stay]["team_id"] if stay in by_id else None
        return config["handover_text"], {"route": "handover", "route_layer": "guard", "department": stay, "team_id": team}, reason
    picked, spent = route_message(config, text, history, now, deps)
    if picked["kind"] == "menu" or (picked["kind"] == "none" and not str(text or "").strip()):
        return orchestrator.menu_text(config), {"route": "menu", "route_layer": picked["layer"], **spent}, None
    if picked["kind"] == "department":
        department = picked["department"]
        part, live = department_config(department, tenant_id, deps)
        reply, metrics, reason = answer_text(part, text, history, context, tenant_id, deps.runtime, explain)
        if reason:  # the department's own hand-over sentence; its team takes the conversation
            reply = part["handover_text"]
        metrics.update(department=department["id"], route_layer=picked["layer"], department_version=live, team_id=department["team_id"], media_pool=part.get("media") or [],
                       tokens=metrics.get("tokens", 0) + spent["tokens"], cost_micro=metrics.get("cost_micro", 0) + spent["cost_micro"])
        return reply, metrics, reason
    try:
        made = engine.respond(config, text, tenant_id, deps.runtime, history, context, explain)  # the orchestrator's own books: what belongs to the business as a whole
    except engine.RuntimeFailure as failure:
        if failure.reason != "no_shelf":
            raise
        return orchestrator.menu_text(config), {**failure.metrics, "route": "menu", "route_layer": "ask", **spent}, None  # no general shelf (yet): a greeting gets the menu, not a hand-over
    made["metrics"].update(tokens=made["metrics"].get("tokens", 0) + spent["tokens"], cost_micro=made["metrics"].get("cost_micro", 0) + spent["cost_micro"])
    if made["route"] == "fallback":  # they are not sure: ask, never guess
        return orchestrator.menu_text(config), {**made["metrics"], "route": "menu", "route_layer": "ask"}, None
    made["metrics"].update(department="general", route_layer="router_general" if picked["layer"] == "router_general" else "general")
    return made["text"], made["metrics"], None


def answer_any(config, text, history, context, tenant_id, deps, now, explain=False):
    """answer_text for an ordinary agent, orchestrate for an orchestrator: the one entry of a real answer, the test chat and a suite run."""
    if config.get("departments"):
        return orchestrate(config, text, history, context, tenant_id, deps, now, explain)
    return answer_text(config, text, history, context, tenant_id, deps.runtime, explain)


def compose(event, agent, tenant, deps, now, history):
    """The text to send: (reply, metrics, hand-over reason or None). Reads the agent's live version, checks the business's monthly spend, then answers (answer_text). Raises
    Refused with a reason code when there is nothing to send."""
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
    try:
        reply_text, metrics, reason = answer_any(config, text, history, {**context_of(event), "media_per_reply": media.limits_of(tenant)["media_per_reply"]}, tenant["tenant_id"], deps, now,
                                                 explain=deps.traces is not None)  # the passages and their scores, for the trace only
    except engine.RuntimeFailure as failure:
        spend(deps, key, failure.metrics, now)
        raise Refused(failure.reason, failure.metrics, config) from None
    spend(deps, key, metrics, now)
    metrics.update(version=live, channel=context_of(event)["channel"], _tenant=tenant["tenant_id"], _text=reply_text)  # the worker takes the underscored ones out again: they are for the trace only
    pool = metrics.pop("media_pool", None)  # an orchestrator's department: the files of the department that answered, not the orchestrator's own
    if metrics.get("media"):  # the items the model chose, for the worker to send after the text (a dry run only describes them)
        by_id = {i["id"]: i for i in (pool if pool is not None else config.get("media")) or []}
        metrics["media_items"] = [by_id[i] for i in metrics["media"] if i in by_id]  # in the order the model chose
    return reply_text, metrics, reason


def dry_turn(config, text, history, context, tenant_id, deps, key, now):
    """One message answered the way the worker would, but only described: {"route", "text", "used"?, "confident", "explain", "tokens", "spent_micro", "retrieval_ms", "llm_ms"} plus, for a
    hand-over (a reason or a failure of the answer), {"reason", "failure", "note"}. What it spends is counted against the business's month. Shared by the test chat (test) and the suite run."""
    try:
        reply_text, metrics, reason = answer_any(config, text, history, context, tenant_id, deps, now, explain=True)
    except engine.RuntimeFailure as failure:  # in real life this customer would be handed to a person: say so
        spend(deps, key, failure.metrics, now)
        return {"route": "handover", "reason": failure.reason, "failure": True, "text": config["handover_text"], "note": handover.note_text(failure.reason, history, text),
                "spent_micro": int(failure.metrics.get("cost_micro") or 0), "explain": failure.metrics.get("explain", []), "error": failure.metrics.get("error"), "http_status": failure.metrics.get("http_status")}
    spend(deps, key, metrics, now)
    out = {"route": metrics.get("route"), "text": reply_text, "confident": bool(metrics.get("confident")), "explain": metrics.get("explain", []), "tokens": int(metrics.get("tokens") or 0),
           "spent_micro": int(metrics.get("cost_micro") or 0), "via": metrics.get("via"), "retrieval_ms": int(metrics.get("retrieval_ms") or 0), "llm_ms": int(metrics.get("llm_ms") or 0)}
    if metrics.get("route_layer"):  # an orchestrator: which department answered and which rule chose it
        out.update(department=metrics.get("department"), route_layer=metrics["route_layer"])
    pool = metrics.pop("media_pool", None)
    out["media"] = media.describe([i for i in (pool if pool is not None else config.get("media")) or [] if i["id"] in (metrics.get("media") or [])])
    if reason:
        out.update(route="handover", reason=reason, failure=False, note=handover.note_text(reason, history, text))
    return out


def dry_target(agent_id, tenant, event, deps):
    """(config, label) for a dry run or a suite run from a draft configuration or a published version, or ({"error": code, ...}, None)."""
    if isinstance(event.get("config"), dict):
        source, used = event["config"], "the editor"
    else:
        number = event.get("version")
        found = deps.store.get(registry.version_key(agent_id, number)) if isinstance(number, int) and not isinstance(number, bool) and number >= 1 else None
        if not found:
            return {"error": "version_not_found"}, None
        source, used = found.get("config"), f"version {number}"
    try:
        return engine.clean_config(source), used
    except engine.ConfigError as error:
        return {"error": "bad_config", "message": str(error)}, None


def owned_pair(event, deps):
    """(agent, tenant) of a dry run or a suite run started by the admin function, or (None, {"error": code}): the tenant of the request must be the agent's own, and enabled."""
    agent_id, tenant_id = event.get("agent_id"), event.get("tenant_id")
    if not core.AGENT_ID.fullmatch(str(agent_id)):
        return None, {"error": "bad_event"}
    try:
        agent, tenant = deps.registry.load(agent_id)
    except Exception as error:
        log("registry_unavailable", agent=agent_id, error=type(error).__name__)
        return None, {"error": "registry_unavailable"}
    if not isinstance(agent, dict) or not isinstance(tenant, dict) or tenant.get("enabled") is not True or agent.get("tenant_id") != tenant.get("tenant_id") or tenant.get("tenant_id") != tenant_id:
        return None, {"error": "not_found"}
    return (agent, tenant), None


def test(event, deps, now):
    """A dry run for Studio's test chat (started by the admin function and nobody else): answer one message the way the worker would, from a draft configuration or from a published
    version, with the history Studio sends, but post nothing to Chatwoot, write no message item and no memory, and hand over to nobody: a hand-over is only described. What it spends
    is counted against the business's month like a real answer. The customer's words (here: the tester's) are not stored or logged. Returns {"result": {...}} or {"error": code}."""
    text = event.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"error": "bad_event"}
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    config, used = dry_target(event["agent_id"], tenant, event, deps)
    if used is None:
        return config
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}
    history = memory.clean(event.get("history"))
    text = text.strip()[:core.MAX_QUESTION_CHARS]
    out = dry_turn(config, text, history, context_of(event), event["tenant_id"], deps, key, now)
    out["used"] = used
    out["spent_micro"] = int(out.get("spent_micro") or 0)
    return {"result": out}


def interview(event, deps, now):
    """The interviewer of a teach session (started by the admin function and nobody else): `kind` "interview" asks the model for the next question about `topic`, `kind` "summary"
    for the plain list of what was learned. The model is the version's provider and model when it names them, else the platform's choice. No search, no Chatwoot, nothing stored here;
    what it costs is counted against the business's month. Returns {"result": {"question" | "text", "topic"?, "spent_micro"}} or {"error": code}."""
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    config, used = dry_target(event["agent_id"], tenant, event, deps)
    if used is None:
        return config
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}
    language = engine.LANGUAGES[config["language"]]
    facts = teach.clean_facts(event.get("facts"))
    extra = {k: config[k] for k in ("provider", "model") if config.get(k)}
    if event["kind"] == "interview":
        topic = event.get("topic")
        if topic not in interview_topics.TOPIC_IDS:
            return {"error": "bad_event"}
        recent = [str(x)[:teach.TEXT_CHARS] for x in (event.get("recent") or []) if isinstance(x, str)][:12]
        weak = [str(x)[:120] for x in (event.get("weak") or []) if isinstance(x, str)][:5]
        messages = interview_topics.question_messages(topic, language, config["instructions"], facts, recent, weak)
    elif event["kind"] == "summary":
        messages = interview_topics.summary_messages(language, config["instructions"], facts)
    else:
        return {"error": "bad_event"}
    try:
        answer = deps.runtime.llm(messages, INTERVIEW_MAX_TOKENS, **extra)
    except Exception as error:
        log("interview_failed", error=type(error).__name__)
        return {"error": "model_unavailable"}
    spend(deps, key, {"cost_micro": answer.get("cost_micro")}, now)
    spent = int(answer.get("cost_micro") or 0)
    if event["kind"] == "interview":
        question = interview_topics.parse_question(answer.get("text"), topic)
        return {"result": {"question": question, "topic": topic, "spent_micro": spent}} if question else {"error": "bad_answer"}
    text = interview_topics.clean_summary(answer.get("text"))
    return {"result": {"text": text, "spent_micro": spent}} if text else {"error": "bad_answer"}


def distill(event, deps, now):
    """Turn what a teach session's client said into proposed facts and instruction lines (started by the admin function and nobody else). `event["evidence"]` is the numbered
    notes ([{"n", "text"}]); the base (a version or a draft) gives the instructions, the language and the model. One model call; nothing is stored here; the cost is counted against the
    business's month. Returns {"result": {"knowledge": [...], "instructions_add", "spent_micro"}} or {"error": code}."""
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    config, used = dry_target(event["agent_id"], tenant, event, deps)
    if used is None:
        return config
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}
    notes = [{"n": e["n"], "text": e["text"][:teach.TEXT_CHARS * 2]} for e in (event.get("evidence") or [])
             if isinstance(e, dict) and isinstance(e.get("n"), int) and not isinstance(e.get("n"), bool) and isinstance(e.get("text"), str) and e["text"].strip()][:teach.MAX_TURNS]
    if not notes:
        return {"error": "bad_event"}
    extra = {k: config[k] for k in ("provider", "model") if config.get(k)}
    messages = interview_topics.distill_messages(engine.LANGUAGES[config["language"]], config["instructions"], notes)
    try:
        answer = deps.runtime.llm(messages, DISTILL_MAX_TOKENS, **extra)
    except Exception as error:
        log("distill_failed", error=type(error).__name__)
        return {"error": "model_unavailable"}
    spend(deps, key, {"cost_micro": answer.get("cost_micro")}, now)
    proposal = interview_topics.parse_proposal(answer.get("text"), {n["n"] for n in notes}, engine.EXTRA_CHARS)
    return {"result": {**proposal, "spent_micro": int(answer.get("cost_micro") or 0)}} if proposal else {"error": "bad_answer"}


def suggest(event, deps, now):
    """Test rows written from the books (started by the admin function and nobody else): passages of the agent's shelves are spread over everything the books hold, and the model writes
    diverse customer questions with the answer a good agent gives, only from those passages. With `dry` nothing is asked: the answer is what it would cost. One model call; nothing is stored
    here (the owner reviews the rows in Studio); the cost counts against the business's month. Returns {"result": {...}} or {"error": code}."""
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    config, used = dry_target(event["agent_id"], tenant, event, deps)
    if used is None:
        return config
    count = event.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or not 3 <= count <= suggest_rows.MAX_COUNT:
        return {"error": "bad_event"}
    if getattr(deps.runtime, "sample", None) is None:
        return {"error": "model_unavailable"}
    try:
        chosen = suggest_rows.sample(deps.runtime.sample(event["tenant_id"], config["collections"]))
    except Exception as error:
        log("suggest_failed", error=type(error).__name__)
        return {"error": "model_unavailable"}
    if not chosen:
        return {"error": "no_books"}
    tokens_in, tokens_out = suggest_rows.estimate_tokens(chosen, count)
    estimate = gemini.cost_micro(gemini.DEFAULT_MODEL, {"prompt_tokens": tokens_in, "completion_tokens": tokens_out})
    if event.get("dry") is True:
        return {"result": {"estimate_micro": estimate, "passages": len(chosen), "count": count}}
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}
    try:
        answer = deps.runtime.llm(suggest_rows.messages(engine.LANGUAGES[config["language"]], chosen, count), suggest_rows.MAX_TOKENS, **{k: config[k] for k in ("provider", "model") if config.get(k)})
    except Exception as error:
        log("suggest_failed", error=type(error).__name__)
        return {"error": "model_unavailable"}
    spend(deps, key, {"cost_micro": answer.get("cost_micro")}, now)
    rows = suggest_rows.parse(answer.get("text"), count, core.MAX_QUESTION_CHARS, suite.EXPECTED_CHARS)
    return {"result": {"rows": rows, "passages": len(chosen), "spent_micro": int(answer.get("cost_micro") or 0)}} if rows else {"error": "bad_answer"}


def consistency_check(event, deps, now):
    """Look for places of a version that disagree (started by the admin function and nobody else): the instructions, the fixed texts, the inbox notes and the media descriptions against the
    corrections a client taught and against each other. One model call; nothing is stored or changed (the owner reviews each suggested change in Studio); the cost counts against the business's
    month. Returns {"result": {"conflicts": [...], "checked": n, "spent_micro": n}} or {"error": code}."""
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    config, used = dry_target(event["agent_id"], tenant, event, deps)
    if used is None:
        return config
    found = consistency.fields(config)
    facts = consistency.corrections(config)
    for department in config.get("departments") or []:  # an orchestrator: the facts of the whole number must agree, so each department's live texts and corrections are read too
        try:
            part, _ = department_config(department, event["tenant_id"], deps)
        except engine.RuntimeFailure:
            continue  # a department that is off or not set up is not part of the check
        found += consistency.department_fields(department, part)
        facts += [f"({department['label']}) {text}" for text in consistency.corrections(part)]
    if len(found) < 2 and not facts:  # nothing to compare: one text and no correction
        return {"result": {"conflicts": [], "checked": len(found), "spent_micro": 0}}
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}
    try:
        answer = deps.runtime.llm(consistency.messages(engine.LANGUAGES[config["language"]], found, facts), consistency.MAX_TOKENS, **{k: config[k] for k in ("provider", "model") if config.get(k)})
    except Exception as error:
        log("consistency_failed", error=type(error).__name__)
        return {"error": "model_unavailable"}
    spend(deps, key, {"cost_micro": answer.get("cost_micro")}, now)
    text = answer.get("text")
    if not isinstance(text, str) or "[" not in text:
        return {"error": "bad_answer"}
    return {"result": {"conflicts": consistency.parse(text, found), "checked": len(found), "spent_micro": int(answer.get("cost_micro") or 0)}}


def compare(event, deps, now):
    """Ask the same few questions of two published versions, side by side (the owner's before and after of a teach session). The questions come from the admin function; every answer is a dry
    turn (nothing posted, nothing stored) whose cost is counted against the business's month. Returns {"result": {"rows": [{"q", "a", "b"}], "spent_micro"}} or {"error": code}."""
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    versions = event.get("versions")
    if not (isinstance(versions, list) and len(versions) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in versions)):
        return {"error": "bad_event"}
    configs = []
    for number in versions:
        config, used = dry_target(event["agent_id"], tenant, {"version": number}, deps)
        if used is None:
            return config
        configs.append(config)
    questions = [q.strip()[:core.MAX_QUESTION_CHARS] for q in (event.get("questions") or []) if isinstance(q, str) and q.strip()][:teach.COMPARE_QUESTIONS]
    if not questions:
        return {"error": "bad_event"}
    key = usage_key(event["tenant_id"], now)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        return {"error": "cap_reached"}
    context = {"channel": "web_widget", "inbox_id": None, "facts": []}

    def ask(job):
        question, config = job
        try:
            out = dry_turn(config, question, [], context, event["tenant_id"], deps, key, now)
            return {"text": out["text"], "route": out["route"]}
        except Exception as error:
            log("compare_failed", error=type(error).__name__)
            return {"text": None, "route": "error"}
    jobs = [(q, c) for q in questions for c in configs]
    with concurrent.futures.ThreadPoolExecutor(max_workers=SUITE_PARALLEL) as pool:
        answers = list(pool.map(ask, jobs))
    rows = [{"q": q, "a": answers[2 * i], "b": answers[2 * i + 1]} for i, q in enumerate(questions)]
    return {"result": {"rows": rows, "versions": versions}}


def repeat_run(event, deps, now):
    """A repeatability check (started by the admin function with an asynchronous event): a few of the agent's suite questions asked several times each of one published version, every time
    as a fresh conversation, one round over all the questions at a time with a short pause (so a busy provider is not mistaken for unstable answers). The record of the check (written
    first by the admin function) gets its progress after every round and, at the end, a signal per question with every different answer, and the version's line in `repeatstate#<agent>`.
    Nothing is posted to Chatwoot and no message item or memory is written; what it costs counts against the business's month. It judges nothing itself: a person reads the result."""
    began = time.monotonic()
    agent_id, number = event.get("agent_id"), event.get("version")
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    key_of = registry.repeat_key(agent_id, number)
    item = deps.store.get(key_of)
    if not item or item.get("tenant_id") != event["tenant_id"] or item.get("status") != "queued" or item.get("run_id") != event.get("run_id"):
        return {"error": "bad_event"}
    config, used = dry_target(agent_id, tenant, {"version": number}, deps)
    if used is None:
        return config
    stored = deps.store.get(registry.suite_key(agent_id)) or {}
    by_id = {c["id"]: c for c in stored.get("cases", [])}
    cases = [by_id[c] for c in item["cases"] if c in by_id]
    times, key = item["times"], usage_key(event["tenant_id"], now)

    def finish(**fields):
        item.update(fields, finished_at=now + int(time.monotonic() - began))
        deps.store.put(key_of, item)
    if deps.store.usage(key) >= monthly_cap_micro(tenant):
        finish(status="failed", error="cap_reached")
        return {"error": "cap_reached"}
    runs, spent = {c["id"]: [] for c in cases}, 0
    item.update(status="running")
    for round_number in range(times):
        for case in cases:
            if time.monotonic() - began > SUITE_BUDGET_SECONDS:  # the worker has a limit: the questions not yet asked are not asked, and the record says so
                item["partial"] = True
                continue
            context = {"channel": case["channel"], "inbox_id": case.get("inbox_id"), "facts": []}

            def answer(turn, history, case=case, context=context):
                return dry_turn(config, turn, history, context, event["tenant_id"], deps, key, now)
            runs[case["id"]].append(repeat.run_once(case, config, answer))
            time.sleep(repeat.GAP_SECONDS)
        item.update(done=round_number + 1)
        deps.store.put(key_of, item)
    asked = [c for c in cases if runs[c["id"]]]
    summaries = [repeat.summarise_case(c, runs[c["id"]]) for c in asked]
    label, code, notes = repeat.judge(summaries)
    spent = sum(r["spent_micro"] for rs in runs.values() for r in rs)
    finish(status="done", label=label, exit_code=code, notes=notes, summaries=summaries, spent_micro=spent)
    state = deps.store.get(registry.repeat_state_key(agent_id)) or {"versions": {}}
    state["versions"][str(number)] = {"run_id": item["run_id"], "label": label, "at": item["finished_at"], "times": times, "signals": {s["id"]: s["signal"] for s in summaries}}
    deps.store.put(registry.repeat_state_key(agent_id), state)
    log("repeat_run", agent=agent_id, version=number, label=label, questions=len(summaries))
    return {"label": label}


def run_case(case, config, tenant, deps, key, now):
    """One test case: its customer messages in order (each answered with the history so far, like a conversation), the last answer judged. A failed case is tried once more: a pass on
    the retry is a pass marked flaky (the model answered differently). Returns the case's record: {"id", "title", "severity", "ok", "flaky", "route", "failed": [...], "answer"}."""
    record = {"id": case["id"], "title": case["title"], "severity": case["severity"], "ok": False, "flaky": False, "ran": True, "route": None, "failed": []}
    context = {"channel": case["channel"], "inbox_id": case.get("inbox_id")}

    def judged(result):
        """What every record shows beside the verdict: the answer (cut) and, when the case has an expected answer, how close in meaning the answer is to it (0 to 1). The meaning is advice
        for the person reading the table, never part of pass or fail; an embedding that cannot be made leaves it out."""
        shown = {"answer": str(result["text"])[:400]}
        if case.get("expected") and getattr(deps.runtime, "extras", None) is not None and result["route"] != "handover":
            try:
                scored = deps.runtime.extras([case["expected"]], str(result["text"]))
                spend(deps, key, {"tokens": scored["tokens"], "cost_micro": scored["cost_micro"]}, now)
                shown["meaning"] = round(min(1.0, max(0.0, float(scored["scores"][0]))), 2)
            except Exception as error:
                log("meaning_failed", agent=case["id"], error=type(error).__name__)
        return shown
    for attempt in (1, 2):
        history, result = [], None
        for turn in case["turns"]:
            if deps.store.usage(key) >= monthly_cap_micro(tenant):
                return {**record, "ran": False, "failed": [{"check": "run", "expected": "a run", "got": "the monthly spend limit was reached"}]}
            result = dry_turn(config, turn, history, context, tenant["tenant_id"], deps, key, now)
            history.append({"c": turn, "b": result["text"], **({"f": 1} if result["route"] == "fallback" else {}), **({"h": 1} if result["route"] == "handover" else {})})
            if result["route"] == "handover":  # in real life the bot goes quiet after a hand-over
                break
        failed = suite.evaluate(case, result, config)
        if not failed:
            return {**record, "ok": True, "flaky": attempt == 2, "route": result["route"], **judged(result)}
        record.update(route=result["route"], failed=failed, **judged(result))
        if result["route"] == "handover":  # why, so a hand-over that was not expected can be told from a failed answer
            record.update(reason=result.get("reason"), failure=bool(result.get("failure")), error=result.get("error"), http_status=result.get("http_status"))
    return record


def suite_run(event, deps, now):
    """A test suite run, started by the admin function (or the local command) with an asynchronous event: run the agent's cases against a published version or a draft, a few at a time,
    write the progress and the verdict to the run's item as it goes (Studio and the command read it), and, for a published version, what that version's last run found. Nothing is posted
    to Chatwoot, no message item or memory is written; the cost of the answers counts against the business's month. The cases are the operator's own test text and are stored with the run's
    results (the answers only for cases that failed); a customer's words are never involved."""
    began = time.monotonic()
    agent_id, run_id = event.get("agent_id"), str(event.get("run_id") or "")
    if not re.fullmatch(r"[0-9a-f]{12}", run_id):
        return {"error": "bad_event"}
    pair, problem = owned_pair(event, deps)
    if problem:
        return problem
    agent, tenant = pair
    run_key = registry.suite_run_key(agent_id, run_id)
    item = deps.store.get(run_key)
    if not item or item.get("tenant_id") != event["tenant_id"] or item.get("status") != "queued":
        return {"error": "bad_event"}
    config, used = dry_target(agent_id, tenant, event, deps)
    which = "central" if event.get("suite") == "central" else None  # the platform's central safety suite instead of the agent's own table
    stored = deps.store.get(registry.central_suite_key() if which else registry.suite_key(agent_id))
    state_key = registry.suite_state_key(agent_id, which)

    def finish(**fields):
        item.update(fields, finished_at=now + int(time.monotonic() - began))
        deps.store.put(run_key, item)
        state = deps.store.get(state_key) or {"runs": [], "versions": {}}
        entry = {"run_id": run_id, "target": item.get("target"), "version": item.get("version"), "status": item["status"], "ok": item.get("ok"), "passed": item.get("passed"), "total": item.get("total"),
                 "must_failed": item.get("must_failed"), "should_failed": item.get("should_failed"), "flaky": item.get("flaky"), "suite_rev": item.get("suite_rev"), "started_at": item.get("started_at"),
                 "finished_at": item["finished_at"]}
        state["runs"] = [entry] + [r for r in state.get("runs", []) if r.get("run_id") != run_id][:19]
        state["latest"] = {"run_id": run_id, "status": item["status"], "started_at": item.get("started_at")}
        if item.get("version") and item["status"] == "done":
            state.setdefault("versions", {})[str(item["version"])] = {k: entry[k] for k in ("run_id", "ok", "passed", "total", "must_failed", "suite_rev", "finished_at")}
        deps.store.put(state_key, state)
        return {"run": run_id, "status": item["status"]}
    if used is None:
        return finish(status="failed", error=config.get("error"), message=config.get("message"))
    if not stored or not stored.get("cases"):
        return finish(status="failed", error="no_suite")
    cases = stored["cases"]
    item.update(status="running", started_at=now, total=len(cases), done=0, results=[], suite_rev=stored.get("rev") or stored.get("version"), target=used)
    deps.store.put(run_key, item)
    key = usage_key(event["tenant_id"], now)
    lock = threading.Lock()
    done_at = time.monotonic() + SUITE_BUDGET_SECONDS
    records = {}

    def work(case):
        base = {"id": case["id"], "title": case["title"], "severity": case["severity"], "ok": False, "flaky": False, "ran": False, "route": None}
        if time.monotonic() > done_at:  # out of time: the rest is reported as not run, never guessed
            record = {**base, "failed": [{"check": "run", "expected": "a run", "got": "the time for the run was used up"}]}
        else:
            try:
                record = run_case(case, config, tenant, deps, key, now)
            except Exception as error:  # a bug in one case does not stop the others
                log("suite_case_error", agent=agent_id, case=case["id"], error=type(error).__name__)
                record = {**base, "failed": [{"check": "run", "expected": "a run", "got": f"an error ({type(error).__name__})"}]}
        with lock:
            records[case["id"]] = record
            item.update(done=len(records), results=[records[c["id"]] for c in cases if c["id"] in records])
            deps.store.put(run_key, item)
        return record
    with concurrent.futures.ThreadPoolExecutor(max_workers=SUITE_PARALLEL) as pool:
        list(pool.map(work, cases))
    results = [records[c["id"]] for c in cases]
    summary = suite.summarise(results)
    log("suite_run", agent=agent_id, run=run_id, passed=summary["passed"], total=summary["total"], must_failed=summary["must_failed"])
    return finish(status="done", results=results, ttl=now + SUITE_RUN_DAYS * 86400, **summary)


def hand_over(event, deps, base, credentials, reason, sentence, history, text, send_text, team_id=None, department=None):
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
    note_text = (f"Department: {department}.\n" if department else "") + handover.note_text(reason, history, text)
    note = call(core.reply_request(base, AUTH_STYLE, token, *ids, note_text, private=True), core.reply_result)
    flags["handover_note"] = int(note[0] == "replied")
    if team_id:  # an orchestrator's department: its own Chatwoot team gets the conversation
        assigned = call(core.assign_request(base, AUTH_STYLE, token, *ids, team_id), core.status_result)
        flags["handover_team"] = int(assigned[0] == "ok")
    opened = call(core.status_request(base, AUTH_STYLE, token, *ids, "open"), core.status_result)
    flags["handover_open"] = int(opened[0] == "ok")
    log("handover", agent=event["agent_id"], message=event.get("message_id"), reason=reason, text=flags.get("handover_text"), note=flags["handover_note"], opened=flags["handover_open"])
    if not (flags["handover_note"] and flags["handover_open"] and sent[0] == "replied"):  # something did not go through: visible in the log and on the item, by code only
        log("handover_incomplete", agent=event["agent_id"], message=event.get("message_id"), reason=reason, text=sent[1] if sent[0] == "failed" else None,
            note=note[1] if note[0] == "failed" else None, opened=opened[1] if opened[0] == "failed" else None)
    return sent, flags


def hand_over_outcome(event, deps, ready, reason, sentence, history, metrics, now):
    """The worker's outcome for a message that goes to a person: replied when the customer's sentence was posted, failed (with its code) when it was not."""
    (kind, detail), flags = hand_over(event, deps, ready["base"], ready["credentials"], reason, sentence, history, event.get("text") or "", True,
                                      team_id=(metrics or {}).get("team_id"), department=(metrics or {}).get("department"))
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


def send_media(event, deps, ready, items):
    """Post the chosen media after the text answer: [[id, outcome]] with outcome attached (a real attachment), link (the address or a short-lived link instead), failed, or unknown (the
    call timed out, so it may have arrived: nothing is sent twice). Never raises and never changes the answer that was already sent."""
    base, token = ready["base"], ready["credentials"]["api_token"]
    ids = (event["account_id"], event["conversation_id"])
    prefix = f"{media.PREFIX}{ready['tenant']['tenant_id']}/{event['agent_id']}/"
    limits = media.limits_of(ready["tenant"])

    def text_message(text):
        try:
            status, raw = deps.http(*core.reply_request(base, AUTH_STYLE, token, *ids, text), HTTP_TIMEOUT_SECONDS)
        except NetworkFailure:
            return False
        return core.reply_result(status, raw)[0] == "replied"

    def send_file(item):
        if deps.files is None or not item["key"].startswith(prefix):  # only this agent's own files
            return "failed"
        data = deps.files.get(item["key"], media.max_bytes(item["kind"], limits))
        if data is None or not media.looks_like(item["key"], data):
            return "failed"
        try:
            status, raw = deps.http(*core.attachment_request(base, AUTH_STYLE, token, *ids, media.file_name(item), media.mime_of(item["key"]), data, item["name"]), MEDIA_TIMEOUT_SECONDS)
            if core.reply_result(status, raw)[0] == "replied":
                return "attached"
        except TimeoutFailure:
            return "unknown"
        except NetworkFailure:
            pass
        return "link" if text_message(media.link_text(item, deps.files.link(item["key"], media.LINK_SECONDS))) else "failed"

    sent = []
    for item in items:
        try:
            if item["kind"] in ("link", "location"):
                outcome = "link" if text_message(media.link_text(item)) else "failed"
            else:
                outcome = send_file(item)
        except Exception as error:  # a file that cannot be read, a store that is down: the customer has the text answer already
            log("media_error", agent=event["agent_id"], media=item["id"], error=type(error).__name__)
            outcome = "failed"
        if outcome in ("failed", "unknown"):
            log("media_not_sent", agent=event["agent_id"], media=item["id"], outcome=outcome)
        sent.append([item["id"], outcome])
    return sent


def combined_message(event, deps, ready, text, items):
    """The answer and the first file in ONE Chatwoot message (the text is the file's caption; links and places are added to the text), or None when that cannot be done: no file,
    no file store, a file of another agent or that is not what its name says, or a text too long for a caption. Nothing is sent here."""
    first = next((i for i in items if i["kind"] in media.FILE_KINDS), None)
    prefix = f"{media.PREFIX}{ready['tenant']['tenant_id']}/{event['agent_id']}/"
    if first is None or deps.files is None or not first["key"].startswith(prefix):
        return None
    inline = [i for i in items if i["kind"] in ("link", "location")]
    caption = text + ("\n\n" + "\n".join(media.link_text(i) for i in inline) if inline else "")
    if len(caption) > MEDIA_CAPTION_CHARS:
        return None
    data = deps.files.get(first["key"], media.max_bytes(first["kind"], media.limits_of(ready["tenant"])))
    if data is None or not media.looks_like(first["key"], data):
        return None
    request = core.attachment_request(ready["base"], AUTH_STYLE, ready["credentials"]["api_token"], event["account_id"], event["conversation_id"], media.file_name(first),
                                      media.mime_of(first["key"]), data, caption)
    return {"request": request, "first": first, "inline": inline}


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
    items = metrics.pop("media_items", None) or []
    if reason:
        return hand_over_outcome(event, deps, ready, reason, text, history, metrics, now)
    combo = None
    if items:  # the answer and the first file as ONE message: the customer gets both at once, not the text and then, much later, the file
        try:
            combo = combined_message(event, deps, ready, text, items)
        except Exception as error:  # a file that cannot be read: the text-first way below handles it
            log("media_error", agent=event["agent_id"], error=type(error).__name__)
    if combo:
        started = time.monotonic()
        try:
            status, raw = deps.http(*combo["request"], MEDIA_TIMEOUT_SECONDS)
        except TimeoutFailure:  # it may have arrived: nothing is sent twice
            return failed("timeout", {**metrics, "media": [[combo["first"]["id"], "unknown"]]})
        except NetworkFailure:
            return failed("network", metrics)
        kind, detail = core.reply_result(status, raw)
        if kind == "replied":
            remember(event, deps, text, metrics, now)
            rest = [i for i in items if i is not combo["first"] and i not in combo["inline"]]
            sent = [[combo["first"]["id"], "attached"]] + [[i["id"], "link"] for i in combo["inline"]]
            metrics = {**metrics, "media": sent + (send_media(event, deps, ready, rest) if rest else []), "media_ms": int((time.monotonic() - started) * 1000), "media_together": 1}
            return {"status": "replied", "reply_id": detail, "metrics": metrics}
        log("media_together_refused", agent=event["agent_id"], reason=detail)  # Chatwoot refused it (a definite answer): the text-first way below
    linked = []
    if items and all(i["kind"] in ("link", "location") for i in items):  # links and places only: they go in the same text message, not in messages of their own
        text += "\n\n" + "\n".join(media.link_text(i) for i in items)
        linked, items = [[i["id"], "link"] for i in items], []
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
    if linked:
        metrics = {**metrics, "media": linked}
    if items:
        started = time.monotonic()
        metrics = {**metrics, "media": send_media(event, deps, ready, items), "media_ms": int((time.monotonic() - started) * 1000)}
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


def write_trace(event, outcome, deps, now):
    """Take the trace-only fields out of the outcome's metrics (they must not reach the message item) and write the message's trace. Returns {"trace_id": id} or {}. Never raises."""
    metrics = outcome.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    data = {name: metrics.pop(name, None) for name in ("_text", "_tenant", "explain")}
    tenant_id = data.pop("_tenant")
    if deps.traces is None or not tenant_id:
        return {}
    trace_id = chatwoot_trace.record(deps.traces, event, tenant_id, outcome, {"text": data["_text"], "explain": data["explain"]}, now)
    if trace_id is None:
        log("trace_not_written", agent=event.get("agent_id"), message=event.get("message_id"))
        return {}
    return {"trace_id": trace_id}


def worker(event, context=None, now=None, deps=None):
    now = int(now if now is not None else time.time())
    deps = deps or default_worker_deps()
    if event.get("kind") == "sweep":  # from the sweeper: a stuck message item, not a customer message
        return sweep(event, deps, now)
    if event.get("kind") == "test":  # from the admin function: Studio's dry run, answered in the same invocation
        return test(event, deps, now)
    if event.get("kind") == "route_run":  # from the admin function: where would each message of an orchestrator's routing table go
        return route_run(event, deps, now)
    if event.get("kind") == "consistency":  # from the admin function: the places of a version that disagree, for the owner to review
        return consistency_check(event, deps, now)
    if event.get("kind") == "suggest":  # from the admin function: test rows written from the agent's books for the owner to review
        return suggest(event, deps, now)
    if event.get("kind") == "repeat":  # from the admin function: a repeatability check, asynchronous
        return repeat_run(event, deps, now)
    if event.get("kind") == "distill":  # from the admin function: a teach session's facts, distilled for the owner to review
        return distill(event, deps, now)
    if event.get("kind") == "compare":  # from the admin function: the same questions asked of two versions
        return compare(event, deps, now)
    if event.get("kind") in ("interview", "summary"):  # from the admin function: the interviewer of a teach session, answered in the same invocation
        return interview(event, deps, now)
    if event.get("kind") == "suite":  # from the admin function or the local command: a test suite run, asynchronous
        return suite_run(event, deps, now)
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
    trace = write_trace(event, outcome, deps, now)  # one trace per message that reached the model's side; never raises
    if outcome["status"] == "replied":
        deps.store.finish(key, "replied", now, {**outcome["metrics"], **trace, **({"reply_id": outcome["reply_id"]} if outcome["reply_id"] else {})})
        log("replied", agent=agent_id, message=message_id, trace=trace.get("trace_id"))
        return {"replied": True}
    deps.store.finish(key, "failed", now, {**outcome["metrics"], **trace, "reason": outcome["reason"]})
    log("failed", agent=agent_id, message=message_id, reason=outcome["reason"])
    raise ReplyFailed(outcome["reason"])
