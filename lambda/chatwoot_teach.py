"""Teach sessions: a business owner's client talks to an agent for an hour or two, asks it questions, marks its answers right or wrong and types the right answer. Everything is
recorded so the owner (and later a distilling step) can turn it into better knowledge and instructions. Nothing here reaches a customer: a session only talks to a dry run of a
published version or a draft, and what the client says never changes a version by itself.

This module holds the pure rules (no AWS): the shape of a session, what a turn may contain, when a session is closed, what is shown to the client and what only to the owner.
A session is ONE item (`teach#<agent>#<sid>`): its settings, the turns, the facts the client confirmed. The admin function reads and writes it; the worker never sees it.

Session:  {"sid", "tenant_id", "agent_id", "status": "open" | "finished" | "revoked", "created_at", "expires_at", "cap_micro", "spent_micro", "client_name", "consent_at",
           "base": {"version": n} | {"config": {...}}, "turns": [...], "facts": [{"n", "text"}], "rev", "ttl"}
Turn:     {"n", "t", "kind": "ask", "q", "a", "route", "src": [book titles], "spent"}   a question and the agent's answer
          {"n", "t", "kind": "feedback", "of": n, "verdict": "right" | "wrong", "correction"}   the client's verdict on answer n
          {"n", "t", "kind": "fact", "text"}                                  something the client tells the agent
"""
import re
import secrets

import chatwoot_registry as reg
import platform_settings

DEFAULT_HOURS = platform_settings.value("chatwoot.teachDefaultHours")
MAX_HOURS = platform_settings.maximum("chatwoot.teachDefaultHours")
MAX_TURNS = platform_settings.value("chatwoot.teachMaxTurns")
TEXT_CHARS = platform_settings.value("chatwoot.teachTextChars")
DEFAULT_CAP_USD = platform_settings.value("chatwoot.teachCapUsd")
MAX_CAP_USD = platform_settings.maximum("chatwoot.teachCapUsd")
RETENTION_DAYS = platform_settings.value("chatwoot.teachRetentionDays")
MAX_FACTS = platform_settings.value("chatwoot.teachFacts")
MAX_SESSIONS = platform_settings.value("chatwoot.teachSessionsKept")
QUESTIONS_PER_TOPIC = platform_settings.value("chatwoot.teachQuestionsPerTopic")
TOPIC_IDS = ("services", "prices", "hours", "location", "policies", "faq", "tone", "never_say", "handover", "extra")  # the interviewer's checklist (chatwoot_interview.TOPICS)
HISTORY_TURNS = platform_settings.value("chatwoot.memoryTurns")
MICRO = 1_000_000
SID = re.compile(r"[0-9a-f]{12}")
NAME_MAX = 60
VERDICTS = ("right", "wrong")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


Invalid = reg.Invalid  # the same error the admin function turns into a 400 with the message


def new_sid():
    return secrets.token_hex(6)


def _text(value, field, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value.strip()) > TEXT_CHARS:
        raise Invalid(f"{field} must be text of 1 to {TEXT_CHARS} characters.")
    return value.strip()


def create(tenant_id, agent_id, sid, body, base, now):
    """A new open session. `body`: hours?, cap_usd?, client_name?; `base` (the version or draft it talks to) was chosen by the caller."""
    unknown = sorted(set(body) - {"hours", "cap_usd", "client_name", "version", "config"})
    if unknown:
        raise Invalid(f"Unknown field: {unknown[0]}.")
    hours = body.get("hours", DEFAULT_HOURS)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0.25 <= hours <= MAX_HOURS:
        raise Invalid(f"hours must be a number from 0.25 to {MAX_HOURS}.")
    cap = body.get("cap_usd", DEFAULT_CAP_USD)
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0.1 <= cap <= MAX_CAP_USD:
        raise Invalid(f"cap_usd must be a number from 0.1 to {MAX_CAP_USD}.")
    name = body.get("client_name", "")
    if not isinstance(name, str) or len(name.strip()) > NAME_MAX:
        raise Invalid(f"client_name must be text of at most {NAME_MAX} characters.")
    expires = now + int(hours * 3600)
    return {"sid": sid, "tenant_id": tenant_id, "agent_id": agent_id, "status": "open", "created_at": now, "expires_at": expires, "cap_micro": round(cap * MICRO), "spent_micro": 0,
            "client_name": name.strip(), "consent_at": None, "base": base, "turns": [], "facts": [], "counts": {}, "summary": None, "rev": 1, "ttl": expires + RETENTION_DAYS * 86400}


