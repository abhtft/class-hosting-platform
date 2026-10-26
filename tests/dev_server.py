"""Local preview of the whole portal: static files from web/ + the REAL Lambda code on /api/* with in-memory AWS.

    python tests/dev_server.py [port]        then open http://127.0.0.1:8777/

Teacher login: teacher / teacher-pass-123.  Student tokens for the demo data are printed at startup.
State lives in memory only; restart to reset.
"""
import json
import mimetypes
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
os.environ.update(AWS_REGION="us-east-1", AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
                  BUCKET="b", DISTRIBUTION_ID="E1", DISTRIBUTION_DOMAIN="d123.cloudfront.net", ROOT_DOMAIN="example.edu",
                  KEYS_PARAM="/k", TENANT_DISTRIBUTION_ID="EMT", ROUTING_ENDPOINT="route.cloudfront.net",
                  BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="dev-origin-secret", GATEWAY_CACHE_SECONDS="0",
                  AI_ENABLED="true", AI_SECRET_NAME="openrouter", AI_DEFAULT_MODEL="anthropic/claude-haiku-5.5", AI_DEFAULT_LIMIT_USD="0.25",
                  STORAGE_ENABLED="true", STORAGE_URL=f"http://127.0.0.1:{port}/storage")
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import app  # noqa: E402
import backends  # noqa: E402
import domains  # noqa: E402
import gateway  # noqa: E402
import openrouter  # noqa: E402
import portal  # noqa: E402
import storage_api  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeOpenRouter, FakeS3, FakeSSM, FakeTenantCloudFront, store  # noqa: E402

WEB = os.path.join(here, "..", "web")
DOWNLOADS = json.load(open(os.path.join(here, "..", "portal-downloads.json"), encoding="utf8"))
_kit = {}


def kit_file(name):
    """starter-kit.zip, kit-version.json and the MCP package files, built once with the same code portal-publish uses."""
    if not _kit:
        import subprocess
        import tempfile
        folder = tempfile.mkdtemp(prefix="dev-kit-")
        subprocess.run(["node", os.path.join(here, "..", "starter", "publish-files.mjs"), folder, f"http://127.0.0.1:{port}"], check=True, capture_output=True)
        for entry in os.listdir(folder):
            _kit[entry] = open(os.path.join(folder, entry), "rb").read()
    return _kit[name]


class DevTenantCloudFront(FakeTenantCloudFront):
    """Advances custom-domain setup with timers so the student page can be watched: each step takes STEP seconds.
    Any domain works; the pretend CNAME becomes visible STEP seconds after the first attempt."""
    STEP = 6

    def __init__(self):
        super().__init__()
        self.first_seen, self.created, self.changed = {}, {}, {}
        self.main_invalidations = []

    def create_distribution_tenant(self, **kw):
        domain = kw["Domains"][0]["Domain"]
        self.first_seen.setdefault(domain, time.time())
        self.dns_ok = lambda d: time.time() - self.first_seen[d] >= self.STEP
        tenant = super().create_distribution_tenant(**kw)
        self.created[tenant["DistributionTenant"]["Id"]] = self.changed[tenant["DistributionTenant"]["Id"]] = time.time()
        return tenant

    def get_distribution_tenant(self, Identifier):
        self.deployed = time.time() - self.changed.get(Identifier, 0) >= self.STEP
        return super().get_distribution_tenant(Identifier)

    def get_managed_certificate_details(self, Identifier):
        self.cert_status = "issued" if time.time() - self.created.get(Identifier, 0) >= self.STEP else "pending-validation"
        return super().get_managed_certificate_details(Identifier)

    def update_distribution_tenant(self, Id, IfMatch, Domains, **kw):
        super().update_distribution_tenant(Id, IfMatch, Domains, **kw)
        self.changed[Id] = time.time()


