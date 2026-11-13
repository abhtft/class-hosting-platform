p = r"D:\Github_Codes\class-hosting-platform-github\lambda\traces.py"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:60]
    s = s.replace(a, b, 1)


# 1. refusals and model time are counted on the summary as steps arrive (so a daily rollup needs only the summaries)
rep('''        if status == "error":
            adds["errors"] = 1
        st.upsert_summary(pk, once, adds)''', '''        if status == "error":
            adds["errors"] = 1
        if isinstance(attrs, dict) and attrs.get("refused") is True:
            adds["refused"] = 1  # the app's "answer" step says the books did not answer
        if kind == "llm":
            adds["llm_ms"] = max(int(dur_ms or 0), 0)
        st.upsert_summary(pk, once, adds)''')
rep('keep = ("s", "slug", "day", "started", "level", "spans", "tokens", "cost_micro", "errors")',
    'keep = ("s", "slug", "day", "started", "level", "spans", "tokens", "cost_micro", "errors", "refused", "llm_ms")')

s += '''

# ---------------------------------------------------------------- daily summaries and drift alerts (T6)
ROLLUP_KEEP_DAYS = 400
DRIFT_MIN_ANSWERS = 20        # a rate over fewer answers than this says nothing
DRIFT_REFUSAL_JUMP = 0.15     # alert when the share of "the books do not say" is this much above the last week's
DRIFT_ERROR_RATE = 0.20       # or when this share of answers had an error
BASELINE_DAYS = 7


def _blank():
    return {"answers": 0, "refused": 0, "errors": 0, "tokens": 0, "cost_micro": 0, "llm_ms": 0}


def _add(into, row):
    into["answers"] += 1
    into["refused"] += 1 if int(row.get("refused", 0) or 0) else 0
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
    publish("Student platform: answers changed on " + day, "\\n".join(problems) + "\\n\\nOpen the Traces tab to see the answers of that day. A jump in refusals often means a book changed, a limit was lowered or the AI model behaves differently.")
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
'''
open(p, "w", encoding="utf8").write(s)

p = r"D:\Github_Codes\class-hosting-platform-github\lambda\app.py"
a = open(p, encoding="utf8").read()
x = '''def remind_about_traces():
    """Once a month, when traces are about to expire, tell the teacher through the alert email (a no-op without the alert email or traces). Never fails the sweep."""
    topic = os.environ.get("ALERTS_TOPIC_ARN")
    if not topic:
        return
    try:
        import traces
        import boto3
        sns = boto3.client("sns")
        sent = traces.remind(time.time(), lambda subject, message: sns.publish(TopicArn=topic, Subject=subject, Message=message))
        if sent is not None:
            print(json.dumps({"traces_reminder_sent": sent}))
    except Exception as error:
        print(json.dumps({"traces_reminder_failed": type(error).__name__}))
'''
assert x in a
y = '''def remind_about_traces():
    """The daily trace jobs: write yesterday's numbers (always, when traces are on), and, with the alert email, once a month remind about expiry and warn when answers changed.
    Never fails the sweep."""
    try:
        import traces
        if not traces.available():
            return
        topic = os.environ.get("ALERTS_TOPIC_ARN")
        if topic:
            import boto3
            sns = boto3.client("sns")
            publish = lambda subject, message: sns.publish(TopicArn=topic, Subject=subject[:99], Message=message)  # noqa: E731
        else:
            publish = lambda subject, message: None  # noqa: E731
        made = traces.daily(time.time(), publish)
        sent = traces.remind(time.time(), publish) if topic else None
        print(json.dumps({"traces_days_summarised": made, "traces_reminder_sent": sent}))
    except Exception as error:
        print(json.dumps({"traces_daily_failed": type(error).__name__}))
'''
a = a.replace(x, y, 1)
open(p, "w", encoding="utf8").write(a)

p = r"D:\Github_Codes\class-hosting-platform-github\lambda\portal.py"
q = open(p, encoding="utf8").read()
t = '''        if method == "GET" and path == "/api/admin/traces/status":'''
assert t in q
q = q.replace(t, '''        if method == "GET" and path == "/api/admin/traces/summary":
            import traces
            qs = event.get("queryStringParameters") or {}
            return reply(200, **traces.daily_view(now, int(qs["days"]) if str(qs.get("days", "")).isdigit() else 14))
''' + t, 1)
q = q.replace("  GET  /api/admin/traces/status", "  GET  /api/admin/traces/summary[?days]   the last days' numbers (answers, 'the books do not say', errors, tokens, cost) from the daily rollups\n  GET  /api/admin/traces/status", 1)
open(p, "w", encoding="utf8").write(q)
print("ok")
