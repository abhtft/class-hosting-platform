"""The teacher's local command (tools/chatwoot_ctl.py) on an in-memory table and an in-memory bucket: it creates a tenant and shows its service key only once, the key then opens the admin API
and no other door, a rotated key kills the old one, disabling a tenant stops its key and its agents, agents are created and changed with the same validation as the
admin API, listing and showing never print a key or a hash, and bad input is explained with a non-zero exit code."""
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import zlib

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

os.environ.update(AWS_REGION="us-east-1", AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AI_SECRET_NAME="secret")
os.environ.pop("BUCKET", None)  # the command must say so when it is not given a bucket
import chatwoot_admin as admin  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_store as cs  # noqa: E402
import embeddings as em  # noqa: E402
import gemini  # noqa: E402
import knowledge_core as kc  # noqa: E402
import knowledge_store as ks  # noqa: E402

spec = importlib.util.spec_from_file_location("chatwoot_ctl", os.path.join(root, "tools", "chatwoot_ctl.py"))
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

NOW = 1_800_000_000
store = cs.MemoryStore()
KEY = re.compile(r"cs_[a-z0-9-]+\.[A-Za-z0-9_-]{32,64}")


def run(*argv, store=store, shelves=None):
    lines = []
    try:
        code = ctl.main(list(argv), store=store, out=lines.append, now=NOW, shelves=shelves)
    except SystemExit as error:  # argparse exits on a usage error
        code = error.code
    return code, "\n".join(lines)


def api(key, method="GET", path="/admin/agents", body=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"authorization": "Bearer " + key}, "body": json.dumps(body) if body is not None else ""}
    out = admin.handler(event, None, now=NOW, store=store)
    return out["statusCode"], json.loads(out["body"])


# ---- create a tenant: the key is shown once and works ----
code, text = run("create-tenant", "acme", "--name", "Acme Shop")
assert code == 0 and len(KEY.findall(text)) == 1 and "only its hash is stored" in text
key = KEY.findall(text)[0]
tenant = store.get("tenant#acme")
assert tenant["enabled"] is True and tenant["agents"] == [] and tenant["rev"] == 1 and tenant["name"] == "Acme Shop"
assert tenant["key_hash"] == reg.hash_secret(key.split(".", 1)[1]) and key.split(".", 1)[1] not in json.dumps(store.items), "only the hash is stored"
assert api(key)[0] == 200, "the printed key opens the admin API"
code, text = run("create-tenant", "acme")
assert code == 1 and "already exists" in text and not KEY.search(text), "a second create changes nothing and shows no key"
assert api(key)[0] == 200
for bad in ("Acme", "a" * 31, "-a", "a b", ""):
    code, text = run("create-tenant", bad)
    assert code == 2 and not KEY.search(text) and store.get(f"tenant#{bad}") is None, repr(bad)
print("  ok  create-tenant prints the service key once, stores only its hash, the key opens the admin API, and a repeat or a bad id changes nothing")

# ---- rotate: the old key dies ----
code, text = run("rotate-key", "acme")
new_key = KEY.findall(text)[0]
assert code == 0 and new_key != key and "no longer works" in text
assert api(key)[0] == 401 and api(new_key)[0] == 200
assert run("rotate-key", "nobody")[0] == 1 and run("rotate-key", "Bad Id")[0] == 1
assert store.get("tenant#acme")["rev"] == 2
print("  ok  rotate-key makes a new key and the old one stops at once")

# ---- disable and enable ----
code, text = run("disable-tenant", "acme")
assert code == 0 and "disabled" in text and api(new_key)[0] == 401, "a disabled tenant's key is dead"
code, text = run("enable-tenant", "acme")
assert code == 0 and "enabled" in text and api(new_key)[0] == 200
assert run("disable-tenant", "nobody")[0] == 1 and run("enable-tenant", "nobody")[0] == 1
print("  ok  disable-tenant kills the key until enable-tenant")

