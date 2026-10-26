"""Run backend/app.py on your computer, the way the platform calls it.

    python tools/dev_server.py [port]        (normally started for you by `npm run dev`)

It listens on http://127.0.0.1:8788 and answers /api/... by building the same "event" the platform sends and calling
handler(event, context). Edit backend/*.py and just call again: changed files are reloaded. Secrets for local runs come from
.env.local (git-ignored). Without an AI key the class AI helper answers from an offline mock, so nothing is spent.
Standard library only.
"""
import base64
import importlib
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
sys.path.insert(0, BACKEND)


def load_env_file(path):
    """NAME=value lines into os.environ (existing variables win). Nothing from the VITE_* family: that is for the browser."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                name = name.strip()
                if name and not name.startswith("VITE_") and name not in os.environ:
                    os.environ[name] = value.strip().strip('"').strip("'")


load_env_file(os.path.join(ROOT, ".env.local"))
import app  # noqa: E402  (backend/app.py)

_stamps = {}


def current_handler():
    """The handler, reloading backend modules whose file changed since the last call."""
    changed = False
    for name in sorted(os.listdir(BACKEND)):
        if name.endswith(".py"):
            stamp = os.path.getmtime(os.path.join(BACKEND, name))
            if _stamps.get(name) not in (None, stamp):
                changed = True
            _stamps[name] = stamp
    if changed:
        for name in list(sys.modules):
            module = sys.modules[name]
            if getattr(module, "__file__", None) and os.path.dirname(os.path.abspath(module.__file__)) == BACKEND:
                importlib.reload(module)
        importlib.reload(app)
    return app.handler


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def do_any(self):
        parts = urlsplit(self.path)
        if parts.path != "/api" and not parts.path.startswith("/api/"):
            return self.reply(404, {"content-type": "application/json"}, json.dumps({"error": "not_found", "message": f"Only /api/... is served here. Try /api{parts.path}"}))
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else None
        event = {
            "rawPath": parts.path[len("/api"):] or "/", "rawQueryString": parts.query,
            "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body, "isBase64Encoded": False,
            "requestContext": {"http": {"method": self.command, "path": parts.path, "sourceIp": self.client_address[0]}},
        }
        started = time.time()
        try:
            result = current_handler()(event, None)
        except Exception as error:  # the platform would answer 502 function_error; here you see why
            import traceback
            traceback.print_exc()
            return self.reply(502, {"content-type": "application/json"}, json.dumps({"error": "function_error", "message": f"{type(error).__name__}: {error}"}))
        if not isinstance(result, dict) or "statusCode" not in result:  # like API Gateway: a plain value becomes a 200 response
            result = {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(result)}
        body_out = result.get("body", "")
        data = base64.b64decode(body_out) if result.get("isBase64Encoded") else (body_out if isinstance(body_out, str) else json.dumps(body_out)).encode()
        if time.time() - started > 10:
            print("  (!) this request took more than 10 s: the platform would stop it", flush=True)
        self.reply(int(result["statusCode"]), result.get("headers") or {}, data)

    def reply(self, status, headers, data):
        data = data.encode() if isinstance(data, str) else data
        self.send_response(status)
        for name, value in headers.items():
            if name.lower() not in ("content-length", "transfer-encoding", "connection"):
                self.send_header(name, value)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_any


if __name__ == "__main__":
    print(f"Backend (Python) on http://127.0.0.1:{PORT}/api/...   AI: {'your own key' if os.environ.get('OPENAI_API_KEY') else 'offline mock'}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
