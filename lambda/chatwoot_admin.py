"""Chatwoot adapter, the Studio-facing admin API (step T3b): a business's Studio registers its agents and publishes versions over HTTP. One Lambda function
(ChatwootAdminFunction) on the same HTTP API as the webhook, routes under /admin, created only when ChatwootEnabled is 'true'.

  Authorization: Bearer cs_<tenant>.<secret>      the tenant's service key; the tenant is the key's, never taken from the body or the address

  GET    /admin/agents                           the tenant's agents
  PUT    /admin/agents/{name}                    create or update: {"account_id", "inbox_ids", "enabled"?, "type"?, "purpose"?, "expected_rev"?}
  GET    /admin/agents/{name}
  DELETE /admin/agents/{name}                    switch the agent off (nothing is deleted)
  PUT    /admin/agents/{name}/versions/{n}       publish version n (must be the next number): {"config": {...}}; a version never changes
  GET    /admin/agents/{name}/versions/{n}
  POST   /admin/agents/{name}/live               {"version": n} chooses the live version (0 = none)
  GET    /admin/agents/{name}/suite              the agent's test suite: {"rev", "cases"}
  PUT    /admin/agents/{name}/suite              replace it: {"cases": [...], "expected_rev"?}
  GET    /admin/agents/{name}/suite/state        the latest runs and, per published version, whether it passed
  POST   /admin/agents/{name}/suite/runs         start a run (asynchronous): {"version"} or {"config"}; answers 202 with its run_id
  GET    /admin/agents/{name}/suite/runs/{id}    a run's progress and, when done, its verdict per case
  POST   /admin/agents/{name}/test               Studio's dry run: {"text", "version" or "config", "history"?, "channel"?, "inbox_id"?}; the worker answers in the same call (nothing is posted
                                                 to Chatwoot, nothing is stored) and the answer, its route and the passages it used come back

Every error is JSON {"error": "<code>", "message": "..."}. An agent of another tenant is "not_found" on every route. A wrong, unknown, rotated or switched-off key is
the same 401 for all of them. Only the registry items (tenant#, agent#) are touched; nothing here reads a secret or calls Chatwoot; the one other thing it may do is ask the worker for a
dry run (the test route). The
teacher creates tenants and their keys with tools/chatwoot_admin.py (never over the web)."""
import base64
import json
import os
import re
import secrets
import time

import chatwoot_core as core
import chatwoot_media as media
import chatwoot_memory as memory
import chatwoot_registry as reg
import chatwoot_suite as suite
import chatwoot_teach as teach
import chatwoot_runtime as engine
import platform_settings
from chatwoot_store import DynamoStore

MAX_BODY_BYTES = platform_settings.kb("chatwoot.maxVersionKB") * 2  # a version plus the room a request needs around it
_NAME = core.AGENT_ID.pattern
LIST = re.compile(r"/admin/agents")
AGENT = re.compile(rf"/admin/agents/({_NAME})")
VERSION = re.compile(rf"/admin/agents/({_NAME})/versions/([0-9]{{1,6}})")
LIVE = re.compile(rf"/admin/agents/({_NAME})/live")
TEST = re.compile(rf"/admin/agents/({_NAME})/test")
MEDIA_UPLOAD = re.compile(rf"/admin/agents/({_NAME})/media/upload-url")
SUITE = re.compile(rf"/admin/agents/({_NAME})/suite")
TEACH = re.compile(rf"/admin/agents/({_NAME})/teach")
TEACH_ONE = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})")
TEACH_ACT = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/(turn|start|finish|revoke|interview|summary|confirm)")
TEACH_PROPOSAL = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/proposal")
TEACH_IMPROVE = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/(propose|draft|compare)")
TEACH_TEST = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/turns/([0-9]{{1,4}})/test")
SUITE_STATE = re.compile(rf"/admin/agents/({_NAME})/suite/state")
SUITE_RUNS = re.compile(rf"/admin/agents/({_NAME})/suite/runs")
SUITE_RUN = re.compile(rf"/admin/agents/({_NAME})/suite/runs/([0-9a-f]{{12}})")


def log(what, **details):
    print(json.dumps({"chatwoot_admin": what, **details}))


def reply(status, payload):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(payload)}


def problem(status, code, message, **more):
    return status, {"error": code, "message": message, **more}


def default_store():
    import boto3
    return DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"]))


# ---------- who is calling ----------

def authenticate(headers, store):
    """The tenant item of the caller, or None. The same work for a malformed key, an unknown tenant and a wrong secret."""
    value = headers.get("authorization") or ""
    parsed = reg.parse_service_key(value[7:]) if value[:7].lower() == "bearer " else None
    if not parsed:
        reg.key_matches(None, "x")
        return None
    tenant_id, secret = parsed
    tenant = store.get(reg.tenant_key(tenant_id))
    if isinstance(tenant, dict) and tenant.get("tenant_id") != tenant_id:
        tenant = None
    return tenant if reg.key_matches(tenant, secret) else None