# ---- agents ----
assert run("agent-set", "shop", "--tenant", "nobody", "--account", "1", "--inboxes", "3")[0] == 1
code, text = run("agent-set", "shop", "--tenant", "acme", "--account", "1", "--inboxes", "3,4", "--type", "chatbot", "--purpose", "sales")
shown = json.loads(text)
assert code == 0 and shown["name"] == "shop" and shown["inbox_ids"] == [3, 4] and shown["enabled"] is True and store.get("tenant#acme")["agents"] == ["shop"]
assert api(new_key, path="/admin/agents/shop")[0] == 200 and [a["name"] for a in api(new_key)[1]["agents"]] == ["shop"], "the admin API sees the agent the command made"
code, text = run("agent-set", "shop", "--tenant", "acme", "--enabled", "false")
assert code == 0 and json.loads(text)["enabled"] is False and json.loads(text)["inbox_ids"] == [3, 4], "an update changes only what is given"
for argv, why in ((("agent-set", "shop", "--tenant", "acme", "--inboxes", "3,x"), "comma-separated"), (("agent-set", "shop", "--tenant", "acme", "--enabled", "maybe"), "true or false"),
                  (("agent-set", "shop", "--tenant", "acme", "--inboxes", "3,3"), "inbox_ids"), (("agent-set", "shop", "--tenant", "acme", "--account", "0"), "account_id"),
                  (("agent-set", "shop", "--tenant", "acme", "--type", "Bad Type"), "type"), (("agent-set", "newone", "--tenant", "acme"), "required")):
    code, text = run(*argv)
    assert code == 2 and why in text, (argv, code, text)
assert store.get("agent#newone") is None
run("create-tenant", "beta")
code, text = run("agent-set", "shop", "--tenant", "beta", "--account", "1", "--inboxes", "3")
assert code == 1 and json.loads(text)["error"] == "name_taken", "a name of another business is refused here too"
code, text = run("show-agent", "shop")
assert code == 0 and json.loads(text)["tenant"] == "acme" and run("show-agent", "nothing")[0] == 1
print("  ok  agent-set creates and changes an agent with the admin API's own validation, refuses a name of another business, and show-agent reads it")

# ---- list and show never print a key or a hash ----
code, text = run("list")
assert code == 0 and "acme  enabled  agents: shop" in text and "beta  enabled  agents: -" in text
everything = text + run("show-agent", "shop")[1] + run("list")[1]
for item in store.items.values():
    if item.get("key_hash"):
        assert item["key_hash"] not in everything
assert not KEY.search(everything)
code, text = run("list", store=cs.MemoryStore())
assert code == 0 and "No tenants" in text
print("  ok  list and show-agent never print a key or a hash")

# ---- usage ----
for argv in ((), ("nonsense",), ("create-tenant",), ("agent-set", "shop")):
    code, text = run(*argv)
    assert code not in (0, None), argv
saved_table = os.environ.pop("CHATWOOT_TABLE", None)
try:
    messages = []
    assert ctl.main(["list"], store=None, out=messages.append, now=NOW) == 2 and "--table" in messages[0], "without a table name the command explains and stops"
finally:
    if saved_table is not None:
        os.environ["CHATWOOT_TABLE"] = saved_table
print("  ok  a missing command, a missing argument or a missing table name stops with a message and a non-zero code")

# ---- the monthly cap ----
code, text = run("set-cap", "acme", "25")
assert code == 0 and store.get("tenant#acme")["monthly_cap_micro"] == 25000000 and "25 US dollars" in text
assert run("set-cap", "acme", "0.5")[0] == 0 and store.get("tenant#acme")["monthly_cap_micro"] == 500000
for bad in ("-1", "abc", "100001", "nan", ""):
    code, text = run("set-cap", "acme", bad)
    assert code == 2 and "US dollars" in text, bad
assert store.get("tenant#acme")["monthly_cap_micro"] == 500000, "a refused cap changes nothing"
assert run("set-cap", "nobody", "5")[0] == 1
assert "cap: 0.5 USD/month" in run("list")[1] and "cap:" not in [l for l in run("list")[1].split("\n") if l.startswith("beta")][0]
print("  ok  set-cap sets a business's monthly spend limit (whole or fractional dollars), refuses odd values, and list shows it")

# ---- the books of a business: a folder to shelves ----
gemini._secret.update(value="google-test-key", at=time.time() + 10 ** 6)
DIMS = 256


def lexical(text, dims=DIMS):
    row = [0.0] * dims
    for term in kc.tokenize(text):
        row[zlib.crc32(term.encode()) % dims] += 1.0
    return row


