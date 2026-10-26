"""A tiny class storage service for the helper tests: the REAL storage_api code on in-memory DynamoDB and S3, over HTTP.

    python tests/storage_fixture.py          prints one JSON line {"url", "key", "tight_key", "off_key"} and then serves until killed

  key        a student with the default caps
  tight_key  a student with a 2-operation allowance and a 1 MB database (to see allowance_used and quota_exceeded)
  off_key    a student whose teacher has switched storage off (to see not_enabled)
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d",
                  KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox",
                  ORIGIN_SECRET="s", STORAGE_ENABLED="true", STORAGE_URL="http://unused")
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import portal  # noqa: E402
import storage  # noqa: E402
import storage_api  # noqa: E402
from fakes import FakeS3, store  # noqa: E402

server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)  # only to learn a free port
port = server.server_address[1]
server.server_close()
backends._clients["s3"] = FakeS3(port)
portal._store = portal.MemoryStore()
now = int(time.time())


def student(name, **caps):
    made = portal.create_token({"days": 30, "student_name": name}, now, lambda slug: f"https://{slug}.example.com/")
    item = portal.get_token(made["item"]["id"])
    item = backends.set_backend(item, {"enabled": True}, now)
    item = storage.set_storage(item, {"enabled": True, **caps}, now)
    return item


keys = {
    "key": storage.settings(student("Fixture"))["key"],
    "tight_key": storage.settings(student("Tight", ops_limit=2, data_mb=1))["key"],
}
off = student("Off")
storage.set_storage(off, {"enabled": False}, now)
keys["off_key"] = storage.settings(portal.get_token(off["id"]))["key"]


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

    def storage(self, method):
        length = int(self.headers.get("content-length") or 0)
        path, _, query = self.path.partition("?")
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        event = {"rawPath": path[len("/storage"):] or "/", "queryStringParameters": params or None,
                 "body": self.rfile.read(length).decode() if length else None,
                 "requestContext": {"http": {"method": method}}, "headers": {k.lower(): v for k, v in self.headers.items()}}
        result = storage_api.handler(event, None)
        self.send(result["statusCode"], result["body"].encode())

    def do_GET(self):
        if self.path.startswith("/s3/"):
            found = store.get(self.path[len("/s3/"):].split("?")[0])
            return self.send(200, found["Body"], "application/octet-stream") if found else self.send(404, b"", "text/plain")
        self.storage("GET")

    def do_PUT(self):
        self.storage("PUT")

    def do_DELETE(self):
        self.storage("DELETE")

    def do_POST(self):
        if self.path != "/s3-post":
            return self.storage("POST")
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
        if last != "file":  # real S3 refuses a form whose file is not the last field
            return self.send(400, b"file must be the last field", "text/plain")
        store[fields["key"]] = {"Body": content}
        self.send(204, b"", "text/plain")


httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
print(json.dumps({"url": f"http://127.0.0.1:{port}/storage", **keys}), flush=True)
httpd.serve_forever()
