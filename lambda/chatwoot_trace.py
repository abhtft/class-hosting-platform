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
LIST_MAX = platform_settings.value("chatwoot.traceListMax")
SCAN_MAX = platform_settings.value("chatwoot.traceScanMax")
SUMMARY_DAYS = platform_settings.value("chatwoot.traceSummaryDays")
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


# ---------------------------------------------------------------- reading (the owner's side: one business, never another)
def default_store():
    """The traces store for the admin function, or None when traces are not switched on (the routes then say so)."""
    return traces.store() if traces.available() else None


def _plain(value):
    """DynamoDB hands back Decimal numbers: whole ones become int, others float."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if hasattr(value, "as_tuple"):
        return int(value) if value == int(value) else float(value)
    return value


def summary_view(row):
    row = _plain(row)
    keep = ("agent", "version", "route", "status", "reason", "channel", "message_id", "conversation_id", "tokens", "cost_micro", "llm_ms", "spans", "errors", "refused", "day", "level")
    out = {k: row[k] for k in keep if k in row}
    out["trace_id"] = str(row["s"]).split("#", 2)[2]
    out["started"] = int(row.get("started", 0))
    return out


def _day_range(day):
    import datetime
    start = int(datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()) * MS
    return start, start + DAY * MS


def _scan(store, tenant_id, agent, before, floor_ms):
    """Summaries of this business's agent, newest first, from `before` down to `floor_ms`, looking at no more than SCAN_MAX rows. Yields (row, False); yields (None, True) when the limit stopped it."""
    owner, looked = owner_of(tenant_id), 0
    while True:
        want = min(LIST_MAX, SCAN_MAX - looked)
        if want <= 0:
            yield None, True
            return
        rows = store.summaries(app=owner, limit=want, before=before)
        for row in rows:
            started = int(row.get("started", 0))
            if started < floor_ms:
                return
            looked += 1
            before = started
            if row.get("agent") == agent:
                yield row, False
        if len(rows) < want:
            return


def list_traces(store, tenant_id, agent, day=None, route=None, limit=None, before=None):
    """{"traces": [summary views, newest first], "next_before": ms or None, "truncated": bool}: one page of this agent's answers, optionally of one day and one route."""
    limit = max(1, min(int(limit or LIST_MAX), LIST_MAX))
    floor_ms, ceiling = 0, None
    if day:
        floor_ms, ceiling = _day_range(day)
    bounds = [x for x in (before, ceiling) if x is not None]
    before = min(bounds) if bounds else None
    out, truncated = [], False
    for row, cut in _scan(store, tenant_id, agent, before, floor_ms):
        if cut:
            truncated = True
            break
        if route and row.get("route") != route:
            continue
        out.append(summary_view(row))
        if len(out) >= limit:
            break
    more = len(out) >= limit
    return {"traces": out, "next_before": out[-1]["started"] if more else None, "truncated": truncated}


def get_trace(store, tenant_id, agent, trace_id):
    """{"trace": summary view, "spans": [...]} or None: the key is built from the caller's business, so another business's id is simply not found; an id of another agent is not found either."""
    if not tc.valid_trace_id(trace_id):
        return None
    pk = traces.pk_of(owner_of(tenant_id), trace_id)
    summary = store.get(pk, "summary")
    if not summary or summary.get("agent") != agent:
        return None
    spans = [{k: _plain(v) for k, v in s.items() if k not in ("s", "id", "slug", "ttl")} for s in store.spans(pk)]
    return {"trace": summary_view(summary), "spans": spans}


def _blank():
    return {"answers": 0, "fallback": 0, "handover": 0, "errors": 0, "cost_micro": 0, "tokens": 0}


def _tally(into, row):
    into["answers"] += 1
    into["fallback"] += 1 if row.get("route") == "fallback" else 0
    into["handover"] += 1 if row.get("route") == "handover" else 0
    into["errors"] += 1 if int(row.get("errors", 0) or 0) else 0
    into["cost_micro"] += int(row.get("cost_micro", 0) or 0)
    into["tokens"] += int(row.get("tokens", 0) or 0)


def summary(store, tenant_id, agent, days=None, now=None):
    """What an agent's answers looked like over the last days: totals, per day and per live version, with the share that fell back or went to a person. Read from the
    summaries only (no text). `truncated` says the scan limit was reached (older answers are not counted)."""
    days = max(1, min(int(days or SUMMARY_DAYS), 30))
    now = int(now if now is not None else time.time())
    floor = (now - days * DAY) * MS
    total, per_day, per_version, truncated = _blank(), {}, {}, False
    for row, cut in _scan(store, tenant_id, agent, None, floor):
        if cut:
            truncated = True
            break
        row = _plain(row)
        _tally(total, row)
        _tally(per_day.setdefault(str(row.get("day") or "?"), _blank()), row)
        _tally(per_version.setdefault(str(int(row.get("version", 0) or 0)), _blank()), row)

    def rated(block):
        n = block["answers"] or 1
        return {**block, "fallback_rate": round(block["fallback"] / n, 3), "handover_rate": round(block["handover"] / n, 3)}

    return {"days": days, "total": rated(total), "per_day": [{"day": d, **rated(b)} for d, b in sorted(per_day.items(), reverse=True)],
            "per_version": {v: rated(b) for v, b in sorted(per_version.items(), key=lambda kv: int(kv[0]))}, "truncated": truncated}


def delete_all(store, tenant_id, agent=None, dry=False):
    """Remove the traces of one business (or of one of its agents). For the teacher's command; returns how many traces were removed (with `dry`: how many would be)."""
    removed, before = 0, None
    while True:
        rows = store.summaries(app=owner_of(tenant_id), limit=LIST_MAX, before=before)
        if not rows:
            return removed
        for row in rows:
            before = int(row.get("started", 0))
            if agent is None or row.get("agent") == agent:
                if not dry:
                    store.delete_trace(row["s"])
                removed += 1
        if len(rows) < LIST_MAX:
            return removed