domains._client = app.cloudfront = DevTenantCloudFront()
app.s3, app.ssm = FakeS3(port), FakeSSM()
# Student backends: a fake Lambda that really runs the uploaded Python/Node handler, behind the real gateway code
dev_logs = FakeLogs()
dev_lambda = FakeLambda(dev_logs)
dev_lambda.polls_until_active = 2
backends._clients.update({"lambda": dev_lambda, "logs": dev_logs, "s3": app.s3})
dev_router = FakeOpenRouter()  # the class AI connector, with a pretend OpenRouter; spend some with /__dev/ai-spend?slug=alice&usd=0.1
openrouter.transport = dev_router
openrouter._secret.update(value="mgmt-secret", at=10**12)
salt = b"dev-salt-dev-sal"
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={
    "username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("teacher-pass-123", salt)), "secret": portal.b64(b"d" * 32)})

DAY = portal.DAY
t0 = int(time.time())


def seed(name, expires_in_days, created_days_ago=30, **fields):
    made = portal.create_token({"days": 30, "student_name": name}, t0 - created_days_ago * DAY, app.site_url)
    tid = made["item"]["id"]
    portal.db().update(f"tok#{tid}", {"expires_at": t0 + int(expires_in_days * DAY), **fields})
    store[f"sites/{made['item']['slug']}/index.html"] = {"Body": f"<h1>{name}</h1>".encode()}
    print(f"  {name:<8} {made['token']}   (expires in {expires_in_days} days)")
    return tid


print("Demo students (paste a token at /student.html):")
seed("Alice", 20, last_deploy_at=t0 - 3600, domain_enabled=True, backend={"enabled": True, "runs_limit": 25, "runs_period": "month", "state": "none"})
seed("Bob", 2, regen={"state": "pending", "requested_at": t0 - 7200, "note": "Need more time for my final project"})
seed("Carol", -1)
dave = seed("Dave", -5, archived=True, archived_at=t0 - 2 * DAY)
seed("Erin", 10, revoked=True)
print("Teacher: teacher / teacher-pass-123")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("access-control-allow-origin", "*")  # like the bucket's CORS rule: the browser PUTs straight to "S3"
        self.end_headers()
        self.wfile.write(body)

    def api(self, method):
        length = int(self.headers.get("content-length") or 0)
        event = {"rawPath": self.path.split("?")[0], "routeKey": "ANY /api/{proxy+}", "body": self.rfile.read(length).decode() if length else "",
                 "requestContext": {"http": {"method": method, "sourceIp": self.client_address[0]}},
                 "headers": {k.lower(): v for k, v in self.headers.items()}}
        result = app.handler(event, None)
        self.send(result["statusCode"], result["body"].encode())

    def storage(self, method):
        """What API Gateway does for the class storage API: pass the request to storage_api.handler (path below /storage)."""
        length = int(self.headers.get("content-length") or 0)
        path, _, query = self.path.partition("?")
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        event = {"rawPath": path[len("/storage"):] or "/", "queryStringParameters": params or None,
                 "body": self.rfile.read(length).decode() if length else None,
                 "requestContext": {"http": {"method": method, "sourceIp": self.client_address[0]}},
                 "headers": {k.lower(): v for k, v in self.headers.items()}}
        result = storage_api.handler(event, None)
        self.send(result["statusCode"], result["body"].encode())

    def s3_post(self):
        """The fake presigned POST target: reads the multipart form and keeps the file under its 'key' field."""
        from email.parser import BytesParser
        from email.policy import default
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        message = BytesParser(policy=default).parsebytes(b"Content-Type: " + self.headers["content-type"].encode() + b"\r\n\r\n" + raw)
        fields, content = {}, b""
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if part.get_filename() is not None or name == "file":
                content = part.get_payload(decode=True)
            else:
                fields[name] = part.get_content()
        store[fields["key"]] = {"Body": content}
        self.send(204, b"", "text/plain")

    def gateway(self, method):
        """What CloudFront + API Gateway do in production: add the origin secret and the viewer IP, then call the gateway."""
        length = int(self.headers.get("content-length") or 0)
        path, _, query = self.path.partition("?")
        event = {"rawPath": path, "rawQueryString": query, "body": self.rfile.read(length).decode() if length else None,
                 "requestContext": {"http": {"method": method, "sourceIp": "10.0.0.1"}},
                 "headers": {**{k.lower(): v for k, v in self.headers.items()}, "x-origin-secret": "dev-origin-secret", "x-viewer-ip": self.client_address[0]}}
        result = gateway.handler(event, None)
        body = result["body"].encode()
        self.send_response(result["statusCode"])
        for name, value in result["headers"].items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.startswith("/storage/"):
            return self.storage("POST")
        if self.path == "/s3-post":
            return self.s3_post()
        if self.path.startswith("/b/"):
            return self.gateway("POST")
        self.api("POST")

    def do_OPTIONS(self):
        self.send_response(204)
        for name, value in (("access-control-allow-origin", "*"), ("access-control-allow-methods", "PUT"), ("access-control-allow-headers", "*")):
            self.send_header(name, value)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/storage/"):
            return self.storage("DELETE")
        if self.path.startswith("/b/"):
            return self.gateway("DELETE")
        self.api("DELETE")

    def do_PUT(self):
        if self.path.startswith("/storage/"):
            return self.storage("PUT")
        if self.path.startswith("/b/"):
            return self.gateway("PUT")
        if self.path.startswith("/api/"):
            return self.api("PUT")
        # otherwise: the fake presigned S3 upload
        length = int(self.headers["content-length"])
        store[self.path[len("/s3/"):]] = {"Body": self.rfile.read(length)}
        self.send(200, b"", "text/plain")

    def do_GET(self):
        if self.path.startswith("/__dev/storage-seed"):  # local demo only: pretend the student's app saved some data and files
            import storage
            query = dict(part.split("=", 1) for part in self.path.partition("?")[2].split("&") if "=" in part)
            lock = portal.db().get(f"slug#{query.get('slug', '')}")
            item = portal.db().get(f"tok#{lock['token']}") if lock else None
            if not item:
                return self.send(404, b'{"ok": false}')
            item = storage.set_storage(item, {"enabled": True}, int(time.time())) if not storage.is_on(item) else item
            for n in range(int(query.get("records", "12"))):
                storage.put_value(item, "notes", f"n{n:04d}", {"text": "demo note " * 20}, int(time.time()))
            for n in range(int(query.get("files", "3"))):
                store[f"userdata/{item['slug']}/demo{n}.bin"] = {"Body": b"x" * (200_000 * (n + 1))}
            return self.send(200, b'{"ok": true}')
        if self.path.startswith("/__dev/ai-spend"):  # local demo only: pretend the student's function used the AI
            query = dict(part.split("=", 1) for part in self.path.partition("?")[2].split("&") if "=" in part)
            key = dev_router.by_name(f"student:{query.get('slug', '')}")
            if key:
                dev_router.spend(key["hash"], float(query.get("usd", "0.01")))
            return self.send(200, json.dumps({"ok": bool(key), "usage": key and key["usage"]}).encode())
        if self.path.startswith("/storage/"):
            return self.storage("GET")
        if self.path.startswith("/s3/"):  # the fake presigned download link
            found = store.get(self.path[len("/s3/"):].split("?")[0])
            return self.send(200, found["Body"], "application/octet-stream") if found else self.send(404, b"", "text/plain")
        if self.path.startswith("/b/"):
            return self.gateway("GET")
        if self.path.startswith("/api/"):
            return self.api("GET")
        name = self.path.split("?")[0].lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(WEB, name))
        if name in ("starter-kit.zip", "kit-version.json", "mcp-version.json") or re.fullmatch(r"class-platform-mcp(-[\w.]+)?\.tgz", name):  # built on first request exactly like portal-publish does
            return self.send(200, kit_file(name), "application/zip" if name.endswith(".zip") else "application/gzip" if name.endswith(".tgz") else "application/json")
        if name in DOWNLOADS:  # the student downloads, published next to the pages (see portal-downloads.json)
            path = os.path.normpath(os.path.join(here, "..", DOWNLOADS[name]))
            return self.send(200, open(path, "rb").read(), "text/plain; charset=utf-8")
        if not path.startswith(os.path.normpath(WEB)) or not os.path.isfile(path):
            return self.send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send(200, open(path, "rb").read(), ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))


httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # bound (and accepting connections) from here on, so the banner below means "ready"
print("Backend demo: sign in as Alice, deploy a backend (see student/backend-template), then try  http://127.0.0.1:%d/b/alice/hello\n" % port, flush=True)
httpd.serve_forever()
