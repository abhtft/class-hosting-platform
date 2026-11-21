"""Repeatability of an agent's answers: pure rules (no AWS), used by `chatwoot_ctl.py suite-repeat`.

Repeatability = the same question, asked of the same version, gets an acceptable answer every time. A case is asked several times, each time as its own conversation, and the
runs are combined here. A run that got no answer because the provider refused (the worker could not make an answer) is `no_answer`: it says nothing about the agent, so it is
counted apart and never as a failure.

  summarise_case(case, runs)   one case's numbers and verdict
  judge(summaries)             the verdict of the whole check and its exit code
  render(...)                  the plain-text table
A run is {"answered": bool, "passed": bool, "route": str | None, "text": str, "via": str | None, "failed": [{"check", "expected", "got"}], "spent_micro": int}.
"""
import collections
import re

STABLE_PASS, FLAKY, STABLE_FAIL, INCONCLUSIVE = "stable-pass", "flaky", "stable-fail", "inconclusive"
SHOULD_SHARE = 0.8      # a `should` case may pass this share of its answered runs and still count as stable
SET_SHARE = 0.9         # the share of cases that must be stable-pass for the whole check to pass (in a set of SET_MIN cases or more)
SET_MIN = 10            # a smaller set is judged case by case: only its must-cases decide
MIN_ANSWERED_SHARE = 0.5  # fewer answered runs than this share of the runs: inconclusive


def normalise(text):
    """An answer without case, punctuation and spacing differences, to count how many different answers came back."""
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def summarise_case(case, runs):
    """{"id", "severity", "runs", "answered", "passed", "no_answer", "verdict", "distinct", "routes", "vias", "failures"} for one case."""
    answered = [r for r in runs if r["answered"]]
    passed = [r for r in answered if r["passed"]]
    severity = case.get("severity", "must")
    if len(answered) < max(1, MIN_ANSWERED_SHARE * len(runs)):
        verdict = INCONCLUSIVE
    elif not passed:
        verdict = STABLE_FAIL
    elif len(passed) == len(answered) or (severity == "should" and len(passed) >= SHOULD_SHARE * len(answered)):
        verdict = STABLE_PASS
    else:
        verdict = FLAKY
    failures = collections.Counter(f"{f['check']}: wanted {f['expected']}, got {f['got']}" for r in answered for f in r["failed"])
    return {"id": case["id"], "severity": severity, "runs": len(runs), "answered": len(answered), "passed": len(passed), "no_answer": len(runs) - len(answered), "verdict": verdict,
            "distinct": len({normalise(r["text"]) for r in answered}), "routes": dict(collections.Counter(r["route"] for r in answered)),
            "vias": sorted({r["via"] for r in runs if r.get("via")}), "failures": [f"{text} ({n}x)" for text, n in failures.most_common(3)]}


def judge(summaries):
    """(ok, exit code, reasons). A `must` case that is not stable-pass fails the check; so does a set with fewer than SET_SHARE of its cases stable-pass; a case without enough
    answers makes the result inconclusive (exit code 2) when nothing else failed."""
    reasons, inconclusive = [], False
    for s in summaries:
        if s["verdict"] == INCONCLUSIVE:
            inconclusive = True
            reasons.append(f"{s['id']}: too few answers ({s['answered']} of {s['runs']}), ask again later")
        elif s["verdict"] != STABLE_PASS and s["severity"] == "must":
            reasons.append(f"{s['id']}: {s['verdict']} ({s['passed']} of {s['answered']} answered runs passed)")
    stable = sum(1 for s in summaries if s["verdict"] == STABLE_PASS)
    if len(summaries) >= SET_MIN and stable < SET_SHARE * len(summaries):
        reasons.append(f"only {stable} of {len(summaries)} cases are stable-pass")
    failed = any(not r.endswith("ask again later") for r in reasons)
    if failed:
        return False, 1, reasons
    return (not inconclusive), (2 if inconclusive else 0), reasons


def render(summaries, version, times, spent_micro, escalated):
    """The table the command prints."""
    lines = [f"Repeatability of version {version}: {len(summaries)} questions, each asked {times} times ({', '.join(escalated)} asked more)" if escalated else
             f"Repeatability of version {version}: {len(summaries)} questions, each asked {times} times"]
    for s in summaries:
        line = f"  {s['verdict']:12} {s['severity']:6} {s['id']:28} {s['passed']}/{s['answered']} passed, {s['distinct']} different answer{'s' if s['distinct'] != 1 else ''}, routes {s['routes']}"
        if s["no_answer"]:
            line += f", {s['no_answer']} with no answer (provider)"
        if s["vias"]:
            line += f", via {', '.join(s['vias'])}"
        lines.append(line)
        lines += [f"                 {text}" for text in s["failures"]]
    stable = sum(1 for s in summaries if s["verdict"] == STABLE_PASS)
    lines.append(f"{stable} of {len(summaries)} stable-pass; cost about {spent_micro / 10000:.2f} cent")
    return lines
