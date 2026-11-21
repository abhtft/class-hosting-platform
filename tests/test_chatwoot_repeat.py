"""Repeatability (tools/chatwoot_repeat.py and the command `suite-repeat`): runs of a case are combined into a verdict (stable-pass, flaky, stable-fail, inconclusive), an answer that could
not be made never counts against the agent, a flaky case is asked more times, the whole check passes only when every must-case is stable, and the numbers are printed and saved."""
import json
import os
import sys
import tempfile

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))
sys.path.insert(0, os.path.join(root, "tools"))

import chatwoot_ctl as ctl  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_repeat as rp  # noqa: E402
import chatwoot_store as cs  # noqa: E402

# ---- the pure rules ----
def run(passed=True, text="Rs 2,000", route="rag", answered=True, via="GOOGLE_API_KEY", failed=None):
    return {"answered": answered, "passed": passed and answered, "route": route if answered else None, "text": text if answered else "", "via": via,
            "failed": failed if failed is not None else ([] if passed else [{"check": "contains_all", "expected": "2,000", "got": "missing"}]), "spent_micro": 10}


must, should = {"id": "price", "severity": "must"}, {"id": "tone", "severity": "should"}
assert rp.normalise("  Rs 2,000 /- Month!! ") == "rs 2 000 month" == rp.normalise("rs 2,000 month")
s = rp.summarise_case(must, [run(), run(text="rs 2,000"), run(text="Rs 2,000.")])
assert (s["verdict"], s["passed"], s["answered"], s["distinct"], s["routes"], s["vias"]) == (rp.STABLE_PASS, 3, 3, 1, {"rag": 3}, ["GOOGLE_API_KEY"]), "case, punctuation and spacing do not make an answer different"
assert rp.summarise_case(must, [run(), run(passed=False), run()])["verdict"] == rp.FLAKY
assert rp.summarise_case(must, [run(passed=False)] * 3)["verdict"] == rp.STABLE_FAIL
f = rp.summarise_case(must, [run(), run(passed=False), run(passed=False)])
assert f["verdict"] == rp.FLAKY and f["failures"] == ["contains_all: wanted 2,000, got missing (2x)"]
assert rp.summarise_case(must, [run(), run(answered=False), run()]) ["verdict"] == rp.STABLE_PASS and rp.summarise_case(must, [run(), run(answered=False), run()])["no_answer"] == 1, "a run with no answer is not a failure"
assert rp.summarise_case(must, [run(answered=False), run(answered=False), run()])["verdict"] == rp.INCONCLUSIVE and rp.summarise_case(must, [run(answered=False)] * 3)["verdict"] == rp.INCONCLUSIVE
assert rp.summarise_case(should, [run()] * 4 + [run(passed=False)])["verdict"] == rp.STABLE_PASS, "a should-case may pass 80 percent of its runs"
assert rp.summarise_case(should, [run()] * 3 + [run(passed=False)] * 2)["verdict"] == rp.FLAKY
assert rp.summarise_case(must, [run(text="a"), run(text="b"), run(text="c", route="fallback")])["distinct"] == 3
print("  ok  a case's runs become a verdict: stable-pass, flaky, stable-fail or inconclusive; a run with no answer is counted apart; should-cases may wobble a little")


def summary(case_id, verdict, severity="must", passed=3, answered=3):
    return {"id": case_id, "severity": severity, "runs": 3, "answered": answered, "passed": passed, "no_answer": 3 - answered, "verdict": verdict, "distinct": 1, "routes": {}, "vias": [], "failures": []}


