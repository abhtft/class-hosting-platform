p = r"D:\Github_Codes\class-hosting-platform-github\lambda\portal.py"
q = open(p, encoding="utf8").read()
a = '''        trace_clear = re.fullmatch('''
assert a in q
q = q.replace(a, '''        if path == "/api/admin/worker" and method == "GET":
            import worker
            return reply(200, **worker.view(now))
        if path == "/api/admin/worker/start" and method == "POST":
            import worker
            return reply(200, **worker.start(body.get("mode") or "run", "teacher", now))
        if path == "/api/admin/worker/stop" and method == "POST":
            import worker
            return reply(200, **worker.stop(now))
        if path == "/api/admin/worker/settings" and method == "POST":
            import worker
            worker.set_settings(body)
            return reply(200, **worker.view(now))
''' + a, 1)
q = q.replace("  POST /api/admin/tokens/{id}/traces/clear", "  GET /api/admin/worker  the compute worker (one small machine, off by default): state, settings, last run, month estimate;  POST .../worker/start {mode: run|keep}, .../worker/stop, .../worker/settings {daily_enabled, hour_utc, max_minutes}  (worker.py)\n  POST /api/admin/tokens/{id}/traces/clear", 1)
open(p, "w", encoding="utf8").write(q)
print("ok")