# ---------- views ----------

def view(agent):
    return {"name": agent["agent_id"], **{k: agent.get(k) for k in ("account_id", "inbox_ids", "enabled", "type", "purpose", "live_version", "versions", "rev", "created_at", "updated_at")}}


def owned(store, tenant, name):
    """The agent if it exists and belongs to this tenant, else None (an agent of another tenant looks like no agent)."""
    agent = store.get(reg.agent_key(name))
    return agent if isinstance(agent, dict) and agent.get("tenant_id") == tenant["tenant_id"] else None


def add_to_tenant(store, tenant, name, now):
    """Put the agent's name in its tenant's list (the list is how 'my agents' is answered without scanning the table). A few tries on a lost race."""
    current = tenant
    for _ in range(3):
        agents = list(current.get("agents") or [])
        if name in agents:
            return True
        if len(agents) >= reg.MAX_AGENTS:
            return False
        grown = {**current, "agents": agents + [name], "rev": current["rev"] + 1, "updated_at": now}
        if store.replace_if(reg.tenant_key(current["tenant_id"]), grown, "rev", current["rev"]):
            return True
        current = store.get(reg.tenant_key(current["tenant_id"]))
        if current is None:
            return False
    return False


# ---------- the operations (shared with the teacher's local command) ----------

def put_agent(store, tenant, name, body, now):
    """Create or update an agent of this tenant: (status, payload)."""
    existing = store.get(reg.agent_key(name))
    if existing is not None and existing.get("tenant_id") != tenant["tenant_id"]:
        return problem(409, "name_taken", "That agent name is already used. Names are shared by all businesses: choose another.")
    settings = reg.clean_agent(body, creating=existing is None)
    if existing is None:
        if len(tenant.get("agents") or []) >= reg.MAX_AGENTS:
            return problem(409, "agent_limit", f"A business may have at most {reg.MAX_AGENTS} agents.")
        item = {"tenant_id": tenant["tenant_id"], "agent_id": name, "enabled": True, "live_version": 0, "versions": 0, "rev": 1, "created_at": now, "updated_at": now, **settings}
        if not store.claim(reg.agent_key(name), item):
            return problem(409, "conflict", "The agent was created by another call at the same moment. Try again.")
        if not add_to_tenant(store, tenant, name, now):
            store.forget(reg.agent_key(name))  # take it back: an agent that is not in its tenant's list would be invisible
            return problem(409, "agent_limit", "The agent could not be added to the business. Try again, or remove an agent first.")
        log("agent_created", tenant=tenant["tenant_id"], agent=name)
        return 201, view(item)
    if "expected_rev" in body and body["expected_rev"] != existing["rev"]:
        return problem(409, "changed", "The agent was changed since you read it.", rev=existing["rev"])
    updated = {**existing, **settings, "rev": existing["rev"] + 1, "updated_at": now}
    if not store.replace_if(reg.agent_key(name), updated, "rev", existing["rev"]):
        return problem(409, "changed", "The agent was changed by another call at the same moment. Read it and try again.")
    log("agent_updated", tenant=tenant["tenant_id"], agent=name)
    return 200, view(updated)


def check_media(config, tenant, name, files):
    """The version's media list must be well formed, belong to this agent, and (when the file store is at hand) the files must really be uploaded."""
    try:
        items = media.clean_items(config.get("media"), tenant["tenant_id"], name)
        if files is None and any(i["kind"] in media.FILE_KINDS for i in items) and os.environ.get("BUCKET"):
            files = media.S3Files()
        for item in items:
            if files is not None and item["kind"] in media.FILE_KINDS and not files.size(item["key"]):
                raise media.Invalid(f"Media {item['id']}: the file was not uploaded. Upload it again.")
    except media.Invalid as error:
        raise reg.Invalid(str(error)) from None


def media_upload(store, tenant, name, body, files):
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    try:
        return 200, media.upload_form(files, tenant["tenant_id"], name, body)
    except media.Invalid as error:
        raise reg.Invalid(str(error)) from None


