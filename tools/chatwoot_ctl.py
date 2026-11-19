#!/usr/bin/env python3
"""The teacher's local command for the Chatwoot adapter's registry. It runs on YOUR computer with YOUR AWS credentials (the web API can never create a tenant or
a service key), writes the adapter's table directly, and reuses the same validation as the admin API.

  python tools/chatwoot_ctl.py --table <ChatwootTableName> create-tenant acme --name "Acme Shop"     prints the tenant's service key ONCE
  python tools/chatwoot_ctl.py --table <...> rotate-key acme                                          a new key; the old one stops working at once
  python tools/chatwoot_ctl.py --table <...> disable-tenant acme    |  enable-tenant acme
  python tools/chatwoot_ctl.py --table <...> list                                                     tenants and their agents (never a key or a hash)
  python tools/chatwoot_ctl.py --table <...> agent-set shop --tenant acme --account 1 --inboxes 3,4 [--enabled true] [--type chatbot] [--purpose sales]
  python tools/chatwoot_ctl.py --table <...> show-agent shop

The table name is the stack output ChatwootTableName (or the environment variable CHATWOOT_TABLE). The region and credentials are the ones boto3 finds (your AWS
profile). A service key is shown only when it is made; only its SHA-256 is stored, so a lost key is replaced with rotate-key, never recovered."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_registry as reg  # noqa: E402


def real_store(table):
    import boto3
    from chatwoot_store import DynamoStore
    return DynamoStore(boto3.resource("dynamodb").Table(table))


def tenant_or_none(store, tenant_id):
    tenant = store.get(reg.tenant_key(tenant_id)) if reg.TENANT_ID.fullmatch(tenant_id or "") else None
    return tenant if isinstance(tenant, dict) else None


def show_key(out, tenant_id, key):
    out(f"Service key of {tenant_id} (shown only now, keep it safe; only its hash is stored):")
    out(f"  {key}")


def create_tenant(store, args, out, now):
    if not reg.TENANT_ID.fullmatch(args.id):
        out("A tenant id is 1 to 30 lowercase letters, digits or '-', starting with a letter or a digit.")
        return 2
    key, key_hash = reg.new_service_key(args.id)
    item = {"tenant_id": args.id, "name": (args.name or args.id)[:80], "key_hash": key_hash, "enabled": True, "agents": [], "rev": 1, "created_at": now}
    if not store.claim(reg.tenant_key(args.id), item):
        out(f"The tenant {args.id} already exists. Use rotate-key for a new key.")
        return 1
    show_key(out, args.id, key)
    return 0


def rotate_key(store, args, out, now):
    tenant = tenant_or_none(store, args.id)
    if not tenant:
        out(f"No tenant {args.id}.")
        return 1
    key, key_hash = reg.new_service_key(args.id)
    if not store.replace_if(reg.tenant_key(args.id), {**tenant, "key_hash": key_hash, "rev": tenant["rev"] + 1, "updated_at": now}, "rev", tenant["rev"]):
        out("The tenant was changed at the same moment. Run the command again.")
        return 1
    show_key(out, args.id, key)
    out("The old key no longer works.")
    return 0


def set_enabled(store, args, out, now, enabled):
    tenant = tenant_or_none(store, args.id)
    if not tenant:
        out(f"No tenant {args.id}.")
        return 1
    if not store.replace_if(reg.tenant_key(args.id), {**tenant, "enabled": enabled, "rev": tenant["rev"] + 1, "updated_at": now}, "rev", tenant["rev"]):
        out("The tenant was changed at the same moment. Run the command again.")
        return 1
    out(f"Tenant {args.id} is now {'enabled' if enabled else 'disabled (its key and its agents stop working at once, within the cache time)'}.")
    return 0


def list_all(store, args, out, now):
    tenants = store.list_prefix("tenant#")
    if not tenants:
        out("No tenants yet.")
    for tenant in tenants:
        out(f"{tenant['tenant_id']}  {'enabled' if tenant.get('enabled') else 'DISABLED'}  agents: {', '.join(tenant.get('agents') or []) or '-'}")
    return 0


def agent_set(store, args, out, now):
    tenant = tenant_or_none(store, args.tenant)
    if not tenant:
        out(f"No tenant {args.tenant}. Create it first with create-tenant.")
        return 1
    body = {}
    if args.account is not None:
        body["account_id"] = args.account
    if args.inboxes is not None:
        try:
            body["inbox_ids"] = [int(part) for part in args.inboxes.split(",") if part.strip()]
        except ValueError:
            out("--inboxes is a comma-separated list of whole numbers, for example 3,4.")
            return 2
    if args.enabled is not None:
        if args.enabled not in ("true", "false"):
            out("--enabled is true or false.")
            return 2
        body["enabled"] = args.enabled == "true"
    for field in ("type", "purpose"):
        if getattr(args, field) is not None:
            body[field] = getattr(args, field)
    try:
        status, payload = admin.put_agent(store, tenant, args.name, body, now)
    except reg.Invalid as error:
        out(str(error))
        return 2
    out(json.dumps(payload, indent=2))
    return 0 if status in (200, 201) else 1


def show_agent(store, args, out, now):
    agent = store.get(reg.agent_key(args.name))
    if not agent:
        out(f"No agent {args.name}.")
        return 1
    out(json.dumps({**admin.view(agent), "tenant": agent.get("tenant_id")}, indent=2))
    return 0


def parser():
    p = argparse.ArgumentParser(prog="chatwoot_ctl.py", description="The teacher's command for the Chatwoot adapter's tenants, service keys and agents.")
    p.add_argument("--table", default=os.environ.get("CHATWOOT_TABLE"), help="the stack output ChatwootTableName (or the environment variable CHATWOOT_TABLE)")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("create-tenant", help="create a business and print its service key once")
    s.add_argument("id")
    s.add_argument("--name")
    for name in ("rotate-key", "disable-tenant", "enable-tenant"):
        sub.add_parser(name).add_argument("id")
    sub.add_parser("list", help="tenants and their agents")
    s = sub.add_parser("agent-set", help="create or change an agent of a tenant")
    s.add_argument("name")
    s.add_argument("--tenant", required=True)
    s.add_argument("--account", type=int)
    s.add_argument("--inboxes")
    s.add_argument("--enabled")
    s.add_argument("--type")
    s.add_argument("--purpose")
    sub.add_parser("show-agent").add_argument("name")
    return p


def main(argv=None, store=None, out=print, now=None):
    args = parser().parse_args(argv)
    now = int(now if now is not None else time.time())
    if store is None:
        if not args.table:
            out("Give the table with --table (the stack output ChatwootTableName) or the environment variable CHATWOOT_TABLE.")
            return 2
        store = real_store(args.table)
    commands = {"create-tenant": create_tenant, "rotate-key": rotate_key, "disable-tenant": lambda *a: set_enabled(*a, False), "enable-tenant": lambda *a: set_enabled(*a, True),
                "list": list_all, "agent-set": agent_set, "show-agent": show_agent}
    return commands[args.command](store, args, out, now)


if __name__ == "__main__":
    sys.exit(main())
