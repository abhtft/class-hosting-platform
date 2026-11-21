"""The test suite rules (lambda/chatwoot_suite.py): a case is checked (its id, severity, channel, turns, every kind of check), a suite has limits, the evaluator judges an answer by the
case's own checks and by the default ones (no markdown, no leaked rule words, the agent's length limit), numbers are compared whatever the spaces, the script check follows the customer's
language, and the standard suite file shipped with the platform is itself valid."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_suite as su  # noqa: E402

GOOD = {"id": "price-sm", "title": "Social media price", "turns": ["kitna lagega?"], "checks": {"route": "rag", "contains_all": ["2,000"]}}
assert su.clean_case(GOOD, 1) == {"id": "price-sm", "title": "Social media price", "severity": "must", "channel": "whatsapp", "turns": ["kitna lagega?"], "checks": {"route": "rag", "contains_all": ["2,000"]}}
full = su.clean_case({"id": "x", "title": " T ", "severity": "should", "channel": "web_widget", "inbox_id": 3, "turns": [" a ", "b"],
                      "checks": {"route": "handover", "handover_reason": "stuck", "contains_any": [" p "], "must_not_contain": ["q"], "script": "latin", "source_contains": ["books"], "max_chars": 300}}, 1)
assert full["title"] == "T" and full["turns"] == ["a", "b"] and full["checks"]["contains_any"] == ["p"] and full["inbox_id"] == 3 and full["channel"] == "web_widget"
assert su.clean_case({"id": "no-checks", "turns": ["hi"]}, 1)["checks"] == {} and su.clean_case({"id": "no-checks", "turns": ["hi"]}, 1)["title"] == "no-checks"
for bad, why in (("text", "JSON object"), ({**GOOD, "extra": 1}, "unknown field"), ({**GOOD, "id": "Bad Id"}, "id must"), ({**GOOD, "id": 5}, "id must"), ({**GOOD, "id": "x" * 41}, "id must"), ({**GOOD, "title": ""}, "title"),
                 ({**GOOD, "title": "t" * 101}, "title"), ({**GOOD, "severity": "maybe"}, "severity"), ({**GOOD, "channel": "WhatsApp"}, "channel"), ({**GOOD, "inbox_id": 0}, "inbox_id"), ({**GOOD, "inbox_id": "3"}, "inbox_id"),
                 ({**GOOD, "turns": []}, "turns"), ({**GOOD, "turns": ["a"] * (su.MAX_TURNS + 1)}, "turns"), ({**GOOD, "turns": [""]}, "turns"), ({**GOOD, "turns": [5]}, "turns"), ({**GOOD, "turns": ["x" * 5000]}, "turns"),
                 ({**GOOD, "checks": []}, "checks is"), ({**GOOD, "checks": {"nonsense": 1}}, "unknown check"), ({**GOOD, "checks": {"route": "maybe"}}, "route"), ({**GOOD, "checks": {"handover_reason": "Bad"}}, "handover_reason"),
                 ({**GOOD, "checks": {"script": "greek"}}, "script"), ({**GOOD, "checks": {"max_chars": 5}}, "max_chars"), ({**GOOD, "checks": {"max_chars": True}}, "max_chars"), ({**GOOD, "checks": {"contains_all": []}}, "contains_all"),
                 ({**GOOD, "checks": {"contains_all": "x"}}, "contains_all"), ({**GOOD, "checks": {"contains_any": ["x"] * 11}}, "contains_any"), ({**GOOD, "checks": {"must_not_contain": ["x" * 101]}}, "must_not_contain")):
    try:
        su.clean_case(bad, 1)
        raise AssertionError(bad)
    except su.Invalid as error:
        assert why.lower() in str(error).lower(), (bad, str(error))
withexp = su.clean_case({**GOOD, "expected": "  The shop opens at nine.  "}, 1)
assert withexp["expected"] == "The shop opens at nine." and "expected" not in su.clean_case(GOOD, 1), "an expected answer is optional, kept trimmed"
for bad in ("", "   ", 5, None, "x" * (su.EXPECTED_CHARS + 1)):
    try:
        su.clean_case({**GOOD, "expected": bad}, 1)
        raise AssertionError(bad)
    except su.Invalid as error:
        assert "expected" in str(error)
assert su.evaluate(su.clean_case({**GOOD, "expected": "Rs 2,000 a month."}, 1), {"route": "rag", "text": "Starts at Rs 2,000."}, {}) == [], "the expected answer never decides pass or fail: the checks do"
print("  ok  a case is checked field by field, with a reason in words for the operator")

assert su.MAX_CASES == 60 and su.MAX_TURNS == 3
assert len(su.clean_suite([{**GOOD, "id": f"c{n}"} for n in range(su.MAX_CASES)])) == su.MAX_CASES
for bad in ("x", [], [GOOD] * 2, [{**GOOD, "id": f"c{n}"} for n in range(su.MAX_CASES + 1)], [{**GOOD, "id": "a", "checks": {"must_not_contain": ["x" * 90] * 10, "contains_any": ["x" * 90] * 10, "contains_all": ["x" * 90] * 10}}] * 1 and "no"):
    try:
        su.clean_suite(bad)
        raise AssertionError(bad)
    except su.Invalid:
        pass
print("  ok  a suite is 1 to 60 cases with different ids")

CONFIG = {"max_answer_chars": 700}


def result(text, route="rag", reason=None, explain=None):
    return {"route": route, "text": text, "reason": reason, "explain": explain or []}


def fails(case_checks, res, config=CONFIG, severity="must"):
    case = su.clean_case({"id": "c", "turns": ["q"], "checks": case_checks}, 1)
    return [f["check"] for f in su.evaluate(case, res, config)]


assert fails({"route": "rag"}, result("hello")) == [] and fails({"route": "fallback"}, result("hello")) == ["route"]
assert fails({"handover_reason": "stuck"}, result("x", "handover", "stuck")) == [] and fails({"handover_reason": "stuck"}, result("x")) == ["handover_reason"]
assert fails({"contains_all": ["2,000", "3,000"]}, result("From Rs 2,000 and Rs 3,000")) == [] and fails({"contains_all": ["2,000", "3,000"]}, result("Rs 2,000")) == ["contains_all"]
assert fails({"contains_any": ["alpha", "beta"]}, result("it is BETA")) == [] and fails({"contains_any": ["alpha", "beta"]}, result("gamma")) == ["contains_any"]
assert fails({"must_not_contain": ["pwned"]}, result("ok")) == [] and fails({"must_not_contain": ["pwned"]}, result("PWNED!")) == ["must_not_contain"]
assert fails({"contains_all": ["91892 25257"]}, result("call +91 9189225257 now")) == [] and fails({"contains_all": ["91892 25257"]}, result("call 91892-25257")) == [], "a number is the same whatever the spaces and hyphens"
assert fails({"contains_all": ["91892 25257"]}, result("call 91892 25258")) == ["contains_all"]
assert fails({"contains_all": ["2,000"]}, result("it is Rs 2000 a month")) == [] and fails({"contains_all": ["2000"]}, result("it is Rs 2,000 a month")) == [] and fails({"contains_all": ["1,00,000"]}, result("Rs 100000")) == [], "a thousands comma does not make a number different"
assert fails({"contains_all": ["2,000"]}, result("Rs 20,000")) == ["contains_all"], "a different number is still different"
assert fails({"script": "devanagari"}, result("यह एक जवाब है")) == [] and fails({"script": "devanagari"}, result("this is english")) == ["script"]
assert fails({"script": "latin"}, result("this is english")) == [] and fails({"script": "latin"}, result("यह जवाब है")) == ["script"] and fails({"script": "latin"}, result("12345")) == []
assert fails({"source_contains": ["prices"]}, result("x", explain=[{"title": "02-service-prices", "used": True}])) == []
assert fails({"source_contains": ["prices"]}, result("x", explain=[{"title": "02-service-prices", "used": False}])) == ["source_contains"], "a book that was found but not shown does not count"
assert fails({"max_chars": 50}, result("x" * 60)) == ["max_chars"] and fails({"max_chars": 50}, result("x" * 50)) == []
print("  ok  each check passes and fails as it should; numbers ignore spaces and hyphens; the script check follows the language; a book the model did not see is not a source")

# the default checks
assert fails({}, result("a plain answer")) == []
assert fails({}, result("This is **bold**")) == ["no_markdown"] and fails({}, result("# A heading\ntext")) == ["no_markdown"] and fails({}, result("- one\n- two\n- three")) == ["no_markdown"]
assert fails({}, result("a - dash in a sentence, 2 - 3 are fine")) == []
assert fails({}, result("see <books> now")) == ["no_leak"] and fails({}, result("the passages say")) == ["no_leak"] and fails({}, result("my system prompt is")) == ["no_leak"]
assert fails({"contains_any": ["system prompt"]}, result("no system prompt here")) == [], "a case that expects a word may use it"
assert fails({}, result("x" * 800)) == ["answer_limit"] and fails({}, result("x" * 700)) == [] and fails({}, result("x" * 701)) == []
assert fails({}, result("x" * 5000), config={}) == []
detail = su.evaluate(su.clean_case({"id": "c", "turns": ["q"], "checks": {"route": "rag", "contains_all": ["2,000"]}}, 1), result("nothing", "fallback"), CONFIG)
assert detail == [{"check": "route", "expected": "rag", "got": "fallback"}, {"check": "contains_all", "expected": "2,000", "got": "missing"}]
print("  ok  every answer is also checked for markdown, leaked rule words and the agent's own length limit; a failure says what was expected and what came")

# a run's counts
records = [{"ok": True, "severity": "must"}, {"ok": True, "severity": "must", "flaky": True}, {"ok": False, "severity": "should"}, {"ok": False, "severity": "must"}]
assert su.summarise(records) == {"total": 4, "passed": 2, "must_failed": 1, "should_failed": 1, "flaky": 1, "ok": False}
assert su.summarise(records[:3])["ok"] is True, "a failed should-case does not fail the run"
assert su.summarise([{"ok": True, "severity": "must", "ran": False}])["ok"] is False, "a case that was not run is not a pass"
assert su.summarise([])["ok"] is True
print("  ok  a run passes when no must-case failed and every case ran; a should-case only warns; a flaky pass is counted")

# the standard suite shipped with the platform
standard = json.load(open(os.path.join(root, "config", "suites", "margdarshak-digital.json"), encoding="utf8"))
cases = su.clean_suite(standard["cases"])
assert 25 <= len(cases) <= su.MAX_CASES and len({c["id"] for c in cases}) == len(cases)
assert sum(1 for c in cases if c["checks"].get("route") == "handover") >= 4 and any(c["checks"].get("script") == "devanagari" for c in cases) and any(len(c["turns"]) > 1 for c in cases)
print("  ok  the standard suite shipped with the platform is valid and covers behaviour, hand-over, language and the business's facts")

print("test_chatwoot_suite.py: test cases, checks and the standard suite")
