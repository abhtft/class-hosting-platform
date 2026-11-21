"""The test suite of an agent (pure rules: no AWS, no network, no model).

A suite is a list of cases an operator writes (or loads from a standard file). A case is a question, or a short conversation of up to a few customer messages, asked as if on a
channel, plus deterministic checks on the LAST answer. The worker (chatwoot_api.py) runs the cases with the same answer logic a customer gets and uses `evaluate` here to judge each one.

A case:
  {"id": "price-sm", "title": "Social media price", "severity": "must" | "should", "channel": "whatsapp", "inbox_id": 16,
   "turns": ["social media management ka kitna lagega?"],                 (1 to chatwoot.suiteMaxTurns customer messages; the last answer is checked)
   "checks": {"route": "rag" | "fallback" | "handover" | "agent", "handover_reason": "person_asked", "contains_all": ["2,000"], "contains_any": ["3,000", "Rs 3000"],
              "must_not_contain": ["PWNED"], "script": "devanagari" | "latin", "source_contains": ["prices"], "max_chars": 700, "media_ids": ["office_video"], "no_media": true}}

Every case also gets the DEFAULT checks: no markdown, no leaked rule words, and not longer than the agent's own limit. Text is compared case-insensitively, and a number written with
different spaces or hyphens ("91892 25257" and "9189225257") is the same number. Nothing here uses a second model: an LLM judge would itself drift.
"""
import json
import re

import chatwoot_core as core
import platform_settings

MAX_CASES = platform_settings.value("chatwoot.suiteMaxCases")
MAX_TURNS = platform_settings.value("chatwoot.suiteMaxTurns")
EXPECTED_CHARS = platform_settings.value("chatwoot.suiteExpectedChars")
SEVERITIES = ("must", "should")
ROUTES = ("rag", "fallback", "handover", "agent")
SCRIPTS = ("devanagari", "latin")
LIST_CHECKS = ("contains_all", "contains_any", "must_not_contain", "source_contains")
CHECK_NAMES = ("route", "handover_reason", "script", "max_chars", "media_ids", "no_media") + LIST_CHECKS
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_CHANNEL = re.compile(r"[a-z_]{1,40}")
_MARKDOWN = re.compile(r"\*\*|__|^\s{0,3}#{1,6}\s|```|^\s*[-*]\s+\S.*\n\s*[-*]\s+\S", re.MULTILINE)
LEAKS = ("<books>", "</books>", "<customer>", "</customer>", "passages", "system prompt")
MAX_SUITE_BYTES = platform_settings.kb("chatwoot.maxVersionKB")


class Invalid(ValueError):
    """The suite or a case is not usable; the text says why, in words for the operator."""


def _text_list(value, field, case_id):
    if not isinstance(value, list) or not 1 <= len(value) <= 10 or not all(isinstance(v, str) and 1 <= len(v.strip()) <= 100 for v in value):
        raise Invalid(f"Case {case_id}: {field} must be a list of 1 to 10 texts of at most 100 characters.")
    return [v.strip() for v in value]


