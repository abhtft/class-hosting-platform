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

import chatwoot_archive as archive
import chatwoot_books as books
import chatwoot_core as core
import chatwoot_media as media
import chatwoot_memory as memory
import chatwoot_orchestrator as orchestrator
import chatwoot_registry as reg
import chatwoot_repeat as repeat
import chatwoot_suggest as suggest_rows
import chatwoot_trace
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
REPEAT = re.compile(rf"/admin/agents/({_NAME})/repeat")
REPEAT_ONE = re.compile(rf"/admin/agents/({_NAME})/repeat/([0-9]{{1,6}})")
TEACH = re.compile(rf"/admin/agents/({_NAME})/teach")
TEACH_ONE = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})")
TEACH_ACT = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/(turn|start|finish|revoke|interview|summary|confirm)")
TEACH_PROPOSAL = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/proposal")
TEACH_IMPROVE = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/(propose|draft|compare)")
TEACH_TEST = re.compile(rf"/admin/agents/({_NAME})/teach/([0-9a-f]{{12}})/turns/([0-9]{{1,4}})/test")
CENTRAL = re.compile(r"/admin/central-suite")
SUITE_STATE = re.compile(rf"/admin/agents/({_NAME})/suite/state")
TRACES = re.compile(rf"/admin/agents/({_NAME})/traces")
TRACES_SUMMARY = re.compile(rf"/admin/agents/({_NAME})/traces/summary")
TRACE_ONE = re.compile(rf"/admin/agents/({_NAME})/traces/(tr_[0-9a-f]{{16}})")
BOOKS = re.compile(r"/admin/books")
BOOKS_REFRESH = re.compile(r"/admin/books/refresh")
BOOKS_UPLOAD = re.compile(r"/admin/books/upload-url")
BOOKS_SEARCH = re.compile(r"/admin/books/search")
BOOKS_START = re.compile(r"/admin/books/([0-9a-f]{12})/start")
BOOKS_SHELF = re.compile(r"/admin/books/shelves/([a-z0-9][a-z0-9_-]{0,39})")
BOOKS_DOC = re.compile(r"/admin/books/([a-z0-9][a-z0-9_-]{0,39})/([a-z0-9][a-z0-9_-]{0,39})")
BOOKS_PASSAGES = re.compile(r"/admin/books/([a-z0-9][a-z0-9_-]{0,39})/([a-z0-9][a-z0-9_-]{0,39})/passages")
ROUTING_TESTS = re.compile(rf"/admin/agents/({_NAME})/routing-tests")
ROUTING_RUN = re.compile(rf"/admin/agents/({_NAME})/routing-run")
CONSISTENCY = re.compile(rf"/admin/agents/({_NAME})/consistency")
SUITE_SUGGEST = re.compile(rf"/admin/agents/({_NAME})/suite/suggest")
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
    shown = {"name": agent["agent_id"], **{k: agent.get(k) for k in ("account_id", "inbox_ids", "enabled", "type", "purpose", "live_version", "versions", "rev", "created_at", "updated_at")}}
    if agent.get("display_name"):  # the name people see; `name` stays the technical one (webhook address, keys)
        shown["display_name"] = agent["display_name"]
    if agent.get("archived_at"):  # archived: switched off, kept for the archive days, can be restored
        shown["archived_at"] = agent["archived_at"]
    if agent.get("overrides"):  # the reasons the owner gave for making a version live although the safety suite failed it
        shown["overrides"] = agent["overrides"]
    return shown


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
    archived = settings.pop("archived", None)
    if existing is None and archived is not None:
        raise reg.Invalid("A new agent cannot be archived.")
    if existing is not None and existing.get("archived_at") and settings.get("enabled") is True and archived is not False:
        return problem(409, "archived", "This agent is archived. Restore it first.")
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
    if "display_name" in settings and not settings["display_name"]:
        updated.pop("display_name", None)  # cleared
    if archived is True:  # switched off and kept for the archive days; an agent a live orchestrator lists as a department is not archived
        users = archive.used_by(store, tenant, name)
        if users:
            return problem(409, "in_use", "The live version of " + ", ".join(users) + " lists this agent as a department. Change that first.", agents=users)
        updated = {**updated, "archived_at": now, "enabled": False}
    elif archived is False:
        updated.pop("archived_at", None)  # restored: it stays switched off until the owner switches it on
    if not store.replace_if(reg.agent_key(name), updated, "rev", existing["rev"]):
        return problem(409, "changed", "The agent was changed by another call at the same moment. Read it and try again.")
    if archived is not None:
        log("agent_archived" if archived else "agent_restored", tenant=tenant["tenant_id"], agent=name)
    log("agent_updated", tenant=tenant["tenant_id"], agent=name)
    return 200, view(updated)


