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

Every error is JSON {"error": "<code>", "message": "..."}. An agent of another tenant is "not_found" on every route. A wrong, unknown, rotated or switched-off key is
the same 401 for all of them. Only the registry items (tenant#, agent#) are touched; nothing here reads a secret, calls Chatwoot or starts another function. The
teacher creates tenants and their keys with tools/chatwoot_admin.py (never over the web)."""
import base64
import json
import os
import re
import time

import chatwoot_core as core
import chatwoot_registry as reg
import platform_settings
from chatwoot_store import DynamoStore

MAX_BODY_BYTES = platform_settings.kb("chatwoot.maxVersionKB") * 2  # a version plus the room a request needs around it
_NAME = core.AGENT_ID.pattern
LIST = re.compile(r"/admin/agents")
AGENT = re.compile(rf"/admin/agents/({_NAME})")
VERSION = re.compile(rf"/admin/agents/({_NAME})/versions/([0-9]{{1,6}})")
LIVE = re.compile(rf"/admin/agents/({_NAME})/live")


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


def put_version(store, tenant, name, number, body, now):
    agent = owned(store, tenant, name)
    if agent is None:
        return problem(404, "not_found", "No such agent.")
    config = reg.clean_config(body)
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


def route(store, tenant, method, path, event, now):
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
            return put_version(store, tenant, name, number, parse_body(event), now)
        return problem(405 if number >= 1 else 404, "method_not_allowed" if number >= 1 else "not_found", "Use GET or PUT, with a version number of 1 or more.")
    found = LIVE.fullmatch(path)
    if found:
        if method != "POST":
            return problem(405, "method_not_allowed", "Use POST.")
        return set_live(store, tenant, found.group(1), parse_body(event), now)
    return problem(404, "not_found", "No such route.")


def handler(event, context=None, now=None, store=None):
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
        status, payload = route(store, tenant, event["requestContext"]["http"]["method"], (event.get("rawPath") or "").rstrip("/"), event, now)
    except reg.Invalid as error:
        return reply(400, {"error": "bad_request", "message": str(error)})
    except OverflowError:
        return reply(413, {"error": "too_large", "message": "The request is too large."})
    except Exception as error:
        log("failed", tenant=tenant["tenant_id"], error=type(error).__name__)
        return reply(500, {"error": "try_again", "message": "Try again in a moment."})
    return reply(status, payload)