def transport(provider, url, headers, body):
    texts = [r["content"]["parts"][0]["text"] for r in body["requests"]]
    return 200, {"embeddings": [{"values": lexical(t, body["requests"][0]["outputDimensionality"])} for t in texts]}


em.transport = transport
ctl.embedding_choice = lambda: (em.DEFAULT_MODEL, DIMS)
RETURNS = ("Returns policy of the shop. You can return any unused item within thirty days of buying it, if you bring the original receipt with you. "
           "The refund is paid back to the original payment method within five working days after we receive the item. Items that were used or damaged cannot be returned. "
           "Gift cards cannot be returned or exchanged for cash at any time. If you have a question about a return please ask our team.")
HOURS = ("# Opening hours\n\nThe shop is open from nine in the morning until six in the evening, Monday to Saturday. On Sunday the shop is closed and nobody answers the phone. "
         "On public holidays the shop is closed as well, and we announce special hours on our noticeboard and on the website a week before. Please plan your visit with these hours in mind.\n")
PRICES = ("<html><body><h1>Price list</h1><p>A cotton shirt costs five hundred rupees. A woollen sweater costs fifteen hundred rupees. A leather belt costs eight hundred rupees. "
          "A canvas bag costs six hundred rupees. All prices include tax and a basic gift wrap if you ask for it at the counter when you pay for your items today.</p></body></html>")
tmp = tempfile.mkdtemp()
folder = os.path.join(tmp, "knowledge", "acme")
for sub_folder in ("faq", "prices", "Bad Shelf"):
    os.makedirs(os.path.join(folder, sub_folder))


def put(relative, data):
    with open(os.path.join(folder, relative), "wb") as handle:
        handle.write(data if isinstance(data, bytes) else data.encode("utf8"))


put("faq/returns.txt", RETURNS)
put("faq/hours.md", HOURS)
put("prices/list.html", PRICES)
put("faq/notes.docx", b"not readable here")
put("Bad Shelf/x.txt", RETURNS)
shelves = ks.Store(ks.MemoryBucket())
FAQ, PRICES_BASE = ks.base_of("tenant", "acme", "faq"), ks.base_of("tenant", "acme", "prices")


def sync(*extra, tenant="acme", shelves=shelves, folder=folder):
    return run("knowledge-sync", "--tenant", tenant, "--folder", folder, *extra, shelves=shelves)


code, text = sync("--dry-run")
assert code == 0 and "faq: would create the shelf" in text and "faq/returns.txt: would add" in text and "prices/list.html: would add" in text
assert "Skipped faq/notes.docx" in text and "Skipped the folder Bad Shelf" in text
assert shelves.meta(FAQ) is None and shelves.meta(PRICES_BASE) is None, "a dry run changes nothing"
code, text = sync()
assert code == 0 and "faq: new shelf" in text and re.search(r"faq/returns.txt: added \(\d+ passages\)", text) and re.search(r"prices/list.html: added \(\d+ passages\)", text), text
meta = shelves.meta(FAQ)
assert sorted(d["source"] for d in meta["docs"]) == ["hours.md", "returns.txt"] and meta["embedding"]["dims"] == DIMS and len(shelves.meta(PRICES_BASE)["docs"]) == 1
assert shelves.meta(ks.base_of("tenant", "acme", "bad-shelf")) is None
code, text = sync()
assert code == 0 and text.count("unchanged") == 3 and "added" not in text and len(shelves.meta(FAQ)["docs"]) == 2, "unchanged files are skipped by content"
old_id = [d["id"] for d in shelves.meta(FAQ)["docs"] if d["source"] == "returns.txt"][0]
put("faq/returns.txt", RETURNS.replace("thirty days", "forty five days"))
code, text = sync()
docs = shelves.meta(FAQ)["docs"]
new_id = [d["id"] for d in docs if d["source"] == "returns.txt"][0]
assert code == 0 and "faq/returns.txt: replaced" in text and new_id != old_id and len(docs) == 2, "a changed file replaces the document of the same name"
assert shelves.bucket.get(f"{FAQ}/docs/{old_id}/doc.json") is None, "the old version is gone from the shelf"
assert "forty five days" in json.dumps(shelves.search(FAQ, "return unused item within forty five days", k=2, cache=em.MemoryCache())["hits"])
os.remove(os.path.join(folder, "faq", "hours.md"))
code, text = sync()
assert code == 0 and "removed" not in text and len(shelves.meta(FAQ)["docs"]) == 2, "nothing is deleted without --prune"
code, text = sync("--prune", "--dry-run")
assert "faq/hours.md: would remove" in text and len(shelves.meta(FAQ)["docs"]) == 2
code, text = sync("--prune")
assert code == 0 and "faq/hours.md: removed" in text and [d["source"] for d in shelves.meta(FAQ)["docs"]] == ["returns.txt"]
put("faq/empty.txt", b"")
code, text = sync()
assert code == 1 and "faq/empty.txt: FAILED" in text and "returns.txt: unchanged" in text, "a file that cannot be read is named, the others are still done"
os.remove(os.path.join(folder, "faq", "empty.txt"))
assert sync(tenant="nobody")[0] == 1 and "No tenant nobody" in sync(tenant="nobody")[1]
assert sync(folder=os.path.join(tmp, "none"))[0] == 1 and "No folder" in sync(folder=os.path.join(tmp, "none"))[1]
code, text = run("knowledge-sync", "--tenant", "acme", "--folder", folder)
assert code == 2 and "--bucket" in text, "without a bucket the command explains and stops"
print("  ok  knowledge-sync turns a folder into shelves: a dry run changes nothing, new and changed files are read, unchanged ones skipped, nothing is removed without --prune, a bad file does not stop the others")