good = [summary(c, rp.STABLE_PASS) for c in "abcde"]
assert rp.judge(good) == (True, 0, [])
ok, code, reasons = rp.judge(good[:4] + [summary("e", rp.FLAKY, passed=2)])
assert (ok, code) == (False, 1) and reasons[0] == "e: flaky (2 of 3 answered runs passed)"
assert rp.judge(good[:4] + [summary("e", rp.FLAKY, severity="should", passed=2)]) == (True, 0, []), "a small set is judged by its must-cases: a should-case that wobbles is shown, not failed"
big = [summary(f"c{n}", rp.STABLE_PASS) for n in range(8)] + [summary("d", rp.FLAKY, "should"), summary("e", rp.STABLE_FAIL, "should", passed=0)]
ok, code, reasons = rp.judge(big)
assert (ok, code) == (False, 1) and reasons == ["only 8 of 10 cases are stable-pass"], "a set of ten or more fails when fewer than 90 percent of its cases are stable, even with no must-case failing"
ok, code, reasons = rp.judge(good[:4] + [summary("e", rp.INCONCLUSIVE, passed=0, answered=0)])
assert (ok, code) == (False, 2) and "too few answers" in reasons[0], "not enough answers is inconclusive, not a failure of the agent"
assert rp.judge(good[:3] + [summary("d", rp.STABLE_FAIL, passed=0), summary("e", rp.INCONCLUSIVE, passed=0, answered=0)])[1] == 1, "a real failure wins over an inconclusive case"
lines = rp.render([summary("price", rp.FLAKY, passed=2), summary("tone", rp.STABLE_PASS)], 9, 3, 25000, ["price"])
assert lines[0].endswith("(price asked more)") and "flaky" in lines[1] and "2/3 passed" in lines[1] and lines[-1] == "1 of 2 stable-pass; cost about 2.50 cent"
print("  ok  the whole check fails on a must-case that is not stable or on too few stable cases, is inconclusive when answers are missing, and prints a plain table")

# ---- the command, with a worker that answers from a script ----
NOW = 1_800_000_000
store = cs.MemoryStore()
key, key_hash = reg.new_service_key("acme")
store.claim(reg.tenant_key("acme"), {"tenant_id": "acme", "name": "Acme", "key_hash": key_hash, "enabled": True, "agents": ["shop"], "rev": 1, "created_at": NOW})
store.claim(reg.agent_key("shop"), {"agent_id": "shop", "tenant_id": "acme", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 1, "rev": 1})
CONFIG = {"instructions": "Be brief.", "language": "en", "collections": ["faq"], "fallback_text": "Sorry.", "max_answer_chars": 600, "mode": "rag"}
store.claim(reg.version_key("shop", 1), {"agent_id": "shop", "tenant_id": "acme", "version": 1, "config": CONFIG, "published_at": NOW})
CASES = [{"id": "price-social-media", "turns": ["price?"], "checks": {"route": "rag", "contains_all": ["2,000"]}},
         {"id": "office-hours", "severity": "should", "turns": ["hours?"], "checks": {"route": "rag", "contains_all": ["10"]}},
         {"id": "unknown-ceo", "turns": ["who is the ceo?"], "checks": {"route": "fallback"}},
         {"id": "handover-person", "turns": ["i want a person"], "checks": {"route": "handover", "handover_reason": "person_asked"}},
         {"id": "language-hindi", "turns": ["क्या दाम है?"], "checks": {"script": "devanagari"}},
         {"id": "two-turns", "turns": ["hi", "price?"], "checks": {"route": "rag"}}]
import chatwoot_admin as admin  # noqa: E402
assert admin.put_suite(store, store.get(reg.tenant_key("acme")), "shop", {"cases": CASES}, NOW)[0] == 201

script = {"calls": [], "mode": "good", "flaky_left": 0}


def worker(payload):
    """Answers like the worker's dry run: by the question, scripted by `script`."""
    script["calls"].append(payload)
    text = payload["text"]
    if script["mode"] == "down":
        raise ConnectionError("no route to the worker")
    if text == "price?":
        if script["mode"] == "flaky_price" and len(script["calls"]) % 2 == 0:
            return {"result": {"route": "rag", "text": "Please ask a person.", "spent_micro": 10, "via": "GOOGLE_API_KEY_ANIVESH"}}
        return {"result": {"route": "rag", "text": "Social media is Rs 2,000 a month.", "spent_micro": 10, "via": "GOOGLE_API_KEY"}}
    if text == "hours?":
        return {"result": {"route": "rag", "text": "We open at 10.", "spent_micro": 10, "via": "GOOGLE_API_KEY"}}
    if text == "who is the ceo?":
        if script["mode"] == "refused":
            return {"result": {"route": "handover", "reason": "llm_error", "failure": True, "text": "A person will help.", "spent_micro": 0, "via": None}}
        return {"result": {"route": "fallback", "text": "Sorry.", "spent_micro": 5, "via": "GOOGLE_API_KEY"}}
    if text == "i want a person":
        return {"result": {"route": "handover", "reason": "person_asked", "failure": False, "text": "A person will contact you.", "spent_micro": 0, "via": None}}
    if text == "hi":
        return {"result": {"route": "fallback", "text": "Welcome!", "spent_micro": 1, "via": "GOOGLE_API_KEY"}}
    return {"result": {"route": "rag", "text": "दाम 2,000 रुपये है।", "spent_micro": 10, "via": "GOOGLE_API_KEY"}}


