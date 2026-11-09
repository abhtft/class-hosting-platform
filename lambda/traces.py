"""Traces: what happened in an answer, step by step (docs/TRACE_SCHEMA.md). Storage, settings and the recording hook.

A trace is a summary item plus one small item per step (span), all in the traces table under one partition `t#<slug>#<trace_id>`:
  sort key "summary"                 one per trace: who, when, totals (tokens, cost, spans), expiry; the only item with index attributes
  sort key "<ts_ms:013d>#<source>#x" one per step; `source` is "platform" (written by the platform's own functions) or "app" (T2)
Every item expires by itself (DynamoDB TTL) after the student's retention (30, 60 or 90 days). Nothing here ever raises into the caller:
`record` swallows its own errors, because tracing must never break or slow an answer.

Per-student settings live on the token item (item["traces"]): enabled, level (metadata | masked | full), retention_days, student_view, daily_limit.
"""
import json
import os
import threading
import time

import portal
import trace_core as tc
from portal import DAY, PortalError
import platform_settings

MS = 1000
MAX_SPANS_PER_TRACE = platform_settings.value("traces.maxSpansPerTrace")
SPANS_PER_TRACE_BUDGET = platform_settings.value("traces.spansPerTraceBudget")  # an app may report this many steps per allowed trace a day (daily_limit x 20), counted atomically
CLEAR_BATCH = platform_settings.value("traces.clearBatch")  # traces removed per call of "delete all of a student's traces" (the teacher page repeats until done)


def available():
    return os.environ.get("TRACES_ENABLED", "") == "true"


def settings(item):
    return tc.normalise_settings((item or {}).get("traces"))


