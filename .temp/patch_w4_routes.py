root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


edit("lambda/worker_jobs.py", '''        if job["state"] in FINISHED:
            stage_queue(now)
    return job''', '''        if job["state"] in FINISHED or job["state"] == "running":
            stage_queue(now)
    return job''')

edit("lambda/portal.py", '''    if method == "GET" and (path == "/api/student/traces" or path.startswith("/api/student/traces/")):''', '''    if path == "/api/student/worker/jobs" or path.startswith("/api/student/worker/jobs/"):
        import worker_jobs
        item = student_token(event, now)
        parts = path[len("/api/student/worker/jobs"):].strip("/").split("/") if path != "/api/student/worker/jobs" else []
        if not parts and method == "POST":
            return reply(201, **worker_jobs.create(item, body, now))
        if not parts and method == "GET":
            return reply(200, **worker_jobs.list_mine(item, now))
        if len(parts) == 1 and method == "GET":
            return reply(200, **worker_jobs.get_mine(item, parts[0], now))
        if len(parts) == 1 and method == "DELETE":
            return reply(200, **worker_jobs.delete(item, parts[0], now))
        if len(parts) == 2 and parts[1] == "submit" and method == "POST":
            return reply(200, **worker_jobs.submit(item, parts[0], now))
        if len(parts) == 2 and parts[1] == "cancel" and method == "POST":
            return reply(200, **worker_jobs.cancel(item, parts[0], now))
        if len(parts) == 2 and parts[1] == "result" and method == "GET":
            return reply(200, **worker_jobs.result_url(item, parts[0], now))
        raise PortalError(404, "Unknown route.")
    if method == "GET" and (path == "/api/student/traces" or path.startswith("/api/student/traces/")):''')
edit("lambda/portal.py", '''        worker_set = re.fullmatch(''', '''        if path == "/api/admin/worker/jobs" and method == "GET":
            import worker_jobs
            return reply(200, **worker_jobs.queue_view(now))
        wjob = re.fullmatch(r"/api/admin/worker/jobs/([a-z0-9][a-z0-9-]{0,29})/([0-9a-f]{12})(/cancel)?", path)
        if wjob and method == "POST" and wjob.group(3):
            import worker_jobs
            return reply(200, **worker_jobs.teacher_cancel(wjob.group(1), wjob.group(2), now))
        if wjob and method == "DELETE" and not wjob.group(3):
            import worker_jobs
            return reply(200, **worker_jobs.teacher_delete(wjob.group(1), wjob.group(2), now))
        worker_set = re.fullmatch(''')
edit("lambda/portal.py", "  POST /api/admin/tokens/{id}/worker-access ", "  GET|POST|DELETE /api/student/worker/jobs[/{id}[/submit|cancel|result]]  a student's own jobs at worker level 3 (worker_jobs.py);  GET /api/admin/worker/jobs, POST .../jobs/{slug}/{id}/cancel, DELETE .../jobs/{slug}/{id}  the teacher's queue\n  POST /api/admin/tokens/{id}/worker-access ")
print("ok")