def check_media(config, tenant, name, files):
    """The version's media list must be well formed within the business's limits, belong to this agent, and (when the file store is at hand) the files must really be uploaded.
    Returns the checked items."""
    try:
        items = media.clean_items(config.get("media"), tenant["tenant_id"], name, media.limits_of(tenant))
        if files is None and any(i["kind"] in media.FILE_KINDS for i in items) and os.environ.get("BUCKET"):
            files = media.S3Files()
        for item in items:
            if files is not None and item["kind"] in media.FILE_KINDS and not files.size(item["key"]):
                raise media.Invalid(f"Media {item['id']}: the file was not uploaded. Upload it again.")
        return items
    except media.Invalid as error:
        raise reg.Invalid(str(error)) from None


def media_upload(store, tenant, name, body, files, now):
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    try:
        return 200, media.upload_form(store, files, tenant, name, body, now)
    except media.Invalid as error:
        raise reg.Invalid(str(error)) from None


def retire_version(store, tenant, name, number):
    """Take a published version away (its files are removed by the janitor when no other version lists them). Never the live version, never the last one."""
    agent = owned(store, tenant, name)
    if agent is None or not 1 <= number <= (agent.get("versions") or 0) or store.get(reg.version_key(name, number)) is None:
        return problem(404, "not_found", "No such version.")
    if agent.get("live_version") == number:
        return problem(409, "version_live", "The live version cannot be retired. Make another version live first.")
    left = [n for n in range(1, (agent.get("versions") or 0) + 1) if store.get(reg.version_key(name, n)) is not None]
    if len(left) <= 1:
        return problem(409, "last_version", "The last version cannot be retired.")
    store.forget(reg.version_key(name, number))
    log("version_retired", tenant=tenant["tenant_id"], agent=name, version=number)
    return 200, {"retired": number, "left": len(left) - 1}


def check_departments(store, tenant, agent, config):
    """An orchestrator's version (one with `departments`) must name department agents of this same business, and must not itself be a department. Invalid otherwise."""
    if "departments" not in config:
        return
    if agent.get("type") == "department":
        raise reg.Invalid("A department agent cannot list departments of its own: only an orchestrator does.")
    try:
        cleaned = orchestrator.clean(config)
    except orchestrator.Invalid as error:
        raise reg.Invalid(str(error)) from None
    for department in cleaned["departments"]:
        found = store.get(reg.agent_key(department["agent"]))
        if not found or found.get("tenant_id") != tenant["tenant_id"] or found.get("type") != "department":
            raise reg.Invalid(f"Department {department['id']}: {department['agent']} must be a department agent of this business (create it with type department).")


def put_version(store, tenant, name, number, body, now, files=None):
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    config = reg.clean_config(body)
    items = check_media(config, tenant, name, files)
    check_departments(store, tenant, agent, config)
    try:
        media.mark_kept(store, tenant["tenant_id"], name, items, now)
    except media.Invalid as error:
        raise reg.Invalid(str(error)) from None
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


def safety_failure(store, name, number):
    """The central safety suite's verdict on a published version when it FAILED a must-case on the suite as it is now, else None. A version that was never run, or was run on an older
    version of the suite, is not a failure here (Studio says so, but it is not held back)."""
    central = store.get(reg.central_suite_key())
    entry = ((store.get(reg.suite_state_key(name, "central")) or {}).get("versions") or {}).get(str(number))
    if not central or not entry or entry.get("suite_rev") != central.get("version"):
        return None
    return entry if entry.get("ok") is False or int(entry.get("must_failed") or 0) > 0 else None


def set_live(store, tenant, name, body, now):
    """Choose the live version. A version that failed a must-case of the central safety suite is not made live unless the caller gives a reason (10 to 300 characters), which is kept
    with the agent (`overrides`, the latest 20)."""
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    number = body.get("version") if isinstance(body, dict) and "version" in body and set(body) <= {"version", "override_reason"} else None
    if not isinstance(number, int) or isinstance(number, bool) or number < 0:
        raise reg.Invalid('Send {"version": n} (a whole number, 0 for none) and, when the safety suite failed, "override_reason".')
    reason = body.get("override_reason")
    if reason is not None and (not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 300):
        raise reg.Invalid("override_reason is the owner's reason in 10 to 300 characters.")
    if number and (number > (agent.get("versions") or 0) or store.get(reg.version_key(name, number)) is None):
        return problem(404, "version_not_found", "No such version.")
    blocked = safety_failure(store, name, number) if number else None
    if blocked and not reason:
        return problem(409, "safety_failed", f"The platform's safety suite found {int(blocked.get('must_failed') or 0)} must-case(s) failed on version {number}. Fix it, or give a reason to make it live anyway.",
                       must_failed=int(blocked.get("must_failed") or 0), run_id=blocked.get("run_id"))
    overrides = dict(agent.get("overrides") or {})
    if blocked:
        overrides[str(number)] = {"reason": reason.strip(), "at": now, "must_failed": int(blocked.get("must_failed") or 0), "run_id": blocked.get("run_id")}
        overrides = dict(sorted(overrides.items(), key=lambda kv: kv[1]["at"])[-20:])
        log("safety_override", tenant=tenant["tenant_id"], agent=name, version=number, must_failed=overrides[str(number)]["must_failed"])
    moved = {**agent, "live_version": number, "rev": agent["rev"] + 1, "updated_at": now, **({"overrides": overrides} if overrides else {})}
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
             "cap_reached": (429, "This business has used its spend limit for the month."), "bad_event": (400, "Send some text."), "registry_unavailable": (503, "Try again in a moment."),
             "no_books": (409, "This agent has no books to write questions from."), "not_orchestrator": (409, "This version is not an orchestrator: it lists no departments."), "bad_event": (400, "Check the shelves and the question."), "model_unavailable": (502, "The model could not be reached. Try again."), "bad_answer": (502, "The model did not give usable rows. Try again.")}
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


