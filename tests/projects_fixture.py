"""The REAL portal and project-sync code on in-memory DynamoDB and S3, over HTTP, for the kit's project sync tests.

    python tests/projects_fixture.py     prints one JSON line {"url", "alice", "bob", "carol"} (student tokens; the teacher is "teacher" / "pw") and serves until killed

The S3 stand-in behaves like the real one where it matters: an upload form must have the file as its LAST field and the SHA-256 field must match the bytes.
"""
import base64
import hashlib
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d",
                  KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s")
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import portal  # noqa: E402
from fakes import FakeS3, store  # noqa: E402

now = lambda: int(time.time())  # noqa: E731
probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
port = probe.server_address[1]
probe.server_close()
portal._store = portal.MemoryStore()
salt = b"saltsaltsaltsalt"
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
backends._clients.update({"s3": FakeS3(port)})
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
tokens = {name: portal.create_token({"days": 30, "student_name": name}, now(), site_url)["token"] for name in ("alice", "bob", "carol")}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def api(self, method):
        length = int(self.headers.get("content-length") or 0)
        event = {"rawPath": self.path.split("?")[0], "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
                 "headers": {k.lower(): v for k, v in self.headers.items()}, "body": self.rfile.read(length).decode() if length else None}
        result = portal.handle(event, site_url, now=now())
        self.send(result["statusCode"], result["body"].encode())

    def do_GET(self):
        if self.path.startswith("/__corrupt/"):  # test only: damage a stored file the way a bad disk or a tampering member would
            key = self.path[len("/__corrupt/"):]
            store[key] = {"Body": b"corrupted" + store[key]["Body"]}
            return self.send(200, b"{}")
        if self.path.startswith("/s3/"):
            key = self.path[len("/s3/"):]
            return self.send(200, store[key]["Body"], "application/octet-stream") if key in store else self.send(404, b"", "text/plain")
        self.api("GET")

    def do_DELETE(self):
        self.api("DELETE")

    def do_POST(self):
        if self.path != "/s3-post":
            return self.api("POST")
        from email.parser import BytesParser
        from email.policy import default
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        message = BytesParser(policy=default).parsebytes(b"Content-Type: " + self.headers["content-type"].encode() + b"\r\n\r\n" + raw)
        fields, content, last = {}, b"", None
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            last = name
            if name == "file":
                content = part.get_payload(decode=True)
            else:
                fields[name] = part.get_content()
        if last != "file":
            return self.send(400, b"file must be the last field", "text/plain")
        if fields.get("x-amz-checksum-sha256") != base64.b64encode(hashlib.sha256(content).digest()).decode():
            return self.send(400, b"BadDigest", "text/plain")
        store[fields["key"]] = {"Body": content}
        self.send(204, b"", "text/plain")


httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
print(json.dumps({"url": f"http://127.0.0.1:{port}", **tokens}), flush=True)
httpd.serve_forever()
