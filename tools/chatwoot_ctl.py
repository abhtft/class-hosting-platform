#!/usr/bin/env python3
"""The teacher's local command for the Chatwoot adapter. It runs on YOUR computer with YOUR AWS credentials (the web API can never create a tenant, a service key
or a book), writes the adapter's table and a business's book shelves directly, and reuses the same validation as the admin API.

  python tools/chatwoot_ctl.py --table <ChatwootTableName> create-tenant acme --name "Acme Shop"     prints the tenant's service key ONCE
  python tools/chatwoot_ctl.py --table <...> rotate-key acme                                          a new key; the old one stops working at once
  python tools/chatwoot_ctl.py --table <...> disable-tenant acme    |  enable-tenant acme
  python tools/chatwoot_ctl.py --table <...> set-cap acme 25                                          this business may spend 25 US dollars a month on answers
  python tools/chatwoot_ctl.py --table <...> list                                                     tenants and their agents (never a key or a hash)
  python tools/chatwoot_ctl.py --table <...> agent-set shop --tenant acme --account 1 --inboxes 3,4 [--enabled true] [--type chatbot] [--purpose sales]
  python tools/chatwoot_ctl.py --table <...> show-agent shop
  python tools/chatwoot_ctl.py --table <...> publish shop --tenant acme --config shop.json [--live]      publish the next version (checked as the worker will use it)
  python tools/chatwoot_ctl.py --table <...> set-live shop --tenant acme --version 2                     choose the live version (0 = none)

  python tools/chatwoot_ctl.py --table <...> --bucket <sites bucket> knowledge-sync --tenant acme [--folder knowledge/acme] [--prune] [--dry-run]
        a folder of books -> the business's shelves. The folder holds one sub-folder per shelf: knowledge/acme/faq/returns.pdf. New and changed files (PDF, text,
        Markdown, HTML) are read and added, unchanged ones are skipped (by content), nothing is deleted unless --prune. Needs the AWS secret that holds the Google
        key (--secret-name, the stack's AiSecretName).
  python tools/chatwoot_ctl.py --bucket <...> eval --tenant acme --collections faq,prices --cases cases.json
        real searches on the shelves: for each case the best match and whether the books were sure; cases that must be refused are checked too.

The table name is the stack output ChatwootTableName (or CHATWOOT_TABLE); the bucket is the sites bucket (or BUCKET). The region and credentials are the ones boto3
finds (your AWS profile). A service key is shown only when it is made; only its SHA-256 is stored, so a lost key is replaced with rotate-key, never recovered."""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_registry as reg  # noqa: E402

SUPPORTED = (".pdf", ".txt", ".text", ".md", ".markdown", ".html", ".htm")


def real_store(table):
    import boto3
    from chatwoot_store import DynamoStore
    return DynamoStore(boto3.resource("dynamodb").Table(table))


def real_shelves(args):
    """The shelf store on the real bucket. The AI secret's name is what the platform's embedding code reads the Google key with."""
    if args.secret_name:
        os.environ["AI_SECRET_NAME"] = args.secret_name
    bucket = args.bucket or os.environ.get("BUCKET")
    if not bucket:
        return None
    import knowledge_store as ks
    return ks.Store(ks.S3Bucket(bucket))


def tenant_or_none(store, tenant_id):
    tenant = store.get(reg.tenant_key(tenant_id)) if reg.TENANT_ID.fullmatch(tenant_id or "") else None
    return tenant if isinstance(tenant, dict) else None


def show_key(out, tenant_id, key):
    out(f"Service key of {tenant_id} (shown only now, keep it safe; only its hash is stored):")
    out(f"  {key}")


def create_tenant(store, args, out, now, shelves=None):
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


def rotate_key(store, args, out, now, shelves=None):
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


def set_cap(store, args, out, now, shelves=None):
    tenant = tenant_or_none(store, args.id)
    if not tenant:
        out(f"No tenant {args.id}.")
        return 1
    try:
        dollars = float(args.usd)
    except ValueError:
        dollars = -1.0
    if not 0 <= dollars <= 100000:
        out("The cap is a number of US dollars a month, from 0 to 100000.")
        return 2
    micro = int(round(dollars * 1000000))
    if not store.replace_if(reg.tenant_key(args.id), {**tenant, "monthly_cap_micro": micro, "rev": tenant["rev"] + 1, "updated_at": now}, "rev", tenant["rev"]):
        out("The tenant was changed at the same moment. Run the command again.")
        return 1
    out(f"Tenant {args.id} may now spend {dollars:g} US dollars a month on answers (0 stops all answers).")
    return 0