def suggest_tests(store, tenant, name, body, invoke):
    """Test rows written from the agent's books (plan T5): the worker reads passages of the shelves of a version or the draft and writes questions with expected answers for the owner to review.
    `dry` true answers with the cost estimate only. Nothing is saved here."""
    unknown = sorted(set(body) - {"version", "config", "count", "dry"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    if ("version" in body) == ("config" in body):
        raise reg.Invalid("Send either version (a published version) or config (a draft), not both and not neither.")
    count = body.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or not 3 <= count <= suggest_rows.MAX_COUNT:
        raise reg.Invalid(f"count is how many rows to write: a whole number from 3 to {suggest_rows.MAX_COUNT}.")
    if "dry" in body and not isinstance(body["dry"], bool):
        raise reg.Invalid("dry is true or false.")
    payload = {"kind": "suggest", "tenant_id": tenant["tenant_id"], "agent_id": name, "count": count, "dry": body.get("dry") is True}
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        payload["config"] = body["config"]
    else:
        number = body["version"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= reg.MAX_VERSIONS:
            raise reg.Invalid("version must be a published version number.")
        payload["version"] = number
    try:
        answer = (invoke or invoke_worker)(payload)
    except Exception as error:
        log("suggest_unavailable", error=type(error).__name__)
        return problem(502, "test_failed", "The rows could not be written. Try again.")
    if not isinstance(answer, dict):
        return problem(502, "test_failed", "The rows could not be written. Try again.")
    if isinstance(answer.get("result"), dict):
        return 200, answer["result"]
    status, message = test_error(answer.get("error"))
    code = answer.get("error") if status != 502 or answer.get("error") in ("model_unavailable", "bad_answer") else "test_failed"
    return problem(status, str(code), answer.get("message") if code == "bad_config" and isinstance(answer.get("message"), str) else message)


def start_repeat(store, tenant, name, body, now, invoke):
    """Start a repeatability check of one published version (asynchronous): a few of the agent's suite questions, each asked several times, for a person to read. The record is written first so
    Studio can read it at once. One check at a time per version. About a cent."""
    unknown = sorted(set(body) - {"version", "times", "cases"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    number = body.get("version", agent.get("live_version"))
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= (agent.get("versions") or 0):
        raise reg.Invalid("version must be a published version number.")
    times = body.get("times", repeat.TIMES)
    if not isinstance(times, int) or isinstance(times, bool) or not 2 <= times <= repeat.MAX_TIMES:
        raise reg.Invalid(f"times must be a whole number from 2 to {repeat.MAX_TIMES}.")
    wanted = body.get("cases", list(repeat.DEFAULT_CASES))
    if not isinstance(wanted, list) or not 1 <= len(wanted) <= suite.MAX_CASES or not all(isinstance(c, str) for c in wanted):
        raise reg.Invalid("cases must be a list of case ids of the agent's suite.")
    stored = store.get(reg.suite_key(name))
    if not stored or not stored.get("cases"):
        return problem(409, "no_suite", "This agent has no test suite yet. Add cases first.")
    have = {c["id"] for c in stored["cases"]}
    cases = [c for c in dict.fromkeys(wanted) if c in have]
    if not cases:
        return problem(409, "no_such_cases", "None of these questions is in the agent's suite.", have=sorted(have)[:10])
    key = reg.repeat_key(name, number)
    other = store.get(key) or {}
    if other.get("status") in ("queued", "running") and now - int(other.get("started_at") or 0) < 300:
        return problem(409, "run_in_progress", "A check of this version is still going. Wait for it to finish.", run_id=other.get("run_id"))
    run_id = secrets.token_hex(6)
    record = {"tenant_id": tenant["tenant_id"], "agent_id": name, "run_id": run_id, "version": number, "status": "queued", "times": times, "cases": cases, "total": times * len(cases),
              "done": 0, "started_at": now, "ttl": now + suite_run_seconds()}
    store.put(key, record)
    try:
        (invoke or invoke_worker_async)({"kind": "repeat", "tenant_id": tenant["tenant_id"], "agent_id": name, "version": number, "run_id": run_id})
    except Exception as error:
        store.put(key, {**record, "status": "failed", "error": "not_started"})
        log("repeat_not_started", tenant=tenant["tenant_id"], agent=name, error=type(error).__name__)
        return problem(502, "run_not_started", "The check could not be started. Try again.")
    log("repeat_started", tenant=tenant["tenant_id"], agent=name, run=run_id)
    return 202, {"run_id": run_id, "version": number, "total": record["total"], "cases": cases}


def suite_run_seconds():
    """How long the record of a check is kept (the same days as a suite run's)."""
    return platform_settings.value("chatwoot.suiteRunDays") * 86400


def get_repeat_state(store, tenant, name):
    """The latest check of every version that has one: {"versions": {"<n>": {"run_id", "label", "at", "times", "signals"}}}."""
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    return 200, {"versions": (store.get(reg.repeat_state_key(name)) or {}).get("versions", {})}


def get_repeat(store, tenant, name, number):
    """The latest check of one version: its progress, and when done the signal per question with every different answer, and the review sheet as Markdown."""
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    item = store.get(reg.repeat_key(name, number))
    if not item or item.get("tenant_id") != tenant["tenant_id"]:
        return problem(404, "not_found", "No check of this version yet.")
    out = {k: item.get(k) for k in ("run_id", "version", "status", "times", "cases", "total", "done", "started_at", "finished_at", "label", "notes", "summaries", "spent_micro", "partial", "error")}
    if item.get("status") == "done":
        out["sheet"] = repeat.sheet(item["summaries"], number, item["times"], item["label"])
    return 200, out


def query_of(event):
    """The query string as a dict: API Gateway gives it parsed; the local dev server gives only the raw string."""
    import urllib.parse
    return event.get("queryStringParameters") or {k: v[0] for k, v in urllib.parse.parse_qs(event.get("rawQueryString") or "").items()}


def traces_off():
    return problem(503, "traces_off", "Traces are not switched on for this platform.")


def list_traces(tenant, name, event, store, trace_store):
    """One page of an agent's answers (newest first): `day` (YYYY-MM-DD, UTC), `route` (rag, fallback, handover), `limit`, `before` (a started time from the last page)."""
    if trace_store is None:
        return traces_off()
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    q = query_of(event)
    unknown = sorted(set(q) - {"day", "route", "limit", "before"})
    if unknown:
        raise reg.Invalid(f"Unknown filter: {unknown[0]}.")
    day, route = q.get("day") or None, q.get("route") or None
    if day is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise reg.Invalid("day is a date like 2026-11-22.")
    if route is not None and route not in ("rag", "agent", "fallback", "handover"):
        raise reg.Invalid("route is rag, agent, fallback or handover.")
    numbers = {}
    for key in ("limit", "before"):
        if q.get(key):
            if not str(q[key]).isdigit():
                raise reg.Invalid(f"{key} is a whole number.")
            numbers[key] = int(q[key])
    try:
        return 200, chatwoot_trace.list_traces(trace_store, tenant["tenant_id"], name, day, route, numbers.get("limit"), numbers.get("before"))
    except ValueError:
        raise reg.Invalid("day is a date like 2026-11-22.")


def get_trace(tenant, name, trace_id, store, trace_store):
    if trace_store is None:
        return traces_off()
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    found = chatwoot_trace.get_trace(trace_store, tenant["tenant_id"], name, trace_id)
    return (200, found) if found else problem(404, "not_found", "No such trace (it may have expired).")


def trace_summary(tenant, name, event, store, trace_store, now):
    """How the agent's answers looked over the last days (default `chatwoot.traceSummaryDays`): totals, per day, per version, with the share that fell back or went to a person."""
    if trace_store is None:
        return traces_off()
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    days = query_of(event).get("days")
    if days is not None and not str(days).isdigit():
        raise reg.Invalid("days is a whole number.")
    return 200, chatwoot_trace.summary(trace_store, tenant["tenant_id"], name, int(days) if days else None, now)


def invoke_books(payload, wait=True):
    """Ask the books function (the only writer of the shelves) to do one thing: wait for its answer, or only start it (a book is read in the background)."""
    import boto3
    response = boto3.client("lambda").invoke(FunctionName=os.environ["CHATWOOT_BOOKS_FUNCTION"], InvocationType="RequestResponse" if wait else "Event", Payload=json.dumps(payload).encode("utf8"))
    if not wait:
        if response.get("StatusCode") != 202:
            raise RuntimeError("the books function was not started")
        return {}
    if response.get("FunctionError"):
        raise RuntimeError("the books function failed")
    return json.loads(response["Payload"].read())


def books_call(tenant, payload, wait=True):
    """invoke_books with the tenant of the KEY (never of the body), the answer mapped to (status, payload) problems; (None, answer) when it went through."""
    try:
        answer = invoke_books({**payload, "tenant_id": tenant["tenant_id"]}, wait)
    except Exception as error:
        log("books_unavailable", error=type(error).__name__)
        return problem(502, "books_unavailable", "The books could not be reached. Try again in a moment."), None
    if isinstance(answer, dict) and answer.get("error"):
        status = {"not_found": 404, "bad_event": 400}.get(answer["error"], 502)
        return problem(status, str(answer["error"]), str(answer.get("message") or "The books could not do that.")), None
    return None, answer


def get_books(store, tenant, now, refresh=False):
    """The business's shelves and books (from the index; built from the shelves on the first look or when asked)."""
    item = store.get(books.index_key(tenant["tenant_id"]))
    if item is None or refresh:
        failed, _ = books_call(tenant, {"kind": "reindex"})
        if failed:
            return failed
        item = store.get(books.index_key(tenant["tenant_id"]))
    return 200, books.view(item, now)


def books_upload(store, tenant, body, files, now):
    try:
        return 200, books.upload_form(store, files, tenant, body, now)
    except books.Invalid as error:
        raise reg.Invalid(str(error)) from None


def books_start(store, tenant, book_id, files, now):
    """The book has been sent to storage: check it arrived, mark it queued and start reading it in the background."""
    item = store.get(books.index_key(tenant["tenant_id"])) or {}
    pending = (item.get("pending") or {}).get(book_id)
    if not pending or pending.get("status") != "uploading":
        return problem(404, "not_found", "No such upload.")
    if not files.size(pending["key"]):
        return problem(409, "not_uploaded", "The file has not arrived. Send it to the address from the upload form first.")
    try:
        books.mark(store, tenant["tenant_id"], book_id, "queued", now)
    except books.Invalid as error:
        raise reg.Invalid(str(error)) from None
    failed, _ = books_call(tenant, {"kind": "ingest", "book_id": book_id}, wait=False)
    if failed:
        books.mark(store, tenant["tenant_id"], book_id, "failed", now, error="The book could not be started. Remove it and add it again.")
        return failed
    return 202, {"book_id": book_id, "status": "queued"}


def books_forget(store, tenant, book_id, now):
    """Remove a book that is pending (failed, or never finished uploading): only its record, nothing is on a shelf."""
    item = store.get(books.index_key(tenant["tenant_id"])) or {}
    pending = (item.get("pending") or {}).get(book_id)
    if not pending or pending.get("status") in ("queued", "reading"):
        return problem(409, "busy", "That book is being read. Wait until it is done or failed.")
    books.mark(store, tenant["tenant_id"], book_id, None, now)
    return 200, {"removed": True}


def shelves_in_use(store, tenant, shelf):
    """The agents whose LIVE version reads this shelf (a shelf in use is not deleted)."""
    users = []
    for name in tenant.get("agents") or []:
        agent = store.get(reg.agent_key(name)) or {}
        live = agent.get("live_version") or 0
        config = (store.get(reg.version_key(name, live)) or {}).get("config") if live else None
        if isinstance(config, dict) and shelf in (config.get("collections") or []):
            users.append(name)
    return users


def books_search(tenant, body, invoke):
    unknown = sorted(set(body) - {"shelves", "query"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    shelves, query = body.get("shelves"), body.get("query")
    if not isinstance(shelves, list) or not 1 <= len(shelves) <= 5 or not all(isinstance(s, str) and books.SHELF.fullmatch(s) for s in shelves):
        raise reg.Invalid("shelves is a list of 1 to 5 shelf names.")
    if not isinstance(query, str) or not query.strip() or len(query) > core.MAX_QUESTION_CHARS:
        raise reg.Invalid(f"query is a question of up to {core.MAX_QUESTION_CHARS} characters.")
    try:
        answer = (invoke or invoke_worker)({"kind": "books_search", "tenant_id": tenant["tenant_id"], "shelves": shelves, "query": query.strip()})
    except Exception as error:
        log("books_search_unavailable", error=type(error).__name__)
        return problem(502, "test_failed", "The search could not be made. Try again.")
    if isinstance(answer, dict) and isinstance(answer.get("result"), dict):
        return 200, answer["result"]
    status, message = test_error((answer or {}).get("error") if isinstance(answer, dict) else None)
    return problem(status, str((answer or {}).get("error") if status != 502 else "test_failed"), message)


def books_route(store, tenant, method, path, event, now, invoke, files):
    """The books routes (None when the path is not one of them)."""
    if BOOKS.fullmatch(path):
        return get_books(store, tenant, now) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    if BOOKS_REFRESH.fullmatch(path):
        return get_books(store, tenant, now, refresh=True) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    if BOOKS_UPLOAD.fullmatch(path):
        return books_upload(store, tenant, parse_body(event), files or media.S3Files(), now) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    if BOOKS_SEARCH.fullmatch(path):
        return books_search(tenant, parse_body(event), invoke) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = BOOKS_START.fullmatch(path)
    if found:
        return books_start(store, tenant, found.group(1), files or media.S3Files(), now) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = BOOKS_SHELF.fullmatch(path)
    if found:
        if method != "DELETE":
            return problem(405, "method_not_allowed", "Use DELETE.")
        users = shelves_in_use(store, tenant, found.group(1))
        if users:
            return problem(409, "shelf_in_use", "The live version of " + ", ".join(users) + " reads this shelf. Make another version live or change its shelves first.", agents=users)
        failed, _ = books_call(tenant, {"kind": "delete_shelf", "shelf": found.group(1)})
        return failed or (200, {"deleted": True})
    found = BOOKS_PASSAGES.fullmatch(path)
    if found:
        if method != "GET":
            return problem(405, "method_not_allowed", "Use GET.")
        failed, answer = books_call(tenant, {"kind": "passages", "shelf": found.group(1), "doc_id": found.group(2)})
        return failed or (200, answer)
    found = BOOKS_DOC.fullmatch(path)
    if found:
        shelf, doc = found.groups()
        if method == "DELETE" and shelf == "pending":  # a pending book (failed or never finished) is forgotten by its id
            return books_forget(store, tenant, doc, now)
        if method != "DELETE":
            return problem(405, "method_not_allowed", "Use DELETE.")
        failed, _ = books_call(tenant, {"kind": "delete_book", "shelf": shelf, "doc_id": doc})
        return failed or (200, {"deleted": True})
    return None


def get_routing_tests(store, tenant, name):
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    item = store.get(reg.routing_tests_key(name)) or {}
    return 200, {"rev": item.get("rev", 0), "rows": item.get("rows", []), "updated_at": item.get("updated_at")}


def put_routing_tests(store, tenant, name, body, now):
    """Replace an orchestrator's routing table: every row is checked (orchestrator.clean_rows), the revision refuses an overwrite of a newer change."""
    unknown = sorted(set(body) - {"rows", "expected_rev"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    try:
        rows = orchestrator.clean_rows(body.get("rows"))
    except orchestrator.Invalid as error:
        raise reg.Invalid(str(error)) from None
    existing = store.get(reg.routing_tests_key(name))
    if "expected_rev" in body and body["expected_rev"] != (existing or {}).get("rev", 0):
        return problem(409, "changed", "The table was changed since you read it.", rev=(existing or {}).get("rev", 0))
    item = {"tenant_id": tenant["tenant_id"], "agent_id": name, "rev": (existing or {}).get("rev", 0) + 1, "rows": rows, "updated_at": now}
    stored = store.replace_if(reg.routing_tests_key(name), item, "rev", existing["rev"]) if existing else store.claim(reg.routing_tests_key(name), item)
    if not stored:
        return problem(409, "changed", "The table was changed by another call at the same moment. Read it and try again.")
    log("routing_tests_saved", tenant=tenant["tenant_id"], agent=name, rows=len(rows))
    return (200 if existing else 201), {"rev": item["rev"], "rows": rows, "updated_at": now}


def run_routing(store, tenant, name, body, invoke):
    """Ask the worker where each row of a routing table would go (the rows come from the caller: Studio sends the table it shows). Nothing is saved."""
    unknown = sorted(set(body) - {"version", "config", "rows"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    if ("version" in body) == ("config" in body):
        raise reg.Invalid("Send either version (a published version) or config (a draft), not both and not neither.")
    try:
        rows = orchestrator.clean_rows(body.get("rows"))
    except orchestrator.Invalid as error:
        raise reg.Invalid(str(error)) from None
    payload = {"kind": "route_run", "tenant_id": tenant["tenant_id"], "agent_id": name, "rows": rows}
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        payload["config"] = body["config"]
    else:
        number = body["version"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= reg.MAX_VERSIONS:
            raise reg.Invalid("version must be a published version number.")
        payload["version"] = number
    try:
        answer = (invoke or invoke_worker)(payload)
    except Exception as error:
        log("routing_run_unavailable", error=type(error).__name__)
        return problem(502, "test_failed", "The routing run could not be made. Try again.")
    if not isinstance(answer, dict):
        return problem(502, "test_failed", "The routing run could not be made. Try again.")
    if isinstance(answer.get("result"), dict):
        return 200, answer["result"]
    status, message = test_error(answer.get("error"))
    code = answer.get("error") if status != 502 else "test_failed"
    return problem(status, str(code), message)


def check_consistency(store, tenant, name, body, invoke):
    """Ask the worker which places of a version (or of the editor's draft) disagree. Nothing is saved: Studio shows each suggested change and the owner accepts or ignores it."""
    unknown = sorted(set(body) - {"version", "config"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    if ("version" in body) == ("config" in body):
        raise reg.Invalid("Send either version (a published version) or config (a draft), not both and not neither.")
    payload = {"kind": "consistency", "tenant_id": tenant["tenant_id"], "agent_id": name}
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        payload["config"] = body["config"]
    else:
        number = body["version"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= reg.MAX_VERSIONS:
            raise reg.Invalid("version must be a published version number.")
        payload["version"] = number
    try:
        answer = (invoke or invoke_worker)(payload)
    except Exception as error:
        log("consistency_unavailable", error=type(error).__name__)
        return problem(502, "test_failed", "The check could not be run. Try again.")
    if not isinstance(answer, dict):
        return problem(502, "test_failed", "The check could not be run. Try again.")
    if isinstance(answer.get("result"), dict):
        return 200, answer["result"]
    status, message = test_error(answer.get("error"))
    code = answer.get("error") if status != 502 or answer.get("error") in ("model_unavailable", "bad_answer") else "test_failed"
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


def get_central_suite(store):
    """The platform's central safety suite: the same for every business, read only (the teacher loads it with the local command)."""
    item = store.get(reg.central_suite_key()) or {}
    return 200, {"version": item.get("version", 0), "title": item.get("title"), "cases": item.get("cases", []), "updated_at": item.get("updated_at")}


def get_suite_state(store, tenant, name, which=None):
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    state = store.get(reg.suite_state_key(name, which)) or {}
    stored = store.get(reg.central_suite_key() if which == "central" else reg.suite_key(name)) or {}
    return 200, {"runs": state.get("runs", []), "versions": state.get("versions", {}), "latest": state.get("latest"), "suite_rev": stored.get("rev", stored.get("version", 0)), "cases": len(stored.get("cases", []))}


def start_suite_run(store, tenant, name, body, now, invoke):
    """Start a run of the agent's suite on a published version or a draft: the run's item is written first (so Studio can read it at once), then the worker is started and not waited for.
    One run at a time per agent. Cost: about a tenth of a cent a case, counted against the business's month."""
    unknown = sorted(set(body) - {"version", "config", "suite"})
    if unknown:
        raise reg.Invalid(f"Unknown field: {unknown[0]}.")
    if body.get("suite", "agent") not in ("agent", "central"):
        raise reg.Invalid("suite must be agent (the agent's own table) or central (the platform's safety suite).")
    which = "central" if body.get("suite") == "central" else None
    body = {k: v for k, v in body.items() if k != "suite"}
    if owned(store, tenant, name) is None:
        return problem(404, "not_found", "No such agent.")
    if ("version" in body) == ("config" in body):
        raise reg.Invalid("Send either version (a published version) or config (a draft), not both and not neither.")
    payload = {"kind": "suite", "tenant_id": tenant["tenant_id"], "agent_id": name, **({"suite": "central"} if which else {})}
    if "config" in body:
        if not isinstance(body["config"], dict) or len(json.dumps(body["config"]).encode("utf8")) > reg.MAX_VERSION_BYTES:
            raise reg.Invalid(f"config must be a JSON object of at most {reg.MAX_VERSION_BYTES // 1024} KB.")
        payload["config"], label, version = body["config"], "the editor", None
    else:
        number = body["version"]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= reg.MAX_VERSIONS:
            raise reg.Invalid("version must be a published version number.")
        payload["version"], label, version = number, f"version {number}", number
    stored = store.get(reg.central_suite_key() if which else reg.suite_key(name))
    if not stored or not stored.get("cases"):
        return problem(409, "no_suite", "The platform has no central safety suite yet." if which else "This agent has no test suite yet. Add cases first.")
    state = store.get(reg.suite_state_key(name, which)) or {"runs": [], "versions": {}}
    for other in (state, store.get(reg.suite_state_key(name, None if which else "central")) or {}):  # one run at a time for an agent, whichever suite it is
        latest = other.get("latest") or {}
        if latest.get("status") in ("queued", "running") and now - int(latest.get("started_at") or 0) < 300:
            return problem(409, "run_in_progress", "A run of this suite is still going. Wait for it to finish.", run_id=latest.get("run_id"))
    run_id = secrets.token_hex(6)
    run = {"tenant_id": tenant["tenant_id"], "agent_id": name, "run_id": run_id, "status": "queued", "target": label, "version": version, "total": len(stored["cases"]), "done": 0, "results": [],
           "suite_rev": stored.get("rev") or stored.get("version"), "started_at": now, "ttl": now + 86400, **({"suite": "central"} if which else {})}
    store.put(reg.suite_run_key(name, run_id), run)
    store.put(reg.suite_state_key(name, which), {**state, "latest": {"run_id": run_id, "status": "queued", "started_at": now}})
    payload["run_id"] = run_id
    try:
        (invoke or invoke_worker_async)(payload)
    except Exception as error:
        store.forget(reg.suite_run_key(name, run_id))
        store.put(reg.suite_state_key(name, which), state)
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
        config, facts, lines = teach.draft_config(base_config(store, name, session), session, engine.MAX_EXTRAS, engine.MAX_INSTRUCTION_CHARS, now)
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


def route(store, tenant, method, path, event, now, invoke=None, files=None, trace_store=None):
    if path == "/admin/books" or path.startswith("/admin/books/"):
        done = books_route(store, tenant, method, path, event, now, invoke, files)
        if done is not None:
            return done
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
        if method == "DELETE" and number >= 1:
            return retire_version(store, tenant, name, number)
        return problem(405 if number >= 1 else 404, "method_not_allowed" if number >= 1 else "not_found", "Use GET or PUT, with a version number of 1 or more.")
    found = LIVE.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return set_live(store, tenant, found.group(1), parse_body(event), now)
    found = REPEAT_ONE.fullmatch(path)
    if found:
        return get_repeat(store, tenant, found.group(1), int(found.group(2))) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = REPEAT.fullmatch(path)
    if found:
        if method == "POST":
            return start_repeat(store, tenant, found.group(1), parse_body(event), now, invoke)
        return get_repeat_state(store, tenant, found.group(1)) if method == "GET" else problem(405, "method_not_allowed", "Use GET or POST.")
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
    found = TRACES_SUMMARY.fullmatch(path)
    if found:
        return trace_summary(tenant, found.group(1), event, store, trace_store, now) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = TRACE_ONE.fullmatch(path)
    if found:
        return get_trace(tenant, found.group(1), found.group(2), store, trace_store) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = TRACES.fullmatch(path)
    if found:
        return list_traces(tenant, found.group(1), event, store, trace_store) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = ROUTING_TESTS.fullmatch(path)
    if found:
        if method == "GET":
            return get_routing_tests(store, tenant, found.group(1))
        return put_routing_tests(store, tenant, found.group(1), parse_body(event), now) if method == "PUT" else problem(405, "method_not_allowed", "Use GET or PUT.")
    found = ROUTING_RUN.fullmatch(path)
    if found:
        return run_routing(store, tenant, found.group(1), parse_body(event), invoke) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = CONSISTENCY.fullmatch(path)
    if found:
        return check_consistency(store, tenant, found.group(1), parse_body(event), invoke) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = SUITE_SUGGEST.fullmatch(path)
    if found:
        return suggest_tests(store, tenant, found.group(1), parse_body(event), invoke) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    found = SUITE_RUNS.fullmatch(path)
    if found:
        return start_suite_run(store, tenant, found.group(1), parse_body(event), now, invoke) if method == "POST" else problem(405, "method_not_allowed", "Use POST.")
    if CENTRAL.fullmatch(path):
        return get_central_suite(store) if method == "GET" else problem(405, "method_not_allowed", "Use GET: only the teacher changes the central suite.")
    found = SUITE_STATE.fullmatch(path)
    if found:
        which = "central" if (event.get("queryStringParameters") or {}).get("which") == "central" else None
        return get_suite_state(store, tenant, found.group(1), which) if method == "GET" else problem(405, "method_not_allowed", "Use GET.")
    found = SUITE.fullmatch(path)
    if found:
        if method == "GET":
            return get_suite(store, tenant, found.group(1))
        return put_suite(store, tenant, found.group(1), parse_body(event), now) if method == "PUT" else problem(405, "method_not_allowed", "Use GET or PUT.")
    found = MEDIA_UPLOAD.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return media_upload(store, tenant, found.group(1), parse_body(event), files or media.S3Files(), now)
    found = TEST.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return run_test(store, tenant, found.group(1), parse_body(event), invoke)
    return problem(404, "not_found", "No such route.")


def handler(event, context=None, now=None, store=None, invoke=None, files=None, trace_store=None):
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
        status, payload = route(store, tenant, event["requestContext"]["http"]["method"], (event.get("rawPath") or "").rstrip("/"), event, now, invoke, files, trace_store if trace_store is not None else chatwoot_trace.default_store())
    except reg.Invalid as error:
        return reply(400, {"error": "bad_request", "message": str(error)})
    except OverflowError:
        return reply(413, {"error": "too_large", "message": "The request is too large."})
    except Exception as error:
        log("failed", tenant=tenant["tenant_id"], error=type(error).__name__)
        return reply(500, {"error": "try_again", "message": "Try again in a moment."})
    return reply(status, payload)
