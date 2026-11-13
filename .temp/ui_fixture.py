"""Local portal for looking at the teacher page: web/ files + the real portal code on in-memory fakes. python .temp/ui_fixture.py [port]  (login teacher / pw)"""
import json
import mimetypes
import os
import sys
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d",
                  KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s",
                  AI_SECRET_NAME="secret", KNOWLEDGE_ENABLED="true", KNOWLEDGE_URL="http://unused", KNOWLEDGE_WORKER_FUNCTION="kworker",
                  KNOWLEDGE_MONTHLY_CAP_USD="3", KNOWLEDGE_EMBEDDING_DIMS="256", STORAGE_ENABLED="true", STORAGE_URL="http://unused")
sys.path[:0] = [os.path.join(root, "lambda"), os.path.join(root, "tests")]
import backends, embeddings as em, gemini, knowledge, knowledge_core as kc, knowledge_store as ks, knowledge_worker, portal  # noqa: E402,E401
from fakes import FakeLambda, FakeLogs, FakeS3  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
now = lambda: int(time.time())  # noqa: E731
gemini._secret.update(value="k", at=time.time() + 10 ** 6)


def transport(provider, url, headers, body):
    rows = []
    for request in body["requests"]:
        row = [0.0] * 256
        for term in kc.tokenize(request["content"]["parts"][0]["text"]):
            row[zlib.crc32(term.encode()) % 256] += 1.0
        rows.append({"values": row})
    return 200, {"embeddings": rows}


em.transport = transport


class HttpBucket(ks.MemoryBucket):
    def presigned_post(self, key, max_bytes, expires=300):
        return {"url": f"http://127.0.0.1:{port}/s3-post", "fields": {"key": key}, "max_bytes": max_bytes, "expires_in": expires}


bucket = HttpBucket()
knowledge.use_bucket(bucket)
portal._store = portal.MemoryStore()
salt = b"saltsaltsaltsalt"
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
logs, lam = FakeLogs(), FakeLambda()
lam.logs = logs
lam.platform["kworker"] = lambda event: knowledge_worker.handler(event, now=now())
backends._clients.update({"lambda": lam, "logs": logs, "s3": FakeS3(port)})
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
for name in ("Asha", "Ravi"):
    made = portal.create_token({"days": 30, "student_name": name}, now(), site_url)
    item = portal.get_token(made["item"]["id"])
    item = backends.set_backend(item, {"enabled": True}, now())
    if name == "Asha":
        knowledge.set_knowledge(item, {"enabled": True}, now())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("access-control-allow-origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def api(self, method):
        length = int(self.headers.get("content-length") or 0)
        event = {"rawPath": self.path.split("?")[0], "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
                 "headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(length).decode() if length else None}
        result = portal.handle(event, site_url, now=now())
        self.send(result["statusCode"], result["body"].encode())

    def do_OPTIONS(self):
        self.send(204, b"")

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.api("GET")
        name = self.path.split("?")[0].lstrip("/") or "teacher.html"
        path = os.path.join(root, "web", name)
        if not os.path.isfile(path):
            return self.send(404, b"not found", "text/plain")
        self.send(200, open(path, "rb").read(), mimetypes.guess_type(path)[0] or "application/octet-stream")

    def do_DELETE(self):
        self.api("DELETE")

    def do_POST(self):
        if self.path != "/s3-post":
            return self.api("POST")
        from email.parser import BytesParser
        from email.policy import default
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        message = BytesParser(policy=default).parsebytes(b"Content-Type: " + self.headers["content-type"].encode() + b"\r\n\r\n" + raw)
        fields, content = {}, b""
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name == "file":
                content = part.get_payload(decode=True)
            else:
                fields[name] = part.get_content()
        bucket.objects[fields["key"]] = content
        self.send(204, b"", "text/plain")


print(f"teacher page: http://127.0.0.1:{port}/teacher.html  (teacher / pw)", flush=True)
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