def closed_reason(session, now):
    """None while the session takes turns, else a code: revoked, finished, expired, turn_limit or cap_reached."""
    if session["status"] == "revoked":
        return "revoked"
    if session["status"] == "finished":
        return "finished"
    if now >= session["expires_at"]:
        return "expired"
    if len(session["turns"]) >= MAX_TURNS:
        return "turn_limit"
    if session["spent_micro"] >= session["cap_micro"]:
        return "cap_reached"
    return None


def clean_turn(body):
    """A turn the client (or the owner) sends: {"kind": "ask", "text"} | {"kind": "fact", "text"} | {"kind": "feedback", "of", "verdict", "correction"?}."""
    if not isinstance(body, dict):
        raise Invalid("Send a JSON object.")
    kind = body.get("kind")
    allowed = {"ask": {"kind", "text"}, "fact": {"kind", "text"}, "feedback": {"kind", "of", "verdict", "correction"}, "answer": {"kind", "of", "text"}}.get(kind)
    if allowed is None:
        raise Invalid("kind must be ask, fact, feedback or answer.")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise Invalid(f"Unknown field: {unknown[0]}.")
    if kind in ("ask", "fact"):
        return {"kind": kind, "text": _text(body.get("text"), "text")}
    number = body.get("of")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise Invalid("of must be the number of the answer you are judging." if kind == "feedback" else "of must be the number of the question you are answering.")
    if kind == "answer":
        return {"kind": "answer", "of": number, "text": _text(body.get("text"), "text")}
    if body.get("verdict") not in VERDICTS:
        raise Invalid("verdict must be right or wrong.")
    return {"kind": "feedback", "of": number, "verdict": body["verdict"], "correction": _text(body.get("correction"), "correction", required=False)}


def answers(session):
    return {t["n"]: t for t in session["turns"] if t["kind"] == "ask"}


def questions(session):
    return {t["n"]: t for t in session["turns"] if t["kind"] == "iq"}


def pending_question(session):
    """The interviewer's question the client has not answered yet, or None."""
    answered = {t["of"] for t in session["turns"] if t["kind"] == "ia"}
    open_ones = [t for t in session["turns"] if t["kind"] == "iq" and t["n"] not in answered]
    return open_ones[-1] if open_ones else None


def next_topic(session):
    """The first topic of the checklist that has had fewer than QUESTIONS_PER_TOPIC answers, or None when the interview is complete."""
    counts = session.get("counts", {})
    return next((t for t in TOPIC_IDS if counts.get(t, 0) < QUESTIONS_PER_TOPIC), None)


def add_question(session, question, topic, spent, now):
    """The session with the interviewer's question added (one open question at a time)."""
    if topic not in TOPIC_IDS:
        raise Invalid("The interviewer asked about an unknown topic.")
    turn = {"n": len(session["turns"]) + 1, "t": now, "kind": "iq", "q": question, "topic": topic, "spent": int(spent or 0)}
    return {**session, "turns": session["turns"] + [turn], "spent_micro": session["spent_micro"] + turn["spent"], "rev": session["rev"] + 1}


def set_summary(session, text, spent, now):
    """The session with 'what I learned' added for the client to confirm."""
    turn = {"n": len(session["turns"]) + 1, "t": now, "kind": "summary", "text": text, "spent": int(spent or 0)}
    return {**session, "turns": session["turns"] + [turn], "summary": {"text": text, "at": now, "confirmed": None}, "spent_micro": session["spent_micro"] + turn["spent"], "rev": session["rev"] + 1}


