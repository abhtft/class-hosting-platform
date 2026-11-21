"""Repeatability (tools/chatwoot_repeat.py and the command `suite-repeat`): a few questions are asked a few times each and the machine only gives clear signals (same, differs,
unexpected, no answer) next to the real answers, for a person to read. No model judges anything. An answer that could not be made never counts against the agent."""
import json
import os
import sys
import tempfile

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))
sys.path.insert(0, os.path.join(root, "tools"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_ctl as ctl  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_repeat as rp  # noqa: E402
import chatwoot_store as cs  # noqa: E402


# ---- the pure rules ----
def run(passed=True, text="Rs 2,000", route="rag", answered=True, via="GOOGLE_API_KEY", failed=None):
    return {"answered": answered, "passed": passed and answered, "route": route if answered else None, "text": text if answered else "", "via": via,
            "failed": failed if failed is not None else ([] if passed else [{"check": "contains_all", "expected": "2,000", "got": "missing"}]), "spent_micro": 10}


case = {"id": "price", "severity": "must"}
assert rp.normalise("  Rs 2,000 /- Month!! ") == "rs 2 000 month" == rp.normalise("rs 2,000 month")
s = rp.summarise_case(case, [run(), run(text="rs 2,000", via="GOOGLE_API_KEY_ANIVESH"), run(text="Rs 2,000 a month")])
assert s["signal"] == rp.SAME and s["agree"] == 1.0 and [a["count"] for a in s["answers"]] == [2, 1] and s["vias"] == ["GOOGLE_API_KEY", "GOOGLE_API_KEY_ANIVESH"], "different wording that passes the same way is the same"
assert s["answers"][0]["text"] == "Rs 2,000" and s["answers"][0]["via"] == ["GOOGLE_API_KEY", "GOOGLE_API_KEY_ANIVESH"], "case and punctuation do not make an answer different"
d = rp.summarise_case(case, [run(), run(passed=False), run()])
assert d["signal"] == rp.DIFFERS and round(d["agree"], 2) == 0.67 and d["failures"] == ["contains_all: wanted 2,000, got missing (1x)"]
r = rp.summarise_case(case, [run(route="fallback"), run(route="rag"), run(route="rag")])
assert r["signal"] == rp.DIFFERS, "a different route is a difference even when every run passes"
u = rp.summarise_case(case, [run(passed=False)] * 3)
assert u["signal"] == rp.UNEXPECTED and u["agree"] == 1.0 and u["failures"] == ["contains_all: wanted 2,000, got missing (3x)"], "failing the same way every time is consistent: not a repeatability problem"
n = rp.summarise_case(case, [run(), run(answered=False), run()])
assert n["signal"] == rp.SAME and n["no_answer"] == 1 and n["answered"] == 2, "a run with no answer is counted apart, never as a failure"
assert rp.summarise_case(case, [run(answered=False), run(answered=False), run()])["signal"] == rp.NO_ANSWER and rp.summarise_case(case, [run(answered=False)] * 3)["signal"] == rp.NO_ANSWER
print("  ok  a question's runs give a signal: same, differs (another route, or some pass and some fail), unexpected (the same failure every time) or no answer; wording alone is not a difference")


def summary(case_id, signal, agree=1.0, answered=3):
    return {"id": case_id, "severity": "must", "runs": 3, "answered": answered, "no_answer": 3 - answered, "signal": signal, "agree": agree, "answers": [], "vias": [], "failures": []}


good = [summary(c, rp.SAME) for c in "abcde"]
assert rp.judge(good) == ("LOOKS CONSISTENT", 0, [])
label, code, notes = rp.judge(good[:4] + [summary("e", rp.DIFFERS, agree=2 / 3)])
assert (label, code) == ("NEEDS A LOOK", 0) and notes == ["e: the answers differ (67% of the runs agree): read them"], "a difference asks for a look, it does not fail the check"
label, code, notes = rp.judge(good[:4] + [summary("e", rp.UNEXPECTED)])
assert (label, code) == ("LOOKS CONSISTENT", 0) and "a matter of quality" in notes[0]
label, code, notes = rp.judge(good[:4] + [summary("e", rp.DIFFERS, agree=1 / 3)])
assert (label, code) == ("UNSTABLE", 1), "only a question whose runs hardly agree at all fails"
label, code, notes = rp.judge(good[:4] + [summary("e", rp.NO_ANSWER, agree=0.0, answered=0)])
assert (label, code) == ("LOOKS CONSISTENT", 0) and "too few answers (0 of 3)" in notes[0], "a question with no answer is noted, it does not fail the others"
assert rp.judge([summary(c, rp.NO_ANSWER, 0.0, 0) for c in "ab"])[:2] == ("INCONCLUSIVE", 2) and rp.judge([])[:2] == ("INCONCLUSIVE", 2)
lines = rp.render([{**summary("price", rp.DIFFERS, 2 / 3), "answers": [{"text": "Rs 2,000 a month.", "route": "rag", "passed": True, "count": 2, "via": ["GOOGLE_API_KEY"]},
                                                              {"text": "Please ask a person.", "route": "rag", "passed": False, "count": 1, "via": []}], "failures": ["contains_all: wanted 2,000, got missing (1x)"]}],
                  9, 3, 25000, ["price"])
assert lines[0] == "Repeatability of version 9: 1 questions, each asked 3 times (price asked more)" and lines[1].startswith("  differs") and "2 different answers" in lines[1]
assert lines[2].strip().startswith("2x [rag, GOOGLE_API_KEY] 'Rs 2,000 a month.'") and "1x [rag, check failed] 'Please ask a person.'" in lines[3] and lines[-1] == "cost about 2.50 cent"
md = rp.sheet([{**summary("price", rp.DIFFERS, 2 / 3), "answers": [{"text": "Rs 2,000 a month.", "route": "rag", "passed": True, "count": 2, "via": ["GOOGLE_API_KEY"]}], "failures": []}], 9, 3, "NEEDS A LOOK")
assert md.startswith("# Repeatability review: version 9") and "**NEEDS A LOOK**" in md and "## price (must): differs" in md and "- **2x**, route `rag`, GOOGLE_API_KEY" in md and "> Rs 2,000 a month." in md
print("  ok  the check is LOOKS CONSISTENT, NEEDS A LOOK (a question differs), UNSTABLE (its runs hardly agree), or INCONCLUSIVE (no question got an answer); the real answers are printed and written to a review sheet")

# ---- the command, with a worker that answers from a script ----
NOW = 1_800_000_000
store = cs.MemoryStore()
key, key_hash = reg.new_service_key("acme")
store.claim(reg.tenant_key("acme"), {"tenant_id": "acme", "name": "Acme", "key_hash": key_hash, "enabled": True, "agents": ["shop"], "rev": 1, "created_at": NOW})
store.claim(reg.agent_key("shop"), {"agent_id": "shop", "tenant_id": "acme", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 1, "rev": 1})
CONFIG = {"instructions": "Be brief.", "language": "en", "collections": ["faq"], "fallback_text": "Sorry.", "max_answer_chars": 600, "mode": "rag"}
store.claim(reg.version_key("shop", 1), {"agent_id": "shop", "tenant_id": "acme", "version": 1, "config": CONFIG, "published_at": NOW})
CASES = [{"id": "price-social-media", "turns": ["price?"], "checks": {"route": "rag", "contains_all": ["2,000"]}},
         {"id": "office-hours", "severity": "should", "turns": ["hours?"], "checks": {"route": "rag", "contains_all": ["9"]}},
         {"id": "unknown-ceo", "turns": ["who is the ceo?"], "checks": {"route": "fallback"}},
         {"id": "handover-person", "turns": ["i want a person"], "checks": {"route": "handover", "handover_reason": "person_asked"}},
         {"id": "language-hindi", "turns": ["क्या दाम है?"], "checks": {"script": "devanagari"}},
         {"id": "two-turns", "turns": ["hi", "price?"], "checks": {"route": "rag"}}]
assert admin.put_suite(store, store.get(reg.tenant_key("acme")), "shop", {"cases": CASES}, NOW)[0] == 201

script = {"calls": [], "mode": "good"}


def worker(payload):
    """Answers like the worker's dry run: by the question, scripted by `script`."""
    script["calls"].append(payload)
    text = payload["text"]
    if script["mode"] == "down":
        raise ConnectionError("no route to the worker")
    if text == "price?":
        if script["mode"] == "differs" and len([c for c in script["calls"] if c["text"] == "price?"]) % 2 == 0:
            return {"result": {"route": "rag", "text": "Please ask a person.", "spent_micro": 10, "via": "GOOGLE_API_KEY_ANIVESH"}}
        return {"result": {"route": "rag", "text": "Social media is Rs 2,000 a month.", "spent_micro": 10, "via": "GOOGLE_API_KEY"}}
    if text == "hours?":
        return {"result": {"route": "rag", "text": "We open at 10.", "spent_micro": 10, "via": "GOOGLE_API_KEY"}}
    if text == "who is the ceo?":
        if script["mode"] == "refused":
            return {"result": {"route": "handover", "reason": "llm_error", "failure": True, "text": "A person will help.", "spent_micro": 0, "via": None}}
        if script["mode"] == "unstable" and len([c for c in script["calls"] if c["text"] == "who is the ceo?"]) % 3 != 0:
            return {"result": {"route": "rag", "text": "The books do not say. 😊", "spent_micro": 5, "via": "GOOGLE_API_KEY"}}
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
assert code == 0 and text.splitlines()[0] == "Repeatability of version 1: 5 questions, each asked 3 times" and "LOOKS CONSISTENT" in text, text
assert len(script["calls"]) == 15 and "two-turns" not in text, "five questions, three times each, nothing more by default"
assert script["calls"][0] == {"kind": "test", "tenant_id": "acme", "agent_id": "shop", "version": 1, "text": "price?", "history": [], "channel": "whatsapp"}
assert [c["text"] for c in script["calls"][:5]] == ["price?", "hours?", "who is the ceo?", "i want a person", "क्या दाम है?"], "a round over all the questions, then the next round"
assert "  unexpected office-hours" in text and "the same check failed every time" in text, "the stale expectation (9, not 10) is not blamed on repeatability: it is the same every time"
assert "3x [rag, GOOGLE_API_KEY] 'We open at 10.'" not in text and "3x [rag, check failed, GOOGLE_API_KEY] 'We open at 10.'" in text, "the real answer is printed"
print("  ok  suite-repeat asks the five standard questions three times each, a round at a time, as fresh conversations on the live version, and prints the real answers")

script["calls"].clear()
script["mode"] = "differs"
code, text = go(*base)
assert code == 0 and "NEEDS A LOOK" in text and "  differs    price-social-media" in text and "Please ask a person." in text and len(script["calls"]) == 15, "a difference is shown, with no extra questions by default"
script["calls"].clear()
code, text = go(*base, "--escalate", "4")
assert "price-social-media: the answers differ in 3 runs, asking 4 more times" in text and len(script["calls"]) == 15 + 4 and "(price-social-media asked more)" in text, "on request a question that differs is asked more times, alone"
script["calls"].clear()
script["mode"] = "unstable"
code, text = go(*base)
assert "unknown-ceo" in text and "The books do not say. 😊" in text and "  differs    unknown-ceo" in text, "a wobble of the route (an emoji that defeats the fallback) is shown with its words"
print("  ok  a question whose answers differ is shown with all its answers and asks for a look; more questions only on request")

script["calls"].clear()
script["mode"] = "refused"
code, text = go(*base)
assert code == 0 and "no answer  unknown-ceo" in text and "unknown-ceo: too few answers (0 of 3)" in text and "UNSTABLE" not in text, "a refused model is no answer, and does not spoil the other questions"
script["calls"].clear()
script["mode"] = "down"
code, text = go(*base)
assert code == 2 and "INCONCLUSIVE" in text and "No question got an answer" in text, "a worker that cannot be reached is inconclusive, never a failure of the agent"
print("  ok  answers the model refused are no answer; a worker that cannot be reached is inconclusive")

script["calls"].clear()
script["mode"] = "good"
code, text = go(*base, "--cases", "all", "--times", "2")
assert code == 0 and "6 questions, each asked 2 times" in text and len(script["calls"]) == 2 * 7, "all cases; the two-turn case asks twice per run"
code, text = go(*base, "--cases", "price-social-media,nope", "--times", "1")
assert code == 0 and "Not in the suite of shop, skipped: nope" in text and "1 questions" in text
assert go(*base, "--cases", "nope")[0] == 2 and go("suite-repeat", "shop", "--tenant", "beta")[0] == 1 and go("suite-repeat", "nobody", "--tenant", "acme")[0] == 1
assert go(*base, "--version", "5")[0] == 1, "a version that is not there"
folder = tempfile.mkdtemp()
path, sheet_path = os.path.join(folder, "repeat.json"), os.path.join(folder, "sheet.md")
script["calls"].clear()
code, text = go(*base, "--times", "1", "--save", path, "--sheet", sheet_path)
saved = json.load(open(path, encoding="utf8"))
md = open(sheet_path, encoding="utf8").read()
assert code == 0 and saved["version"] == 1 and len(saved["summaries"]) == 5 and saved["runs"]["price-social-media"][0]["text"].startswith("Social media") and f"Saved to {path}" in text
assert md.startswith("# Repeatability review: version 1") and "## price-social-media (must): same" in md and "> Social media is Rs 2,000 a month." in md and f"Review sheet saved to {sheet_path}" in text
lines = []
assert ctl.main(list(base), store=store, out=lines.append, now=NOW, ask=None, pause=lambda: None) == 2 and "--worker" in "".join(lines), "without a worker the command says so"
print("  ok  all cases or a chosen few; unknown cases and agents are reported; a missing worker is said plainly; the numbers, every answer and a review sheet can be saved")

print("test_chatwoot_repeat.py: repeatability of the answers")