def clean_case(case, position):
    """One case, checked and tidied; Invalid says what is wrong."""
    if not isinstance(case, dict):
        raise Invalid(f"Case {position}: a case is a JSON object.")
    unknown = sorted(set(case) - {"id", "title", "severity", "channel", "inbox_id", "turns", "checks", "expected"})
    if unknown:
        raise Invalid(f"Case {position}: unknown field {unknown[0]}.")
    case_id = case.get("id")
    if not isinstance(case_id, str) or not _ID.fullmatch(case_id):
        raise Invalid(f"Case {position}: id must be lowercase letters, digits and - (up to 40), for example price-sm.")
    title = case.get("title", case_id)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise Invalid(f"Case {case_id}: title must be text of at most 100 characters.")
    severity = case.get("severity", "must")
    if severity not in SEVERITIES:
        raise Invalid(f"Case {case_id}: severity must be must or should.")
    channel = case.get("channel", "whatsapp")
    if not isinstance(channel, str) or not _CHANNEL.fullmatch(channel):
        raise Invalid(f"Case {case_id}: channel is a short lowercase word such as whatsapp or web_widget.")
    out = {"id": case_id, "title": title.strip(), "severity": severity, "channel": channel}
    if "inbox_id" in case:
        inbox = case["inbox_id"]
        if not isinstance(inbox, int) or isinstance(inbox, bool) or inbox < 1:
            raise Invalid(f"Case {case_id}: inbox_id must be a whole number above zero.")
        out["inbox_id"] = inbox
    turns = case.get("turns")
    if not isinstance(turns, list) or not 1 <= len(turns) <= MAX_TURNS or not all(isinstance(t, str) and t.strip() and len(t) <= core.MAX_QUESTION_CHARS for t in turns):
        raise Invalid(f"Case {case_id}: turns must be 1 to {MAX_TURNS} customer messages of at most {core.MAX_QUESTION_CHARS} characters.")
    out["turns"] = [t.strip() for t in turns]
    if "expected" in case:  # the answer a good agent gives, in the owner's words: shown to people and compared in meaning (advice only); the checks below decide pass or fail
        expected = case["expected"]
        if not isinstance(expected, str) or not 1 <= len(expected.strip()) <= EXPECTED_CHARS:
            raise Invalid(f"Case {case_id}: expected must be text of at most {EXPECTED_CHARS} characters.")
        out["expected"] = expected.strip()
    checks = case.get("checks", {})
    if not isinstance(checks, dict):
        raise Invalid(f"Case {case_id}: checks is a JSON object.")
    unknown = sorted(set(checks) - set(CHECK_NAMES))
    if unknown:
        raise Invalid(f"Case {case_id}: unknown check {unknown[0]}. The checks are: {', '.join(CHECK_NAMES)}.")
    clean = {}
    if "route" in checks:
        if checks["route"] not in ROUTES:
            raise Invalid(f"Case {case_id}: route must be one of {', '.join(ROUTES)}.")
        clean["route"] = checks["route"]
    if "handover_reason" in checks:
        if not isinstance(checks["handover_reason"], str) or not re.fullmatch(r"[a-z_]{1,30}", checks["handover_reason"]):
            raise Invalid(f"Case {case_id}: handover_reason is a short lowercase word such as person_asked.")
        clean["handover_reason"] = checks["handover_reason"]
    if "script" in checks:
        if checks["script"] not in SCRIPTS:
            raise Invalid(f"Case {case_id}: script must be devanagari or latin.")
        clean["script"] = checks["script"]
    if "max_chars" in checks:
        if not isinstance(checks["max_chars"], int) or isinstance(checks["max_chars"], bool) or not 20 <= checks["max_chars"] <= 4000:
            raise Invalid(f"Case {case_id}: max_chars must be a whole number from 20 to 4000.")
        clean["max_chars"] = checks["max_chars"]
    for name in LIST_CHECKS:
        if name in checks:
            clean[name] = _text_list(checks[name], name, case_id)
    if "media_ids" in checks:
        ids = checks["media_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 5 or not all(isinstance(i, str) and re.fullmatch(r"[a-z0-9_]{1,24}", i) for i in ids):
            raise Invalid(f"Case {case_id}: media_ids must list 1 to 5 media ids (lowercase letters, digits and _).")
        clean["media_ids"] = list(ids)
    if "no_media" in checks:
        if checks["no_media"] is not True:
            raise Invalid(f"Case {case_id}: no_media can only be true (no media must be sent).")
        clean["no_media"] = True
    out["checks"] = clean
    return out


def clean_suite(cases):
    """The cases of a suite, checked: a list of at most MAX_CASES cases with different ids, small enough to store."""
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise Invalid(f"A suite has 1 to {MAX_CASES} cases.")
    out, seen = [], set()
    for position, case in enumerate(cases, 1):
        item = clean_case(case, position)
        if item["id"] in seen:
            raise Invalid(f"Two cases have the id {item['id']}.")
        seen.add(item["id"])
        out.append(item)
    if len(json.dumps(out, ensure_ascii=False).encode("utf8")) > MAX_SUITE_BYTES:
        raise Invalid(f"The suite is larger than {MAX_SUITE_BYTES // 1024} KB.")
    return out


# ---------- judging an answer ----------

def _flat(text):
    """Lowercase, and without the spaces and hyphens that split a number: so "91892 25257", "91892-25257" and "9189225257" are the same."""
    return re.sub(r"[\s\- ]+", "", str(text).lower())


def has(text, needle):
    return needle.lower() in str(text).lower() or _flat(needle) in _flat(text)


def script_share(text):
    """The share of the letters of `text` that are Devanagari (0 when there are no letters)."""
    letters = [c for c in str(text) if c.isalpha()]
    return sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters) if letters else 0.0


