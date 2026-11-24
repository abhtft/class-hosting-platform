"""The orchestrator (plan O1 of docs/notes/ORCHESTRATOR-PLAN.md): one WhatsApp number, several departments. Pure rules: no AWS, no network, no model.

An orchestrator is an ordinary agent whose version also holds `departments`: each one names another agent of the same business (of type `department`, with no inbox of its own)
that answers for it. For every message the worker (chatwoot_api.orchestrate) runs, cheapest first:
  1. the guard (the hand-over rules of chatwoot_handover, for the whole number);
  2. the rules of `route` below: "menu", a reply to the menu (a number or a department's name), a department's keyword (only when exactly one department matches);
  3. sticky: the department the conversation was in, while it is recent (`sticky_minutes`);
  4. the orchestrator's own books ("general": address, owner, what services exist); when they are not sure, the menu. Never a guess.
Later phases add a meaning match and a small model between 3 and 4 (O2).
"""
import re

import chatwoot_core as core
import platform_settings

MAX_DEPARTMENTS = platform_settings.value("chatwoot.orchestratorMaxDepartments")
STICKY_MINUTES = platform_settings.value("chatwoot.orchestratorStickyMinutes")  # the default when a version does not set its own
MENU_WORDS = ("menu", "main menu", "options", "start", "मेनू")
DEFAULT_MENU_PROMPT = "Aap kis baare mein poochh rahe hain? Number likh kar bhejiye:"
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,29}")
_WORDS = re.compile(r"[^\wऀ-ॿ]+")


class Invalid(ValueError):
    pass


def _text(value, name, limit, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise Invalid(f"{name} must be text of at most {limit} characters" + ("" if not required else " (required)") + ".")
    return value.strip()


def _list(value, name, most, each):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > most or not all(isinstance(v, str) and v.strip() and len(v) <= each for v in value):
        raise Invalid(f"{name} must be a list of at most {most} texts of at most {each} characters each.")
    return list(dict.fromkeys(v.strip() for v in value))


def clean(config):
    """The orchestrator part of a version, checked: {"departments", "menu_prompt", "sticky_minutes"}, or Invalid. Called only when the version has `departments`."""
    found = config.get("departments")
    if not isinstance(found, list) or not 2 <= len(found) <= MAX_DEPARTMENTS:
        raise Invalid(f"departments must list 2 to {MAX_DEPARTMENTS} departments.")
    out, ids, agents = [], set(), set()
    for d in found:
        if not isinstance(d, dict) or sorted(set(d) - {"id", "label", "agent", "team_id", "description", "keywords", "examples"}):
            raise Invalid("Each department is {id, label, agent, team_id, description, keywords, examples} and nothing else.")
        if not isinstance(d.get("id"), str) or not _ID.fullmatch(d["id"]) or d["id"] in ids:
            raise Invalid("Each department needs a different id: lowercase letters, digits, _ and - (up to 30).")
        if not isinstance(d.get("agent"), str) or not core.AGENT_ID.fullmatch(d["agent"]) or d["agent"] in agents:
            raise Invalid(f"Department {d['id']}: agent must name a different department agent of this business.")
        team = d.get("team_id")
        if team is not None and (not isinstance(team, int) or isinstance(team, bool) or team < 1):
            raise Invalid(f"Department {d['id']}: team_id is the Chatwoot team number (or left out).")
        ids.add(d["id"])
        agents.add(d["agent"])
        out.append({"id": d["id"], "label": _text(d.get("label"), f"Department {d['id']}: label", 40), "agent": d["agent"], "team_id": team,
                    "description": _text(d.get("description"), f"Department {d['id']}: description", 300, required=False),
                    "keywords": _list(d.get("keywords"), f"Department {d['id']}: keywords", 30, 40), "examples": _list(d.get("examples"), f"Department {d['id']}: examples", 30, 200)})
    sticky = config.get("sticky_minutes", STICKY_MINUTES)
    if not isinstance(sticky, int) or isinstance(sticky, bool) or not 0 <= sticky <= 24 * 60:
        raise Invalid("sticky_minutes must be a whole number from 0 (never stay) to 1440.")
    prompt = _text(config.get("menu_prompt"), "menu_prompt", 300, required=False) or DEFAULT_MENU_PROMPT
    return {"departments": out, "menu_prompt": prompt, "sticky_minutes": sticky}


def menu_text(config):
    """The menu the customer gets: the prompt and one numbered line per department."""
    return config["menu_prompt"] + "\n" + "\n".join(f"{n}. {d['label']}" for n, d in enumerate(config["departments"], 1))


def normalise(text):
    """Lowercase words separated by single spaces (Devanagari kept), for matching."""
    return " ".join(_WORDS.sub(" ", str(text or "").lower()).split())


def current(history, now, minutes):
    """The department the conversation is in: the department of the last turn, when that turn is recent enough; else None."""
    if not history or not minutes:
        return None
    last = history[-1]
    when = last.get("t")
    if not last.get("d") or not isinstance(when, int) or now - when > minutes * 60:
        return None
    return last["d"]


def route(text, config, history, now):
    """Where this message goes, by the rules only: {"kind": "menu" | "department" | "none", "department": the department dict or None, "layer": a short word for the trace}."""
    said = normalise(text)
    departments = config["departments"]
    if said in MENU_WORDS:
        return {"kind": "menu", "department": None, "layer": "menu_word"}
    if said.isdigit() and 1 <= int(said) <= len(departments):  # a reply to the menu
        return {"kind": "department", "department": departments[int(said) - 1], "layer": "menu_number"}
    for d in departments:
        if said == normalise(d["label"]) or said == d["id"]:
            return {"kind": "department", "department": d, "layer": "menu_label"}
    padded = f" {said} "
    matched = [d for d in departments if any(f" {normalise(k)} " in padded for k in d["keywords"] if normalise(k))]
    if len(matched) == 1:
        return {"kind": "department", "department": matched[0], "layer": "keyword"}
    if len(matched) > 1:  # two departments named in one message: ask, never guess
        return {"kind": "menu", "department": None, "layer": "keyword_tie"}
    stay = current(history, now, config["sticky_minutes"])
    if stay:
        for d in departments:
            if d["id"] == stay:
                return {"kind": "department", "department": d, "layer": "sticky"}
    return {"kind": "none", "department": None, "layer": "none"}
