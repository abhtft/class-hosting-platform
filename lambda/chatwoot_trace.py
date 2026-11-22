"""Traces of the Chatwoot agents (plan TR1 of docs/notes/TR0-TRACEABILITY-AUDIT.md): one trace per customer message, in the same table and format as the student traces.

A business's traces belong to the owner key `cw:<tenant>`. The colon cannot appear in a student's slug, so no student can ever read them. The worker hands over what it already
has (the message, the outcome, the passages that were found, the reply) and `record` writes one summary and a few spans in ONE batch, after the reply was sent. It never raises
and never waits for anything but one table: a trace that cannot be written is logged by the caller and the customer is not affected.

Text (the customer's words, the reply) is kept only when `chatwoot.traceText` is 1, and then masked (phone numbers, Aadhaar, PAN, UPI and the like are replaced); with 0 only numbers
and labels are stored. Every item expires after `chatwoot.traceRetentionDays`.
"""
import time

import platform_settings
import trace_core as tc
import traces

OWNER_PREFIX = tc.BOT_PREFIX
RETENTION_DAYS = platform_settings.value("chatwoot.traceRetentionDays")
KEEP_TEXT = platform_settings.value("chatwoot.traceText")
LEVEL = "masked" if KEEP_TEXT else "metadata"
DAY, MS = traces.DAY, traces.MS


def owner_of(tenant_id):
    return OWNER_PREFIX + str(tenant_id)


def is_bot_owner(app):
    return isinstance(app, str) and app.startswith(OWNER_PREFIX)


def used_llm(metrics):
    """The model was asked for this answer (a book answer or an agent answer; a fallback and a hand-over never ask it)."""
    return bool(metrics.get("llm_ms") or metrics.get("via") or metrics.get("route") in ("rag", "agent"))


def build_spans(event, outcome, data, started_ms):
    """[(name, kind, duration_ms, status, attrs)] for one message, from what the worker knows. Numbers and labels first; text keys are reduced by the privacy level when written."""
    metrics = outcome.get("metrics") or {}
    passages = [{"title": p.get("title"), "page": p.get("page"), "section": p.get("section"), "score": p.get("score"), "used": bool(p.get("used"))} for p in (data.get("explain") or [])[:20]]
    spans = [("retrieval", "retriever", int(metrics.get("retrieval_ms") or 0), "ok",
              {"query": event.get("text") or "", "shelves": metrics.get("shelves") or [], "hit_count": int(metrics.get("hits") or 0), "confident": bool(metrics.get("confident")),
               "corrections_used": int(metrics.get("updates") or 0), "hits": passages})]
    if used_llm(metrics):
        spans.append(("llm", "llm", int(metrics.get("llm_ms") or 0), "ok" if outcome.get("status") == "replied" else "error",
                      {"provider": metrics.get("via") or "", "tokens": int(metrics.get("tokens") or 0), "cost_micro": int(metrics.get("cost_micro") or 0), "answer": data.get("text") or ""}))
    spans.append(("answer", "guard", 0, "ok" if outcome.get("status") == "replied" else "error",
                  {"route": metrics.get("route") or "none", "handover_reason": metrics.get("handover") or "", "status": outcome.get("status") or "", "reason_code": outcome.get("reason") or "",
                   **({} if used_llm(metrics) else {"text": data.get("text") or ""})}))
    if metrics.get("media"):
        spans.append(("media", "tool", int(metrics.get("media_ms") or 0), "ok", {"count": len(metrics["media"]), "together": bool(metrics.get("media_together"))}))
    return spans


def record(store, event, tenant_id, outcome, data, now, started_ms=None):
    """Write the trace of one message and return its id, or None when nothing was written. `store` is a traces store (traces.store() or a MemoryTraces). Never raises."""
    try:
        metrics = outcome.get("metrics") or {}
        owner = owner_of(tenant_id)
        trace_id = tc.new_trace_id()
        pk = traces.pk_of(owner, trace_id)
        expires = int(now) + RETENTION_DAYS * DAY
        ts_ms = int(started_ms if started_ms is not None else int(now) * MS)
        rows = []
        for number, (name, kind, dur, status, attrs) in enumerate(build_spans(event, outcome, data, ts_ms)):
            span = tc.make_span(name, kind, "platform", ts_ms + number, dur, status, attrs, LEVEL)
            rows.append((tc.span_sort_key(ts_ms + number, "platform"), {**span, "slug": owner, "ttl": expires}))
        store.put_many(pk, rows)
        route = metrics.get("route") or "none"
        once = {"slug": owner, "app": owner, "day": traces.day_id(int(now)), "started": ts_ms, "level": LEVEL, "ttl": expires, "agent": str(event.get("agent_id") or ""), "route": route,
                "version": int(metrics.get("version") or 0), "channel": str(metrics.get("channel") or ""), "status": str(outcome.get("status") or ""), "reason": str(outcome.get("reason") or ""),
                "message_id": int(event.get("message_id") or 0), "conversation_id": int(event.get("conversation_id") or 0)}
        adds = {"spans": len(rows), "tokens": int(metrics.get("tokens") or 0), "cost_micro": int(metrics.get("cost_micro") or 0), "llm_ms": int(metrics.get("llm_ms") or 0),
                "llm_calls": 1 if used_llm(metrics) else 0}
        if outcome.get("status") != "replied":
            adds["errors"] = 1
        if route == "fallback":
            adds["refused"] = 1
        store.upsert_summary(pk, once, adds)
        return trace_id
    except Exception:  # tracing never breaks an answer
        return None
