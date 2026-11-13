root = r"D:\Github_Codes\class-hosting-platform-github"


def edit(p, a, b):
    s = open(root + "\\" + p, encoding="utf8").read()
    assert a in s, (p, a[:60])
    open(root + "\\" + p, "w", encoding="utf8").write(s.replace(a, b, 1))


# ---------------- worker.py
edit("lambda/worker.py", '''MODES = ("run", "keep")''', '''MODES = ("run", "keep")
LEVELS = (0, 1, 2, 3)  # per student: 0 off, 1 reports, 2 platform tasks, 3 their own code (docs/COMPUTE_WORKER_PLAN.md section 11)
LEVEL_NAMES = {0: "off", 1: "reports", 2: "platform tasks", 3: "own code"}
DEFAULT_CAP_MINUTES, MIN_CAP_MINUTES, MAX_CAP_MINUTES = 2400, 60, 14400  # the class-wide monthly cap on machine minutes: 40 hours''')
edit("lambda/worker.py", '''    return {"daily_enabled": raw.get("daily_enabled") is True, "hour_utc": hour if hour in HOUR_CHOICES else 3, "max_minutes": minutes}''', '''    cap = raw.get("monthly_cap_minutes")
    cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and MIN_CAP_MINUTES <= cap <= MAX_CAP_MINUTES else DEFAULT_CAP_MINUTES
    return {"daily_enabled": raw.get("daily_enabled") is True, "hour_utc": hour if hour in HOUR_CHOICES else 3, "max_minutes": minutes, "monthly_cap_minutes": cap}''')
edit("lambda/worker.py", '''    if not changes:
        raise PortalError(400, "Nothing to change.")
    _db().put({"pk": "worker#settings", **settings(), **changes})
    return settings()''', '''    if "monthly_cap_minutes" in body:
        v = body["monthly_cap_minutes"]
        if isinstance(v, bool) or not isinstance(v, int) or not MIN_CAP_MINUTES <= v <= MAX_CAP_MINUTES:
            raise PortalError(400, f"monthly_cap_minutes must be a whole number from {MIN_CAP_MINUTES} to {MAX_CAP_MINUTES}.")
        changes["monthly_cap_minutes"] = v
    if not changes:
        raise PortalError(400, "Nothing to change.")
    _db().put({"pk": "worker#settings", **settings(), **changes})
    return settings()''')
edit("lambda/worker.py", '''    limit = settings()["max_minutes"] * 60
    _write_mode(mode, now)''', '''    cfg = settings()
    if _month_minutes(now) >= cfg["monthly_cap_minutes"]:
        raise PortalError(409, f"The monthly cap of {cfg['monthly_cap_minutes']} machine minutes is reached. Raise it on the Worker tab, or wait for next month.")
    limit = cfg["max_minutes"] * 60
    _write_mode(mode, now)''')
edit("lambda/worker.py", '''            "month": {"minutes": minutes, "compute_usd": compute, "disk_usd": round(disk * DISK_PER_GB_MONTH, 2),''', '''            "month": {"minutes": minutes, "cap_minutes": settings()["monthly_cap_minutes"], "compute_usd": compute, "disk_usd": round(disk * DISK_PER_GB_MONTH, 2),''')
edit("lambda/worker.py", '''def handler(event, context=None):''', '''# ---------------------------------------------------------------- what each student may do (the teacher's per-student switch)
ACCESS_DEFAULT = {"level": 0, "requests_per_day": 5, "minutes_per_month": 30, "job_minutes": 10, "network": False}
ACCESS_BOUNDS = {"requests_per_day": (0, 100), "minutes_per_month": (0, 1440), "job_minutes": (1, 60)}


def access(item):
    """A student's worker access with every value in range (old or odd data reads as the safe default: level 0, no network)."""
    raw = (item or {}).get("worker_access")
    raw = raw if isinstance(raw, dict) else {}
    out = dict(ACCESS_DEFAULT)
    if raw.get("level") in LEVELS and not isinstance(raw.get("level"), bool):
        out["level"] = raw["level"]
    for key, (low, high) in ACCESS_BOUNDS.items():
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and low <= v <= high:
            out[key] = v
    out["network"] = raw.get("network") is True and out["level"] == 3  # the internet only ever applies to a student's own code
    return out


def access_view(item):
    if not available():
        return {"available": False}
    return {"available": True, **access(item), "level_name": LEVEL_NAMES[access(item)["level"]], "bounds": {k: list(v) for k, v in ACCESS_BOUNDS.items()}}


def set_access(item, body):
    """Teacher: set one student's level and quotas. Nothing here starts or reaches the machine."""
    if not available():
        raise PortalError(409, "The compute worker is not installed on this platform.")
    if not isinstance(body, dict):
        raise PortalError(400, "Send a JSON object.")
    changes = {}
    if "level" in body:
        if isinstance(body["level"], bool) or body["level"] not in LEVELS:
            raise PortalError(400, "level must be 0 (off), 1 (reports), 2 (platform tasks) or 3 (their own code).")
        changes["level"] = body["level"]
    for key, (low, high) in ACCESS_BOUNDS.items():
        if key in body:
            v = body[key]
            if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
                raise PortalError(400, f"{key} must be a whole number from {low} to {high}.")
            changes[key] = v
    if "network" in body:
        if not isinstance(body["network"], bool):
            raise PortalError(400, "network must be true or false.")
        changes["network"] = body["network"]
    if not changes:
        raise PortalError(400, "Nothing to change.")
    merged = {**access(item), **changes}
    if merged["network"] and merged["level"] != 3:
        raise PortalError(400, "Internet access only applies to level 3 (a student's own code).")
    portal.db().update(item["pk"], {"worker_access": merged})
    return access({"worker_access": merged})


def handler(event, context=None):''')

# ---------------- portal.py: the token view and the route
edit("lambda/portal.py", '''        "traces": traces_view(item),
    }''', '''        "traces": traces_view(item),
        "worker_access": worker_access_view(item),
    }''')
edit("lambda/portal.py", '''def traces_view(item):''', '''def worker_access_view(item):
    import worker
    return worker.access_view(item)


def traces_view(item):''')
edit("lambda/portal.py", '''        if path == "/api/admin/worker" and method == "GET":''', '''        worker_set = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/worker-access", path)
        if method == "POST" and worker_set:
            import worker
            item = get_token(worker_set.group(1))
            worker.set_access(item, body)
            return reply(200, token=view(db().get(item["pk"]) or item, now, site_url))
        if path == "/api/admin/worker" and method == "GET":''')
edit("lambda/portal.py", "  GET /api/admin/worker  the compute worker", "  POST /api/admin/tokens/{id}/worker-access  {level 0-3, requests_per_day, minutes_per_month, job_minutes, network}  a student's compute worker access (worker.py; level 0 = off)\n  GET /api/admin/worker  the compute worker")
print("ok")