def go(*argv):
    lines = []
    code = ctl.main(list(argv), store=store, out=lines.append, now=NOW, ask=worker, pause=lambda: None)
    return code, "\n".join(lines)


base = ("suite-repeat", "shop", "--tenant", "acme")
script["mode"] = "good"
code, text = go(*base)
assert code == 0 and "REPEATABLE" in text.splitlines()[-1] and "NOT" not in text and "5 questions, each asked 3 times" in text and "5 of 5 stable-pass" in text, text
assert len(script["calls"]) == 15 and "two-turns" not in text, "five questions, three times each, and nothing else"
first = script["calls"][0]
assert first == {"kind": "test", "tenant_id": "acme", "agent_id": "shop", "version": 1, "text": "price?", "history": [], "channel": "whatsapp"}, first
assert [c["text"] for c in script["calls"][:5]] == ["price?", "hours?", "who is the ceo?", "i want a person", "क्या दाम है?"], "a round over all the questions, then the next round"
print("  ok  suite-repeat asks the five standard questions three times each, a round at a time, as fresh conversations on the live version")

script["calls"].clear()
script["mode"] = "flaky_price"
code, text = go(*base)
assert code == 1 and "NOT REPEATABLE" in text and "price-social-media: not stable in 3 runs, asking 10 more times" in text and "(price-social-media asked more)" in text, text
assert len(script["calls"]) == 15 + 10 and "price-social-media: flaky" in text and "contains_all: wanted 2,000, got missing" in text and "GOOGLE_API_KEY_ANIVESH" in text, "only the case that wobbled is asked more, and the answer's key is shown"
print("  ok  a case that is not stable is asked ten more times alone; the check fails and shows what failed and which key answered")

script["calls"].clear()
script["mode"] = "refused"
code, text = go(*base)
assert code == 2 and "INCONCLUSIVE" in text and "inconclusive must   unknown-ceo" in text and "unknown-ceo: too few answers (0 of 3)" in text and "3 with no answer (provider)" in text and "NOT REPEATABLE" not in text, text
script["calls"].clear()
script["mode"] = "down"
code, text = go(*base)
assert code == 2 and "INCONCLUSIVE" in text and "5 of 5" not in text, "a worker that cannot be reached is inconclusive, never a failure of the agent"
print("  ok  answers the model refused, or a worker that cannot be reached, are no_answer: the result is inconclusive, not a failure")

script["calls"].clear()
script["mode"] = "good"
code, text = go(*base, "--cases", "all", "--times", "2")
assert code == 0 and "6 questions, each asked 2 times" in text and len(script["calls"]) == 2 * 7, "all cases; the two-turn case asks twice per run"
code, text = go(*base, "--cases", "price-social-media,nope", "--times", "1")
assert code == 0 and "Not in the suite of shop, skipped: nope" in text and "1 questions" in text
assert go(*base, "--cases", "nope")[0] == 2 and go("suite-repeat", "shop", "--tenant", "beta")[0] == 1 and go("suite-repeat", "nobody", "--tenant", "acme")[0] == 1
assert go(*base, "--version", "5")[0] == 1, "a version that is not there"
path = os.path.join(tempfile.mkdtemp(), "repeat.json")
script["calls"].clear()
code, text = go(*base, "--times", "1", "--save", path)
saved = json.load(open(path, encoding="utf8"))
assert code == 0 and saved["version"] == 1 and len(saved["summaries"]) == 5 and len(saved["runs"]["price-social-media"]) == 1 and saved["runs"]["price-social-media"][0]["text"].startswith("Social media") and f"Saved to {path}" in text
lines = []
assert ctl.main(list(base), store=store, out=lines.append, now=NOW, ask=None, pause=lambda: None) == 2 and "--worker" in "".join(lines), "without a worker the command says so"
print("  ok  all cases or a chosen few; unknown cases and agents are reported; a missing worker is said plainly; the numbers and every answer can be saved")

print("test_chatwoot_repeat.py: repeatability of the answers")