def day_id(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def pk_of(slug, trace_id):
    return f"t#{slug}#{trace_id}"


# ---------------------------------------------------------------- the store (memory for tests, DynamoDB for real)
class MemoryTraces:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def get(self, pk, sk):
        with self.lock:
            row = self.rows.get((pk, sk))
            return dict(row) if row else None

    def put(self, pk, sk, row):
        with self.lock:
            self.rows[(pk, sk)] = {"s": pk, "id": sk, **row}

    def upsert_summary(self, pk, once, adds):
        """Create the summary with `once` values if absent, then add `adds` to its counters. Returns the item."""
        with self.lock:
            row = self.rows.setdefault((pk, "summary"), {"s": pk, "id": "summary", **once})
            for k, v in adds.items():
                row[k] = row.get(k, 0) + v
            return dict(row)

    def update(self, pk, sk, fields):
        with self.lock:
            if (pk, sk) in self.rows:
                self.rows[(pk, sk)].update(fields)

    def spans(self, pk):
        with self.lock:
            keys = sorted(sk for (p, sk) in self.rows if p == pk and sk != "summary")
            return [dict(self.rows[(pk, sk)]) for sk in keys]

    def summaries(self, app=None, day=None, limit=50, before=None):
        """Newest first. Memory scans; DynamoDB uses the by-app / by-day indexes."""
        with self.lock:
            rows = [dict(r) for (p, sk), r in self.rows.items() if sk == "summary"
                    and (app is None or r.get("app") == app) and (day is None or r.get("day") == day)
                    and (before is None or r.get("started", 0) < before)]
        rows.sort(key=lambda r: -r.get("started", 0))
        return rows[:limit]

    def oldest(self, first_day, last_day):
        """The summary that started first, looking day by day from first_day (YYYY-MM-DD) to last_day. None when there is none."""
        with self.lock:
            rows = [dict(r) for (p, sk), r in self.rows.items() if sk == "summary" and first_day <= r.get("day", "") <= last_day]
        return min(rows, key=lambda r: r.get("started", 0)) if rows else None

    def delete_trace(self, pk):
        with self.lock:
            gone = [k for k in self.rows if k[0] == pk]
            for k in gone:
                del self.rows[k]
            return len(gone)


class DynamoTraces:
    """The same operations on the TracesTable. Index names match template.yaml: by-app (app, started) and by-day (day, started)."""

    def __init__(self, table_name):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)

    def get(self, pk, sk):
        return self.table.get_item(Key={"s": pk, "id": sk}).get("Item")

    def put(self, pk, sk, row):
        import json
        from decimal import Decimal
        row = json.loads(json.dumps(row, default=str), parse_float=Decimal)  # DynamoDB has no floats
        self.table.put_item(Item={"s": pk, "id": sk, **row})

    def upsert_summary(self, pk, once, adds):
        names, values, sets, add = {}, {}, [], []
        for i, (k, v) in enumerate(once.items()):
            names[f"#o{i}"], values[f":o{i}"] = k, v
            sets.append(f"#o{i} = if_not_exists(#o{i}, :o{i})")
        for i, (k, v) in enumerate(adds.items()):
            names[f"#a{i}"], values[f":a{i}"] = k, v
            add.append(f"#a{i} :a{i}")
        expr = "SET " + ", ".join(sets) + (" ADD " + ", ".join(add) if add else "")
        return self.table.update_item(Key={"s": pk, "id": "summary"}, UpdateExpression=expr, ExpressionAttributeNames=names,
                                      ExpressionAttributeValues=values, ReturnValues="ALL_NEW")["Attributes"]

    def update(self, pk, sk, fields):
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":f{i}": v for i, v in enumerate(fields.values())}
        self.table.update_item(Key={"s": pk, "id": sk}, UpdateExpression="SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(fields))),
                               ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                               ConditionExpression="attribute_exists(s)")

    def spans(self, pk):
        from boto3.dynamodb.conditions import Key
        out, kwargs = [], {"KeyConditionExpression": Key("s").eq(pk) & Key("id").lt("summary")}
        while True:
            page = self.table.query(**kwargs)
            out += page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                return out
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def summaries(self, app=None, day=None, limit=50, before=None):
        from boto3.dynamodb.conditions import Key
        index, field, value = ("by-app", "app", app) if app is not None else ("by-day", "day", day)
        if value is None:
            raise ValueError("a listing needs an app or a day")
        cond = Key(field).eq(value) & (Key("started").lt(before) if before is not None else Key("started").gte(0))
        rows, kwargs = [], {"IndexName": index, "KeyConditionExpression": cond, "ScanIndexForward": False, "Limit": limit}
        while len(rows) < limit:
            page = self.table.query(**kwargs)
            rows += [r for r in page.get("Items", []) if day is None or r.get("day") == day]
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return rows[:limit]

    def oldest(self, first_day, last_day):
        import datetime
        from boto3.dynamodb.conditions import Key
        day = datetime.date.fromisoformat(first_day)
        end = datetime.date.fromisoformat(last_day)
        while day <= end:  # one small query per day, oldest first: the first day that has anything holds the oldest trace
            page = self.table.query(IndexName="by-day", KeyConditionExpression=Key("day").eq(day.isoformat()), ScanIndexForward=True, Limit=1)
            if page.get("Items"):
                return page["Items"][0]
            day += datetime.timedelta(days=1)
        return None

    def delete_trace(self, pk):
        from boto3.dynamodb.conditions import Key
        count, kwargs = 0, {"KeyConditionExpression": Key("s").eq(pk), "ProjectionExpression": "#s, #i",
                            "ExpressionAttributeNames": {"#s": "s", "#i": "id"}}
        while True:
            page = self.table.query(**kwargs)
            with self.table.batch_writer() as batch:
                for row in page.get("Items", []):
                    batch.delete_item(Key={"s": row["s"], "id": row["id"]})
                    count += 1
            if "LastEvaluatedKey" not in page:
                return count
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


_store = []


def store():
    if not _store:
        _store.append(DynamoTraces(os.environ["TRACES_TABLE"]) if os.environ.get("TRACES_TABLE") else MemoryTraces())
    return _store[0]