# ---- eval: real searches on the shelves ----
cases = os.path.join(tmp, "cases.json")
with open(cases, "w", encoding="utf8") as handle:
    json.dump([{"question": "return unused item within forty five days with receipt", "expect": "forty five days"},
               {"question": "how much does a leather belt cost in rupees", "expect": "eight hundred rupees"},
               {"question": "Explain quantum chromodynamics gluon confinement", "refuse": True}], handle)
code, text = run("eval", "--tenant", "acme", "--collections", "faq,prices", "--cases", cases, shelves=shelves)
lines = text.split("\n")
assert code == 0 and [l.split()[0] for l in lines[:3]] == ["PASS", "PASS", "PASS"] and "3 of 3 cases as expected" in lines[-1], text
assert "faq/returns" in lines[0] and "prices/list" in lines[1] and "sure=yes" in lines[0] and "sure=no" in lines[2], text
with open(cases, "w", encoding="utf8") as handle:
    json.dump([{"question": "return unused item within forty five days", "expect": "a sentence that is not in any book"}, {"question": "how much does a leather belt cost in rupees", "refuse": True}], handle)
code, text = run("eval", "--tenant", "acme", "--collections", "faq,prices", "--cases", cases, shelves=shelves)
assert code == 1 and text.count("FAIL") == 2 and "0 of 2 cases as expected" in text, "a wrong expectation and a refusal that was answered are failures"
assert run("eval", "--tenant", "acme", "--collections", "faq", "--cases", os.path.join(tmp, "missing.json"), shelves=shelves)[0] == 2
with open(cases, "w", encoding="utf8") as handle:
    handle.write("not json")
assert run("eval", "--tenant", "acme", "--collections", "faq", "--cases", cases, shelves=shelves)[0] == 2
with open(cases, "w", encoding="utf8") as handle:
    json.dump({"question": "x"}, handle)
assert run("eval", "--tenant", "acme", "--collections", "faq", "--cases", cases, shelves=shelves)[0] == 2
code, text = run("eval", "--tenant", "acme", "--collections", "faq", "--cases", cases)
assert code == 2 and "--bucket" in text
print("  ok  eval searches the real shelves and reports, per question, whether the books were sure and what matched; wrong expectations and unwanted answers fail the run")

