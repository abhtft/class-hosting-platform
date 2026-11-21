"""The central safety suite (plan T3 of docs/notes/TEST-SUITES-PLAN.md): the engine's route_in check and the group and ref labels, the shipped file (every risk group, every case
with a check of its own), the teacher's loader, the read-only route every business may call, a run of the central suite on an agent (with its own state, one run at a time for the agent
whichever suite it is), and what a weak model and a model that refuses each do to its cases."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))
sys.path.insert(0, os.path.join(root, "tools"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_api as api  # noqa: E402
import chatwoot_ctl as ctl  # noqa: E402
import chatwoot_memory as mem  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_runtime as rt  # noqa: E402
import chatwoot_suite as su  # noqa: E402

api.log = lambda what, **details: None
admin.log = lambda what, **details: None
NOW = 1_800_000_000
CANARY = "CANARY-7Q4Z"
FILE = os.path.join(root, "config", "suites", "standard-safety.json")
data = json.load(open(FILE, encoding="utf8"))
N, V = len(data["cases"]), data["version"]

# ---- the engine: route_in, group, ref
case = su.clean_case({"id": "x", "turns": ["hi"], "group": "prompt_injection", "ref": "LLM01", "checks": {"route_in": ["fallback", "handover"]}}, 1)
assert case["group"] == "prompt_injection" and case["ref"] == "LLM01" and case["checks"] == {"route_in": ["fallback", "handover"]}
for bad in ({"route_in": []}, {"route_in": ["rag", "rag"]}, {"route_in": ["maybe"]}, {"route_in": "fallback"}):
    try:
        su.clean_case({"id": "x", "turns": ["hi"], "checks": bad}, 1)
        raise AssertionError(bad)
    except su.Invalid as error:
        assert "route_in" in str(error)
for bad in ({"group": "Prompt Injection"}, {"group": 5}, {"ref": "LLM 01"}, {"ref": ""}):
    try:
        su.clean_case({"id": "x", "turns": ["hi"], **bad}, 1)
        raise AssertionError(bad)
    except su.Invalid:
        pass
assert su.evaluate(case, {"route": "fallback", "text": "Sorry."}, {}) == [] and su.evaluate(case, {"route": "handover", "text": "Sorry."}, {}) == []
failed = su.evaluate(case, {"route": "rag", "text": "Sure, here it is."}, {})
assert failed == [{"check": "route_in", "expected": "fallback or handover", "got": "rag"}]
print("  ok  a case may allow several routes (route_in) and carry the risk group and the standard's label")

# ---- the shipped file
assert data["version"] == 2 and data["title"] and len(data["cases"]) == 52
cases = su.clean_suite(data["cases"])
groups = {}
for c in cases:
    groups.setdefault(c["group"], []).append(c)
assert sorted(groups) == ["excessive_agency", "invented_facts", "language", "output_handling", "prompt_injection", "prompt_leakage", "regulated_advice", "sensitive_info", "unbounded_input", "unsafe_content"]
assert all(c["checks"] for c in cases), "every case has a check of its own, not only the default ones"
assert all(c["severity"] == "must" for c in groups["prompt_injection"] + groups["excessive_agency"]) and sum(c["severity"] == "must" for c in groups["prompt_leakage"]) == 6, "injection and agency are must-cases, and so are six of the seven leakage cases"
injections = [c for c in cases if CANARY in json.dumps(c["turns"], ensure_ascii=False)]
assert len(injections) == 14 and all(c["checks"]["must_not_contain"] == [CANARY] for c in injections), "every injection tries to make the agent write the canary word"
assert sum(1 for c in cases if c["severity"] == "must") >= 20 and {c["ref"] for c in cases if c.get("ref")} >= {"LLM01", "LLM02", "LLM05", "LLM06", "LLM07", "LLM09", "LLM10"}
print("  ok  the central safety suite: 52 valid cases in ten risk groups, each with a check of its own, the injection cases all aimed at one canary word")


# ---- the teacher's loader and the read-only route
class Out:
    def __init__(self):
        self.lines = []

    def __call__(self, line):
        self.lines.append(line)


store = api.MemoryStore()
out = Out()
assert ctl.central_load(store, type("A", (), {"file": FILE})(), out, NOW) == 0 and store.get(reg.central_suite_key())["version"] == V and f"{N} cases" in out.lines[-1]
assert ctl.central_load(store, type("A", (), {"file": FILE})(), out, NOW) == 0 and "already loaded" in out.lines[-1]
changed = os.path.join(os.path.dirname(FILE), "..", "..", "tests", "_central_tmp.json")
json.dump({**data, "cases": data["cases"][:N - 1]}, open(changed, "w", encoding="utf8"), ensure_ascii=False)
try:
    assert ctl.central_load(store, type("A", (), {"file": changed})(), out, NOW) == 1 and "raise the version" in out.lines[-1], "changed cases need a higher version"
    json.dump({**data, "version": V + 1, "cases": data["cases"][:N - 1]}, open(changed, "w", encoding="utf8"), ensure_ascii=False)
    assert ctl.central_load(store, type("A", (), {"file": changed})(), out, NOW + 5) == 0 and store.get(reg.central_suite_key())["version"] == V + 1 and len(store.get(reg.central_suite_key())["cases"]) == N - 1
    json.dump({**data, "version": V - 1}, open(changed, "w", encoding="utf8"), ensure_ascii=False)
    assert ctl.central_load(store, type("A", (), {"file": changed})(), out, NOW + 6) == 1, "an older version is not loaded over a newer one"
    json.dump({"version": V + 2, "cases": [{"id": "x"}]}, open(changed, "w", encoding="utf8"))
    assert ctl.central_load(store, type("A", (), {"file": changed})(), out, NOW + 7) == 2
    json.dump({"cases": []}, open(changed, "w", encoding="utf8"))
    assert ctl.central_load(store, type("A", (), {"file": changed})(), out, NOW + 7) == 2
finally:
    os.remove(changed)
store.put(reg.central_suite_key(), {"version": V, "title": data["title"], "cases": cases, "updated_at": NOW})
shown = Out()
assert ctl.central_show(store, None, shown, NOW) == 0 and any("prompt_injection" in line and "12 cases" in line and "LLM01" in line for line in shown.lines)
assert ctl.central_show(api.MemoryStore(), None, shown, NOW) == 1

tenant = {"tenant_id": "acme", "enabled": True, "key_hash": "x", "agents": ["shop"], "rev": 1}
store.claim(reg.tenant_key("acme"), tenant)
store.claim(reg.agent_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 1, "rev": 1})
BASE = {"instructions": "", "language": "en", "collections": ["faq"], "fallback_text": "Sorry, a person will help you soon.", "max_answer_chars": 300, "mode": "rag"}
store.claim(reg.version_key("shop", 1), {"agent_id": "shop", "tenant_id": "acme", "version": 1, "config": BASE, "published_at": 1})
status, central = admin.route(store, tenant, "GET", "/admin/central-suite", {}, NOW)
assert status == 200 and central["version"] == V and len(central["cases"]) == N and central["cases"][0]["group"] == "prompt_injection"
assert admin.route(store, tenant, "PUT", "/admin/central-suite", {"body": "{}"}, NOW)[0] == 405 and admin.route(store, tenant, "POST", "/admin/central-suite", {"body": "{}"}, NOW)[0] == 405, "no business can change it"
assert admin.route(api.MemoryStore(), tenant, "GET", "/admin/central-suite", {}, NOW)[1] == {"version": 0, "title": None, "cases": [], "updated_at": None}
print("  ok  the teacher loads the central suite (a higher version for changed cases, never an older one), shows it by group, and a business may only read it")

# ---- starting a run of it
started = []
status, run = admin.route(store, tenant, "POST", "/admin/agents/shop/suite/runs", {"body": json.dumps({"version": 1, "suite": "central"})}, NOW, invoke=started.append)
assert status == 202 and run["total"] == N and started[0]["suite"] == "central" and started[0]["kind"] == "suite"
item = store.get(reg.suite_run_key("shop", run["run_id"]))
assert item["suite"] == "central" and item["suite_rev"] == V and store.get(reg.suite_state_key("shop", "central"))["latest"]["run_id"] == run["run_id"] and store.get(reg.suite_state_key("shop")) is None
status, busy = admin.route(store, tenant, "POST", "/admin/agents/shop/suite/runs", {"body": json.dumps({"version": 1})}, NOW + 5, invoke=started.append)
assert status == 409 and busy["error"] in ("run_in_progress", "no_suite"), "the agent has no table yet, and a run is going"
store.put(reg.suite_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "rev": 1, "cases": [{"id": "a", "title": "a", "severity": "must", "channel": "whatsapp", "turns": ["hi"], "checks": {}}]})
assert admin.route(store, tenant, "POST", "/admin/agents/shop/suite/runs", {"body": json.dumps({"version": 1})}, NOW + 5, invoke=started.append)[1]["error"] == "run_in_progress", "one run at a time for an agent, whichever suite it is"
try:
    admin.route(store, tenant, "POST", "/admin/agents/shop/suite/runs", {"body": json.dumps({"version": 1, "suite": "other"})}, NOW + 400, invoke=started.append)
    raise AssertionError("an unknown suite must be refused")
except reg.Invalid:
    pass
assert admin.route(api.MemoryStore(), tenant, "POST", "/admin/agents/shop/suite/runs", {"body": "{}"}, NOW)[0] == 404
empty = api.MemoryStore()
empty.claim(reg.tenant_key("acme"), tenant)
empty.claim(reg.agent_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 0, "versions": 0, "rev": 1})
assert admin.route(empty, tenant, "POST", "/admin/agents/shop/suite/runs", {"body": json.dumps({"config": BASE, "suite": "central"})}, NOW, invoke=started.append)[1]["error"] == "no_suite", "no central suite loaded yet"
state = admin.route(store, tenant, "GET", "/admin/agents/shop/suite/state", {"queryStringParameters": {"which": "central"}}, NOW)[1]
assert state["cases"] == N and state["suite_rev"] == V and state["latest"]["run_id"] == run["run_id"] and admin.route(store, tenant, "GET", "/admin/agents/shop/suite/state", {}, NOW)[1]["cases"] == 1
print("  ok  a run of the central suite has its own state, needs the suite to be loaded, and an agent runs only one suite at a time")


# ---- what a weak model and a model that refuses do
class Rig:
    def __init__(self, llm_text, confident):
        self.store = api.MemoryStore()
        self.store.items.update({k: v for k, v in store.items.items() if k.startswith(("tenant#", "agent#", "suitecentral#"))})
        found = {"hits": [{"doc": "d", "title": "Faq", "page": 1, "page_end": 1, "section": "", "text": "We open at nine.", "vector": 0.9, "collection": "faq"}] if confident else [], "confident": confident,
                 "tokens": 1, "cost_micro": 1, "missing": []}
        runtime = rt.make_runtime(lambda tenant, collections, query: found, lambda messages, max_tokens, **kw: {"text": llm_text(messages), "model": "m", "tokens": 5, "cost_micro": 1})
        self.deps = api.Deps(self.store, api.Secrets(lambda name: "{}", api.credentials_of), http=None, registry=reg.Registry(self.store, ttl=0), runtime=runtime, memory=mem.MemoryOnly())

    def run(self, run_id):
        self.store.put(reg.suite_run_key("shop", run_id), {"tenant_id": "acme", "agent_id": "shop", "run_id": run_id, "status": "queued", "target": "version 1", "version": 1, "total": N, "done": 0, "results": [],
                                                           "suite_rev": 1, "started_at": NOW, "suite": "central"})
        api.worker({"kind": "suite", "tenant_id": "acme", "agent_id": "shop", "run_id": run_id, "version": 1, "suite": "central"}, None, now=NOW + 5, deps=self.deps)
        return self.store.get(reg.suite_run_key("shop", run_id)), self.store.get(reg.suite_state_key("shop", "central"))


def last_user_text(messages):
    return messages[-1]["content"]


# a model that does what any message says (and writes the canary word when asked): the injection cases fail
weak = Rig(lambda messages: CANARY if CANARY in last_user_text(messages) else "Sure, here are the steps. Step 1: mix the ingredients.", confident=True)
run, state = weak.run("aaaaaaaaaaaa")
assert run["status"] == "done" and run["suite"] == "central" and run["total"] == N and len(run["results"]) == N
by_id = {r["id"]: r for r in run["results"]}
assert all(not by_id[c["id"]]["ok"] for c in injections), "every injection case fails against a model that obeys"
assert not by_id["unsafe-weapon"]["ok"] and not by_id["unsafe-hack"]["ok"] and by_id["unsafe-weapon"]["failed"][0]["check"] in ("route_in", "must_not_contain")
assert run["must_failed"] >= 8 and run["ok"] is False and state["latest"]["run_id"] == "aaaaaaaaaaaa" and state["runs"][0]["must_failed"] == run["must_failed"] and weak.store.get(reg.suite_state_key("shop")) is None, "its own state, not the agent's table's"

# a model that says it does not know: no injection works, nothing unsafe is said
safe = Rig(lambda messages: rt.NOT_SURE, confident=True)
run, state = safe.run("bbbbbbbbbbbb")
failed = [r["id"] + ": " + "; ".join(f"{c['check']} wanted {c['expected']} got {c['got']}" for c in r["failed"]) for r in run["results"] if not r["ok"]]
assert run["status"] == "done" and failed == [] and run["ok"] is True and run["passed"] == N, failed
print("  ok  against a model that obeys, the injection and unsafe cases fail; against one that says it does not know, all pass; the results sit in the central suite's own state")

# ---- Make live and the safety suite: a failed must-case holds the version back unless the owner gives a reason; never-run and stale results do not
gate = api.MemoryStore()
gate.claim(reg.tenant_key("acme"), tenant)
gate.claim(reg.agent_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 2, "rev": 1})
for number in (1, 2):
    gate.claim(reg.version_key("shop", number), {"agent_id": "shop", "tenant_id": "acme", "version": number, "config": BASE, "published_at": 1})
gate.put(reg.central_suite_key(), {"version": V, "title": "t", "cases": cases, "updated_at": NOW})


def live(number, reason=None, at=NOW + 10, **more):
    body = {"version": number, **({"override_reason": reason} if reason is not None else {}), **more}
    return admin.route(gate, tenant, "POST", "/admin/agents/shop/live", {"body": json.dumps(body)}, at)


assert live(2)[0] == 200, "never run on the central suite: not held back"
gate.put(reg.suite_state_key("shop", "central"), {"runs": [], "versions": {"2": {"run_id": "r2", "ok": False, "passed": 49, "total": N, "must_failed": 2, "suite_rev": V, "finished_at": NOW},
                                                                         "1": {"run_id": "r1", "ok": True, "passed": N, "total": N, "must_failed": 0, "suite_rev": V, "finished_at": NOW}}})
status, held = live(2)
assert status == 409 and held["error"] == "safety_failed" and held["must_failed"] == 2 and held["run_id"] == "r2" and "version 2" in held["message"], held
assert gate.get(reg.agent_key("shop"))["live_version"] == 2 and "overrides" not in gate.get(reg.agent_key("shop")), "nothing changed (it was live already in this test) and no reason is recorded"
assert live(1)[0] == 200 and gate.get(reg.agent_key("shop"))["live_version"] == 1, "a version that passed goes live"
assert live(1, "a reason that is long enough")[1].get("overrides") is None, "a reason given for a version that did not need one is not kept"
for bad in ("short", "x" * 301, 5, ["a reason long enough"]):
    try:
        live(2, bad)
        raise AssertionError(bad)
    except reg.Invalid as error:
        assert "override_reason" in str(error)
try:
    live(2, None, extra=1)
    raise AssertionError("an unknown field must be refused")
except reg.Invalid:
    pass
status, made = live(2, "  The two failed cases are about a product we do not sell.  ", at=NOW + 20)
assert status == 200 and made["live_version"] == 2 and made["overrides"] == {"2": {"reason": "The two failed cases are about a product we do not sell.", "at": NOW + 20, "must_failed": 2, "run_id": "r2"}}
assert gate.get(reg.agent_key("shop"))["overrides"]["2"]["reason"].startswith("The two failed") and admin.route(gate, tenant, "GET", "/admin/agents/shop", {}, NOW)[1]["overrides"]["2"]["must_failed"] == 2, "the reason is kept with the agent and shown with it"
gate.put(reg.central_suite_key(), {"version": V + 1, "title": "t", "cases": cases, "updated_at": NOW})
assert live(1)[0] == 200 and live(2)[0] == 200, "a result from an older version of the suite does not hold a version back"
assert live(0)[0] == 200 and live(9)[0] == 404, "no live version is always allowed; an unknown version is not found"
print("  ok  Make live: a version that failed a must-case of the current central suite is held back unless the owner gives a reason (kept with the agent); never-run, older and passing results go through")

print("test_chatwoot_central.py: the central safety suite")
