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
import re
import secrets
import sys
import threading
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


# ---- background jobs, the way the platform runs them: POST /api/_jobs/<name> starts one, GET /api/_jobs/<id> reads it (in memory only) ----
JOBS = {}
JOB_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
JOB_INPUT_MAX, JOB_RESULT_MAX, JOB_MINUTES = 64 * 1024, 100 * 1024, 5


def run_job(job):
    job.update(status="running", started=int(time.time()))
    try:
        result = current_handler()({"job": {"id": job["id"], "name": job["name"], "payload": job["payload"]}}, None)
        if len(json.dumps(result).encode()) > JOB_RESULT_MAX:
            job.update(status="failed", error="The job's result is larger than 100 KB. Save big results with class storage and return a reference.")
        else:
            job.update(status="done", result=result)
    except Exception:  # the platform shows the page a plain message and keeps the details in your backend logs; here they are in this terminal
        import traceback
        traceback.print_exc()
        job.update(status="failed", error="The job raised an error. The details are printed in this terminal.")
    job["finished"] = int(time.time())
    if job["finished"] - job["started"] > JOB_MINUTES * 60:
        print(f"  (!) job {job['name']} ran longer than {JOB_MINUTES} minutes: the platform would have stopped it (the teacher sets the limit, up to 15)", flush=True)


def public_job(job, with_result=True):
    out = {k: job[k] for k in ("id", "name", "status", "created", "started", "finished") if job.get(k) is not None}
    if job["status"] == "failed":
        out["error"] = job["error"]
    if with_result and job["status"] == "done":
        out["result"] = job["result"]
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def jobs(self, path, raw):
        """The reserved /api/_jobs routes, with the platform's answers (no per-day limit locally; one job at a time, like the default)."""
        def send(code, **body):  # not named "status": that is a field of a job's JSON
            self.reply(code, {"content-type": "application/json"}, json.dumps(body))
        rest = [p for p in path[len("/api/_jobs"):].split("/") if p]
        if self.command == "GET" and not rest:
            return send(200, jobs=[public_job(j, False) for j in sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)[:20]])
        if self.command == "GET" and len(rest) == 1:
            if not JOB_ID.match(rest[0]):
                return send(400, error="bad_job_id", message="That is not a job id.")
            return send(200, **public_job(JOBS[rest[0]])) if rest[0] in JOBS else send(404, error="no_such_job", message="No such job (restarting npm run dev forgets local jobs).")
        if self.command != "POST" or len(rest) != 1:
            return send(405, error="method_not_allowed", message="Use POST /api/_jobs/<name> to start a job and GET /api/_jobs/<id> to read it.")
        if not JOB_NAME.match(rest[0]):
            return send(400, error="bad_job_name", message="A job name uses lowercase letters, digits, - and _ (up to 40 characters).")
        if len((raw or "").encode()) > JOB_INPUT_MAX:
            return send(413, error="payload_too_large", message="The job's input is limited to 64 KB. Save big inputs with class storage and send a reference.")
        try:
            payload = json.loads(raw) if (raw or "").strip() else None
        except ValueError:
            return send(400, error="bad_payload", message="Send the job's input as JSON (or nothing).")
        if any(j["status"] in ("queued", "running") for j in JOBS.values()):
            return send(429, error="jobs_busy", message="A job is already running. Wait until it finishes, then start the next one.")
        job = {"id": secrets.token_hex(16), "name": rest[0], "payload": payload, "status": "queued", "created": int(time.time())}
        JOBS[job["id"]] = job
        threading.Thread(target=run_job, args=(job,), daemon=True).start()
        return send(202, id=job["id"], name=job["name"], status="queued")

    def log_message(self, fmt, *args):
        print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def do_any(self):
        parts = urlsplit(self.path)
        if parts.path != "/api" and not parts.path.startswith("/api/"):
            return self.reply(404, {"content-type": "application/json"}, json.dumps({"error": "not_found", "message": f"Only /api/... is served here. Try /api{parts.path}"}))
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else None
        if parts.path == "/api/_jobs" or parts.path.startswith("/api/_jobs/"):
            return self.jobs(parts.path, body)
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
