#!/usr/bin/env python3
"""The teacher's local command for the Chatwoot adapter. It runs on YOUR computer with YOUR AWS credentials (the web API can never create a tenant, a service key
or a book), writes the adapter's table and a business's book shelves directly, and reuses the same validation as the admin API.

  python tools/chatwoot_ctl.py --table <ChatwootTableName> create-tenant acme --name "Acme Shop"     prints the tenant's service key ONCE
  python tools/chatwoot_ctl.py --table <...> rotate-key acme                                          a new key; the old one stops working at once
  python tools/chatwoot_ctl.py --table <...> disable-tenant acme    |  enable-tenant acme
  python tools/chatwoot_ctl.py --table <...> set-cap acme 25                                          this business may spend 25 US dollars a month on answers
  python tools/chatwoot_ctl.py --table <...> set-limits acme media_items=20 total_mb=500   this business's media limits (a name alone shows nothing new; --clear removes its own limits)
  python tools/chatwoot_ctl.py --table <...> list                                                     tenants and their agents (never a key or a hash)
  python tools/chatwoot_ctl.py --table <...> agent-set shop --tenant acme --account 1 --inboxes 3,4 [--enabled true] [--type chatbot] [--purpose sales]
  python tools/chatwoot_ctl.py --table <...> show-agent shop
  python tools/chatwoot_ctl.py --table <...> publish shop --tenant acme --config shop.json [--live]      publish the next version (checked as the worker will use it)
  python tools/chatwoot_ctl.py --table <...> set-live shop --tenant acme --version 2                     choose the live version (0 = none)
  python tools/chatwoot_ctl.py --table <...> suite-load shop --tenant acme --file config/suites/margdarshak-digital.json     save an agent's test suite (a file with "cases")
  python tools/chatwoot_ctl.py --table <...> central-load --file config/suites/standard-safety.json     load the platform's central safety suite (every business may read it, none may change it)
  python tools/chatwoot_ctl.py --table <...> central-show                                              its version and its cases by risk group
  python tools/chatwoot_ctl.py --table <...> --worker <...> suite-run shop --tenant acme --central [--version N | --config file.json] [--wait]     run the central suite on an agent
  python tools/chatwoot_ctl.py --table <...> suite-show shop --tenant acme                              the suite and what its latest runs found, per published version
  python tools/chatwoot_ctl.py --table <...> --worker <ChatwootWorkerFunction name> suite-run shop --tenant acme [--version N | --config file.json] [--wait]
  python tools/chatwoot_ctl.py --table <...> --worker <...> suite-run --all-live [--tenant acme]       run the suite of every agent that has a live version and a suite, wait, exit 1 on a failure
        (a run asks the real model about 30 questions: about 2 cents; run it after every deployment and every new book: `knowledge-sync ... --then-suite` does it for you)

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
import chatwoot_media as media  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_runtime as engine  # noqa: E402
import chatwoot_suite as suite  # noqa: E402
import chatwoot_repeat as repeat  # noqa: E402

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


def set_limits(store, args, out, now, shelves=None):
    tenant = tenant_or_none(store, args.id)
    if not tenant:
        out(f"No tenant {args.id}.")
        return 1
    own = {} if args.clear else dict(tenant.get("limits") or {})
    try:
        for pair in args.pairs:
            name, _, text = pair.partition("=")
            own[name] = int(text) if text.lstrip("-").isdigit() else text
        own = media.clean_limits(own)
    except media.Invalid as error:
        out(str(error))
        return 2
    changed = {**tenant, "rev": tenant["rev"] + 1, "updated_at": now}
    changed.pop("limits", None)
    if own:
        changed["limits"] = own
    if (args.pairs or args.clear) and not store.replace_if(reg.tenant_key(args.id), changed, "rev", tenant["rev"]):
        out("The tenant was changed at the same moment. Run the command again.")
        return 1
    effective = media.limits_of(changed)
    files, used = media.usage(store, args.id)
    out(f"Media limits of {args.id} (its own where set, else the platform's):")
    for name in media.LIMIT_SETTINGS:
        out(f"  {name} = {effective[name]}{'  (own)' if name in own else ''}")
    out(f"  in use: {files} files, {used // 1024 // 1024} MB")
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


def set_live_version(store, tenant, name, number, out, now, reason=None):
    try:
        status, payload = admin.set_live(store, tenant, name, {"version": number, **({"override_reason": reason} if reason else {})}, now)
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
    return set_live_version(store, tenant, args.name, args.version, out, now, getattr(args, "override_reason", None))


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


# ---------- the test suite of an agent ----------

def real_invoke(args):
    """The function that starts the worker on a run (asynchronously), or None when no worker is named."""
    name = args.worker or os.environ.get("CHATWOOT_WORKER_FUNCTION")
    if not name:
        return None

    def invoke(payload):
        import boto3
        response = boto3.client("lambda").invoke(FunctionName=name, InvocationType="Event", Payload=json.dumps(payload).encode("utf8"))
        if response.get("StatusCode") != 202:
            raise RuntimeError("the worker was not started")
    return invoke


def suite_load(store, args, out, now, shelves=None):
    tenant = tenant_or_none(store, args.tenant)
    if not tenant:
        out(f"No tenant {args.tenant}.")
        return 1
    try:
        with open(args.file, encoding="utf8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        out(f"Cannot read {args.file} as JSON ({type(error).__name__}).")
        return 2
    try:
        status, payload = admin.put_suite(store, tenant, args.name, {"cases": data.get("cases") if isinstance(data, dict) else data}, now)
    except reg.Invalid as error:
        out(str(error))
        return 2
    if status in (200, 201):
        out(f"Saved the suite of {args.name}: {len(payload['cases'])} cases, revision {payload['rev']}.")
        return 0
    out(payload.get("message", "The suite was not saved."))
    return 1


def central_load(store, args, out, now, shelves=None):
    """Load the central safety suite from a file {"version", "title", "cases"}. A new load needs a higher version number, or the same one with the same cases (nothing changes)."""
    try:
        with open(args.file, encoding="utf8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        out(f"Cannot read {args.file} as JSON ({type(error).__name__}).")
        return 2
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        out('The file is {"version": a whole number from 1, "title": "...", "cases": [...]}.')
        return 2
    try:
        cases = suite.clean_suite(data.get("cases"))
    except suite.Invalid as error:
        out(str(error))
        return 2
    current = store.get(reg.central_suite_key()) or {}
    if version < current.get("version", 0) or (version == current.get("version", 0) and cases != current.get("cases")):
        out(f"The loaded central suite is version {current.get('version')}: raise the version number in the file to load changed cases.")
        return 1
    if version == current.get("version", 0):
        out(f"Central suite version {version} is already loaded with these {len(cases)} cases.")
        return 0
    store.put(reg.central_suite_key(), {"version": version, "title": str(data.get("title") or "")[:200], "cases": cases, "updated_at": now})
    out(f"Loaded the central safety suite: version {version}, {len(cases)} cases.")
    return 0


def central_show(store, args, out, now, shelves=None):
    item = store.get(reg.central_suite_key())
    if not item:
        out("No central safety suite is loaded.")
        return 1
    groups = {}
    for case in item["cases"]:
        groups.setdefault(case.get("group", "other"), []).append(case)
    out(f"Central safety suite version {item['version']}: {len(item['cases'])} cases. {item.get('title') or ''}".rstrip())
    for group, cases in groups.items():
        out(f"  {group:18} {len(cases):2} cases ({sum(1 for c in cases if c['severity'] == 'must')} must)  {', '.join(sorted({c['ref'] for c in cases if c.get('ref')}))}")
    return 0


def suite_show(store, args, out, now, shelves=None):
    tenant = tenant_or_none(store, args.tenant)
    agent = admin.owned(store, tenant, args.name) if tenant else None
    if not agent:
        out(f"No agent {args.name} for tenant {args.tenant}.")
        return 1
    _, state = admin.get_suite_state(store, tenant, args.name)
    out(f"{args.name}: {state['cases']} cases (revision {state['suite_rev']}); live version {agent.get('live_version') or 'none'}.")
    for version, found in sorted(state["versions"].items(), key=lambda kv: int(kv[0])):
        out(f"  version {version}: {'passed' if found['ok'] else 'FAILED'}  {found['passed']} of {found['total']}  (must-cases failed: {found['must_failed']}, suite revision {found['suite_rev']})")
    for run in state["runs"][:5]:
        out(f"  run {run['run_id']}  {run['target']}  {run['status']}  {run.get('passed')} of {run.get('total')}  flaky {run.get('flaky')}")
    return 0


def print_run(out, run):
    for r in run.get("results", []):
        mark = "FLAKY" if r.get("flaky") else ("PASS" if r.get("ok") else ("SKIP" if r.get("ran") is False else "FAIL"))
        line = f"  {mark:5} {r['severity']:6} {r['id']}"
        if not r.get("ok"):
            line += "  <- " + "; ".join(f"{c['check']}: wanted {c['expected']}, got {c['got']}" for c in r.get("failed", []))
        out(line)
    if run.get("status") == "failed":
        out(f"The run failed: {run.get('error')} {run.get('message') or ''}".rstrip())
    else:
        out(f"{run.get('passed')} of {run.get('total')} passed; must-cases failed: {run.get('must_failed')}; should-cases failed: {run.get('should_failed')}; flaky: {run.get('flaky')}  ->  {'OK' if run.get('ok') else 'FAILED'}")


def wait_for_run(store, name, run_id, seconds, pause):
    """The run's item once it is done or failed, or the last item read when the time is up."""
    deadline = time.monotonic() + seconds
    while True:
        item = store.get(reg.suite_run_key(name, run_id)) or {}
        if item.get("status") in ("done", "failed") or time.monotonic() > deadline:
            return item
        pause()


