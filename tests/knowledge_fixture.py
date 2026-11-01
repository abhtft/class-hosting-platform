"""A tiny class knowledge service for the helper tests: the REAL knowledge_api, worker and search code on in-memory DynamoDB and S3, over HTTP.

    python tests/knowledge_fixture.py          prints one JSON line {"url", "key", "tight_key", "off_key", "library"} and then serves until killed

  key        a student with the default caps
  tight_key  a student with 2 searches a day and room for 1 collection (to see allowance_used and quota_exceeded)
  off_key    a student whose teacher has switched knowledge off (to see not_enabled)
  library    the name of a class library collection that holds one small book about the water cycle

Books are read by the real worker inside the "start" request, so a book is already ready when the helper first looks. Embeddings are a fake that hashes words.
"""
import json
import os
import sys
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d",
                  KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s",
                  AI_SECRET_NAME="secret", KNOWLEDGE_ENABLED="true", KNOWLEDGE_URL="http://unused", KNOWLEDGE_WORKER_FUNCTION="kworker",
                  KNOWLEDGE_MONTHLY_CAP_USD="3", KNOWLEDGE_EMBEDDING_DIMS="256")
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import backends  # noqa: E402
import embeddings as em  # noqa: E402
import gemini  # noqa: E402
import knowledge  # noqa: E402
import knowledge_api  # noqa: E402
import knowledge_core as kc  # noqa: E402
import knowledge_store as ks  # noqa: E402
import knowledge_worker  # noqa: E402
import portal  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeS3  # noqa: E402

DIMS = 256
now = int(time.time())
gemini._secret.update(value="google-test-key", at=time.time() + 10 ** 6)


def transport(provider, url, headers, body):
    rows = []
    for request in body["requests"]:
        row = [0.0] * DIMS
        for term in kc.tokenize(request["content"]["parts"][0]["text"]):
            row[zlib.crc32(term.encode()) % DIMS] += 1.0
        rows.append({"values": row})
    return 200, {"embeddings": rows}


em.transport = transport

server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)  # only to learn a free port
port = server.server_address[1]
server.server_close()


class HttpBucket(ks.MemoryBucket):
    """The in-memory bucket, with an upload form that points at this fixture (like the real presigned POST points at S3)."""

    def presigned_post(self, key, max_bytes, expires=300):
        return {"url": f"http://127.0.0.1:{port}/s3-post", "fields": {"key": key}, "max_bytes": max_bytes, "expires_in": expires}


bucket = HttpBucket()
knowledge.use_bucket(bucket)
portal._store = portal.MemoryStore()
logs, lam = FakeLogs(), FakeLambda()
lam.logs = logs
lam.platform["kworker"] = lambda event: knowledge_worker.handler(event, now=now)
backends._clients.update({"lambda": lam, "logs": logs, "s3": FakeS3(port)})


def student(name, **caps):
    made = portal.create_token({"days": 30, "student_name": name}, now, lambda slug: f"https://{slug}.example.com/")
    item = portal.get_token(made["item"]["id"])
    item = backends.set_backend(item, {"enabled": True}, now)
    return knowledge.set_knowledge(item, {"enabled": True, **caps}, now)


keys = {
    "key": knowledge.settings(student("Fixture"))["key"],
    "tight_key": knowledge.settings(student("Tight", searches_limit=2, collections=1))["key"],
}
off = student("Off")
knowledge.set_knowledge(off, {"enabled": False}, now)
keys["off_key"] = knowledge.settings(portal.get_token(off["id"]))["key"]

# one small class library book
library = knowledge.library_owner()
knowledge.create_collection(library, "science", "Science", now)
form = knowledge.upload_url(library, "science", "water.txt", 400, now)
bucket.objects[form["fields"]["key"]] = (
    b"The Water Cycle\n\nWater evaporates from oceans and lakes when the sun heats it. The vapour rises, cools and condenses into clouds. "
    b"Rain, snow and hail then fall as precipitation and the water flows back to rivers and the sea. Plants also release water vapour, "
    b"which is called transpiration.")
knowledge.start(library, "science", form["doc_id"], now)


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
        event = {"rawPath": self.path.split("?")[0][len("/knowledge"):] or "/", "body": self.rfile.read(length).decode() if length else None,
                 "requestContext": {"http": {"method": method}}, "headers": {k.lower(): v for k, v in self.headers.items()}}
        result = knowledge_api.handler(event, None, now=now)
        self.send(result["statusCode"], result["body"].encode())

    def do_GET(self):
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
        if last != "file":  # real S3 refuses a form whose file is not the last field
            return self.send(400, b"file must be the last field", "text/plain")
        bucket.objects[fields["key"]] = content
        self.send(204, b"", "text/plain")


httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
print(json.dumps({"url": f"http://127.0.0.1:{port}/knowledge", "library": "library/science", **keys}), flush=True)
httpd.serve_forever()