def evaluate(case, result, config):
    """The failed checks of one case on its last answer: a list of {"check", "expected", "got"} (empty = the case passed). `result` is the worker's dry-run answer (route, text,
    reason, explain...), `config` the agent's configuration (for its answer-length limit). The case's own checks come first, then the default ones."""
    text = str(result.get("text") or "")
    checks = case.get("checks") or {}
    failed = []

    def fail(name, expected, got):
        failed.append({"check": name, "expected": str(expected)[:120], "got": str(got)[:120]})
    if "route" in checks and result.get("route") != checks["route"]:
        fail("route", checks["route"], result.get("route"))
    if "handover_reason" in checks and result.get("reason") != checks["handover_reason"]:
        fail("handover_reason", checks["handover_reason"], result.get("reason") or "no hand-over")
    for needle in checks.get("contains_all", []):
        if not has(text, needle):
            fail("contains_all", needle, "missing")
    if checks.get("contains_any") and not any(has(text, needle) for needle in checks["contains_any"]):
        fail("contains_any", " | ".join(checks["contains_any"]), "none of them")
    for needle in checks.get("must_not_contain", []):
        if has(text, needle):
            fail("must_not_contain", needle, "present")
    if "script" in checks:
        share = script_share(text)
        if (checks["script"] == "devanagari" and share < 0.3) or (checks["script"] == "latin" and share > 0.05):
            fail("script", checks["script"], f"{round(share * 100)}% Devanagari")
    if checks.get("source_contains"):
        used = [str(e.get("title", "")) for e in result.get("explain") or [] if e.get("used")]
        for needle in checks["source_contains"]:
            if not any(needle.lower() in title.lower() for title in used):
                fail("source_contains", needle, ", ".join(used) or "no book used")
    if "max_chars" in checks and len(text) > checks["max_chars"]:
        fail("max_chars", checks["max_chars"], len(text))
    sent = [str(m.get("id")) for m in result.get("media") or [] if isinstance(m, dict)]
    for wanted in checks.get("media_ids", []):
        if wanted not in sent:
            fail("media_ids", wanted, ", ".join(sent) or "no media sent")
    if checks.get("no_media") and sent:
        fail("no_media", "no media", ", ".join(sent))
    # the default checks of every case
    if _MARKDOWN.search(text):
        fail("no_markdown", "plain chat text", "markdown found")
    for word in LEAKS:
        if word in text.lower() and word not in " ".join(checks.get("contains_any", []) + checks.get("contains_all", [])).lower():
            fail("no_leak", f"no {word}", "present")
    limit = (config or {}).get("max_answer_chars")
    if isinstance(limit, int) and len(text) > limit + 1:
        fail("answer_limit", limit, len(text))
    return failed


def summarise(results):
    """Counts for a finished run: the cases that passed, failed (must and should apart) and were flaky (failed once, passed on the retry)."""
    must_failed = sum(1 for r in results if not r.get("ok") and r.get("severity") == "must")
    should_failed = sum(1 for r in results if not r.get("ok") and r.get("severity") == "should")
    return {"total": len(results), "passed": sum(1 for r in results if r.get("ok")), "must_failed": must_failed, "should_failed": should_failed, "flaky": sum(1 for r in results if r.get("flaky")),
            "ok": must_failed == 0 and all(r.get("ran", True) for r in results)}