def confirm_summary(session, ok, note, now):
    """The client's verdict on the summary: yes, or no with what is wrong (kept as a fact too)."""
    summary = session.get("summary")
    if not summary or summary.get("confirmed") is not None:
        raise Invalid("There is no summary waiting for an answer.")
    if not isinstance(ok, bool):
        raise Invalid("ok must be true or false.")
    note = _text(note, "note", required=False)
    turn = {"n": len(session["turns"]) + 1, "t": now, "kind": "confirm", "ok": ok, "text": note}
    new_facts = list(session["facts"])
    if not ok and note:
        new_facts.append({"n": turn["n"], "text": f"The client said the summary was not right: {note}"[:TEXT_CHARS * 2]})
    return {**session, "turns": session["turns"] + [turn], "facts": new_facts[-MAX_FACTS:], "summary": {**summary, "confirmed": ok}, "rev": session["rev"] + 1}


def summary_state(session):
    """None (no summary yet), "waiting" (the client has not answered), True (confirmed) or False (the client said it is not right)."""
    summary = session.get("summary")
    if not summary:
        return None
    return "waiting" if summary.get("confirmed") is None else summary["confirmed"]


def notes(session):
    """What the summary is written from: every answered interview question and every fact the client told (as plain sentences)."""
    return [f["text"] for f in session["facts"]]


def recent_talk(session, limit=6):
    """The last few turns as short plain lines for the interviewer (the client's words are data to it)."""
    lines = []
    for t in session["turns"]:
        if t["kind"] == "ask":
            lines.append(f"Client asked the assistant: {t['q']}")
        elif t["kind"] == "iq":
            lines.append(f"Interviewer asked: {t['q']}")
        elif t["kind"] == "ia":
            lines.append(f"Client answered: {t['text']}")
        elif t["kind"] == "fact":
            lines.append(f"Client told the assistant: {t['text']}")
    return [line[:TEXT_CHARS] for line in lines[-limit:]]


def history(session):
    """The earlier questions and answers as the worker's memory format ([{"c", "b"}]), the last few only."""
    return [{"c": t["q"], "b": t["a"]} for t in session["turns"] if t["kind"] == "ask"][-HISTORY_TURNS:]


def facts(session):
    """What the client confirmed, as plain sentences for the agent to treat as true inside this session."""
    return [f["text"] for f in session["facts"]][-MAX_FACTS:]


def clean_facts(value):
    """The worker's side of the same list: at most MAX_FACTS short texts, anything else ignored."""
    if not isinstance(value, list):
        return []
    return [f.strip()[:TEXT_CHARS * 2] for f in value if isinstance(f, str) and f.strip()][:MAX_FACTS]


def add_turn(session, event, now, answer=None):
    """The session with the turn added (a new dict; `rev` moves on). `answer` is the worker's result for an ask. Raises Invalid for a verdict on an answer that does not exist."""
    turns = list(session["turns"])
    number = len(turns) + 1
    new_facts = list(session["facts"])
    spent = session["spent_micro"]
    if event["kind"] == "ask":
        turn = {"n": number, "t": now, "kind": "ask", "q": event["text"], "a": str(answer.get("text") or "")[:TEXT_CHARS * 4], "route": answer.get("route"),
                "src": [str(e.get("title"))[:80] for e in (answer.get("explain") or []) if e.get("used")][:4], "spent": int(answer.get("spent_micro") or 0)}
        spent += turn["spent"]
    elif event["kind"] == "fact":
        turn = {"n": number, "t": now, "kind": "fact", "text": event["text"]}
        new_facts.append({"n": number, "text": event["text"]})
    elif event["kind"] == "answer":
        asked = questions(session).get(event["of"])
        if asked is None or any(t["kind"] == "ia" and t["of"] == event["of"] for t in session["turns"]):
            raise Invalid("There is no such open question to answer.")
        turn = {"n": number, "t": now, "kind": "ia", "of": event["of"], "text": event["text"]}
        new_facts.append({"n": number, "text": f"Asked: {asked['q']} The client answered: {event['text']}"[:TEXT_CHARS * 2]})
        counts = {**session.get("counts", {}), asked["topic"]: session.get("counts", {}).get(asked["topic"], 0) + 1}
        return {**session, "turns": turns + [turn], "facts": new_facts[-MAX_FACTS:], "counts": counts, "rev": session["rev"] + 1}
    else:
        target = answers(session).get(event["of"])
        if target is None:
            raise Invalid("There is no such answer to judge.")
        turn = {"n": number, "t": now, "kind": "feedback", "of": event["of"], "verdict": event["verdict"], "correction": event["correction"]}
        if event["verdict"] == "wrong" and event["correction"]:
            new_facts.append({"n": number, "text": f"When a customer asks \"{target['q']}\", the right answer is: {event['correction']}"[:TEXT_CHARS * 2]})
    return {**session, "turns": turns + [turn], "facts": new_facts[-MAX_FACTS:], "spent_micro": spent, "rev": session["rev"] + 1}