def suite_run_one(store, tenant, name, body, args, out, now, pause):
    invoke = getattr(args, "invoke", None) or real_invoke(args)
    if invoke is None:
        out("Name the worker with --worker (the stack's ChatwootWorkerFunction) or the environment variable CHATWOOT_WORKER_FUNCTION.")
        return 2
    try:
        status, started = admin.start_suite_run(store, tenant, name, body, now, invoke)
    except reg.Invalid as error:
        out(str(error))
        return 2
    if status != 202:
        out(f"{name}: {started.get('message', 'the run did not start')}")
        return 1
    out(f"{name}: a run of the suite on {started['target']} ({started['total']} cases) was started: {started['run_id']}")
    if not args.wait:
        return 0
    run = wait_for_run(store, name, started["run_id"], args.timeout, pause)
    if run.get("status") not in ("done", "failed"):
        out("The run is not finished yet; look again with suite-show.")
        return 1
    print_run(out, run)
    return 0 if run.get("status") == "done" and run.get("ok") else 1


def suite_run(store, args, out, now, shelves=None):
    pause = getattr(args, "pause", None) or (lambda: time.sleep(3))
    if args.all_live:
        code = 0
        for item in store.list_prefix("agent#"):
            if item["pk"].count("#") != 1 or not item.get("live_version") or (args.tenant and item.get("tenant_id") != args.tenant):
                continue
            tenant = tenant_or_none(store, item["tenant_id"])
            if not tenant or (not getattr(args, "central", False) and not store.get(reg.suite_key(item["agent_id"]))):
                out(f"{item['agent_id']}: no suite, skipped")
                continue
            args.wait = True
            code = max(code, suite_run_one(store, tenant, item["agent_id"], {"version": item["live_version"], **({"suite": "central"} if getattr(args, "central", False) else {})}, args, out, now, pause))
        return code
    if not args.name or not args.tenant:
        out("Give an agent name and --tenant, or --all-live.")
        return 2
    tenant = tenant_or_none(store, args.tenant)
    agent = admin.owned(store, tenant, args.name) if tenant else None
    if not agent:
        out(f"No agent {args.name} for tenant {args.tenant}.")
        return 1
    if args.config:
        try:
            with open(args.config, encoding="utf8") as handle:
                body = {"config": json.load(handle)}
        except (OSError, ValueError) as error:
            out(f"Cannot read {args.config} as JSON ({type(error).__name__}).")
            return 2
    elif args.version:
        body = {"version": args.version}
    elif agent.get("live_version"):
        body = {"version": agent["live_version"]}
    else:
        out(f"{args.name} has no live version: name one with --version or a draft with --config.")
        return 1
    if getattr(args, "central", False):
        body["suite"] = "central"
    return suite_run_one(store, tenant, args.name, body, args, out, now, pause)


