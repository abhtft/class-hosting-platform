"""Repeatability of an agent's answers: pure rules (no AWS, no model judge), used by `chatwoot_ctl.py suite-repeat`.

Repeatability = the same question, asked of the same version, gets the same kind of answer every time. A few questions are asked a few times each, every time as its own
conversation. The machine only gives clear signals and puts the real answers in front of a person, who decides: the set is small enough to read in a minute.

Signals per question:
  same        every answered run took the same route and passed the same way (the wording may differ)
  differs     the runs took different routes, or some passed and some failed: real instability
  unexpected  every answered run failed the same way: consistent, so a matter of quality or of an out-of-date expectation, not of repeatability
  no answer   too few runs got an answer (a refused model, an unreachable worker): says nothing about the agent
A run is {"answered": bool, "passed": bool, "route": str | None, "text": str, "via": str | None, "failed": [{"check", "expected", "got"}], "spent_micro": int}.
"""
import collections
import re

SAME, DIFFERS, UNEXPECTED, NO_ANSWER = "same", "differs", "unexpected", "no answer"
MIN_ANSWERED_SHARE = 0.5  # fewer answered runs than this share of the runs: no answer
MAJORITY_MIN = 0.6        # when fewer than this share of a question's answered runs agree (route and result), the check says plainly that it is unstable (exit code 1)
SHOWN_CHARS = 220         # how much of an answer the printed table shows


def normalise(text):
    """An answer without case, punctuation and spacing differences, to count how many different answers came back."""
    return re.sub(r"[\W_]+", " ", str(text or "").lower()).strip()


def answers_of(runs):
    """The different answers of the answered runs: [{"text", "route", "passed", "count", "via"}], the most common first. Case and punctuation do not make an answer different."""
    groups = {}
    for r in runs:
        if not r["answered"]:
            continue
        group = groups.setdefault(normalise(r["text"]), {"text": r["text"], "route": r["route"], "passed": r["passed"], "count": 0, "via": set()})
        group["count"] += 1
        if r.get("via"):
            group["via"].add(r["via"])
    return [{**g, "via": sorted(g["via"])} for g in sorted(groups.values(), key=lambda g: -g["count"])]


def summarise_case(case, runs):
    """One question's numbers and signal."""
    answered = [r for r in runs if r["answered"]]
    base = {"id": case["id"], "severity": case.get("severity", "must"), "runs": len(runs), "answered": len(answered), "no_answer": len(runs) - len(answered), "answers": answers_of(runs),
            "vias": sorted({r["via"] for r in runs if r.get("via")})}
    if len(answered) < max(1, MIN_ANSWERED_SHARE * len(runs)):
        return {**base, "signal": NO_ANSWER, "agree": 0.0, "failures": []}
    shape = collections.Counter((r["route"], r["passed"]) for r in answered)
    agree = shape.most_common(1)[0][1] / len(answered)
    failures = collections.Counter(f"{f['check']}: wanted {f['expected']}, got {f['got']}" for r in answered for f in r["failed"])
    if len(shape) > 1:
        signal = DIFFERS
    elif not answered[0]["passed"]:
        signal = UNEXPECTED
    else:
        signal = SAME
    return {**base, "signal": signal, "agree": agree, "failures": [f"{text} ({n}x)" for text, n in failures.most_common(3)]}


def judge(summaries):
    """(label, exit code, notes). Exit 0 unless a question is plainly unstable (exit 1: fewer than MAJORITY_MIN of its answered runs agree) or not one question got an answer (exit 2)."""
    if not summaries or all(s["signal"] == NO_ANSWER for s in summaries):
        return "INCONCLUSIVE", 2, ["No question got an answer: the model may be refused or the worker unreachable. Ask again later."]
    notes = []
    for s in summaries:
        if s["signal"] == DIFFERS:
            notes.append(f"{s['id']}: the answers differ ({round(s['agree'] * 100)}% of the runs agree): read them")
        elif s["signal"] == UNEXPECTED:
            notes.append(f"{s['id']}: the same check failed every time: a matter of quality or an out-of-date case, not of repeatability")
        elif s["signal"] == NO_ANSWER:
            notes.append(f"{s['id']}: too few answers ({s['answered']} of {s['runs']}), ask again later")
    if any(s["signal"] != NO_ANSWER and s["agree"] < MAJORITY_MIN for s in summaries):
        return "UNSTABLE", 1, notes
    if any(s["signal"] == DIFFERS for s in summaries):
        return "NEEDS A LOOK", 0, notes
    return "LOOKS CONSISTENT", 0, notes


def render(summaries, version, times, spent_micro, escalated):
    """The lines the command prints: per question its signal and its different answers, so a person can read them."""
    head = f"Repeatability of version {version}: {len(summaries)} questions, each asked {times} times"
    lines = [head + (f" ({', '.join(escalated)} asked more)" if escalated else "")]
    for s in summaries:
        extra = f", {s['no_answer']} with no answer (provider)" if s["no_answer"] and s["signal"] != NO_ANSWER else ""
        lines.append(f"  {s['signal']:10} {s['id']}: {s['answered']} answered, {len(s['answers'])} different answer{'' if len(s['answers']) == 1 else 's'}{extra}")
        lines += [f"             {a['count']}x [{a['route']}{'' if a['passed'] else ', check failed'}{', ' + ', '.join(a['via']) if a['via'] else ''}] {a['text'][:SHOWN_CHARS].strip()!r}" for a in s["answers"]]
        lines += [f"             {text}" for text in s["failures"]]
    lines.append(f"cost about {spent_micro / 10000:.2f} cent")
    return lines


def sheet(summaries, version, times, label):
    """A review sheet in Markdown for a person: every question, its signal, and every different answer with how often it came."""
    lines = [f"# Repeatability review: version {version}", "", f"{len(summaries)} questions, each asked {times} times. Result: **{label}**.", "",
             "A machine only marks what is clear (the route, the facts the case needs). Read the answers and decide.", ""]
    for s in summaries:
        lines += [f"## {s['id']} ({s['severity']}): {s['signal']}", ""]
        for a in s["answers"]:
            lines += [f"- **{a['count']}x**, route `{a['route']}`{'' if a['passed'] else ', **a check failed**'}{(', ' + ', '.join(a['via'])) if a['via'] else ''}", f"  > {a['text'].strip()}"]
        lines += [f"- check: {text}" for text in s["failures"]]
        lines += [f"- {s['no_answer']} run(s) got no answer from the provider" if s["no_answer"] else "", ""]
    return "\n".join(line for line in lines if line is not None) + "\n"