def view(session, now, owner):
    """What a caller may see. The client gets the conversation and the time left; the owner also gets the books each answer used, the cost, the facts and the limits."""
    turns = []
    for t in session["turns"]:
        shown = {k: v for k, v in t.items() if k not in ("src", "spent", "route")}
        if owner:
            shown.update({k: t[k] for k in ("src", "spent", "route") if k in t})
        turns.append(shown)
    out = {"sid": session["sid"], "agent": session["agent_id"], "status": session["status"], "closed": closed_reason(session, now), "client_name": session["client_name"],
           "consent_at": session["consent_at"], "created_at": session["created_at"], "expires_at": session["expires_at"], "seconds_left": max(session["expires_at"] - now, 0),
           "turns": turns, "summary": summary_state(session),
           "interview": {"open_question": (pending_question(session) or {}).get("n"), "topics_left": sum(1 for t in TOPIC_IDS if session.get("counts", {}).get(t, 0) < QUESTIONS_PER_TOPIC)}}
    if owner:
        out.update(base=session["base"] if "version" in session["base"] else {"config": "a draft"}, cap_usd=session["cap_micro"] / MICRO, spent_usd=session["spent_micro"] / MICRO,
                   facts=session["facts"])
    return out


def row(session, now):
    """One line of the owner's list of sessions."""
    return {"sid": session["sid"], "status": session["status"], "closed": closed_reason(session, now), "client_name": session["client_name"], "created_at": session["created_at"],
            "expires_at": session["expires_at"], "questions": sum(1 for t in session["turns"] if t["kind"] == "ask"), "answers": sum(1 for t in session["turns"] if t["kind"] == "ia"),
            "summary": summary_state(session), "corrections": sum(1 for t in session["turns"] if t["kind"] == "feedback" and t["verdict"] == "wrong"),
            "spent_usd": session["spent_micro"] / MICRO}


def test_case(session, number):
    """A suite case from answer `number` and the client's correction of it: the question, and the numbers the client wrote (the one check a machine can make without a model).
    Returns (case, note) or raises Invalid."""
    ask = answers(session).get(number)
    if ask is None:
        raise Invalid("There is no such answer.")
    fixes = [t for t in session["turns"] if t["kind"] == "feedback" and t["of"] == number and t["verdict"] == "wrong" and t["correction"]]
    if not fixes:
        raise Invalid("The client has not corrected this answer.")
    numbers = [n.rstrip(",.") for n in _NUMBER.findall(fixes[-1]["correction"])][:5]
    checks = {"contains_all": numbers} if numbers else {}
    case = {"id": f"teach-{session['sid']}-{number}", "title": ("Client: " + ask["q"])[:100], "severity": "should", "turns": [ask["q"]], "checks": checks}
    note = "Checks the numbers in the client's correction: " + ", ".join(numbers) + "." if numbers else "The correction has no number, so only the default checks apply: read the client's words and add a check."
    return case, note