# ---------- repeatability: the same few questions, asked several times ----------



def real_ask(args):
    """The function that asks the worker for ONE dry-run answer and waits for it, or None when no worker is named."""
    name = args.worker or os.environ.get("CHATWOOT_WORKER_FUNCTION")
    if not name:
        return None

    def ask(payload):
        import boto3
        response = boto3.client("lambda").invoke(FunctionName=name, InvocationType="RequestResponse", Payload=json.dumps(payload).encode("utf8"))
        if response.get("FunctionError"):
            raise RuntimeError("the worker failed")
        return json.loads(response["Payload"].read())
    return ask


def ask_case(case, tenant_id, name, version, config, ask, pause):
    """One run of a case through the worker's dry run (see chatwoot_repeat.run_once)."""
    def answer(turn, history):
        payload = {"kind": "test", "tenant_id": tenant_id, "agent_id": name, "version": version, "text": turn, "history": history, "channel": case["channel"],
                   **({"inbox_id": case["inbox_id"]} if case.get("inbox_id") else {})}
        reply = ask(payload)
        pause()
        return reply.get("result") if isinstance(reply, dict) else None
    return repeat.run_once(case, config, answer)


def suite_repeat(store, args, out, now, shelves=None):
    """Ask a few of an agent's suite questions several times each and report how stable the answers are (see chatwoot_repeat.py). Nothing is posted or stored."""
    tenant = tenant_or_none(store, args.tenant)
    agent = admin.owned(store, tenant, args.name) if tenant else None
    if not agent:
        out(f"No agent {args.name} for tenant {args.tenant}.")
        return 1
    version = args.version or agent.get("live_version")
    stored = store.get(reg.version_key(args.name, version)) if version else None
    if not stored:
        out(f"{args.name} has no version to ask: name one with --version (or make one live).")
        return 1
    try:
        config = engine.clean_config(stored["config"])
    except engine.ConfigError as error:
        out(f"Version {version} cannot be used: {error}")
        return 2
    suite_item = store.get(reg.suite_key(args.name)) or {}
    by_id = {c["id"]: c for c in suite_item.get("cases", [])}
    wanted = list(by_id) if args.cases == "all" else [c.strip() for c in (args.cases or ",".join(repeat.DEFAULT_CASES)).split(",") if c.strip()]
    missing = [c for c in wanted if c not in by_id]
    cases = [by_id[c] for c in wanted if c in by_id]
    if missing:
        out(f"Not in the suite of {args.name}, skipped: {', '.join(missing)}")
    if not cases:
        out("There is nothing to ask: load the suite first (suite-load) or name cases that exist.")
        return 2
    ask = getattr(args, "ask", None) or real_ask(args)
    if ask is None:
        out("Name the worker with --worker (the stack's ChatwootWorkerFunction) or the environment variable CHATWOOT_WORKER_FUNCTION.")
        return 2
    pause = getattr(args, "pause", None) or (lambda: time.sleep(args.gap))
    runs = {c["id"]: [] for c in cases}
    for _ in range(args.times):  # one round over all the questions, then the next: a provider that is busy for a moment spoils one run of several cases, not all runs of one
        for case in cases:
            runs[case["id"]].append(ask_case(case, tenant["tenant_id"], args.name, version, config, ask, pause))
    summaries = {c["id"]: repeat.summarise_case(c, runs[c["id"]]) for c in cases}
    escalated = [c["id"] for c in cases if summaries[c["id"]]["signal"] == repeat.DIFFERS] if args.escalate else []  # off by default: a person reads the answers; on request a question that differs is asked more times, alone
    for case in cases:
        if case["id"] in escalated:
            out(f"{case['id']}: the answers differ in {args.times} runs, asking {args.escalate} more times")
            for _ in range(args.escalate):
                runs[case["id"]].append(ask_case(case, tenant["tenant_id"], args.name, version, config, ask, pause))
            summaries[case["id"]] = repeat.summarise_case(case, runs[case["id"]])
    ordered = [summaries[c["id"]] for c in cases]
    spent = sum(r["spent_micro"] for rs in runs.values() for r in rs)
    for line in repeat.render(ordered, version, args.times, spent, escalated):
        out(line)
    label, code, notes = repeat.judge(ordered)
    out(label)
    for note in notes:
        out(f"  - {note}")
    if args.sheet:
        with open(args.sheet, "w", encoding="utf8") as handle:
            handle.write(repeat.sheet(ordered, version, args.times, label))
        out(f"Review sheet saved to {args.sheet}")
    if args.save:
        with open(args.save, "w", encoding="utf8") as handle:
            json.dump({"agent": args.name, "version": version, "times": args.times, "summaries": ordered, "runs": runs}, handle, ensure_ascii=False, indent=1)
        out(f"Saved to {args.save}")
    return code


