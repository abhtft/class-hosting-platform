"""The owner's side of the Chatwoot traces (plan TR2 and TR4 of docs/notes/TR0-TRACEABILITY-AUDIT.md): the list of an agent's answers with its filters and paging, one trace with its
steps, the summary of the last days per day and per version, the isolation between businesses and between agents, the answers when traces are off, the teacher's route for a
business's trace, and the command that removes a business's traces."""
import base64
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))
sys.path.insert(0, os.path.join(root, "tools"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import chatwoot_store as cs  # noqa: E402
import chatwoot_trace as ct  # noqa: E402
import traces as tr  # noqa: E402

admin.log = lambda what, **details: None
NOW = 1_800_000_000
DAY = 86400
tstore = tr.MemoryTraces()
store = cs.MemoryStore()


def make_tenant(tenant_id, agents):
    key, key_hash = reg.new_service_key(tenant_id)
    store.claim(reg.tenant_key(tenant_id), {"tenant_id": tenant_id, "name": tenant_id, "key_hash": key_hash, "enabled": True, "agents": list(agents), "rev": 1, "created_at": NOW})
    for name in agents:
        store.claim(reg.agent_key(name), {"tenant_id": tenant_id, "agent_id": name, "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 1, "versions": 2, "rev": 1})
    return key


KEYS = {"acme": make_tenant("acme", ["shop", "help"]), "beta": make_tenant("beta", ["shop2"])}


def call(path, key="acme", method="GET", query=None, traces_store="default"):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"Authorization": "Bearer " + KEYS[key]}, "body": "", "isBase64Encoded": False, "queryStringParameters": query}
    kwargs = {} if traces_store == "default" else {"trace_store": traces_store}
    out = admin.handler(event, None, now=NOW, store=store, **kwargs)
    return out["statusCode"], json.loads(out["body"])


def add(tenant, agent, n, route="rag", version=1, at=None, errors=0, cost=10):
    """One stored trace the way the worker writes it: three steps and a summary."""
    started_s = at if at is not None else NOW - n * 60
    outcome = {"status": "replied" if not errors else "failed", "reason": "network" if errors else "", "metrics": {"route": route, "version": version, "tokens": 5, "cost_micro": cost, "llm_ms": 40, "retrieval_ms": 9, "hits": 1,
               "confident": True, "shelves": ["faq"], "channel": "whatsapp", "via": "X"}}
    event = {"agent_id": agent, "message_id": 1000 + n, "conversation_id": 7, "text": f"question {n}"}
    explain = [{"title": "Prices", "page": 2, "section": "", "score": 0.91, "used": True}]
    return ct.record(tstore, event, tenant, outcome, {"text": f"answer {n}", "explain": explain}, started_s, started_ms=started_s * 1000)


ids = [add("acme", "shop", n) for n in range(1, 8)]
add("acme", "shop", 8, route="fallback", version=2)
add("acme", "shop", 9, route="handover", version=2)
add("acme", "shop", 10, route="rag", version=2, errors=1)
add("acme", "help", 11)
other = add("beta", "shop2", 12)
add("acme", "shop", 13, at=NOW - 3 * DAY, route="fallback")
assert all(ids), "the fixtures were written"

# ---- the list
status, page = call("/admin/agents/shop/traces", traces_store=tstore)
assert status == 200
shop_all = page["traces"]
assert [t["agent"] for t in shop_all] == ["shop"] * len(shop_all) and len(shop_all) == 11, "only this agent's answers, newest first"
assert shop_all == sorted(shop_all, key=lambda t: -t["started"])
assert {"trace_id", "started", "route", "version", "status", "tokens", "cost_micro", "llm_ms", "message_id", "conversation_id"} <= set(shop_all[0]) and "text" not in json.dumps(shop_all) and "question" not in json.dumps(shop_all), "the list holds no customer text"
status, only = call("/admin/agents/shop/traces", query={"route": "fallback"}, traces_store=tstore)
assert [t["route"] for t in only["traces"]] == ["fallback", "fallback"] and status == 200
status, small = call("/admin/agents/shop/traces", query={"limit": "3"}, traces_store=tstore)
assert len(small["traces"]) == 3 and small["next_before"] == small["traces"][-1]["started"], "a full page says where the next one starts"
status, nxt = call("/admin/agents/shop/traces", query={"limit": "3", "before": str(small["next_before"])}, traces_store=tstore)
assert not ({t["trace_id"] for t in small["traces"]} & {t["trace_id"] for t in nxt["traces"]}) and nxt["traces"][0]["started"] < small["traces"][-1]["started"], "the next page continues without repeats"
day = tr.day_id(NOW)
status, today = call("/admin/agents/shop/traces", query={"day": day}, traces_store=tstore)
assert len(today["traces"]) == 10 and all(t["day"] == day for t in today["traces"]), "one day only"
for bad in ({"day": "yesterday"}, {"day": "2026-13-45"}, {"route": "weird"}, {"limit": "x"}, {"before": "-1"}, {"colour": "red"}):
    assert call("/admin/agents/shop/traces", query=bad, traces_store=tstore)[0] == 400, bad
print("  ok  the list shows only this agent's answers newest first, with no customer text, filters by route and day, pages without repeats and refuses odd filters")

# ---- one trace
status, one = call(f"/admin/agents/shop/traces/{shop_all[0]['trace_id']}", traces_store=tstore)
assert status == 200 and one["trace"]["trace_id"] == shop_all[0]["trace_id"] and [s["name"] for s in one["spans"]] == ["retrieval", "llm", "answer"], one
assert one["spans"][0]["attrs"]["hits"][0]["score"] == 0.91 and one["spans"][0]["attrs"]["query"].startswith("question") and "slug" not in json.dumps(one) and '"ttl"' not in json.dumps(one)
assert call(f"/admin/agents/help/traces/{shop_all[0]['trace_id']}", traces_store=tstore)[0] == 404, "another agent's trace id is not found under this agent"
assert call(f"/admin/agents/shop2/traces/{other}", key="acme", traces_store=tstore)[0] == 404, "another business's agent is not found"
assert call(f"/admin/agents/shop/traces/{other}", key="acme", traces_store=tstore)[0] == 404, "another business's trace id is not found, even under the right agent name"
assert call(f"/admin/agents/shop2/traces/{other}", key="beta", traces_store=tstore)[0] == 200, "its own business reads it"
assert call("/admin/agents/shop/traces/tr_nothing", traces_store=tstore)[0] == 404 and call("/admin/agents/nope/traces", traces_store=tstore)[0] == 404
assert call("/admin/agents/shop/traces", method="POST", traces_store=tstore)[0] == 405 and call(f"/admin/agents/shop/traces/{ids[0]}", method="DELETE", traces_store=tstore)[0] == 405, "read only"
print("  ok  one trace with its steps; another agent's, another business's or an unknown id is the same 404; the API is read only")

# ---- the summary
status, s = call("/admin/agents/shop/traces/summary", traces_store=tstore)
assert status == 200 and s["total"]["answers"] == 11 and s["total"]["fallback"] == 2 and s["total"]["handover"] == 1 and s["total"]["errors"] == 1 and s["truncated"] is False, s
assert s["per_version"]["1"]["answers"] == 8 and s["per_version"]["2"]["answers"] == 3 and s["per_version"]["2"]["fallback_rate"] == round(1 / 3, 3) and s["per_version"]["2"]["handover_rate"] == round(1 / 3, 3)
assert [d["day"] for d in s["per_day"]] == sorted((d["day"] for d in s["per_day"]), reverse=True) and sum(d["answers"] for d in s["per_day"]) == 11
status, narrow = call("/admin/agents/shop/traces/summary", query={"days": "1"}, traces_store=tstore)
assert narrow["total"]["answers"] == 10, "the answer three days ago is outside one day"
assert call("/admin/agents/shop/traces/summary", query={"days": "x"}, traces_store=tstore)[0] == 400
ct.SCAN_MAX = 4
status, cut = call("/admin/agents/shop/traces/summary", traces_store=tstore)
assert cut["truncated"] is True and cut["total"]["answers"] <= 4, "a scan that hit its limit says so"
status, cutlist = call("/admin/agents/shop/traces", query={"route": "fallback"}, traces_store=tstore)
assert cutlist["truncated"] is True
ct.SCAN_MAX = 2000
print("  ok  the summary counts answers, fallbacks, hand-overs and errors per day and per version with rates, honours the day range and says when the scan limit stopped it")

# ---- traces off, and the teacher's route
os.environ["TRACES_ENABLED"] = ""
status, off = call("/admin/agents/shop/traces")
assert status == 503 and off["error"] == "traces_off", "with traces off the owner is told so"
print("  ok  with traces switched off the routes answer 503 traces_off")

# ---- deleting a business's traces (the teacher's command)
removed = ct.delete_all(tstore, "acme", agent="help")
assert removed == 1 and call("/admin/agents/help/traces", traces_store=tstore)[1]["traces"] == []
assert len(call("/admin/agents/shop/traces", traces_store=tstore)[1]["traces"]) == 11
removed = ct.delete_all(tstore, "acme")
assert removed == 11 and call("/admin/agents/shop/traces", traces_store=tstore)[1]["traces"] == []
assert call(f"/admin/agents/shop2/traces/{other}", key="beta", traces_store=tstore)[0] == 200, "another business's traces are untouched"
assert not [k for k in tstore.rows if k[0].startswith("t#cw:acme#")], "no step or summary is left"
print("  ok  deleting a business's traces (or one agent's) removes every step and summary and touches no other business")

# ---- the teacher's command
import chatwoot_ctl as ctl  # noqa: E402
tstore2 = tr.MemoryTraces()
for n in range(3):
    ct.record(tstore2, {"agent_id": "shop", "message_id": n + 1, "conversation_id": 1, "text": "q"}, "acme", {"status": "replied", "metrics": {"route": "rag"}}, {"text": "a"}, NOW + n)
ct.record(tstore2, {"agent_id": "help", "message_id": 9, "conversation_id": 1, "text": "q"}, "acme", {"status": "replied", "metrics": {"route": "rag"}}, {"text": "a"}, NOW)
LINES = []
assert ctl.main(["delete-traces", "acme", "--agent", "shop"], out=LINES.append, traces_store=tstore2) == 0 and "3 trace(s) of agent shop of acme would be removed" in LINES[-1] and len([k for k in tstore2.rows if k[1] == "summary"]) == 4, "without --yes nothing is removed"
assert ctl.main(["delete-traces", "acme", "--agent", "shop", "--yes"], out=LINES.append, traces_store=tstore2) == 0 and "Removed 3 trace(s)" in LINES[-1] and len([k for k in tstore2.rows if k[1] == "summary"]) == 1
assert ctl.main(["delete-traces", "acme"], out=LINES.append, traces_store=tstore2) == 0 and "would be removed" in LINES[-1]
os.environ.pop("TRACES_TABLE", None)
assert ctl.main(["delete-traces", "acme"], out=LINES.append) == 2 and "--traces-table" in LINES[-1], "no table named: a plain message, nothing happens"
print("  ok  delete-traces counts first and removes only with --yes, for one agent or the whole business")
