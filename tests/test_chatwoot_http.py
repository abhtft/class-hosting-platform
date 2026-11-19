"""The real HTTP client the Chatwoot worker uses (chatwoot_api.post_json), tested against a small server on this computer: the request it sends (method, path, headers,
body), the answer it returns for success and failure statuses, that a redirect is NOT followed (the bot's token must never travel to another address), that a
huge answer is cut, and that a missing answer becomes a timeout or a network failure. No network and no Chatwoot are involved."""
import http.server
import json
import os
import socket
import sys
import threading
import time

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_api as api  # noqa: E402

SEEN = []  # what the server received: (method, path, headers, body)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN.append((self.command, self.path, dict(self.headers), body))
        if self.path == "/ok":
            self.reply(200, b'{"id": 5, "content": "hello"}')
        elif self.path == "/forbidden":
            self.reply(403, b'{"description": "Access denied"}')
        elif self.path == "/broken":
            self.reply(500, b"oops")
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/big":
            self.reply(200, b"x" * 200000)
        elif self.path == "/slow":
            time.sleep(1.5)
            self.reply(200, b'{"id": 6}')
        else:
            self.reply(404, b"")

    def reply(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except OSError:
            pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": "Bearer test-only-token-abc123"}
BODY = json.dumps({"content": "Received (message 1)", "message_type": "outgoing", "private": False}).encode()

try:
    # ---- the request: a POST with the path, headers and body it was given ----
    status, answer = api.post_json(BASE + "/ok", HEADERS, BODY, 5)
    assert status == 200 and json.loads(answer) == {"id": 5, "content": "hello"}
    method, path, seen_headers, seen_body = SEEN[-1]
    assert method == "POST" and path == "/ok" and seen_body == BODY
    assert seen_headers["Authorization"] == "Bearer test-only-token-abc123" and seen_headers["Content-Type"] == "application/json" and seen_headers["Accept"] == "application/json"
    print("  ok  a POST is sent with the given path, headers and body, and the status and answer come back")

    # ---- failure statuses are answers, not exceptions ----
    assert api.post_json(BASE + "/forbidden", HEADERS, BODY, 5) == (403, b'{"description": "Access denied"}')
    assert api.post_json(BASE + "/broken", HEADERS, BODY, 5) == (500, b"oops")
    assert api.post_json(BASE + "/missing", HEADERS, BODY, 5)[0] == 404
    print("  ok  a 403, a 500 and a 404 are returned as statuses with their text")

    # ---- a redirect is not followed ----
    before = len(SEEN)
    status, _ = api.post_json(BASE + "/redirect", HEADERS, BODY, 5)
    assert status == 302, status
    assert [s[1] for s in SEEN[before:]] == ["/redirect"], "the second address must never be called: the token would travel there"
    print("  ok  a redirect is returned as a 302 and not followed")

    # ---- an answer is read only up to a cap ----
    status, answer = api.post_json(BASE + "/big", HEADERS, BODY, 5)
    assert status == 200 and len(answer) == 65536
    print("  ok  a huge answer is cut at a small cap")

    # ---- no answer in time ----
    started = time.time()
    try:
        api.post_json(BASE + "/slow", HEADERS, BODY, 0.3)
        raise AssertionError("a slow answer must time out")
    except api.TimeoutFailure:
        pass
    assert time.time() - started < 1.4, "it gave up at the timeout, not when the server answered"
    assert issubclass(api.TimeoutFailure, api.NetworkFailure)
    print("  ok  an answer that does not come in time is a TimeoutFailure")

    # ---- nobody listening ----
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    try:
        api.post_json(f"http://127.0.0.1:{port}/ok", HEADERS, BODY, 1)
        raise AssertionError("a closed port must fail")
    except api.NetworkFailure:  # Linux refuses at once (NetworkFailure itself); Windows keeps trying until the timeout (its subclass TimeoutFailure): both mean "no answer"
        pass
    print("  ok  a closed port is a NetworkFailure (or its TimeoutFailure)")
finally:
    server.shutdown()

print("test_chatwoot_http.py: the real HTTP client")
