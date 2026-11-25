"""Archiving an agent (plan: docs/notes/KNOWLEDGE-TAB-PLAN.md follow-up, decided 2026-11-26): the owner archives an agent instead of deleting it. An archived agent is switched off, leaves the
list of agents (Studio shows it under "Archived") and keeps everything for `chatwoot.archiveDays` days, during which the owner can restore it. After that the purge function (once a day) removes it
for good: the agent, its versions, its tests and their runs, its routing table, its repeatability results and its teach sessions. What is NOT removed here: the bot's secret
(`chatwoot/<name>`: the teacher's), the books (they belong to the business), and messages, memory and traces (they expire by themselves). Media files that no version lists any more are
removed by the media janitor on its own schedule.

Pure rules and the purge; the admin function archives and restores (chatwoot_admin.set_archived), the purge function (ChatwootPurgeFunction, `handler` below) purges.
"""
import json
import time

import chatwoot_registry as reg
import platform_settings

ARCHIVE_DAYS = platform_settings.value("chatwoot.archiveDays")


def log(what, **details):
    print(json.dumps({"chatwoot_archive": what, **details}))


def due(agent, now, days=None):
    """True when an archived agent has waited long enough to be removed for good."""
    days = ARCHIVE_DAYS if days is None else days
    at = agent.get("archived_at") if isinstance(agent, dict) else None
    return isinstance(at, int) and not isinstance(at, bool) and now - at >= days * 86400


def used_by(store, tenant, name):
    """The agents of the business whose LIVE version lists this agent as a department (an agent in use is not archived or removed)."""
    users = []
    for other in tenant.get("agents") or []:
        agent = store.get(reg.agent_key(other)) or {}
        live = agent.get("live_version") or 0
        config = (store.get(reg.version_key(other, live)) or {}).get("config") if live else None
        if isinstance(config, dict) and any(isinstance(d, dict) and d.get("agent") == name for d in config.get("departments") or []):
            users.append(other)
    return users


def keys_of(store, name, versions):
    """Every item that belongs to this agent: exact keys and the delimited families (suite runs, teach sessions, repeat results). Never a prefix that could match another agent's name."""
    keys = [reg.agent_key(name), reg.suite_key(name), reg.routing_tests_key(name), reg.suite_state_key(name), reg.suite_state_key(name, "central"), reg.teach_index_key(name), reg.repeat_state_key(name)]
    keys += [reg.version_key(name, n) for n in range(1, int(versions or 0) + 1)]
    for prefix in (f"suiterun#{name}#", f"teach#{name}#", f"repeat#{name}#"):
        keys += [item["pk"] for item in store.list_prefix(prefix) if isinstance(item.get("pk"), str) and item["pk"].startswith(prefix)]
    return sorted(set(keys))


def remove_from_tenant(store, tenant_id, name, now):
    for _ in range(4):
        tenant = store.get(reg.tenant_key(tenant_id))
        if not tenant or name not in (tenant.get("agents") or []):
            return True
        shrunk = {**tenant, "agents": [a for a in tenant["agents"] if a != name], "rev": tenant["rev"] + 1, "updated_at": now}
        if store.replace_if(reg.tenant_key(tenant_id), shrunk, "rev", tenant["rev"]):
            return True
    return False


def purge(store, now, days=None):
    """Remove every archived agent that has waited long enough. Returns {"purged": [names], "skipped": n}. An agent whose business, list entry or use cannot be settled is skipped and tried
    again tomorrow: nothing is removed on a guess. The agent's own item goes last, so a half-removed agent is found again."""
    purged, skipped = [], 0
    for item in store.list_prefix("agent#"):
        key = item.get("pk")
        if not isinstance(key, str) or key.count("#") != 1 or "config" in item or not due(item, now, days):
            continue  # not an agent item (a version item has a second # and a config), or not due
        name, tenant_id = key[len("agent#"):], item.get("tenant_id")
        try:
            tenant = store.get(reg.tenant_key(tenant_id)) if isinstance(tenant_id, str) else None
            if not isinstance(tenant, dict) or tenant.get("tenant_id") != tenant_id or item.get("agent_id") != name:
                raise ValueError("not settled")
            if used_by(store, tenant, name):
                raise ValueError("in use")
            if not remove_from_tenant(store, tenant_id, name, now):
                raise ValueError("list")
            for other in keys_of(store, name, item.get("versions")):
                if other != reg.agent_key(name):
                    store.forget(other)
            store.forget(reg.agent_key(name))
            purged.append(name)
            log("purged", tenant=tenant_id, agent=name)
        except Exception as error:
            skipped += 1
            log("purge_skipped", agent=name, why=str(error)[:40] if isinstance(error, ValueError) else type(error).__name__)
    return {"purged": purged, "skipped": skipped}


def default_store():
    import os
    import boto3
    from chatwoot_store import DynamoStore
    return DynamoStore(boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"]))


def handler(event=None, context=None, now=None, store=None):
    """Once a day: remove the archived agents that waited their days. Returns the names removed (never anything else about them)."""
    now = int(now if now is not None else time.time())
    done = purge(store or default_store(), now)
    log("swept", purged=len(done["purged"]), skipped=done["skipped"])
    return done
