"""The orchestrator (plan O1, lambda/chatwoot_orchestrator.py and chatwoot_api.orchestrate): one number, several departments. The rules (menu, a reply to the menu, a keyword,
the department the conversation is in), a department answering with its own live version, the orchestrator's own books for what belongs to the whole business, the menu when
nothing is sure, the guard for the whole number with the department's team, and a department that cannot answer handed to a person. Runs on in-memory fakes."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_api as api  # noqa: E402
import chatwoot_memory as mem  # noqa: E402
import chatwoot_orchestrator as orch  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_runtime as rt  # noqa: E402

api.log = lambda what, **details: None
admin.log = lambda what, **details: None
api.BASE_URL = "https://chat.example.org"
api.AUTH_STYLE = "bearer"
NOW = 1_800_000_000

DEPARTMENTS = [
    {"id": "library", "label": "Library", "agent": "lib", "team_id": 4, "description": "Reading room", "keywords": ["library", "seat", "लाइब्रेरी"], "examples": ["seat milegi?"]},
    {"id": "classes", "label": "Digital classes", "agent": "cls", "team_id": None, "keywords": ["class", "batch"]},
]
ORCH = {"instructions": "", "language": "hinglish", "collections": ["general"], "fallback_text": "Sorry.", "departments": DEPARTMENTS, "handover_text": "Team aapse baat karegi."}
LIB = {"instructions": "Library desk.", "language": "hinglish", "collections": ["library"], "fallback_text": "Library: pata nahi.", "handover_text": "Library team aapse baat karegi."}
CLS = {"instructions": "Classes desk.", "language": "hinglish", "collections": ["classes"], "fallback_text": "Classes: pata nahi."}

# ---- the pure rules
cfg = rt.clean_config(ORCH)
assert [d["id"] for d in cfg["departments"]] == ["library", "classes"] and cfg["sticky_minutes"] == orch.STICKY_MINUTES and cfg["menu_prompt"] == orch.DEFAULT_MENU_PROMPT
assert orch.menu_text(cfg).endswith("1. Library\n2. Digital classes")
for bad in ([], [DEPARTMENTS[0]], [DEPARTMENTS[0], DEPARTMENTS[0]], [DEPARTMENTS[0], {**DEPARTMENTS[1], "agent": "lib"}], [DEPARTMENTS[0], {**DEPARTMENTS[1], "id": "Bad Id"}],
            [DEPARTMENTS[0], {**DEPARTMENTS[1], "team_id": "4"}], [DEPARTMENTS[0], {**DEPARTMENTS[1], "extra": 1}], [DEPARTMENTS[0], {**DEPARTMENTS[1], "label": ""}],
            [DEPARTMENTS[0], {**DEPARTMENTS[1], "keywords": "class"}]):
    try:
        rt.clean_config({**ORCH, "departments": bad})
        raise AssertionError(bad)
    except rt.ConfigError:
        pass
for bad in (-1, 1441, True, "30"):
    try:
        rt.clean_config({**ORCH, "sticky_minutes": bad})
        raise AssertionError(bad)
    except rt.ConfigError:
        pass
assert "departments" not in rt.clean_config(LIB), "an ordinary agent is unchanged"


def where(text, history=None, now=NOW):
    got = orch.route(text, cfg, history or [], now)
    return got["kind"], (got["department"] or {}).get("id"), got["layer"]


assert where("Menu") == ("menu", None, "menu_word") and where(" main  menu! ") == ("menu", None, "menu_word")
assert where("1") == ("department", "library", "menu_number") and where("2") == ("department", "classes", "menu_number") and where("3")[0] == "none" and where("0")[0] == "none"
assert where("digital classes") == ("department", "classes", "menu_label") and where("library")[1] == "library"
assert where("Library ki seat kitne ki hai?") == ("department", "library", "keyword") and where("लाइब्रेरी कब खुलती है") == ("department", "library", "keyword")
assert where("class aur library dono ka time?") == ("menu", None, "keyword_tie"), "two departments named: ask"
assert where("classroom") [0] == "none", "a keyword is a whole word, not a piece of one"
recent = [{"c": "1", "b": "...", "d": "library", "t": NOW - 60}]
assert where("aur fees?", recent) == ("department", "library", "sticky")
assert where("aur fees?", [{**recent[0], "t": NOW - 31 * 60}])[0] == "none", "a department chosen long ago does not hold"
assert where("batch timing?", recent) == ("department", "classes", "keyword"), "a clear keyword moves the conversation"
assert orch.current(recent, NOW, 0) is None, "sticky_minutes 0: never stay"
print("  ok  the rules: menu words, a reply to the menu by number or name, one keyword (whole words, Devanagari too), a tie asks, a recent department holds, an old one does not")

# memory keeps the department and its time; DynamoDB numbers come back as Decimal
store = mem.MemoryOnly()
if mem.enabled():
    mem.remember(store, "front", 7, "1", "Library ...", False, NOW, department="library")
    turn = mem.load(store, "front", 7, NOW + 1)[-1]
    assert turn["d"] == "library" and turn["t"] == NOW
from decimal import Decimal  # noqa: E402
assert mem.clean([{"c": "a", "b": "b", "d": "library", "t": Decimal(NOW)}])[0]["t"] == NOW and "d" not in mem.clean([{"c": "a", "b": "b", "d": "Bad Id!"}])[0]
print("  ok  memory keeps the department and the time of a turn, and reads DynamoDB's numbers back")


# ---- the worker, end to end on fakes
class Fakes:
    def __init__(self):
        self.store = api.MemoryStore()
        self.requests, self.llm_calls, self.searches = [], [], []
        self.answer = "Library 8 baje khulti hai."
        self.confident = {"library": True, "classes": True, "general": False}
        self.runtime = rt.make_runtime(self.search, self.ask)
        self.mem = mem.MemoryOnly()
        self.deps = api.Deps(self.store, api.Secrets(self.read, api.credentials_of), http=self.post, registry=reg.Registry(self.store, ttl=0), runtime=self.runtime, memory=self.mem)
        tenant = {"tenant_id": "acme", "enabled": True, "key_hash": "x", "agents": ["front", "lib", "cls"], "rev": 1}
        self.store.claim(reg.tenant_key("acme"), tenant)
        self.store.claim(reg.agent_key("front"), {"tenant_id": "acme", "agent_id": "front", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 1, "rev": 1, "type": "orchestrator"})
        self.store.claim(reg.version_key("front", 1), {"agent_id": "front", "tenant_id": "acme", "version": 1, "config": ORCH})
        for name, config in (("lib", LIB), ("cls", CLS)):
            self.store.claim(reg.agent_key(name), {"tenant_id": "acme", "agent_id": name, "account_id": 1, "inbox_ids": [], "enabled": True, "live_version": 1, "versions": 1, "rev": 1, "type": "department"})
            self.store.claim(reg.version_key(name, 1), {"agent_id": name, "tenant_id": "acme", "version": 1, "config": config})

    def read(self, name):
        return json.dumps({"bot_secret": "s", "api_token": "test-only-token-abc123XYZ", "account_id": 1}) if name.endswith("/front") else None

    def search(self, tenant, collections, query):
        self.searches.append(list(collections))
        shelf = collections[0]
        hit = {"doc": shelf, "title": shelf, "page": 1, "page_end": 1, "section": "", "text": f"{shelf} facts", "vector": 0.9, "collection": shelf}
        return {"hits": [hit], "confident": self.confident.get(shelf, False), "tokens": 1, "cost_micro": 1, "missing": []}

    def ask(self, messages, max_tokens):
        self.llm_calls.append(messages)
        return {"text": self.answer, "model": "m", "tokens": 5, "cost_micro": 10}

    def post(self, url, headers, body, timeout):
        self.requests.append((url, json.loads(body.decode("utf8"))))
        return 200, b'{"id": 77}'


MESSAGE = 100


def send(f, text, now=NOW):
    global MESSAGE
    MESSAGE += 1
    fields = {"message_id": MESSAGE, "conversation_id": 7, "account_id": 1, "inbox_id": 3}
    f.store.claim(f"msg#front#{MESSAGE}", {**fields, "agent_id": "front", "status": "received", "received_at": now, "ttl": now + 86400})
    out = api.worker({"agent_id": "front", "delivery_id": "d", "text": text, "channel": "whatsapp", "attachments": [], **fields}, None, now=now, deps=f.deps)
    return out, f.store.items[f"msg#front#{MESSAGE}"]


f = Fakes()
out, item = send(f, "Namaste")
assert out == {"replied": True} and item["route"] == "menu" and f.requests[-1][1]["content"].endswith("2. Digital classes") and not f.llm_calls, "a greeting the general books cannot answer gets the menu, no model call"
out, item = send(f, "1", NOW + 5)
assert item["route"] == "rag" and item["department"] == "library" and item["route_layer"] == "menu_number" and f.searches[-1] == ["library"] and "Library desk." in f.llm_calls[-1][0]["content"]
assert f.requests[-1][1]["content"] == "Library 8 baje khulti hai."
out, item = send(f, "aur fees kitni hai?", NOW + 30)
assert item["department"] == "library" and item["route_layer"] == "sticky", "a follow-up stays with the library"
out, item = send(f, "batch kab start hoga?", NOW + 60)
assert item["department"] == "classes" and f.searches[-1] == ["classes"], "a clear keyword moves to classes"
out, item = send(f, "menu", NOW + 70)
assert item["route"] == "menu"
f.confident["general"] = True
f.answer = "Hum Darbhanga mein hain."
out, item = send(f, "aap kahan ho?", NOW + 3000)
assert item["department"] == "general" and item["route_layer"] == "general" and f.searches[-1] == ["general"], "what belongs to the whole business: the orchestrator's own books"
print("  ok  the worker: the menu for a greeting, a department by number, a follow-up stays, a keyword moves, 'menu' brings it back, the general books answer the rest")

# the guard for the whole number: a person asked for in the library goes to the library's team with a note naming it
f = Fakes()
send(f, "1")
before = len(f.requests)
out, item = send(f, "mujhe kisi insaan se baat karni hai", NOW + 10)
calls = [url.rsplit("/", 1)[-1] for url, _ in f.requests[before:]]
assert item["handover"] == "person_asked" and item.get("handover_team") == 1 and "assignments" in calls and "toggle_status" in calls
body = dict(f.requests[before:])[[u for u, _ in f.requests[before:] if u.endswith("/assignments")][0]]
assert body == {"team_id": 4}
note = [b["content"] for u, b in f.requests[before:] if b.get("private")][0]
assert note.startswith("Department: library.")
print("  ok  the guard hands over for the whole number; in a department, its Chatwoot team gets the conversation and the note names the department")

# a department that cannot answer: the customer is handed to a person, never left in silence
f = Fakes()
f.store.items[reg.agent_key("lib")]["enabled"] = False
out, item = send(f, "library ki seat?")
assert item["handover"] == "department_unavailable" and item.get("department") == "library"
f = Fakes()
f.store.items[reg.agent_key("lib")]["type"] = "agent"
assert send(f, "1")[1]["handover"] == "department_unavailable", "only an agent of type department can answer for an orchestrator"
print("  ok  a department that is off, not a department or not set up hands the customer to a person")

# the test chat (dry run) of an orchestrator says which department answered and why
f = Fakes()
got = api.worker({"kind": "test", "tenant_id": "acme", "agent_id": "front", "text": "2", "version": 1}, None, now=NOW, deps=f.deps)["result"]
assert got["department"] == "classes" and got["route_layer"] == "menu_number"
print("  ok  the test chat of an orchestrator shows the department and the rule that chose it")

# ---- publish: an orchestrator must name department agents of the same business
astore = api.MemoryStore()
key = "acme-key"
admin_tenant = {"tenant_id": "acme", "enabled": True, "agents": ["front", "lib"], "rev": 1}
astore.claim(reg.tenant_key("acme"), admin_tenant)
astore.claim(reg.agent_key("front"), {"tenant_id": "acme", "agent_id": "front", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 0, "versions": 0, "rev": 1})
astore.claim(reg.agent_key("lib"), {"tenant_id": "acme", "agent_id": "lib", "account_id": 1, "inbox_ids": [], "enabled": True, "live_version": 0, "versions": 0, "rev": 1, "type": "department"})
astore.claim(reg.agent_key("cls"), {"tenant_id": "other", "agent_id": "cls", "account_id": 1, "inbox_ids": [], "enabled": True, "live_version": 0, "versions": 0, "rev": 1, "type": "department"})
try:
    admin.put_version(astore, admin_tenant, "front", 1, {"config": ORCH}, NOW)
    raise AssertionError("cls belongs to another business")
except reg.Invalid as error:
    assert "cls" in str(error)
astore.items[reg.agent_key("cls")]["tenant_id"] = "acme"
assert admin.put_version(astore, admin_tenant, "front", 1, {"config": ORCH}, NOW)[0] == 201
try:
    admin.put_version(astore, admin_tenant, "lib", 1, {"config": ORCH}, NOW)
    raise AssertionError("a department lists no departments")
except reg.Invalid:
    pass
assert reg.clean_agent({"account_id": 1, "type": "department"}, creating=True)["inbox_ids"] == []
for bad in ({"account_id": 1, "type": "department", "inbox_ids": [3]}, {"account_id": 1}):
    try:
        reg.clean_agent(bad, creating=True)
        raise AssertionError(bad)
    except reg.Invalid:
        pass
print("  ok  publishing: an orchestrator names department agents of its own business; a department has no inbox and lists no departments")
