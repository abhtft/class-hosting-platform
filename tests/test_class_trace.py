"""Kit helpers and traces (Python): one answer carries one trace id to the AI and the book search, the helper's own steps are sent once at the end, an agent run
sends a step per tool call and keeps the same trace across approval, and a dead trace service never changes an answer. A fake local server plays the platform."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "student"))
seen = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        seen.append((self.path, self.headers.get("x-trace-id"), body))
        if self.path == "/v1/search":
            out = {"hits": [{"text": "Ohm's law: V = IR.", "title": "Physics", "page": 1, "page_end": 1, "section": "Ohm", "collection": "mine", "score": 0.9}], "confident": True}
        elif self.path == "/ai/chat/completions":
            out = {"choices": [{"message": {"content": "V equals I times R [1]."}}]}
        else:
            out = {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
os.environ.update(CLASS_KNOWLEDGE_URL=base, CLASS_KNOWLEDGE_KEY="ks_x.secretsecretsecretsecret", OPENAI_API_KEY="k", OPENAI_BASE_URL=base + "/ai", AI_MODEL="m", AI_MOCK="0")
import class_agent  # noqa: E402
import class_ai  # noqa: E402
import class_knowledge as kb  # noqa: E402

out = kb.answer("What is Ohm's law?")
tid = out["trace_id"]
assert tid.startswith("tr_") and len(tid) == 19 and out["confident"], out
paths = [(p, t) for p, t, _ in seen]
assert ("/v1/search", tid) in paths and ("/ai/chat/completions", tid) in paths, paths
posts = [b for p, t, b in seen if p == "/v1/trace"]
assert len(posts) == 1 and posts[0]["trace_id"] == tid and [s["name"] for s in posts[0]["spans"]] == ["answer"], posts
attrs = posts[0]["spans"][0]["attrs"]
assert attrs["confident"] is True and attrs["refused"] is False and attrs["sources"] == 1 and attrs["cited"] == 1, attrs
assert class_ai.trace_id() is None, "the trace ends with the answer"
seen.clear()
second = kb.answer("What is Ohm's law?")["trace_id"]
assert second != tid
print("  ok  answer(): one trace id on the search, the AI call and the answer step, sent once, then ended")

# an agent: a tool call, an approval, a resume on the same trace
calls = []
tools = [class_agent.tool("look", "Look something up", {"q": "text"}, lambda a: "found it"),
         class_agent.tool("save", "Save a note", {"text": "text"}, lambda a: calls.append(a) or "saved", needs_approval=True)]
replies = [{"tool": "look", "args": {"q": "x"}}, {"tool": "save", "args": {"text": "hello"}}, {"final": "all done"}]
seen.clear()
first = class_agent.run("find and save", tools, mock=replies, model=lambda m, n: json.dumps(replies.pop(0)))
assert first["status"] == "needs_approval" and first["trace_id"].startswith("tr_"), first
posts = [b for p, t, b in seen if p == "/v1/trace"]
assert len(posts) == 1 and posts[0]["trace_id"] == first["trace_id"]
names = [s["name"] for s in posts[0]["spans"]]
assert names == ["tool:look", "tool:save", "agent"], names
assert posts[0]["spans"][1]["status"] == "ok" and posts[0]["spans"][2]["attrs"]["status"] == "needs_approval"
seen.clear()
done = class_agent.resume(first["state"], True, tools, model=lambda m, n: json.dumps(replies.pop(0)))
assert done["status"] == "done" and done["trace_id"] == first["trace_id"] and calls == [{"text": "hello"}], done
posts = [b for p, t, b in seen if p == "/v1/trace"]
assert len(posts) == 1 and posts[0]["trace_id"] == first["trace_id"], "the resume continues the same trace"
names = [s["name"] for s in posts[0]["spans"]]
assert names == ["tool:save", "agent:final", "agent"], ("only the new steps are sent", names)
print("  ok  agent: a step per tool call, one agent summary, the same trace across approval and resume, nothing sent twice")

# a dead trace service never changes an answer
server.shutdown()
server.server_close()
os.environ["CLASS_KNOWLEDGE_URL"] = "http://127.0.0.1:9"
class_ai.trace_begin()
class_ai.trace_span("x")
assert class_ai.trace_flush().startswith("tr_")
os.environ["AI_MOCK"] = "1"
assert kb.NOT_SURE and class_ai.ask("hi", mock="fine") == "fine"
print("  ok  an unreachable trace service changes nothing")