# publish and set-live: the way to give an agent its first version without Studio
cfg = os.path.join(tempfile.mkdtemp(), "shop.json")
good = {"instructions": "Be brief.", "language": "en", "collections": ["faq"], "fallback_text": "A person will reply.", "max_answer_chars": 600, "mode": "rag"}
open(cfg, "w", encoding="utf8").write(json.dumps(good))
assert run("publish", "shop", "--tenant", "nobody", "--config", cfg)[0] == 1
assert run("publish", "nope", "--tenant", "acme", "--config", cfg)[0] == 1
code, text = run("publish", "shop", "--tenant", "acme", "--config", os.path.join(os.path.dirname(cfg), "missing.json"))
assert code == 2 and "Cannot read" in text
bad = os.path.join(os.path.dirname(cfg), "bad.json")
open(bad, "w", encoding="utf8").write(json.dumps({**good, "collections": []}))
code, text = run("publish", "shop", "--tenant", "acme", "--config", bad)
assert code == 2 and "collections" in text and (store.get(reg.agent_key("shop")).get("versions") or 0) == 0, "a configuration the worker cannot use is refused before it is stored"
code, text = run("publish", "shop", "--tenant", "acme", "--config", cfg)
assert code == 0 and "NOT live" in text and store.get(reg.agent_key("shop"))["versions"] == 1 and store.get(reg.agent_key("shop"))["live_version"] == 0
assert run("set-live", "shop", "--tenant", "acme", "--version", "9")[0] == 1
assert run("set-live", "shop", "--tenant", "acme", "--version", "1")[0] == 0 and store.get(reg.agent_key("shop"))["live_version"] == 1
open(cfg, "w", encoding="utf8").write(json.dumps({**good, "mode": "auto"}))
assert run("publish", "shop", "--tenant", "acme", "--config", cfg, "--live")[0] == 0
shop = store.get(reg.agent_key("shop"))
assert shop["versions"] == 2 and shop["live_version"] == 2 and store.get(reg.version_key("shop", 2))["config"]["mode"] == "auto"
assert run("set-live", "shop", "--tenant", "acme", "--version", "0")[0] == 0 and store.get(reg.agent_key("shop"))["live_version"] == 0
print("  ok  publish checks the configuration as the worker will, stores the next version without making it live (unless --live); set-live chooses or clears the live version")

# ---- the test suite commands: load, show, run on a version / a draft / every live agent, and after a new book ----
suite_dir = tempfile.mkdtemp()
suite_file = os.path.join(suite_dir, "suite.json")
open(suite_file, "w", encoding="utf8").write(json.dumps({"cases": [{"id": "a", "turns": ["when do you open?"], "checks": {"route": "rag"}}, {"id": "b", "turns": ["price?"], "checks": {"contains_all": ["2,000"]}}]}))
assert run("suite-load", "shop", "--tenant", "nobody", "--file", suite_file)[0] == 1 and run("suite-load", "shop", "--tenant", "acme", "--file", os.path.join(suite_dir, "missing.json"))[0] == 2
open(os.path.join(suite_dir, "bad.json"), "w", encoding="utf8").write(json.dumps({"cases": [{"id": "Bad Id", "turns": ["x"]}]}))
code, text = run("suite-load", "shop", "--tenant", "acme", "--file", os.path.join(suite_dir, "bad.json"))
assert code == 2 and "id must" in text
code, text = run("suite-load", "shop", "--tenant", "acme", "--file", suite_file)
assert code == 0 and "2 cases, revision 1" in text
code, text = run("suite-show", "shop", "--tenant", "acme")
assert code == 0 and "2 cases (revision 1)" in text and run("suite-show", "nope", "--tenant", "acme")[0] == 1

outcome = {"ok": True, "started": []}


def worker(payload):
    """A stand-in for the worker (asynchronous in real life): it finishes the run at once with a chosen verdict."""
    outcome["started"].append(payload)
    key = f"suiterun#{payload['agent_id']}#{payload['run_id']}"
    item = store.get(key)
    results = [{"id": "a", "severity": "must", "ok": True, "flaky": False, "ran": True, "failed": []},
               {"id": "b", "severity": "must", "ok": outcome["ok"], "flaky": False, "ran": True, "failed": [] if outcome["ok"] else [{"check": "contains_all", "expected": "2,000", "got": "missing"}]}]
    done = {"status": "done", "results": results, "done": 2, "total": 2, "passed": 2 if outcome["ok"] else 1, "must_failed": 0 if outcome["ok"] else 1, "should_failed": 0, "flaky": 0, "ok": outcome["ok"]}
    store.put(key, {**item, **done})


def run_ctl(*argv, invoke=worker):
    lines = []
    try:
        code = ctl.main(list(argv), store=store, out=lines.append, now=NOW, shelves=shelves, invoke=invoke, pause=lambda: None)
    except SystemExit as error:
        code = error.code
    return code, "\n".join(lines)