def list_all(store, args, out, now, shelves=None):
    tenants = store.list_prefix("tenant#")
    if not tenants:
        out("No tenants yet.")
    for tenant in tenants:
        cap = tenant.get("monthly_cap_micro")
        out(f"{tenant['tenant_id']}  {'enabled' if tenant.get('enabled') else 'DISABLED'}  agents: {', '.join(tenant.get('agents') or []) or '-'}"
            + (f"  cap: {cap / 1000000:g} USD/month" if isinstance(cap, int) else ""))
    return 0


def agent_set(store, args, out, now, shelves=None):
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


def publish(store, args, out, now, shelves=None):
    """Publish the next version of an agent from a JSON file of its configuration (the same checks as the admin API, plus the runtime's own check so a
    configuration the worker could not use is refused now). With --live the new version also becomes the live one."""
    tenant = tenant_or_none(store, args.tenant)
    agent = admin.owned(store, tenant, args.name) if tenant else None
    if not agent:
        out(f"No agent {args.name} for tenant {args.tenant}. Create the agent first with agent-set.")
        return 1
    try:
        with open(args.config, encoding="utf8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as error:
        out(f"Cannot read {args.config} as JSON ({type(error).__name__}).")
        return 2
    import chatwoot_runtime as runtime  # imported here: it loads the search code, which the other commands do not need
    try:
        runtime.clean_config(config)
    except runtime.ConfigError as error:
        out(f"The runtime could not use this configuration: {error}")
        return 2
    number = (agent.get("versions") or 0) + 1
    try:
        status, payload = admin.put_version(store, tenant, args.name, number, {"config": config}, now)
    except reg.Invalid as error:
        out(str(error))
        return 2
    out(json.dumps(payload, indent=2))
    if status not in (200, 201):
        return 1
    if args.live:
        return set_live_version(store, tenant, args.name, number, out, now)
    out(f"Version {number} is published and NOT live. Make it live with: set-live {args.name} --tenant {args.tenant} --version {number}")
    return 0


def set_live_version(store, tenant, name, number, out, now):
    try:
        status, payload = admin.set_live(store, tenant, name, {"version": number}, now)
    except reg.Invalid as error:
        out(str(error))
        return 2
    out(json.dumps(payload, indent=2))
    return 0 if status == 200 else 1


def set_live(store, args, out, now, shelves=None):
    tenant = tenant_or_none(store, args.tenant)
    if not tenant:
        out(f"No tenant {args.tenant}.")
        return 1
    return set_live_version(store, tenant, args.name, args.version, out, now)


def show_agent(store, args, out, now, shelves=None):
    agent = store.get(reg.agent_key(args.name))
    if not agent:
        out(f"No agent {args.name}.")
        return 1
    out(json.dumps({**admin.view(agent), "tenant": agent.get("tenant_id")}, indent=2))
    return 0


# ---------- the books of a business ----------

def embedding_choice():
    """The embedding model and vector size new shelves are created with (the platform's own settings)."""
    import platform_settings
    return platform_settings.value("knowledge.embeddingModel"), platform_settings.value("knowledge.embeddingDims")


def folder_files(folder, out):
    """{shelf name: [(file name, bytes)]} from <folder>/<shelf>/<file>. Files of a kind the platform cannot read, and odd folder names, are named and skipped."""
    import knowledge_store as ks
    shelves = {}
    for shelf in sorted(os.listdir(folder)):
        path = os.path.join(folder, shelf)
        if not os.path.isdir(path):
            continue
        if not ks.NAME_RE.match(shelf):
            out(f"Skipped the folder {shelf}: a shelf name is lowercase letters, digits, - and _ (up to 40 characters).")
            continue
        files = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(name)[1].lower() not in SUPPORTED:
                out(f"Skipped {shelf}/{name}: only PDF, text, Markdown and HTML files can be read.")
                continue
            with open(full, "rb") as handle:
                files.append((name, handle.read()))
        shelves[shelf] = files
    return shelves


def knowledge_sync(store, args, out, now, shelves=None, cache=None):
    """Make a business's shelves match a folder. Content decides: a file whose content is already on the shelf is skipped; a changed file replaces the document of
    the same file name; a file that is gone from the folder is removed only with --prune."""
    import embeddings as em
    import knowledge_ingest as ki
    import knowledge_store as ks
    if shelves is None:
        out("Give the sites bucket with --bucket (or BUCKET) and the AI secret's name with --secret-name.")
        return 2
    if not tenant_or_none(store, args.tenant):
        out(f"No tenant {args.tenant}. Create it first with create-tenant.")
        return 1
    folder = args.folder or os.path.join("knowledge", args.tenant)
    if not os.path.isdir(folder):
        out(f"No folder {folder}. Put the books in {folder}/<shelf>/<file> and run this again.")
        return 1
    cache = cache if cache is not None else em.MemoryCache()
    failures = 0
    for shelf, files in folder_files(folder, out).items():
        base = ks.base_of("tenant", args.tenant, shelf)
        meta = shelves.meta(base)
        if meta is None:
            model, dims = embedding_choice()
            out(f"{shelf}: {'would create the shelf' if args.dry_run else 'new shelf'}")
            if not args.dry_run:
                meta = shelves.create_collection(base, shelf, model=model, dims=dims)
        have = {d.get("source"): d["id"] for d in (meta or {}).get("docs", []) if d.get("source")}
        present = {d["id"] for d in (meta or {}).get("docs", [])}
        for name, data in files:
            doc_id = "d" + hashlib.sha256(data).hexdigest()[:20]
            if doc_id in present:
                out(f"{shelf}/{name}: unchanged")
                continue
            replaces = have.get(name)
            if args.dry_run:
                out(f"{shelf}/{name}: would {'replace the older version of' if replaces else 'add'} this book")
                continue
            try:
                done = ki.process(data, name, meta["embedding"], cache=cache)
                meta = shelves.put_document(base, {**done["doc"], "id": doc_id}, done["passages"], done["vectors"])
                if replaces and replaces != doc_id:
                    meta = shelves.delete_document(base, replaces)
                present.add(doc_id)
                have[name] = doc_id
                out(f"{shelf}/{name}: {'replaced' if replaces else 'added'} ({len(done['passages'])} passages)")
            except (ki.IngestError, ks.StoreError, em.EmbeddingError) as error:
                failures += 1
                out(f"{shelf}/{name}: FAILED: {error}")
        if args.prune and meta:
            names = {name for name, _ in files}
            for doc in meta.get("docs", []):
                if doc.get("source") and doc["source"] not in names:
                    out(f"{shelf}/{doc['source']}: {'would remove' if args.dry_run else 'removed'} (no longer in the folder)")
                    if not args.dry_run:
                        meta = shelves.delete_document(base, doc["id"])
    return 1 if failures else 0


def eval_cases(store, args, out, now, shelves=None, cache=None):
    """Real searches on a business's shelves, to calibrate the books and the confidence gate: each case is {"question", "expect"?, "refuse"?}."""
    import chatwoot_runtime as runtime
    import embeddings as em
    if shelves is None:
        out("Give the sites bucket with --bucket (or BUCKET) and the AI secret's name with --secret-name.")
        return 2
    try:
        with open(args.cases, encoding="utf8") as handle:
            cases = json.load(handle)
    except (OSError, ValueError) as error:
        out(f"The cases file cannot be read: {error}")
        return 2
    if not isinstance(cases, list) or not all(isinstance(c, dict) and isinstance(c.get("question"), str) for c in cases):
        out('The cases file is a JSON list of {"question": "...", "expect": "...", "refuse": true} objects.')
        return 2
    collections = [part.strip() for part in args.collections.split(",") if part.strip()]
    cache = cache if cache is not None else em.MemoryCache()
    wrong = 0
    for case in cases:
        found = runtime.search_shelves(shelves, args.tenant, collections, case["question"], k=args.k, cache=cache)
        best = found["hits"][0] if found["hits"] else None
        if case.get("refuse"):
            ok = not found["confident"]
        else:
            expect = str(case.get("expect") or "").lower()
            ok = found["confident"] and (not expect or any(expect in (h["text"] + " " + h["title"]).lower() for h in found["hits"]))
        wrong += 0 if ok else 1
        where = f"{best['collection']}/{best['title']} p.{best['page']}" if best else "-"
        out(f"{'PASS' if ok else 'FAIL'}  {'refuse' if case.get('refuse') else 'answer'}  sure={'yes' if found['confident'] else 'no'}  best={best['vector']:.2f}  top={where}  | {case['question'][:70]}" if best
            else f"{'PASS' if ok else 'FAIL'}  {'refuse' if case.get('refuse') else 'answer'}  sure=no  best=-     top=-  | {case['question'][:70]}")
    out(f"{len(cases) - wrong} of {len(cases)} cases as expected.")
    return 1 if wrong else 0


def parser():
    p = argparse.ArgumentParser(prog="chatwoot_ctl.py", description="The teacher's command for the Chatwoot adapter's tenants, service keys, agents and books.")
    p.add_argument("--table", default=os.environ.get("CHATWOOT_TABLE"), help="the stack output ChatwootTableName (or the environment variable CHATWOOT_TABLE)")
    p.add_argument("--bucket", default=None, help="the sites bucket, for the book commands (or the environment variable BUCKET)")
    p.add_argument("--secret-name", default=None, help="the AI secret's name (the stack's AiSecretName), for the book commands")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("create-tenant", help="create a business and print its service key once")
    s.add_argument("id")
    s.add_argument("--name")
    for name in ("rotate-key", "disable-tenant", "enable-tenant"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("set-cap", help="the most a business may spend a month, in US dollars")
    s.add_argument("id")
    s.add_argument("usd")
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
    s = sub.add_parser("publish", help="publish the next version of an agent from a JSON configuration file")
    s.add_argument("name")
    s.add_argument("--tenant", required=True)
    s.add_argument("--config", required=True, help="a JSON file: instructions, language, collections, fallback_text, max_answer_chars, mode")
    s.add_argument("--live", action="store_true", help="also make the new version the live one")
    s = sub.add_parser("set-live", help="choose which published version is live (0 = none, the agent stays silent)")
    s.add_argument("name")
    s.add_argument("--tenant", required=True)
    s.add_argument("--version", type=int, required=True)
    s = sub.add_parser("knowledge-sync", help="a folder of books to a business's shelves")
    s.add_argument("--tenant", required=True)
    s.add_argument("--folder")
    s.add_argument("--prune", action="store_true", help="also remove books that are no longer in the folder")
    s.add_argument("--dry-run", action="store_true", help="show what would happen and change nothing")
    s = sub.add_parser("eval", help="real searches on a business's shelves")
    s.add_argument("--tenant", required=True)
    s.add_argument("--collections", required=True, help="comma-separated shelf names")
    s.add_argument("--cases", required=True, help="a JSON file of questions")
    s.add_argument("--k", type=int, default=4)
    return p


NEEDS_TABLE = {"create-tenant", "rotate-key", "disable-tenant", "enable-tenant", "set-cap", "list", "agent-set", "show-agent", "publish", "set-live", "knowledge-sync"}


def main(argv=None, store=None, out=print, now=None, shelves=None):
    args = parser().parse_args(argv)
    now = int(now if now is not None else time.time())
    if store is None and args.command in NEEDS_TABLE:
        if not args.table:
            out("Give the table with --table (the stack output ChatwootTableName) or the environment variable CHATWOOT_TABLE.")
            return 2
        store = real_store(args.table)
    if shelves is None and args.command in ("knowledge-sync", "eval"):
        shelves = real_shelves(args)
    commands = {"create-tenant": create_tenant, "rotate-key": rotate_key, "disable-tenant": lambda *a: set_enabled(*a[:4], False), "enable-tenant": lambda *a: set_enabled(*a[:4], True),
                "set-cap": set_cap, "list": list_all, "agent-set": agent_set, "show-agent": show_agent, "publish": publish, "set-live": set_live, "knowledge-sync": knowledge_sync, "eval": eval_cases}
    return commands[args.command](store, args, out, now, shelves)


if __name__ == "__main__":
    sys.exit(main())