def put_version(store, tenant, name, number, body, now, files=None):
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    config = reg.clean_config(body)
    check_media(config, tenant, name, files)
    versions = agent.get("versions") or 0
    if number > reg.MAX_VERSIONS:
        return problem(409, "version_limit", f"An agent may have at most {reg.MAX_VERSIONS} versions.")
    key = reg.version_key(name, number)
    if number <= versions:  # a version never changes: the same configuration again is a harmless repeat, a different one is refused
        found = store.get(key)
        if found and found.get("config") == config:
            return 200, {"version": number, "unchanged": True}
        return problem(409, "version_exists", "A published version never changes. Publish the next number instead.", expected=versions + 1)
    if number != versions + 1:
        return problem(409, "out_of_order", "Versions are published in order, one at a time.", expected=versions + 1)
    item = {"agent_id": name, "tenant_id": tenant["tenant_id"], "version": number, "config": config, "published_at": now}
    if not store.claim(key, item):
        found = store.get(key)  # left by an earlier call that did not move the pointer: carry on if it is the same configuration
        if not found or found.get("config") != config:
            return problem(409, "version_exists", "A published version never changes. Publish the next number instead.", expected=versions + 1)
    moved = {**agent, "versions": number, "rev": agent["rev"] + 1, "updated_at": now}
    if not store.replace_if(reg.agent_key(name), moved, "rev", agent["rev"]):
        return problem(409, "changed", "The agent was changed by another call at the same moment. Send the same request again.")
    log("version_published", tenant=tenant["tenant_id"], agent=name, version=number)
    return 201, {"version": number, "agent": view(moved)}


def set_live(store, tenant, name, body, now):
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    number = body.get("version") if isinstance(body, dict) and set(body) == {"version"} else None
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        raise reg.Invalid('Send {"version": n}: a whole number, 0 for none.')
    if number and (number > (agent.get("versions") or 0) or store.get(reg.version_key(name, number)) is None):
        return problem(404, "version_not_found", "No such version.")
    moved = {**agent, "live_version": number, "rev": agent["rev"] + 1, "updated_at": now}
    if not store.replace_if(reg.agent_key(name), moved, "rev", agent["rev"]):
        return problem(409, "changed", "The agent was changed by another call at the same moment. Read it and try again.")
    log("live_version_set", tenant=tenant["tenant_id"], agent=name, version=number)
    return 200, view(moved)


# ---------- the HTTP routes ----------

def parse_body(event):
    raw = event.get("body") or ""
    data = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode("utf8")
    if len(data) > MAX_BODY_BYTES:
        raise OverflowError
    try:
        body = json.loads(data.decode("utf8")) if data else {}
    except (ValueError, UnicodeDecodeError):
        raise reg.Invalid("The body must be JSON.") from None
    if not isinstance(body, dict):
        raise reg.Invalid("Send a JSON object.")
    return body


def invoke_worker_async(payload):
    """Start the worker on a suite run and do not wait (a run takes up to a minute and a half; the caller reads the run's item)."""
    import boto3
    response = boto3.client("lambda").invoke(FunctionName=os.environ["CHATWOOT_WORKER_FUNCTION"], InvocationType="Event", Payload=json.dumps(payload).encode("utf8"))
    if response.get("StatusCode") != 202:
        raise RuntimeError("the worker was not started")


def invoke_worker(payload):
    """Ask the worker for a dry run and wait for its answer (a synchronous call: Studio's test chat shows it at once)."""
    import boto3
    response = boto3.client("lambda").invoke(FunctionName=os.environ["CHATWOOT_WORKER_FUNCTION"], InvocationType="RequestResponse", Payload=json.dumps(payload).encode("utf8"))
    if response.get("FunctionError"):
        raise RuntimeError("the worker failed")
    return json.loads(response["Payload"].read())


def test_error(code):
    """(HTTP status, plain message) for a worker's dry-run error code; anything unknown is a 502."""
    known = {"bad_config": (400, "The configuration cannot be used."), "version_not_found": (404, "No such version."), "not_found": (404, "No such agent."),
             "cap_reached": (429, "This business has used its spend limit for the month."), "bad_event": (400, "Send some text."), "registry_unavailable": (503, "Try again in a moment.")}
    return known.get(code, (502, "The test could not be run. Try again."))