# ---------------------------------------------------------------- recording (called from the platform's own functions)
def record(item, trace_id, name, kind="other", attrs=None, ts_ms=None, dur_ms=0, status="ok", tokens=0, cost_micro=0, now=None, source="platform"):
    """Add one step to a trace. Returns the trace id, or None when nothing was recorded. Never raises."""
    try:
        if not (available() and item and item.get("slug")):
            return None
        cfg = settings(item)
        if not cfg["enabled"]:
            return None
        now = int(now if now is not None else time.time())
        slug = item["slug"]
        trace_id = tc.trace_id_or_new(trace_id)
        pk = pk_of(slug, trace_id)
        st = store()
        existing = st.get(pk, "summary")
        if existing is None:
            # a new trace: it counts against the student's daily allowance (atomic, so parallel answers cannot overshoot)
            if portal.db().counter_add(f"trd#{slug}#{day_id(now)}", 1, limit=cfg["daily_limit"], ttl=now + 2 * DAY, now=now) is None:
                return None
        elif int(existing.get("spans", 0)) >= MAX_SPANS_PER_TRACE:
            return None  # one trace never grows without limit, whatever an app sends
        ts_ms = int(ts_ms if ts_ms is not None else now * MS)
        expires = now + cfg["retention_days"] * DAY
        span = tc.make_span(name, kind, source, ts_ms, dur_ms, status, attrs, cfg["level"])
        st.put(pk, tc.span_sort_key(ts_ms, source), {**span, "slug": slug, "ttl": expires})
        once = {"slug": slug, "app": slug, "day": day_id(now), "started": ts_ms, "level": cfg["level"], "ttl": expires}
        adds = {"spans": 1, "tokens": int(tokens or 0), "cost_micro": int(cost_micro or 0)}
        if status == "error":
            adds["errors"] = 1
        if isinstance(attrs, dict) and attrs.get("refused") is True:
            adds["refused"] = 1  # the app's "answer" step says the books did not answer
        if kind == "llm":
            adds["llm_ms"] = max(int(dur_ms or 0), 0)
            adds["llm_calls"] = 1
        if kind == "retriever" and isinstance(attrs, dict) and attrs.get("confident") is False:
            adds["unsure"] = 1  # a search the books could not answer; with no model call after it, the visitor was told "the books do not say"
        st.upsert_summary(pk, once, adds)
        return trace_id
    except Exception:  # tracing must never break an answer
        return None


# ---------------------------------------------------------------- the teacher's side
def view_settings(item):
    return {"available": available(), **settings(item)}


def set_settings(item, body):
    if not available():
        raise PortalError(409, "Traces are not switched on for this platform.")
    try:
        changes = tc.validate_settings(body)
    except ValueError as e:
        raise PortalError(400, str(e))
    merged = {**settings(item), **changes}
    portal.db().update(item["pk"], {"traces": merged})
    return {"available": True, **merged}


def _summary_view(row):
    row = portal._plain(row)  # DynamoDB returns Decimal numbers
    keep = ("s", "slug", "day", "started", "level", "spans", "tokens", "cost_micro", "errors", "refused", "llm_ms", "unsure", "llm_calls")
    out = {k: (int(row[k]) if isinstance(row.get(k), (int, float)) and k != "started" else row.get(k)) for k in keep if k in row}
    out["trace_id"] = str(row["s"]).split("#", 2)[2]
    out.pop("s", None)
    out["started"] = int(row.get("started", 0))
    return out


def list_traces(slug=None, day=None, limit=50, before=None):
    if not available():
        return {"available": False, "traces": []}
    limit = max(1, min(int(limit or 50), 200))
    rows = store().summaries(app=slug, day=day or (None if slug else day_id(int(time.time()))), limit=limit, before=before)
    return {"available": True, "traces": [_summary_view(r) for r in rows]}


def get_trace(slug, trace_id):
    if not available():
        raise PortalError(409, "Traces are not switched on for this platform.")
    if not tc.valid_trace_id(trace_id):
        raise PortalError(400, "That is not a trace id.")
    pk = pk_of(slug, trace_id)
    summary = store().get(pk, "summary")
    if not summary:
        raise PortalError(404, "No such trace (it may have expired).")
    spans = [{k: portal._plain(v) for k, v in s.items() if k not in ("s", "slug", "ttl")} for s in store().spans(pk)]
    return {"trace": _summary_view(summary), "spans": spans}


