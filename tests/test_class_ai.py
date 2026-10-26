"""student/class_ai.py against a local OpenAI-compatible server: success, every error kind, retries, timeouts, the offline mock,
the default= escape hatch, and that the key and the prompt never reach stdout or an error."""
import contextlib
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "student"))
import class_ai  # noqa: E402

KEY = "sk-class-SECRET-0123456789"
PROMPT = "my private prompt about alice"
script, seen = [], []  # script: list of (status, body, delay) answered in order; seen: the requests received


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        seen.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
        status, text, delay = script.pop(0) if script else (200, OK, 0)
        time.sleep(delay)
        data = text.encode()
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # the client gave up (the timeout test): nothing to deliver


OK = json.dumps({"choices": [{"message": {"role": "assistant", "content": "Hello from the model"}}]})
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}/v1"


def env(**values):
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_MODEL", "AI_MOCK", "AI_TIMEOUT", "AWS_LAMBDA_FUNCTION_NAME"):
        os.environ.pop(name, None)
    os.environ.update(values)


def platform(**extra):
    env(OPENAI_API_KEY=KEY, OPENAI_BASE_URL=BASE, AI_MODEL="the-model", **extra)
    script.clear()
    seen.clear()


def run(fn, *args, **kwargs):
    """Call fn, capturing stdout. Returns (result_or_exception, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            return fn(*args, **kwargs), out.getvalue()
        except class_ai.AiError as e:
            return e, out.getvalue()


everything = []  # all outputs seen, scanned for secrets at the end

# 1. success: the request is OpenAI-shaped
platform()
result, out = run(class_ai.ask, PROMPT, system="be brief", max_tokens=50)
assert result == "Hello from the model", result
assert seen[0]["path"] == "/v1/chat/completions" and seen[0]["auth"] == f"Bearer {KEY}"
assert seen[0]["body"] == {"model": "the-model", "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": PROMPT}], "max_tokens": 50}
assert out == "", "a successful call logs nothing"
print("  ok  success: path, bearer key, model, system prompt, max_tokens")

# 2. model override, temperature, trailing slash and a base URL given without /v1
platform()
os.environ["OPENAI_BASE_URL"] = BASE + "/"
run(class_ai.ask, "hi", model="other-model", temperature=0.2)
assert seen[0]["body"]["model"] == "other-model" and seen[0]["body"]["temperature"] == 0.2 and seen[0]["path"] == "/v1/chat/completions"
print("  ok  model override, temperature, trailing slash")

# 3. each HTTP status maps to one friendly kind
for status, kind, requests in ((401, "disabled", 1), (402, "cap_reached", 1), (403, "cap_reached", 1), (400, "provider_error", 1), (404, "provider_error", 1)):
    platform()
    script.append((status, json.dumps({"error": {"message": f"provider says {KEY}"}}), 0))
    result, out = run(class_ai.ask, PROMPT)
    assert isinstance(result, class_ai.AiError) and result.kind == kind and result.status == status, (status, result)
    assert len(seen) == requests, f"{status}: no retry for {kind}"
    assert result.message == class_ai.MESSAGES[kind]
    everything += [out, str(result), repr(result), result.message]
print("  ok  401 disabled, 402/403 cap_reached, 400/404 provider_error (no retry)")

# 4. busy: one short retry, then give up
platform()
script += [(429, "{}", 0), (200, OK, 0)]
result, _ = run(class_ai.ask, "hi")
assert result == "Hello from the model" and len(seen) == 2, "429 then success is retried once"
platform()
script += [(500, "oops", 0), (503, "oops", 0)]
result, out = run(class_ai.ask, PROMPT)
assert isinstance(result, class_ai.AiError) and result.kind == "busy" and len(seen) == 2, result
everything += [out, str(result)]
print("  ok  429/5xx are retried once, then busy")

# 5. timeouts
platform(AI_TIMEOUT="1")
script.append((200, OK, 3))
started = time.time()
result, out = run(class_ai.ask, PROMPT)
assert isinstance(result, class_ai.AiError) and result.kind == "timeout" and time.time() - started < 3, result
everything += [out, str(result)]
print("  ok  a slow answer ends in a timeout error within the limit")

# 6. unreadable replies
for body in ("not json", json.dumps({"choices": []}), json.dumps({"nothing": 1}), json.dumps({"choices": [{"message": None}]})):
    platform()
    script.append((200, body, 0))
    result, _ = run(class_ai.ask, "hi")
    assert isinstance(result, class_ai.AiError) and result.kind == "provider_error", (body, result)
platform()
script.append((200, json.dumps({"choices": [{"message": {"content": None}}]}), 0))
assert run(class_ai.ask, "hi")[0] == "", "null content becomes an empty string"
platform()
script.append((200, "\n\n  " + OK + "  \n", 0))
assert run(class_ai.ask, "hi")[0] == "Hello from the model", "leading whitespace in the body is fine"
print("  ok  bad JSON or shape is provider_error; null content is empty; whitespace is tolerated")

# 7. connection refused
platform()
os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:1/v1"
result, out = run(class_ai.ask, PROMPT)
assert isinstance(result, class_ai.AiError) and result.kind in ("provider_error", "timeout"), result
everything += [out, str(result)]
print("  ok  an unreachable provider is a friendly error")

# 8. default= swallows every error and logs only kind and status
platform()
script.append((403, "{}", 0))
result, out = run(class_ai.ask, PROMPT, default="AI is resting")
assert result == "AI is resting" and out.strip() == "class_ai: cap_reached (status 403)", (result, out)
everything.append(out)
platform()
script.append((200, OK, 0))
assert run(class_ai.ask, "hi", default="AI is resting")[0] == "Hello from the model", "the default is only used on errors"
print("  ok  default= returns the fallback text on error; the log line has only kind and status")

# 9. not enabled, mock mode, AI_MOCK
env(AWS_LAMBDA_FUNCTION_NAME="sd-alice")  # on the platform without the variables
result, out = run(class_ai.ask, PROMPT)
assert isinstance(result, class_ai.AiError) and result.kind == "not_enabled" and not class_ai.available(), result
env()  # a laptop: no variables
result, out = run(class_ai.ask, "hello there")
assert result == "[mock AI] You asked: hello there" and class_ai.available() and out == ""
env(AI_MOCK="0")
assert run(class_ai.ask, "x")[0].kind == "not_enabled", "AI_MOCK=0 forbids the mock"
platform(AI_MOCK="1")
assert run(class_ai.ask, "x")[0].startswith("[mock AI]") and not seen, "AI_MOCK=1 forces the mock and makes no request"
platform()
assert class_ai.available()
env(OPENAI_API_KEY=KEY, AWS_LAMBDA_FUNCTION_NAME="x")
assert run(class_ai.ask, "x")[0].kind == "not_enabled" and not class_ai.available(), "a key without a base URL is not usable"
print("  ok  not_enabled on the platform, offline mock on a laptop, AI_MOCK overrides, available()")

# 10. chat() with a full message list
platform()
run(class_ai.chat, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}])
assert [m["content"] for m in seen[0]["body"]["messages"]] == ["a", "b", "c"]
print("  ok  chat() sends the whole conversation")

# 10b. mock= is what the offline mock answers (so a structured reply can be tried offline), and is ignored by the real AI
env()
assert run(class_ai.ask, "q", mock='{"questions": []}')[0] == '{"questions": []}'
assert run(class_ai.chat, [{"role": "user", "content": "q"}], mock="canned")[0] == "canned"
assert run(class_ai.ask, "q", mock="")[0] == "", "an empty mock is still a mock"
platform()
assert run(class_ai.ask, "q", mock="canned")[0] == "Hello from the model" and len(seen) == 1, "the real AI answers; the mock text is ignored"
print("  ok  mock= gives the offline mock a chosen reply and is ignored by the real AI")

# 11. nothing secret in anything the wrapper printed or raised
blob = "\n".join(everything)
assert KEY not in blob and "SECRET" not in blob, "the key leaked into an error or a log"
assert PROMPT not in blob, "the prompt leaked into an error or a log"
server.shutdown()
print("test_class_ai.py: all passed")
