"""Local stand-in for API Gateway + S3 + SSM + CloudFront that runs the REAL Lambda handler.

    python fake_api.py <lambda_dir> <port> [root_domain]

POST /upload-url, /publish  -> handled by lambda/app.py (key for student "alice" is "s3cret")
PUT  /s3/<key>              -> fake presigned upload
GET  /                      -> dump of stored objects + CloudFront invalidations (for assertions)
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

lambda_dir, port = sys.argv[1], int(sys.argv[2])
os.environ.update(
    AWS_REGION="us-east-1", AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
    BUCKET="b", DISTRIBUTION_ID="E1", DISTRIBUTION_DOMAIN="d123.cloudfront.net",
    ROOT_DOMAIN=sys.argv[3] if len(sys.argv) > 3 else "", KEYS_PARAM="/student-deploy/keys",
)
sys.path.insert(0, lambda_dir)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402

from fakes import FakeCloudFront, FakeS3, FakeSSM, invalidations, store  # noqa: E402


app.s3, app.ssm, app.cloudfront = FakeS3(port), FakeSSM(), FakeCloudFront()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        store[self.path[len("/s3/"):]] = {"Body": self.rfile.read(int(self.headers["Content-Length"]))}
        self._send(200, b"")

    def do_POST(self):
        event = {"routeKey": f"POST {self.path}", "headers": {k.lower(): v for k, v in self.headers.items()}}
        result = app.handler(event, None)
        self._send(result["statusCode"], result["body"].encode())

    def do_GET(self):
        objects = {k: {**{kk: vv for kk, vv in v.items() if kk != "Body"}, "size": len(v["Body"])} for k, v in store.items()}
        self._send(200, json.dumps({"objects": objects, "invalidations": invalidations}).encode())


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