def delete_trace(slug, trace_id):
    if not tc.valid_trace_id(trace_id):
        raise PortalError(400, "That is not a trace id.")
    return {"removed": store().delete_trace(pk_of(slug, trace_id))}


# ---------------------------------------------------------------- the student's side (their own app only, as the teacher allows)
def _student_mode(item):
    """What this student may see: None (nothing), "metadata" (numbers only) or "stored" (what was stored). The teacher decides per student."""
    if not available():
        raise PortalError(409, "Traces are not switched on for this platform.")
    mode = settings(item)["student_view"]
    if mode == "off":
        raise PortalError(403, "Your teacher has not switched on trace viewing for you. Ask your teacher if you would like to see how your app answers.")
    return mode


def student_list(item, limit=20, before=None):
    mode = _student_mode(item)
    rows = store().summaries(app=item["slug"], limit=max(1, min(int(limit or 20), 50)), before=before)
    return {"view": mode, "traces": [{k: v for k, v in _summary_view(r).items() if k != "slug"} for r in rows]}


def student_get(item, trace_id):
    """One trace of the student's OWN app: the partition is built from the signed-in student's slug, so another app's id is simply not found."""
    mode = _student_mode(item)
    if not tc.valid_trace_id(trace_id):
        raise PortalError(404, "No such trace (it may have expired).")
    data = get_trace(item["slug"], trace_id)
    spans = [tc.strip_for_student(s, mode) for s in data["spans"]]
    trace = {k: v for k, v in data["trace"].items() if k != "slug"}
    return {"view": mode, "trace": trace, "spans": spans}


# ---------------------------------------------------------------- archive, reminder and download (T7)
REMIND_DAYS = platform_settings.value("traces.remindDays")
EXPORT_MAX_TRACES = platform_settings.value("traces.exportMaxTraces")
EXPORT_MAX_BYTES = platform_settings.value("traces.exportMaxBytes")  # one page of a download: far below the 6 MB limit of a function answer