def run_test(store, tenant, name, body, invoke):
    """Validate a dry-run request, hand it to the worker with the tenant of the KEY (never of the body) and map the worker's answer."""
    unknown = sorted(set(body) - {"text", "history", "version", "config", "channel", "inbox_id"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise reg.Invalid("text is required: what the customer writes.")
    if ("version" in body) == ("config" in body):
        raise reg.Invalid("Send either version (a published version) or config (a draft), not both and not neither.")
    if "history" in body and not (isinstance(body["history"], list) and len(body["history"]) <= 50):
        raise reg.Invalid("history is a list of earlier turns: {\"c\": what the customer wrote, \"b\": what the bot answered}.")
    payload = {"kind": "test", "tenant_id": tenant["tenant_id"], "agent_id": name, "text": text.strip()[:core.MAX_QUESTION_CHARS], "history": memory.clean(body.get("history") or [])}
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        payload["config"] = body["config"]
    else:
        number = body["version"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= reg.MAX_VERSIONS:
            raise reg.Invalid("version must be a published version number.")
        payload["version"] = number
    channel = body.get("channel")
    if channel is not None:
        if not isinstance(channel, str) or not re.fullmatch(r"[a-z_]{1,40}", channel):
            raise reg.Invalid("channel is a short lowercase word such as whatsapp or web_widget.")
        payload["channel"] = channel
    inbox = body.get("inbox_id")
    if inbox is not None:
        if not isinstance(inbox, int) or isinstance(inbox, bool) or inbox < 1:
            raise reg.Invalid("inbox_id must be a whole number above zero.")
        payload["inbox_id"] = inbox
    answer = (invoke or invoke_worker)(payload)
    if not isinstance(answer, dict):
        return problem(502, "test_failed", "The test could not be run. Try again.")
    if "result" in answer and isinstance(answer["result"], dict):
        return 200, answer["result"]
    status, message = test_error(answer.get("error"))
    code = answer.get("error") if status != 502 else "test_failed"
    return problem(status, str(code), answer.get("message") if code == "bad_config" and isinstance(answer.get("message"), str) else message)


def get_suite(store, tenant, name):
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    item = store.get(reg.suite_key(name)) or {}
    return 200, {"rev": item.get("rev", 0), "cases": item.get("cases", []), "updated_at": item.get("updated_at")}


def put_suite(store, tenant, name, body, now):
    """Replace an agent's test suite: every case is checked (suite.clean_suite), the revision refuses an overwrite of a newer change."""
    unknown = sorted(set(body) - {"cases", "expected_rev"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    try:
        cases = suite.clean_suite(body.get("cases"))
    except suite.Invalid as error:
        raise reg.Invalid(str(error)) from None
    existing = store.get(reg.suite_key(name))
    if "expected_rev" in body and body["expected_rev"] != (existing or {}).get("rev", 0):
        return problem(409, "changed", "The suite was changed since you read it.", rev=(existing or {}).get("rev", 0))
    item = {"tenant_id": tenant["tenant_id"], "agent_id": name, "rev": (existing or {}).get("rev", 0) + 1, "cases": cases, "updated_at": now}
    stored = store.replace_if(reg.suite_key(name), item, "rev", existing["rev"]) if existing else store.claim(reg.suite_key(name), item)
    if not stored:
        return problem(409, "changed", "The suite was changed by another call at the same moment. Read it and try again.")
    log("suite_saved", tenant=tenant["tenant_id"], agent=name, cases=len(cases))
    return (200 if existing else 201), {"rev": item["rev"], "cases": cases, "updated_at": now}


def get_suite_state(store, tenant, name):
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    state = store.get(reg.suite_state_key(name)) or {}
    stored = store.get(reg.suite_key(name)) or {}
    return 200, {"runs": state.get("runs", []), "versions": state.get("versions", {}), "latest": state.get("latest"), "suite_rev": stored.get("rev", 0), "cases": len(stored.get("cases", []))}


def start_suite_run(store, tenant, name, body, now, invoke):
    """Start a run of the agent's suite on a published version or a draft: the run's item is written first (so Studio can read it at once), then the worker is started and not waited for.
    One run at a time per agent. Cost: about a tenth of a cent a case, counted against the business's month."""
    unknown = sorted(set(body) - {"version", "config"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    if ("version" in body) == ("config" in body):
        raise reg.Invalid("Send either version (a published version) or config (a draft), not both and not neither.")
    payload = {"kind": "suite", "tenant_id": tenant["tenant_id"], "agent_id": name}
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        payload["config"], label, version = body["config"], "the editor", None
    else:
        number = body["version"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= reg.MAX_VERSIONS:
            raise reg.Invalid("version must be a published version number.")
        payload["version"], label, version = number, f"version {number}", number
    stored = store.get(reg.suite_key(name))
    if not stored or not stored.get("cases"):
        return problem(409, "no_suite", "This agent has no test suite yet. Add cases first.")
    state = store.get(reg.suite_state_key(name)) or {"runs": [], "versions": {}}
    latest = state.get("latest") or {}
    if latest.get("status") in ("queued", "running") and now - int(latest.get("started_at") or 0) < 300:
        return problem(409, "run_in_progress", "A run of this suite is still going. Wait for it to finish.", run_id=latest.get("run_id"))
    run_id = secrets.token_hex(6)
    run = {"tenant_id": tenant["tenant_id"], "agent_id": name, "run_id": run_id, "status": "queued", "target": label, "version": version, "total": len(stored["cases"]), "done": 0, "results": [],
           "suite_rev": stored.get("rev"), "started_at": now, "ttl": now + 86400}
    store.put(reg.suite_run_key(name, run_id), run)
    store.put(reg.suite_state_key(name), {**state, "latest": {"run_id": run_id, "status": "queued", "started_at": now}})
    payload["run_id"] = run_id
    try:
        (invoke or invoke_worker_async)(payload)
    except Exception as error:
        store.forget(reg.suite_run_key(name, run_id))
        store.put(reg.suite_state_key(name), state)
        log("suite_not_started", tenant=tenant["tenant_id"], agent=name, error=type(error).__name__)
        return problem(502, "run_not_started", "The run could not be started. Try again.")
    log("suite_started", tenant=tenant["tenant_id"], agent=name, run=run_id)
    return 202, {"run_id": run_id, "total": run["total"], "target": label}


def get_suite_run(store, tenant, name, run_id):
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    item = store.get(reg.suite_run_key(name, run_id))
    if not item or item.get("tenant_id") != tenant["tenant_id"]:
        return problem(404, "not_found", "No such run.")
    return 200, {k: v for k, v in item.items() if k not in ("pk", "ttl")}


# ---------- teach sessions (chatwoot_teach.py) ----------

def load_session(store, tenant, name, sid):
    """The session if it is this agent's and this business's, else None (another business's session looks like none)."""
    if owned(store, tenant, name) is None:
        return None
    session = store.get(reg.teach_key(name, sid))
    return session if isinstance(session, dict) and session.get("tenant_id") == tenant["tenant_id"] and session.get("agent_id") == name else None


def add_to_teach_index(store, name, sid):
    """Put the session first in the agent's list of sessions (a few tries on a lost race)."""
    key = reg.teach_index_key(name)
    for _ in range(3):
        current = store.get(key)
        item = {"sids": ([sid] + [s for s in (current or {}).get("sids", []) if s != sid])[:teach.MAX_SESSIONS], "rev": (current or {}).get("rev", 0) + 1}
        if (store.claim(key, item) if current is None else store.replace_if(key, item, "rev", current["rev"])):
            return


def create_teach(store, tenant, name, body, now):
    """Open a teach session on a published version (the live one unless `version` is given) or on a draft `config`. Returns the session as the owner sees it."""
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    if "version" in body and "config" in body:
        raise reg.Invalid("Send either version or config, not both.")
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        try:
            base = {"config": engine.clean_config(body["config"])}
        except engine.ConfigError as error:
            raise reg.Invalid(str(error)) from None
    else:
        number = body.get("version", agent.get("live_version"))
        if not number:
            return problem(400, "no_live_version", "This agent has no live version: name a version or send a draft config.")
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= (agent.get("versions") or 0):
            raise reg.Invalid("version must be a published version number.")
        base = {"version": number}
    session = teach.create(tenant["tenant_id"], name, teach.new_sid(), body, base, now)
    if not store.claim(reg.teach_key(name, session["sid"]), session):
        return problem(409, "try_again", "Try again.")
    add_to_teach_index(store, name, session["sid"])
    log("teach_created", tenant=tenant["tenant_id"], agent=name)
    return 201, teach.view(session, now, owner=True)


def list_teach(store, tenant, name, now):
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    sessions = [store.get(reg.teach_key(name, sid)) for sid in (store.get(reg.teach_index_key(name)) or {}).get("sids", [])]
    return 200, {"sessions": [teach.row(s, now) for s in sessions if isinstance(s, dict) and s.get("tenant_id") == tenant["tenant_id"]]}


def get_teach(store, tenant, name, sid, owner, now):
    session = load_session(store, tenant, name, sid)
    return (200, teach.view(session, now, owner)) if session else problem(404, "not_found", "No such session.")


def teach_act(store, tenant, name, sid, action, body, now, invoke):
    """start (the client's name and the moment they read the consent line), turn (ask, fact or feedback), finish, revoke."""
    session = load_session(store, tenant, name, sid)
    if session is None:
        return problem(404, "not_found", "No such session.")
    if action == "revoke":
        changed = {**session, "status": "revoked", "rev": session["rev"] + 1}
    elif action == "finish":
        if session["status"] != "open":
            return 200, teach.view(session, now, owner=False)
        changed = {**session, "status": "finished", "rev": session["rev"] + 1}
    else:
        reason = teach.closed_reason(session, now)
        if reason:
            return problem(410 if reason in ("expired", "revoked", "finished") else 429, reason, TEACH_CLOSED.get(reason, "This session is closed."), closed=reason)
        if action == "start":
            unknown = sorted(set(body) - {"client_name"})
            if unknown:
                raise reg.Invalid(f"Unknown field: {unknown[0]}.")
            name_given = body.get("client_name", session["client_name"])
            if not isinstance(name_given, str) or len(name_given.strip()) > teach.NAME_MAX:
                raise reg.Invalid(f"client_name must be text of at most {teach.NAME_MAX} characters.")
            changed = {**session, "client_name": name_given.strip(), "consent_at": session["consent_at"] or now, "rev": session["rev"] + 1}
        elif action in ("interview", "summary", "confirm"):
            if not session["consent_at"]:
                return problem(409, "not_started", "Start the session first.")
            if action == "confirm":
                unknown = sorted(set(body) - {"ok", "note"})
                if unknown:
                    raise reg.Invalid(f"Unknown field: {unknown[0]}.")
                changed = teach.confirm_summary(session, body.get("ok"), body.get("note"), now)
            else:
                waiting = teach.pending_question(session) if action == "interview" else None
                if waiting is not None:  # one open question at a time: the same one is shown again, nothing is asked of the model
                    return 200, {"n": waiting["n"], "question": waiting["q"], "topic": waiting["topic"], "closed": None, "again": True}
                topic = teach.next_topic(session) if action == "interview" else None
                if action == "interview" and topic is None:
                    return 200, {"done": True, "closed": None}
                if action == "summary" and not teach.notes(session):
                    raise reg.Invalid("Nothing has been said yet to summarise.")
                payload = {"kind": action, "tenant_id": tenant["tenant_id"], "agent_id": name, "facts": teach.notes(session), **session["base"]}
                if action == "interview":
                    payload.update(topic=topic, recent=teach.recent_talk(session), weak=weak_spots(store, name))
                made = (invoke or invoke_worker)(payload)
                if not isinstance(made, dict) or not isinstance(made.get("result"), dict):
                    status, message = test_error((made or {}).get("error") if isinstance(made, dict) else None)
                    if isinstance(made, dict) and made.get("error") in ("model_unavailable", "bad_answer"):
                        return problem(502, str(made["error"]), "The assistant could not think of a question. Try again in a moment.")
                    return problem(status, "test_failed" if status == 502 else str(made.get("error")), message)
                result = made["result"]
                changed = (teach.add_question(session, result["question"], result["topic"], result.get("spent_micro"), now) if action == "interview"
                           else teach.set_summary(session, result["text"], result.get("spent_micro"), now))
        else:
            if not session["consent_at"]:
                return problem(409, "not_started", "Start the session first.")
            event = teach.clean_turn(body)
            answer = None
            if event["kind"] == "ask":
                payload = {"kind": "test", "tenant_id": tenant["tenant_id"], "agent_id": name, "text": event["text"][:core.MAX_QUESTION_CHARS], "history": memory.clean(teach.history(session)),
                           "facts": teach.facts(session), "channel": "web_widget", **session["base"]}
                made = (invoke or invoke_worker)(payload)
                if not isinstance(made, dict) or not isinstance(made.get("result"), dict):
                    status, message = test_error((made or {}).get("error") if isinstance(made, dict) else None)
                    return problem(status, "test_failed" if status == 502 else str(made.get("error")), message)
                answer = made["result"]
            changed = teach.add_turn(session, event, now, answer)
    if not store.replace_if(reg.teach_key(name, sid), changed, "rev", session["rev"]):
        return problem(409, "changed", "The session changed at the same moment. Try again.")
    out = {"status": changed["status"], "closed": teach.closed_reason(changed, now), "seconds_left": max(changed["expires_at"] - now, 0)}
    if action in ("turn", "interview", "summary", "confirm"):
        last = changed["turns"][-1]
        out.update(n=last["n"], answer=last.get("a"))
        if action == "interview":
            out.update(question=last["q"], topic=last["topic"])
        if action == "summary":
            out.update(summary=last["text"])
    return 200, out


TEACH_CLOSED = {"expired": "This session has ended.", "revoked": "This link is no longer active.", "finished": "This session is finished.", "turn_limit": "This session is full.",
                "cap_reached": "This session reached its limit."}


def worker_result(made):
    """(result, None) from a worker's answer, or (None, (status, payload)) with a plain message for the caller."""
    if isinstance(made, dict) and isinstance(made.get("result"), dict):
        return made["result"], None
    code = made.get("error") if isinstance(made, dict) else None
    if code in ("model_unavailable", "bad_answer"):
        return None, problem(502, str(code), "The assistant could not work this out. Try again in a moment.")
    status, message = test_error(code)
    return None, problem(status, "test_failed" if status == 502 else str(code), message)


def base_config(store, name, session):
    """The configuration a session talks to: the draft it was opened on, or the stored configuration of its version."""
    if "config" in session["base"]:
        return session["base"]["config"]
    found = store.get(reg.version_key(name, session["base"]["version"]))
    if not found:
        raise reg.Invalid("The version this session talks to is no longer there.")
    return found["config"]


def teach_improve(store, tenant, name, sid, action, body, now, invoke):
    """What the owner does with a finished session: propose (the model turns what the client said into facts and instruction lines to review), draft (the accepted ones become the next
    version, with a test for every corrected answer) and compare (the same questions asked of two versions). Nothing here changes what customers see: the new version is not live."""
    session = load_session(store, tenant, name, sid)
    if session is None:
        return problem(404, "not_found", "No such session.")
    agent = owned(store, tenant, name)
    if action == "compare":
        unknown = sorted(set(body) - {"a", "b"})
        if unknown:
            raise reg.Invalid(f"Unknown field: {unknown[0]}.")
        versions = [body.get("a"), body.get("b")]
        if not all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= (agent.get("versions") or 0) for v in versions) or versions[0] == versions[1]:
            raise reg.Invalid("a and b must be two different published versions.")
        asked = list(dict.fromkeys(t["q"] for t in session["turns"] if t["kind"] == "ask"))[:teach.COMPARE_QUESTIONS]
        if not asked:
            raise reg.Invalid("The session has no customer questions to compare.")
        result, trouble = worker_result((invoke or invoke_worker)({"kind": "compare", "tenant_id": tenant["tenant_id"], "agent_id": name, "versions": versions, "questions": asked}))
        return trouble or (200, result)
    proposal = session.get("proposal")
    if action == "propose":
        if proposal and proposal.get("version"):
            return problem(409, "already_made", "A version was already made from this session.")
        evidence = teach.evidence(session)
        if not evidence:
            raise reg.Invalid("Nothing has been said yet to learn from: answer some questions or correct an answer first.")
        result, trouble = worker_result((invoke or invoke_worker)({"kind": "distill", "tenant_id": tenant["tenant_id"], "agent_id": name, "evidence": evidence, **session["base"]}))
        if trouble:
            return trouble
        changed = teach.set_proposal(session, result, now)
        changed["spent_micro"] = session["spent_micro"] + int(result.get("spent_micro") or 0)
        extra = {}
    else:  # draft
        if not proposal:
            raise reg.Invalid("There is no proposal yet.")
        if proposal.get("version"):
            return problem(409, "already_made", f"Version {proposal['version']} was already made from this session.")
        config, facts, lines = teach.draft_config(base_config(store, name, session), session, engine.MAX_EXTRAS, engine.MAX_INSTRUCTION_CHARS)
        try:
            engine.clean_config(config)
        except engine.ConfigError as error:
            raise reg.Invalid(str(error)) from None
        number = (agent.get("versions") or 0) + 1
        status, made = put_version(store, tenant, name, number, {"config": config}, now)
        if status != 201:
            return status, made
        added = 0
        try:
            _, current = get_suite(store, tenant, name)
            have = {c["id"] for c in current["cases"]}
            new = [case for case, _ in teach.corrected_cases(session) if case["id"] not in have]
            if new:
                status, saved = put_suite(store, tenant, name, {"cases": current["cases"] + new, "expected_rev": current["rev"]}, now)
                added = len(new) if status in (200, 201) else 0
        except (reg.Invalid, KeyError):
            added = 0  # a full or unreadable suite must not undo a published version
        changed = {**session, "proposal": {**proposal, "version": number}, "rev": session["rev"] + 1}
        extra = {"version": number, "facts_added": facts, "instruction_lines_added": lines, "tests_added": added}
    if not store.replace_if(reg.teach_key(name, sid), changed, "rev", session["rev"]):
        return problem(409, "changed", "The session changed at the same moment. Try again.")
    log("teach_" + action, tenant=tenant["tenant_id"], agent=name)
    return 200, {**extra, "proposal": changed["proposal"]}


def save_teach_proposal(store, tenant, name, sid, body, now):
    session = load_session(store, tenant, name, sid)
    if session is None:
        return problem(404, "not_found", "No such session.")
    changed = teach.decide(session, body, now)
    if not store.replace_if(reg.teach_key(name, sid), changed, "rev", session["rev"]):
        return problem(409, "changed", "The session changed at the same moment. Try again.")
    return 200, {"proposal": changed["proposal"]}


def weak_spots(store, name):
    """Titles of the cases the agent's latest suite run got wrong (at most five): what the interviewer should ask about first. Best effort: no suite, no run, nothing."""
    try:
        latest = (store.get(reg.suite_state_key(name)) or {}).get("latest") or {}
        run = store.get(reg.suite_run_key(name, latest.get("run_id"))) if latest.get("run_id") else None
        return [str(r.get("title") or r.get("id"))[:120] for r in (run or {}).get("results", []) if not r.get("ok")][:5]
    except Exception:
        return []


def save_teach_test(store, tenant, name, sid, number, now):
    """Make a suite case from the client's correction of answer `number` (the numbers in it are the check), added to the agent's suite."""
    session = load_session(store, tenant, name, sid)
    if session is None:
        return problem(404, "not_found", "No such session.")
    case, note = teach.test_case(session, number)
    _, current = get_suite(store, tenant, name)
    cases = [c for c in current["cases"] if c["id"] != case["id"]] + [case]
    status, saved = put_suite(store, tenant, name, {"cases": cases, "expected_rev": current["rev"]} if current["rev"] else {"cases": cases}, now)
    if status not in (200, 201):
        return status, saved
    return 201, {"case": case, "note": note, "suite_rev": saved["rev"]}


def route(store, tenant, method, path, event, now, invoke=None, files=None):
    if LIST.fullmatch(path):
        if method != "GET":
            return problem(405, "method_not_allowed", "Use GET.")
        agents = [owned(store, tenant, name) for name in tenant.get("agents") or []]
        return 200, {"agents": [view(a) for a in agents if a]}
    found = AGENT.fullmatch(path)
    if found:
        name = found.group(1)
        if method == "GET":
            agent = owned(store, tenant, name)
            return (200, view(agent)) if agent else problem(404, "not_found", "No such agent.")
        if method == "PUT":
            return put_agent(store, tenant, name, parse_body(event), now)
        if method == "DELETE":
            agent = owned(store, tenant, name)
            if agent is None:
                return problem(404, "not_found", "No such agent.")
            return put_agent(store, tenant, name, {"enabled": False}, now)
        return problem(405, "method_not_allowed", "Use GET, PUT or DELETE.")
    found = VERSION.fullmatch(path)
    if found:
        name, number = found.group(1), int(found.group(2))
        if method == "GET":
            agent = owned(store, tenant, name)
            version = store.get(reg.version_key(name, number)) if agent and 1 <= number <= (agent.get("versions") or 0) else None
            if not version:
                return problem(404, "not_found", "No such version.")
            return 200, {"version": number, "config": version["config"], "published_at": version.get("published_at"), "live": agent.get("live_version") == number}
        if method == "PUT" and number >= 1:
            return put_version(store, tenant, name, number, parse_body(event), now, files)
        return problem(405 if number >= 1 else 404, "method_not_allowed" if number >= 1 else "not_found", "Use GET or PUT, with a version number of 1 or more.")
    found = LIVE.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return set_live(store, tenant, found.group(1), parse_body(event), now)
    found = TEACH_PROPOSAL.fullmatch(path)
    if found:
        return save_teach_proposal(store, tenant, found.group(1), found.group(2), parse_body(event), now) if method == "PUT" else problem(405, "method_not_allowed", "Use PUT.")
    found = TEACH_IMPROVE.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return teach_improve(store, tenant, found.group(1), found.group(2), found.group(3), parse_body(event), now, invoke)
    found = TEACH_TEST.fullmatch(path)
    if found:
        return save_teach_test(store, tenant, found.group(1), found.group(2), int(found.group(3)), now) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = TEACH_ACT.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return teach_act(store, tenant, found.group(1), found.group(2), found.group(3), parse_body(event), now, invoke)
    found = TEACH_ONE.fullmatch(path)
    if found:
        if method != "GET":
            return problem(405, "method_not_allowed", "Use GET.")
        return get_teach(store, tenant, found.group(1), found.group(2), (event.get("queryStringParameters") or {}).get("view") != "client", now)
    found = TEACH.fullmatch(path)
    if found:
        if method == "POST":
            return create_teach(store, tenant, found.group(1), parse_body(event), now)
        return list_teach(store, tenant, found.group(1), now) if method == "GET" else problem(405, "method_not_allowed", "Use GET or POST.")
    found = SUITE_RUN.fullmatch(path)
    if found:
        return get_suite_run(store, tenant, found.group(1), found.group(2)) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = SUITE_RUNS.fullmatch(path)
    if found:
        return start_suite_run(store, tenant, found.group(1), parse_body(event), now, invoke) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = SUITE_STATE.fullmatch(path)
    if found:
        return get_suite_state(store, tenant, found.group(1)) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = SUITE.fullmatch(path)
    if found:
        if method == "GET":
            return get_suite(store, tenant, found.group(1))
        return put_suite(store, tenant, found.group(1), parse_body(event), now) if method == "PUT" else problem(405, "method_not_allowed", "Use GET or PUT.")
    found = MEDIA_UPLOAD.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return media_upload(store, tenant, found.group(1), parse_body(event), files or media.S3Files())
    found = TEST.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return run_test(store, tenant, found.group(1), parse_body(event), invoke)
    return problem(404, "not_found", "No such route.")


def handler(event, context=None, now=None, store=None, invoke=None, files=None):
    now = int(now if now is not None else time.time())
    store = store or default_store()
    headers = {name.lower(): value for name, value in (event.get("headers") or {}).items()}
    try:
        tenant = authenticate(headers, store)
    except Exception as error:
        log("store_unavailable", error=type(error).__name__)
        return reply(500, {"error": "try_again", "message": "Try again in a moment."})
    if tenant is None:
        return reply(401, {"error": "unauthorized", "message": "Send the business's service key as: Authorization: Bearer cs_..."})
    try:
        status, payload = route(store, tenant, event["requestContext"]["http"]["method"], (event.get("rawPath") or "").rstrip("/"), event, now, invoke, files)
    except reg.Invalid as error:
        return reply(400, {"error": "bad_request", "message": str(error)})
    except OverflowError:
        return reply(413, {"error": "too_large", "message": "The request is too large."})
    except Exception as error:
        log("failed", tenant=tenant["tenant_id"], error=type(error).__name__)
        return reply(500, {"error": "try_again", "message": "Try again in a moment."})
    return reply(status, payload)
