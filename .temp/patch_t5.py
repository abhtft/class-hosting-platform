p = r"D:\Github_Codes\class-hosting-platform-github\lambda\traces.py"
s = open(p, encoding="utf8").read()


def rep(a, b):
    global s
    assert a in s, a[:60]
    s = s.replace(a, b, 1)


rep('''        if st.get(pk, "summary") is None:
            # a new trace: it counts against the student's daily allowance (atomic, so parallel answers cannot overshoot)
            if portal.db().counter_add(f"trd#{slug}#{day_id(now)}", 1, limit=cfg["daily_limit"], ttl=now + 2 * DAY, now=now) is None:
                return None''', '''        existing = st.get(pk, "summary")
        if existing is None:
            # a new trace: it counts against the student's daily allowance (atomic, so parallel answers cannot overshoot)
            if portal.db().counter_add(f"trd#{slug}#{day_id(now)}", 1, limit=cfg["daily_limit"], ttl=now + 2 * DAY, now=now) is None:
                return None
        elif int(existing.get("spans", 0)) >= MAX_SPANS_PER_TRACE:
            return None  # one trace never grows without limit, whatever an app sends''')
rep('''MS = 1000
''', '''MS = 1000
MAX_SPANS_PER_TRACE = 200
SPANS_PER_TRACE_BUDGET = 20  # an app may report this many steps per allowed trace a day (daily_limit x 20), counted atomically
CLEAR_BATCH = 100  # traces removed per call of "delete all of a student's traces" (the teacher page repeats until done)
''')
s += '''

def clear_student(slug, limit=CLEAR_BATCH):
    """Teacher: delete traces of one student now (for example when a project ends). Removes up to `limit` per call; `more` says to call again."""
    if not available():
        raise PortalError(409, "Traces are not switched on for this platform.")
    removed = 0
    rows = store().summaries(app=slug, limit=limit)
    for row in rows:
        removed += 1 if store().delete_trace(row["s"]) else 0
    return {"removed": removed, "more": len(rows) >= limit}
'''
open(p, "w", encoding="utf8").write(s)

# the app-reported steps get a daily budget in knowledge_api
p = r"D:\Github_Codes\class-hosting-platform-github\lambda\knowledge_api.py"
k = open(p, encoding="utf8").read()
a = '''    kept = 0
    for span in spans[:MAX_APP_SPANS]:'''
assert a in k
k = k.replace(a, '''    kept = 0
    if traces.available() and traces.settings(item)["enabled"]:  # a daily budget of steps an app may report, so a loop cannot fill the table
        budget = traces.settings(item)["daily_limit"] * traces.SPANS_PER_TRACE_BUDGET
        n = len(spans[:MAX_APP_SPANS])
        if portal.db().counter_add(f"trd#{item['slug']}#{traces.day_id(now)}#spans", n, limit=budget, ttl=now + 2 * portal.DAY, now=now) is None:
            return {"trace_id": trace_id, "recorded": 0}
    for span in spans[:MAX_APP_SPANS]:''', 1)
open(p, "w", encoding="utf8").write(k)

# portal route
p = r"D:\Github_Codes\class-hosting-platform-github\lambda\portal.py"
q = open(p, encoding="utf8").read()
a = '''        if method == "GET" and path == "/api/admin/traces":'''
assert a in q
q = q.replace(a, '''        trace_clear = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/traces/clear", path)
        if method == "POST" and trace_clear:
            import traces
            item = get_token(trace_clear.group(1))
            return reply(200, **traces.clear_student(item["slug"]))
''' + a, 1)
q = q.replace("  GET  /api/admin/traces/status", "  POST /api/admin/tokens/{id}/traces/clear   delete a student's traces now (up to 100 per call: repeat while `more` is true)\n  GET  /api/admin/traces/status", 1)
open(p, "w", encoding="utf8").write(q)
print("ok")