def status(now=None):
    """For the teacher's banner and the reminder: how many days until the oldest stored trace expires by itself."""
    if not available():
        return {"available": False}
    now = int(now if now is not None else time.time())
    first = store().oldest(day_id(now - 100 * DAY), day_id(now))
    if not first:
        return {"available": True, "oldest": None}
    expires = int(first["ttl"])
    return {"available": True, "oldest": {"started": int(first["started"]), "day": first["day"], "slug": first.get("slug")}, "expires_at": expires,
            "days_left": max(0, (expires - now) // DAY), "remind": expires - now <= REMIND_DAYS * DAY}


def _days(first, last):
    import datetime
    day, end = datetime.date.fromisoformat(first), datetime.date.fromisoformat(last)
    while day <= end:
        yield day.isoformat()
        day += datetime.timedelta(days=1)


def _valid_day(value, what):
    import datetime
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise PortalError(400, f"{what} must be a date like 2026-11-07.")


def _shelf_match(spans, shelf):
    """True when a step searched this shelf (a name like science or library/science)."""
    for sp in spans:
        found = (sp.get("attrs") or {}).get("collections")
        if isinstance(found, dict) and shelf in found:
            return True
    return False


def export_page(slug=None, shelf=None, first=None, last=None, cursor=None):
    """One page of an archive: whole traces (summary and steps) for a date range, optionally one app and/or one shelf, newest day first.
    `cursor` {"day", "before"} continues where the last page stopped; `next` is None at the end. What is in the traces is what was stored at the student's level."""
    if not available():
        raise PortalError(409, "Traces are not switched on for this platform.")
    today = day_id(int(time.time()))
    last = _valid_day(last or today, "to")
    first = _valid_day(first or day_id(int(time.time()) - 90 * DAY), "from")
    if first > last or (len(list(_days(first, last))) > 400):
        raise PortalError(400, "Choose a range of at most 400 days, with from before to.")
    cursor = cursor if isinstance(cursor, dict) else {}
    days = [d for d in _days(first, last)][::-1]
    start = days.index(cursor["day"]) if cursor.get("day") in days else 0
    before = cursor.get("before") if isinstance(cursor.get("before"), int) and cursor.get("day") in days else None
    records, size, nxt = [], 0, None
    for i in range(start, len(days)):
        day = days[i]
        while True:
            rows = store().summaries(day=day, limit=50, before=before)
            for row in rows:
                if slug and row.get("app") != slug:
                    before = int(row.get("started", 0))
                    continue
                pk = row["s"]
                spans = [{k: portal._plain(v) for k, v in sp.items() if k not in ("s", "slug", "ttl")} for sp in store().spans(pk)]
                before = int(row.get("started", 0))
                if shelf and not _shelf_match(spans, shelf):
                    continue
                rec = {**_summary_view(row), "app": row.get("app"), "spans": spans}
                size += len(json.dumps(rec, default=str))
                records.append(rec)
                if len(records) >= EXPORT_MAX_TRACES or size >= EXPORT_MAX_BYTES:
                    return {"records": records, "next": {"day": day, "before": before}}
            if len(rows) < 50:
                break
        before = None
    return {"records": records, "next": nxt}


def remind(now, publish):
    """Once a month, when traces will expire within REMIND_DAYS: tell the teacher through the alert email. `publish(subject, message)` sends it. Never raises."""
    try:
        if not available():
            return None
        now = int(now)
        st = status(now)
        if not st.get("remind"):
            return None
        month = time.strftime("%Y-%m", time.gmtime(now))
        if portal.db().counter_add(f"trremind#{month}", 1, limit=1, ttl=now + 40 * DAY, now=now) is None:
            return None  # already sent this month
        publish("Student platform: traces will expire soon",
                f"The oldest stored answer traces (from {st['oldest']['day']}) expire in {st['days_left']} days. Traces are deleted automatically after 30, 60 or 90 days. "
                "If you want to keep them, open the teacher page, go to the Traces tab and choose Download archive. The file may contain personal text, so keep it safe.")
        return st["days_left"]
    except Exception:
        return None


def clear_student(slug, limit=None):
    """Teacher: delete traces of one student now (for example when a project ends). Removes up to `limit` per call; `more` says to call again."""
    if not available():
        raise PortalError(409, "Traces are not switched on for this platform.")
    limit = limit or CLEAR_BATCH
    removed = 0
    rows = store().summaries(app=slug, limit=limit)
    for row in rows:
        removed += 1 if store().delete_trace(row["s"]) else 0
    return {"removed": removed, "more": len(rows) >= limit}


# ---------------------------------------------------------------- daily summaries and drift alerts (T6)
ROLLUP_KEEP_DAYS = platform_settings.value("traces.rollupKeepDays")
DRIFT_MIN_ANSWERS = platform_settings.value("traces.driftMinAnswers")        # a rate over fewer answers than this says nothing
DRIFT_REFUSAL_JUMP = platform_settings.value("traces.driftRefusalJump")     # alert when the share of "the books do not say" is this much above the last week's
DRIFT_ERROR_RATE = platform_settings.value("traces.driftErrorRate")       # or when this share of answers had an error
BASELINE_DAYS = platform_settings.value("traces.baselineDays")


def _blank():
    return {"answers": 0, "refused": 0, "errors": 0, "tokens": 0, "cost_micro": 0, "llm_ms": 0}


def _add(into, row):
    into["answers"] += 1
    # a refusal: the app said so, or the books were not confident and no model call followed (an agent that searches again is not a refusal)
    into["refused"] += 1 if (int(row.get("refused", 0) or 0) or (int(row.get("unsure", 0) or 0) and not int(row.get("llm_calls", 0) or 0))) else 0
    into["errors"] += 1 if int(row.get("errors", 0) or 0) else 0
    into["tokens"] += int(row.get("tokens", 0) or 0)
    into["cost_micro"] += int(row.get("cost_micro", 0) or 0)
    into["llm_ms"] += int(row.get("llm_ms", 0) or 0)


def compute_day(day):
    """The numbers of one day from the trace summaries alone (one index query per 100 answers): totals and per student."""
    total, apps, before = _blank(), {}, None
    while True:
        rows = store().summaries(day=day, limit=100, before=before)
        for row in rows:
            row = portal._plain(row)
            _add(total, row)
            _add(apps.setdefault(row.get("app") or "?", _blank()), row)
            before = int(row.get("started", 0))
        if len(rows) < 100:
            break
    return {"day": day, **total, "apps": apps}


def save_day(day, data, now):
    store().put(f"r#{day}", "all", {**data, "ttl": int(now) + ROLLUP_KEEP_DAYS * DAY})


def stored_day(day):
    row = store().get(f"r#{day}", "all")
    return portal._plain({k: v for k, v in row.items() if k not in ("s", "id", "ttl")}) if row else None


def daily_view(now=None, days=14):
    """For the teacher: the last `days` days (newest first), from the stored rollups, with today computed live. A missing day is computed when it is asked for."""
    if not available():
        return {"available": False, "days": []}
    now = int(now if now is not None else time.time())
    out = []
    for i in range(max(1, min(int(days or 14), 60))):
        day = day_id(now - i * DAY)
        data = None if i == 0 else stored_day(day)
        data = data or compute_day(day)
        if data["answers"] or i == 0:
            out.append(data)
    return {"available": True, "days": out}


def check_drift(day, data, baseline, publish, now):
    """Compare one day with the week before and say what looks wrong (the class, and each student with enough answers). One alert email a day at most."""
    problems = []

    def judge(label, d, base):
        if d["answers"] < DRIFT_MIN_ANSWERS:
            return
        rate = d["refused"] / d["answers"]
        was = (base["refused"] / base["answers"]) if base["answers"] >= DRIFT_MIN_ANSWERS else None
        if was is not None and rate - was >= DRIFT_REFUSAL_JUMP:
            problems.append(f"{label}: {round(rate * 100)}% of answers said the books do not say (the week before: {round(was * 100)}%), over {d['answers']} answers")
        if d["errors"] / d["answers"] >= DRIFT_ERROR_RATE:
            problems.append(f"{label}: {round(d['errors'] / d['answers'] * 100)}% of answers had an error, over {d['answers']} answers")

    judge("The whole class", data, baseline["total"])
    for slug, d in data["apps"].items():
        judge(slug, d, baseline["apps"].get(slug, _blank()))
    if not problems:
        return None
    if portal.db().counter_add(f"trdrift#{day}", 1, limit=1, ttl=int(now) + 10 * DAY, now=int(now)) is None:
        return None
    publish("Student platform: answers changed on " + day, "\n".join(problems) + "\n\nOpen the Traces tab to see the answers of that day. A jump in refusals often means a book changed, a limit was lowered or the AI model behaves differently.")
    return problems


def daily(now, publish):
    """The daily job: write yesterday's numbers (and any of the last 3 days missing), then look for a change. Never raises."""
    try:
        if not available():
            return None
        now = int(now)
        made = []
        for i in (3, 2, 1):
            day = day_id(now - i * DAY)
            if stored_day(day) is None:
                data = compute_day(day)
                save_day(day, data, now)
                made.append(day)
        day = day_id(now - DAY)
        data = stored_day(day)
        if not data or not data["answers"]:
            return made
        baseline_total, baseline_apps = _blank(), {}
        for i in range(2, 2 + BASELINE_DAYS):
            old = stored_day(day_id(now - i * DAY))
            if not old:
                continue
            for k in baseline_total:
                baseline_total[k] += old.get(k, 0)
            for slug, d in (old.get("apps") or {}).items():
                into = baseline_apps.setdefault(slug, _blank())
                for k in into:
                    into[k] += d.get(k, 0)
        check_drift(day, data, {"total": baseline_total, "apps": baseline_apps}, publish, now)
        return made
    except Exception:
        return None
