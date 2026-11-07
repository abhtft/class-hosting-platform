"""Web portal API: deploy tokens, teacher login, student login.

Teacher (password -> signed session, 8 h):
  POST /api/teacher/login                {username, password}
  GET  /api/admin/tokens
  POST /api/admin/tokens                 {days, student_name?, slug?}   -> plaintext token, shown once
  POST /api/admin/tokens/{id}/revoke
  POST /api/admin/tokens/{id}/extend     {days}
  POST /api/admin/tokens/{id}/offline    take the site offline now (archived, restorable)
  POST /api/admin/tokens/{id}/online     bring a taken-offline site back, expiry unchanged (409 if the token expired or is revoked: extend it instead)
  GET  /api/admin/requests               pending regeneration requests
  POST /api/admin/tokens/{id}/approve    {days}      new duration counted from now; brings an offline site back
  POST /api/admin/tokens/{id}/deny       {reason?}
  POST /api/admin/tokens/{id}/domain-access   {enabled}   switch custom domains on/off for a student
  POST /api/admin/tokens/{id}/backend    {enabled?, runs_limit?, runs_period?}   backend on/off and run allowance
  POST /api/admin/tokens/{id}/ai         {enabled?, limit_usd?, period?, model?, provider? (openrouter|gemini)}   class AI connector (ai.py); {} refreshes usage
  POST /api/admin/tokens/{id}/storage    {enabled?, data_mb?, files_mb?, ops_limit?, ops_period?}   class storage connector (storage.py)
  POST /api/admin/tokens/{id}/storage/clear   {scope: data | files | all}   delete a student's stored data and/or files
  GET  /api/admin/storage                storage across the class: per student sizes and counts, totals (never the content)
  POST /api/admin/tokens/{id}/knowledge  {enabled?, collections?, documents?, passages?, stored_mb?, upload_mb?, searches_limit?, searches_period?, embed_tokens?}
                                         class knowledge connector (knowledge.py); POST .../knowledge/clear removes all of the student's books
  POST /api/admin/tokens/{id}/traces     {enabled?, level? (metadata|masked|full), retention_days? (30|60|90), student_view? (off|metadata|stored), daily_limit?}   trace settings (traces.py)
  GET /api/admin/worker  the compute worker (one small machine, off by default): state, settings, last run, month estimate;  POST .../worker/start {mode: run|keep}, .../worker/stop, .../worker/settings {daily_enabled, hour_utc, max_minutes}  (worker.py)
  POST /api/admin/tokens/{id}/traces/clear   delete a student's traces now (up to 100 per call: repeat while `more` is true)
  GET  /api/admin/traces/summary[?days]   the last days' numbers (answers, 'the books do not say', errors, tokens, cost) from the daily rollups
  GET  /api/admin/traces/status  how long until the oldest traces expire;  GET /api/admin/traces/export?from&to&slug&shelf&cursor  one page of an archive (whole traces)
  GET  /api/admin/traces[?slug&day&limit&before]   recent traces (summaries, newest first);  GET|DELETE /api/admin/traces/{slug}/{trace_id}   one trace with its steps
  GET  /api/admin/knowledge              the class library and this month's spending against the ceiling
  POST|DELETE /api/admin/knowledge/library/collections[/{name}[/documents[/{id}[/start]]]]   the teacher's class library (knowledge.py admin_route)

Student (the token itself is the login -> signed session, 12 h):
  POST /api/student/login                {token}
  GET  /api/student/me
  POST /api/student/regen-request        {note?}     only in the last WARN_DAYS before expiry, or after it
  POST /api/student/upload-url, /api/student/publish   deploy (handled in app.py)
  PUT/DELETE /api/student/domain, GET /api/student/domain/status   custom domain (see domains.py)
  GET/DELETE /api/student/backend, POST .../backend/upload-url|publish, PUT .../backend/env, DELETE .../backend/env/<NAME>,
  GET .../backend/logs?minutes=&limit=&errors=1                     the newest log lines of the student's backend (see backends.py)
  GET  /api/student/ai                   refresh the AI usage numbers (ai.py)
  GET  /api/student/storage              storage usage including the file count and size (storage.py)
  GET  /api/student/traces[?limit&before]  /{trace_id}   the student's own app's traces, only as the teacher allows (traces.py)
  GET|POST|DELETE /api/student/knowledge[/collections|/{shelf}/documents[/{id}[/start]]]   the student's own book shelf for the kit's folder sync (knowledge.py student_route)

A token is active until expires_at, "expired" (read-only) for GRACE_DAYS, then "offline".
Only a SHA-256 of each token is stored. Teacher and student sessions use different signing keys.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

RESERVED = {"sites", "uploads", "archive", "deploy", "www", "api", "admin", "mail", "ftp", "app", "apps", "static", "assets", "bs", "lenden"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
GRACE_DAYS = int(os.environ.get("OFFLINE_GRACE_DAYS", "3"))
WARN_DAYS = int(os.environ.get("WARN_DAYS", "3"))
MAX_DAYS = 365
ROOT_DOMAIN = os.environ.get("ROOT_DOMAIN", "")
TEACHER_SESSION_SECONDS = 8 * 3600
STUDENT_SESSION_SECONDS = 12 * 3600
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
DAY = 86400


class PortalError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------- storage ----------
# One DynamoDB table, string key "pk":  tok#<sha256>  token record,  slug#<slug>  slug lock,  fail#<ip>  login failures.

class DynamoStore:
    def __init__(self, table_name):
        import boto3
        self.table = boto3.resource("dynamodb").Table(table_name)

    def get(self, pk):
        item = self.table.get_item(Key={"pk": pk}).get("Item")
        return _plain(item) if item else None

    def put(self, item, if_absent=False):
        from botocore.exceptions import ClientError
        extra = {"ConditionExpression": "attribute_not_exists(pk)"} if if_absent else {}
        try:
            self.table.put_item(Item=item, **extra)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def delete(self, pk):
        self.table.delete_item(Key={"pk": pk})

    def list_prefix(self, prefix):
        from boto3.dynamodb.conditions import Attr
        items, kwargs = [], {"FilterExpression": Attr("pk").begins_with(prefix)}
        while True:
            page = self.table.scan(**kwargs)
            items.extend(_plain(i) for i in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def update(self, pk, fields, remove=()):
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": v for i, v in enumerate(fields.values())}
        names.update({f"#r{i}": k for i, k in enumerate(remove)})
        expr = ("SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields))) if fields else "") + \
               (" REMOVE " + ", ".join(f"#r{i}" for i in range(len(remove))) if remove else "")
        args = dict(Key={"pk": pk}, UpdateExpression=expr.strip(), ExpressionAttributeNames=names,
                    ConditionExpression="attribute_exists(pk)", ReturnValues="ALL_NEW")
        if values:
            args["ExpressionAttributeValues"] = values
        return _plain(self.table.update_item(**args)["Attributes"])

    def counter_add(self, pk, delta=1, limit=None, ttl=None, now=None):
        """Atomically add to item["count"]. With a limit, the new count may never exceed it: returns the new count,
        or None when the limit would be exceeded (no overshoot, even with parallel callers)."""
        from botocore.exceptions import ClientError
        if limit is not None and limit <= 0:
            return None
        names, values = {"#c": "count", "#u": "updated_at"}, {":d": delta, ":u": int(now if now is not None else time.time())}
        update = "SET #u = :u"
        if ttl is not None:
            names["#t"], values[":t"] = "ttl", ttl
            update += ", #t = :t"
        update += " ADD #c :d"
        extra = {}
        if limit is not None:
            values[":max"] = limit - delta
            extra["ConditionExpression"] = "attribute_not_exists(#c) OR #c <= :max"
        try:
            result = self.table.update_item(Key={"pk": pk}, UpdateExpression=update, ExpressionAttributeNames=names,
                                            ExpressionAttributeValues=values, ReturnValues="UPDATED_NEW", **extra)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return int(result["Attributes"]["count"])

    def counter_sub(self, pk, now=None):
        """Decrement item["count"] but never below zero. Returns the new count or None if it was already zero."""
        from botocore.exceptions import ClientError
        try:
            result = self.table.update_item(
                Key={"pk": pk}, UpdateExpression="SET #u = :u ADD #c :n", ConditionExpression="#c > :z",
                ExpressionAttributeNames={"#c": "count", "#u": "updated_at"},
                ExpressionAttributeValues={":n": -1, ":z": 0, ":u": int(now if now is not None else time.time())}, ReturnValues="UPDATED_NEW")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return int(result["Attributes"]["count"])


class MemoryStore:
    """Same interface as DynamoStore; used by tests."""

    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    def get(self, pk):
        return json.loads(json.dumps(self.items[pk])) if pk in self.items else None

    def put(self, item, if_absent=False):
        if if_absent and item["pk"] in self.items:
            return False
        self.items[item["pk"]] = json.loads(json.dumps(item))
        return True

    def delete(self, pk):
        self.items.pop(pk, None)

    def list_prefix(self, prefix):
        return [self.get(pk) for pk in sorted(self.items) if pk.startswith(prefix)]

    def update(self, pk, fields, remove=()):
        self.items[pk].update(json.loads(json.dumps(fields)))
        for key in remove:
            self.items[pk].pop(key, None)
        return self.get(pk)

    def counter_add(self, pk, delta=1, limit=None, ttl=None, now=None):
        with self.lock:
            if limit is not None and limit <= 0:
                return None
            item = self.items.setdefault(pk, {"pk": pk})
            count = item.get("count", 0)
            if limit is not None and count + delta > limit:
                return None
            item["count"], item["updated_at"] = count + delta, int(now if now is not None else time.time())
            if ttl is not None:
                item["ttl"] = ttl
            return item["count"]

    def counter_sub(self, pk, now=None):
        with self.lock:
            item = self.items.get(pk)
            if not item or item.get("count", 0) <= 0:
                return None
            item["count"] -= 1
            item["updated_at"] = int(now if now is not None else time.time())
            return item["count"]


def _plain(value):
    from decimal import Decimal
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


_store = None

# Names already used by CLI students (their folders share sites/<name>/). app.py plugs in the real lookup.
legacy_names = lambda: set()  # noqa: E731
# Brings archive/<slug>/ back to sites/<slug>/ (set by app.py).
restore_site = lambda slug, tenant_id=None: None  # noqa: E731
archive_site = lambda slug, tenant_id=None: None  # noqa: E731  (moves sites/<slug>/ to archive/<slug>/)


def db():
    global _store
    if _store is None:
        _store = DynamoStore(os.environ["TOKENS_TABLE"])
    return _store


# ---------- teacher account ----------
# SSM parameter JSON: {"username", "salt", "hash", "secret"} (all base64). Written by `node admin.mjs teacher-setup`.
_teacher = {"loaded_at": 0.0, "value": {}}


def teacher_config():
    if time.time() - _teacher["loaded_at"] > 60:
        import boto3
        value = boto3.client("ssm").get_parameter(Name=os.environ["TEACHER_PARAM"])["Parameter"]["Value"]
        _teacher["value"] = json.loads(value or "{}")
        _teacher["loaded_at"] = time.time()
    return _teacher["value"]


def scrypt_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def require_teacher_config():
    cfg = teacher_config()
    if not cfg.get("hash") or not cfg.get("secret"):
        raise PortalError(503, "Teacher account is not set up yet. Run: node admin.mjs teacher-setup <username>")
    return cfg


# ---------- sessions ----------
def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signing_key(role):
    secret = unb64(require_teacher_config()["secret"])
    return hmac.new(secret, f"{role}-session".encode(), hashlib.sha256).digest()


def sign_session(role, subject, ttl, now):
    payload = b64(json.dumps({"r": role, "s": subject, "e": int(now + ttl)}).encode())
    return payload + "." + b64(hmac.new(signing_key(role), payload.encode(), hashlib.sha256).digest())


def verify_session(role, event, now):
    header = (event.get("headers") or {}).get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    payload, _, sig = token.partition(".")
    expected = b64(hmac.new(signing_key(role), payload.encode(), hashlib.sha256).digest())
    if not payload or not hmac.compare_digest(sig.encode(), expected.encode()):
        raise PortalError(401, "Please sign in again.")
    try:
        data = json.loads(unb64(payload))
    except ValueError:
        raise PortalError(401, "Please sign in again.")
    if data.get("r") != role or data.get("e", 0) <= now:
        raise PortalError(401, "Your session has expired. Please sign in again.")
    return data["s"]


# ---------- tokens ----------
def token_hash(token):
    return hashlib.sha256(token.strip().encode()).hexdigest()


def status_of(item, now):
    if item.get("revoked"):
        return "revoked"
    if item.get("archived"):  # taken offline (by the sweeper or by the teacher): uploads stay blocked until restored
        return "offline"
    if now < item["expires_at"]:
        return "active"
    if now < item["expires_at"] + GRACE_DAYS * DAY:
        return "expired"
    return "offline"


def view(item, now, site_url):
    status = status_of(item, now)
    regen = item.get("regen") or {"state": "none"}
    return {
        "id": item["id"], "slug": item["slug"], "student_name": item.get("student_name", ""), "hint": item["hint"],
        "url": site_url(item["slug"]), "status": status, "created_at": item["created_at"],
        "expires_at": item["expires_at"], "offline_at": item["expires_at"] + GRACE_DAYS * DAY,
        "last_deploy_at": item.get("last_deploy_at"), "can_deploy": status == "active",
        "warn_at": item["expires_at"] - WARN_DAYS * DAY, "regen": regen, "archived": bool(item.get("archived")),
        "archived_at": item.get("archived_at"),
        "can_request_regen": status != "revoked" and (status == "offline" or now >= item["expires_at"] - WARN_DAYS * DAY) and regen["state"] != "pending",
        "custom_domain": domain_view(item),
        "backend": backend_view(item, now),
        "ai": ai_view(item, now),
        "storage": storage_view(item, now),
        "knowledge": knowledge_view(item, now),
        "traces": traces_view(item),
    }


def backend_view(item, now):
    import backends
    return backends.view(item, now)


def ai_view(item, now):
    import ai
    return ai.view(item, now)


def storage_view(item, now):
    import storage
    return storage.view(item, now)


def traces_view(item):
    import traces
    return traces.view_settings(item)


def knowledge_view(item, now):
    import knowledge
    return knowledge.view(item, now)


DOMAIN_MESSAGES = {
    "waiting_dns": "Waiting for your DNS record. Add the CNAME shown below; this page continues on its own.",
    "issuing_certificate": "DNS found. Issuing your HTTPS certificate (usually 1-5 minutes).",
    "finishing": "Certificate ready. Finishing setup (usually 1-5 minutes).",
    "live": "Your custom domain is live.",
    "removing": "Removing your custom domain...",
}


def domain_view(item):
    domain = item.get("domain", "")
    state = ("removing" if item.get("domain_removing") else item.get("domain_state") or "waiting_dns") if domain else "none"
    return {
        "available": bool(os.environ.get("TENANT_DISTRIBUTION_ID")), "enabled": bool(item.get("domain_enabled")),
        "domain": domain, "state": state, "message": DOMAIN_MESSAGES.get(state, ""),
        "cname_target": os.environ.get("ROUTING_ENDPOINT", ""), "site_url": f"https://{domain}/" if domain else "",
    }


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30].strip("-")


def random_slug():
    return "s-" + secrets.token_hex(3)


def claim_slug(requested, student_name, token_id):
    """Teacher-set slug > slug from student name > random. Returns the slug now reserved for token_id."""
    if requested:
        slug = requested.strip().lower()
        if not SLUG_RE.match(slug) or slug in RESERVED:
            raise PortalError(400, "Slug must be 1-30 lowercase letters, digits or '-' and not a reserved name.")
        if slug in legacy_names() or not db().put({"pk": f"slug#{slug}", "token": token_id}, if_absent=True):
            raise PortalError(409, f"The name '{slug}' is already taken.")
        return slug
    base = slugify(student_name)
    candidates = [base] if base and base not in RESERVED and SLUG_RE.match(base) else []
    candidates += [f"{base[:23]}-{secrets.token_hex(2)}" if base else random_slug() for _ in range(5)]
    for slug in candidates:
        slug = slug.strip("-")
        if SLUG_RE.match(slug) and slug not in RESERVED and slug not in legacy_names() and db().put({"pk": f"slug#{slug}", "token": token_id}, if_absent=True):
            return slug
    raise PortalError(500, "Could not find a free site name. Try setting one explicitly.")


def parse_days(body):
    days = body.get("days")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise PortalError(400, f"days must be a whole number from 1 to {MAX_DAYS}.")
    return days


def create_token(body, now, site_url):
    days = parse_days(body)
    name = str(body.get("student_name") or "").strip()
    if len(name) > 60:
        raise PortalError(400, "Student name is too long (max 60 characters).")
    token = "mds_" + secrets.token_urlsafe(16)
    tid = token_hash(token)
    slug = claim_slug(body.get("slug"), name, tid)
    item = {
        "pk": f"tok#{tid}", "id": tid, "slug": slug, "student_name": name, "hint": token[-4:],
        "created_at": now, "expires_at": now + days * DAY, "revoked": False, "regen": {"state": "none"},
    }
    try:
        db().put(item)
    except Exception:
        db().delete(f"slug#{slug}")
        raise
    return {"token": token, "item": view(item, now, site_url)}


def get_token(tid):
    item = db().get(f"tok#{tid}") if re.fullmatch(r"[0-9a-f]{64}", tid or "") else None
    if not item:
        raise PortalError(404, "Token not found.")
    return item


# ---------- routing ----------
def handle(event, site_url, now=None):
    now = int(now if now is not None else time.time())
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"].rstrip("/")
    try:
        body = parse_body(event) if method in ("POST", "PUT") else {}
        return dispatch(method, path, body, event, now, site_url)
    except PortalError as e:
        return reply(e.status, error=str(e))


def dispatch(method, path, body, event, now, site_url):
    if (method, path) == ("POST", "/api/teacher/login"):
        return teacher_login(body, event, now)
    if (method, path) == ("POST", "/api/student/login"):
        return student_login(body, now, site_url)
    if (method, path) == ("POST", "/api/student/regen-request"):
        return regen_request(student_token(event, now), body, now, site_url)
    if (method, path) == ("GET", "/api/student/me"):
        return reply(200, token=view(student_token(event, now), now, site_url))
    if path == "/api/student/backend" or path.startswith("/api/student/backend/"):
        return student_backend(method, path, body, event, now, site_url)
    if (method, path) == ("GET", "/api/student/ai"):
        import ai
        return reply(200, token=view(ai.refresh_usage(student_token(event, now), now), now, site_url))
    if (method, path) == ("GET", "/api/student/storage"):
        import storage
        item = student_token(event, now)
        files = storage.files_usage(item) if storage.available() and storage.settings(item).get("enabled") else None
        return reply(200, token=view(item, now, site_url), files=files)
    if method == "GET" and (path == "/api/student/traces" or path.startswith("/api/student/traces/")):
        import traces
        item = student_token(event, now)
        if path == "/api/student/traces":
            q = event.get("queryStringParameters") or {}
            return reply(200, **traces.student_list(item, q.get("limit") or 20, int(q["before"]) if str(q.get("before", "")).isdigit() else None))
        return reply(200, **traces.student_get(item, path.rsplit("/", 1)[1]))
    if path == "/api/student/knowledge" or path.startswith("/api/student/knowledge/"):
        import knowledge
        routed = knowledge.student_route(student_token(event, now), method, path, body, now)
        if routed is None:
            return reply(404, error="Unknown route.")
        return reply(routed[0], **routed[1])
    if path in ("/api/student/domain", "/api/student/domain/status") and method in ("PUT", "DELETE", "GET"):
        import domains
        item = student_token(event, now)
        if (method, path) == ("PUT", "/api/student/domain"):
            item = domains.set_domain(item, body, now)
        elif (method, path) == ("DELETE", "/api/student/domain"):
            item = domains.remove_domain(item)
        elif (method, path) == ("GET", "/api/student/domain/status"):
            item = domains.advance(item)
        else:
            return reply(404, error="Unknown route.")
        extra = {"dns": domains.dns_hint(item)} if method != "DELETE" and domain_view(item)["state"] == "waiting_dns" else {}
        return reply(200, token=view(item, now, site_url), **extra)

    if path.startswith("/api/admin/"):
        verify_session("teacher", event, now)
        if (method, path) == ("GET", "/api/admin/tokens"):
            items = sorted(db().list_prefix("tok#"), key=lambda i: -i["created_at"])
            return reply(200, tokens=[view(i, now, site_url) for i in items])
        if (method, path) == ("POST", "/api/admin/tokens"):
            return reply(201, **create_token(body, now, site_url))
        if (method, path) == ("GET", "/api/admin/requests"):
            pending = [i for i in db().list_prefix("tok#") if (i.get("regen") or {}).get("state") == "pending" and not i.get("revoked")]
            pending.sort(key=lambda i: i["regen"]["requested_at"])
            return reply(200, requests=[view(i, now, site_url) for i in pending])
        backend = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/backend", path)
        if method == "POST" and backend:
            import backends
            item = backends.set_backend(get_token(backend.group(1)), body, now)
            return reply(200, token=view(item, now, site_url))
        ai_route = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/ai", path)
        if method == "POST" and ai_route:
            import ai
            item = ai.set_ai(get_token(ai_route.group(1)), body, now)
            return reply(200, token=view(item, now, site_url))
        if (method, path) == ("GET", "/api/admin/storage"):
            import storage
            return reply(200, **storage.overview(now))
        clear_route = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/storage/clear", path)
        if method == "POST" and clear_route:
            import storage
            item = get_token(clear_route.group(1))
            cleared = storage.clear(item, body.get("scope"), now)
            return reply(200, cleared=cleared, token=view(item, now, site_url))
        storage_route = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/storage", path)
        if method == "POST" and storage_route:
            import storage
            item = storage.set_storage(get_token(storage_route.group(1)), body, now)
            return reply(200, token=view(item, now, site_url))
        trace_set = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/traces", path)
        if method == "POST" and trace_set:
            import traces
            item = get_token(trace_set.group(1))
            traces.set_settings(item, body)
            return reply(200, token=view(db().get(item["pk"]) or item, now, site_url))
        if path == "/api/admin/worker" and method == "GET":
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
        trace_clear = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/traces/clear", path)
        if method == "POST" and trace_clear:
            import traces
            item = get_token(trace_clear.group(1))
            return reply(200, **traces.clear_student(item["slug"]))
        if method == "GET" and path == "/api/admin/traces":
            import traces
            q = event.get("queryStringParameters") or {}
            before = int(q["before"]) if str(q.get("before", "")).isdigit() else None
            return reply(200, **traces.list_traces(q.get("slug") or None, q.get("day") or None, q.get("limit") or 50, before))
        if method == "GET" and path == "/api/admin/traces/summary":
            import traces
            qs = event.get("queryStringParameters") or {}
            return reply(200, **traces.daily_view(now, int(qs["days"]) if str(qs.get("days", "")).isdigit() else 14))
        if method == "GET" and path == "/api/admin/traces/status":
            import traces
            return reply(200, **traces.status(now))
        if method == "GET" and path == "/api/admin/traces/export":
            import traces
            q = event.get("queryStringParameters") or {}
            cursor = None
            if q.get("cursor"):
                try:
                    cursor = json.loads(q["cursor"])
                except ValueError:
                    raise PortalError(400, "That cursor is not valid.")
            return reply(200, **traces.export_page(q.get("slug") or None, q.get("shelf") or None, q.get("from") or None, q.get("to") or None, cursor))
        trace_one = re.fullmatch(r"/api/admin/traces/([a-z0-9][a-z0-9-]{0,29})/(tr_[0-9a-f]{16})", path)
        if method == "GET" and trace_one:
            import traces
            return reply(200, **traces.get_trace(trace_one.group(1), trace_one.group(2)))
        if method == "DELETE" and trace_one:
            import traces
            return reply(200, **traces.delete_trace(trace_one.group(1), trace_one.group(2)))
        know_clear = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/knowledge/clear", path)
        if method == "POST" and know_clear:
            import knowledge
            item = get_token(know_clear.group(1))
            return reply(200, cleared=knowledge.portal_call(knowledge.clear, item, now), token=view(item, now, site_url))
        know_route = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/knowledge", path)
        if method == "POST" and know_route:
            import knowledge
            item = knowledge.set_knowledge(get_token(know_route.group(1)), body, now)
            return reply(200, token=view(item, now, site_url))
        if path == "/api/admin/knowledge" or path.startswith("/api/admin/knowledge/"):
            import knowledge
            routed = knowledge.admin_route(method, path, body, now)
            if routed:
                return reply(routed[0], **routed[1])
        access = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/domain-access", path)
        if method == "POST" and access:
            import domains
            item = domains.set_access(get_token(access.group(1)), body.get("enabled"))
            return reply(200, token=view(item, now, site_url))
        match = re.fullmatch(r"/api/admin/tokens/([0-9a-f]{64})/(revoke|extend|offline|online|approve|deny)", path)
        if method == "POST" and match:
            return token_action(get_token(match.group(1)), match.group(2), body, now, site_url)
    return reply(404, error="Unknown route.")


def student_backend(method, path, body, event, now, site_url):
    import backends
    item = student_token(event, now)
    sub = path[len("/api/student/backend"):]
    env_name = re.fullmatch(r"/env/([A-Za-z_][A-Za-z0-9_]*)", sub)
    if (method, sub) == ("GET", ""):
        item = backends.advance(item, now)  # setup advances one step per call, like the custom-domain poll
    elif (method, sub) == ("POST", "/upload-url"):
        return reply(200, **backends.upload_url(item, now))
    elif (method, sub) == ("POST", "/publish"):
        item = backends.publish(item, now)
    elif (method, sub) == ("PUT", "/env"):
        item = backends.set_env(item, body.get("name"), body.get("value"), now)
    elif method == "DELETE" and env_name:
        item = backends.delete_env(item, env_name.group(1), now)
    elif (method, sub) == ("GET", "/logs"):
        query = event.get("queryStringParameters") or {}
        return reply(200, **backends.logs(item, now, minutes=query.get("minutes"), limit=query.get("limit"), errors=query.get("errors") in ("1", "true")))
    elif (method, sub) == ("DELETE", ""):
        item = backends.remove(item, now)
    else:
        return reply(404, error="Unknown route.")
    return reply(200, token=view(item, now, site_url))


def parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw)
    except ValueError:
        raise PortalError(400, "Request body must be JSON.")
    if not isinstance(body, dict):
        raise PortalError(400, "Request body must be a JSON object.")
    return body


def client_ip(event):
    """Behind CloudFront the socket IP is the edge's, so use the viewer IP the CloudFront function adds,
    but only when the request also carries our secret origin header (direct API calls could forge it)."""
    headers = event.get("headers") or {}
    secret = os.environ.get("ORIGIN_SECRET", "")
    if secret and hmac.compare_digest(headers.get("x-origin-secret", "").encode(), secret.encode()) and headers.get("x-viewer-ip"):
        return headers["x-viewer-ip"].strip()
    return event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")


def teacher_login(body, event, now):
    cfg = require_teacher_config()
    fail_key = f"fail#{client_ip(event)}"
    record = db().get(fail_key) or {}
    if record.get("locked_until", 0) > now:
        raise PortalError(429, "Too many failed attempts. Try again in a few minutes.")

    username, password = str(body.get("username", "")), str(body.get("password", ""))
    digest = scrypt_hash(password, unb64(cfg["salt"]))  # always computed, so timing doesn't reveal a valid username
    ok = hmac.compare_digest(username.encode(), cfg["username"].encode()) & hmac.compare_digest(digest, unb64(cfg["hash"]))
    if not ok:
        count = record.get("count", 0) + 1 if record.get("first_at", 0) > now - LOCKOUT_SECONDS else 1
        db().put({
            "pk": fail_key, "count": count, "first_at": record.get("first_at", now) if count > 1 else now,
            "locked_until": now + LOCKOUT_SECONDS if count >= MAX_FAILED_LOGINS else 0, "ttl": now + 2 * LOCKOUT_SECONDS,
        })
        raise PortalError(401, "Wrong username or password.")
    db().delete(fail_key)
    return reply(200, session=sign_session("teacher", cfg["username"], TEACHER_SESSION_SECONDS, now),
                 expires_at=now + TEACHER_SESSION_SECONDS)


def student_login(body, now, site_url):
    token = str(body.get("token", "")).strip()
    item = db().get(f"tok#{token_hash(token)}") if token else None
    if not item or item.get("revoked"):
        raise PortalError(401, "This token is not valid. Check it or ask your teacher.")
    return reply(200, session=sign_session("student", item["id"], STUDENT_SESSION_SECONDS, now),
                 token=view(item, now, site_url))


def student_token(event, now):
    item = db().get(f"tok#{verify_session('student', event, now)}")
    if not item or item.get("revoked"):
        raise PortalError(401, "This token is no longer valid.")
    return item


def reactivate(item, expires_at, fields):
    """Set a new expiry; if the site was already taken offline, bring it back first."""
    if item.get("archived"):
        restore_site(item["slug"], tenant_id=item.get("tenant_id"))
        fields = {**fields, "archived": False}
    return db().update(item["pk"], {**fields, "expires_at": expires_at})


def take_offline(item, now):
    """Archive the site (idempotent: safe to repeat if an earlier run died halfway)."""
    if not item.get("archived"):
        archive_site(item["slug"], tenant_id=item.get("tenant_id"))
    return db().update(item["pk"], {"archived": True, "archived_at": item.get("archived_at") or now})


def sweep(now=None):
    """Daily job: archive the site of every token that is past expiry + grace. Archives are never deleted automatically."""
    now = int(now if now is not None else time.time())
    done, failed = [], []
    for item in db().list_prefix("tok#"):
        if item.get("archived") or item.get("revoked") or status_of(item, now) != "offline":
            continue
        try:
            take_offline(item, now)
            done.append(item["slug"])
        except Exception as e:  # one bad site must not stop the rest; it is retried tomorrow
            failed.append(f"{item['slug']}: {e}")
    removed = []
    if os.environ.get("TENANT_DISTRIBUTION_ID"):
        import domains
        removed, failed_removals = domains.finish_pending_removals()
        failed += failed_removals
    import ai
    ai_result = ai.sweep(now)  # disables the AI key of every student who is no longer active (and re-enables after an extension)
    failed += ai_result["failed"]
    result = {"archived": done, "failed": failed, "domains_removed": removed}
    if ai_result["changed"]:
        result["ai_keys_changed"] = ai_result["changed"]
    return result


def token_action(item, action, body, now, site_url):
    regen = item.get("regen") or {}
    if action == "revoke":
        item = db().update(item["pk"], {"revoked": True})
    elif action == "offline":
        item = take_offline(item, now)
    elif action == "online":  # bring a taken-offline site back WITHOUT changing the expiry (an expired token needs an extension, or the sweeper would archive it again)
        if item.get("revoked"):
            raise PortalError(409, "This token is revoked.")
        if not item.get("archived"):
            raise PortalError(409, "The site is already online.")
        if now >= item["expires_at"]:
            raise PortalError(409, "This token has expired. Extend it to bring the site back.")
        item = reactivate(item, item["expires_at"], {})
    elif action == "extend":
        days = parse_days(body)
        if item.get("revoked"):
            raise PortalError(409, "This token is revoked.")
        item = reactivate(item, max(item["expires_at"], now) + days * DAY, {})
    else:
        if regen.get("state") != "pending" or item.get("revoked"):
            raise PortalError(409, "There is no pending request for this token.")
        if action == "approve":
            days = parse_days(body)
            item = reactivate(item, now + days * DAY, {"regen": {**regen, "state": "approved", "resolved_at": now, "days": days}})
        else:
            reason = str(body.get("reason") or "").strip()[:300]
            item = db().update(item["pk"], {"regen": {**regen, "state": "denied", "resolved_at": now, "reason": reason}})
    import ai
    item = ai.sync_lifecycle(item, now)  # revoke/offline/expiry disable the student's AI key; extend/approve enable it again
    return reply(200, token=view(item, now, site_url))


def regen_request(item, body, now, site_url):
    v = view(item, now, site_url)
    if v["regen"]["state"] == "pending":
        raise PortalError(409, "You already have a request waiting for your teacher.")
    if not v["can_request_regen"]:
        raise PortalError(403, f"You can request regeneration in the last {WARN_DAYS} days before your token expires.")
    note = str(body.get("note") or "").strip()
    if len(note) > 300:
        raise PortalError(400, "The note is too long (max 300 characters).")
    item = db().update(item["pk"], {"regen": {"state": "pending", "requested_at": now, "note": note}})
    return reply(200, token=view(item, now, site_url))


def reply(status, **body):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}