def free_run():
    """Let the next run start: the suite allows one run at a time, and the stand-in worker does not move the state pointer."""
    state = store.get("suitestate#shop") or {}
    store.put("suitestate#shop", {**state, "latest": {"run_id": "x", "status": "done", "started_at": 0}})


assert run("set-live", "shop", "--tenant", "acme", "--version", "1")[0] == 0
code, text = run_ctl("suite-run", "shop", "--tenant", "acme")
assert code == 0 and "a run of the suite on version 1 (2 cases) was started" in text and outcome["started"][-1]["version"] == 1 and "PASS" not in text, "without --wait it only starts the run"
free_run()
code, text = run_ctl("suite-run", "shop", "--tenant", "acme", "--wait")
assert code == 0 and "PASS  must   a" in text and "2 of 2 passed" in text and "OK" in text.splitlines()[-1], text
free_run()
outcome["ok"] = False
code, text = run_ctl("suite-run", "shop", "--tenant", "acme", "--version", "2", "--wait")
assert code == 1 and "FAIL  must   b  <- contains_all: wanted 2,000, got missing" in text and "FAILED" in text.splitlines()[-1] and outcome["started"][-1]["version"] == 2, text
free_run()
draft = os.path.join(suite_dir, "draft.json")
open(draft, "w", encoding="utf8").write(json.dumps({"collections": ["faq"], "fallback_text": "Sorry."}))
code, text = run_ctl("suite-run", "shop", "--tenant", "acme", "--config", draft)
assert code == 0 and "the editor" in text and outcome["started"][-1]["config"] == {"collections": ["faq"], "fallback_text": "Sorry."}
free_run()
assert run_ctl("suite-run", "shop", "--tenant", "acme", "--config", os.path.join(suite_dir, "missing.json"))[0] == 2
assert run_ctl("suite-run", "shop")[0] == 2 and run_ctl("suite-run", "nope", "--tenant", "acme")[0] == 1
code, text = run_ctl("suite-run", "shop", "--tenant", "acme", "--wait", invoke=None)
assert code == 2 and "--worker" in text, "without a worker to start the command says so"
os.environ.pop("CHATWOOT_WORKER_FUNCTION", None)

# every agent with a live version and a suite: a failure anywhere is exit 1
outcome["ok"] = True
free_run()
code, text = run_ctl("suite-run", "--all-live", "--tenant", "acme")
assert code == 0 and "shop: a run of the suite on version 1" in text and "OK" in text, text
free_run()
outcome["ok"] = False
assert run_ctl("suite-run", "--all-live", "--tenant", "acme")[0] == 1
free_run()
code, text = run_ctl("suite-run", "--all-live", "--tenant", "margdarshak-none")
assert code == 0 and "shop" not in text, "another business's agents are not run"
print("  ok  suite-load saves a checked suite, suite-show reports it, suite-run starts a run on the live version, a version or a draft and with --wait prints each case and exits 1 on a failed must-case; --all-live runs every live agent that has a suite")

# a new book can change an answer: knowledge-sync --then-suite asks the suite afterwards
outcome["ok"] = True
free_run()
book_folder = os.path.join(tempfile.mkdtemp(), "knowledge", "acme", "faq")
os.makedirs(book_folder)
open(os.path.join(book_folder, "extra.txt"), "w", encoding="utf8").write("Extra shop notes. " * 30 + "The shop gives a free coffee to every customer on the first Monday of the month, whatever they buy.")
code, text = run_ctl("knowledge-sync", "--tenant", "acme", "--folder", os.path.dirname(book_folder), "--then-suite")
assert code == 0 and "extra.txt: added" in text and "a run of the suite on version 1" in text and "OK" in text.splitlines()[-1], text
free_run()
outcome["ok"] = False
code, text = run_ctl("knowledge-sync", "--tenant", "acme", "--folder", os.path.dirname(book_folder), "--then-suite")
assert code == 1, "a book that breaks the suite is a failed command"
free_run()
before = len(outcome["started"])
code, text = run_ctl("knowledge-sync", "--tenant", "acme", "--folder", os.path.dirname(book_folder), "--dry-run", "--then-suite")
assert len(outcome["started"]) == before, "a dry run starts nothing"
print("  ok  knowledge-sync --then-suite runs the suites of the business's live agents after the books are in, and fails the command when one breaks")

print("test_chatwoot_ctl.py: the teacher's local command")