def parser():
    p = argparse.ArgumentParser(prog="chatwoot_ctl.py", description="The teacher's command for the Chatwoot adapter's tenants, service keys, agents and books.")
    p.add_argument("--table", default=os.environ.get("CHATWOOT_TABLE"), help="the stack output ChatwootTableName (or the environment variable CHATWOOT_TABLE)")
    p.add_argument("--bucket", default=None, help="the sites bucket, for the book commands (or the environment variable BUCKET)")
    p.add_argument("--secret-name", default=None, help="the AI secret's name (the stack's AiSecretName), for the book commands")
    p.add_argument("--worker", default=None, help="the stack's ChatwootWorkerFunction name (or the environment variable CHATWOOT_WORKER_FUNCTION), for suite-run")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("create-tenant", help="create a business and print its service key once")
    s.add_argument("id")
    s.add_argument("--name")
    for name in ("rotate-key", "disable-tenant", "enable-tenant"):
        sub.add_parser(name).add_argument("id")
    s = sub.add_parser("set-cap", help="the most a business may spend a month, in US dollars")
    s.add_argument("id")
    s.add_argument("usd")
    s = sub.add_parser("set-limits", help="a business's media limits (items, per reply, sizes, total space and files); no pairs shows them")
    s.add_argument("id")
    s.add_argument("pairs", nargs="*", help="name=number, for example media_items=20 total_mb=500")
    s.add_argument("--clear", action="store_true", help="remove the business's own limits (the platform's apply)")
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
    s.add_argument("--override-reason", help="when the central safety suite failed this version: why it is made live anyway (10 to 300 characters; kept with the agent)")
    s = sub.add_parser("knowledge-sync", help="a folder of books to a business's shelves")
    s.add_argument("--tenant", required=True)
    s.add_argument("--folder")
    s.add_argument("--prune", action="store_true", help="also remove books that are no longer in the folder")
    s.add_argument("--dry-run", action="store_true", help="show what would happen and change nothing")
    s.add_argument("--then-suite", action="store_true", help="afterwards run the test suite of every live agent of the business (about 2 cents each) and exit 1 if one fails")
    s.add_argument("--timeout", type=int, default=300, help="with --then-suite: seconds to wait for a run")
    s = sub.add_parser("eval", help="real searches on a business's shelves")
    s.add_argument("--tenant", required=True)
    s.add_argument("--collections", required=True, help="comma-separated shelf names")
    s.add_argument("--cases", required=True, help="a JSON file of questions")
    s.add_argument("--k", type=int, default=4)
    s = sub.add_parser("suite-load", help="save an agent's test suite from a JSON file with \"cases\"")
    s.add_argument("name")
    s.add_argument("--tenant", required=True)
    s.add_argument("--file", required=True)
    s = sub.add_parser("suite-show", help="an agent's suite and what its latest runs found")
    s.add_argument("name")
    s.add_argument("--tenant", required=True)
    s = sub.add_parser("central-load", help="load the platform's central safety suite from a JSON file with version, title and cases")
    s.add_argument("--file", required=True)
    sub.add_parser("central-show", help="the central safety suite's version and its cases by risk group")
    s = sub.add_parser("suite-run", help="run an agent's test suite on a published version or a draft (about 2 cents)")
    s.add_argument("--central", action="store_true", help="run the platform's central safety suite instead of the agent's own table")
    s.add_argument("name", nargs="?")
    s.add_argument("--tenant")
    s.add_argument("--version", type=int)
    s.add_argument("--config", help="a JSON configuration file: a draft to test")
    s.add_argument("--wait", action="store_true", help="wait for the run and print the result; exit 1 when a must-case failed")
    s.add_argument("--all-live", action="store_true", help="every agent with a live version and a suite (waits; for after a deployment)")
    s.add_argument("--timeout", type=int, default=300, help="seconds to wait for a run")
    s = sub.add_parser("suite-repeat", help="ask a few suite questions several times each and report how repeatable the answers are (about a cent)")
    s.add_argument("name")
    s.add_argument("--tenant", required=True)
    s.add_argument("--version", type=int, help="the version to ask (the live one by default)")
    s.add_argument("--cases", help="comma-separated case ids, or all (default: five questions of the standard suite)")
    s.add_argument("--times", type=int, default=repeat.TIMES, help="how many times each question is asked")
    s.add_argument("--escalate", type=int, default=0, help="how many more times a question whose answers differ is asked, alone (0: not at all)")
    s.add_argument("--gap", type=float, default=repeat.GAP_SECONDS, help="seconds to wait between two questions (rate limits)")
    s.add_argument("--save", help="write the numbers and every answer to this JSON file")
    s.add_argument("--sheet", help="write a review sheet (Markdown) for a person to read")
    return p


NEEDS_TABLE = {"create-tenant", "rotate-key", "disable-tenant", "enable-tenant", "set-cap", "set-limits", "list", "agent-set", "show-agent", "publish", "set-live", "knowledge-sync", "suite-load", "suite-show", "suite-run", "suite-repeat", "central-load", "central-show"}


def main(argv=None, store=None, out=print, now=None, shelves=None, invoke=None, pause=None, ask=None):
    args = parser().parse_args(argv)
    args.invoke, args.pause, args.ask = invoke, pause, ask  # tests start the worker in-process and do not wait
    now = int(now if now is not None else time.time())
    if store is None and args.command in NEEDS_TABLE:
        if not args.table:
            out("Give the table with --table (the stack output ChatwootTableName) or the environment variable CHATWOOT_TABLE.")
            return 2
        store = real_store(args.table)
    if shelves is None and args.command in ("knowledge-sync", "eval"):
        shelves = real_shelves(args)
    commands = {"create-tenant": create_tenant, "rotate-key": rotate_key, "disable-tenant": lambda *a: set_enabled(*a[:4], False), "enable-tenant": lambda *a: set_enabled(*a[:4], True),
                "set-cap": set_cap, "set-limits": set_limits, "list": list_all, "agent-set": agent_set, "show-agent": show_agent, "publish": publish, "set-live": set_live, "knowledge-sync": knowledge_sync, "eval": eval_cases, "suite-load": suite_load, "suite-show": suite_show, "suite-run": suite_run, "suite-repeat": suite_repeat, "central-load": central_load, "central-show": central_show}
    code = commands[args.command](store, args, out, now, shelves)
    if args.command == "knowledge-sync" and args.then_suite and not args.dry_run and code == 0:  # a new book can change an answer: ask the suite
        args.all_live, args.name, args.wait, args.version, args.config = True, None, True, None, None
        return suite_run(store, args, out, now, shelves)
    return code


if __name__ == "__main__":
    sys.exit(main())
