"""Traces (lambda/trace_core.py, traces.py and the hooks in ai_proxy.py / knowledge_api.py, the teacher's routes in portal.py): the privacy levels,
masking, a trace made of platform steps, joining steps by trace id, the daily limit, retention, the teacher's settings, listing and deleting,
and that tracing can never break an answer. DynamoDB and Google are fakes."""
import json
import os
import sys
import time

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E",
                  DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k", ROOT_DOMAIN="example.com", BACKEND_ENABLED="true",
                  SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t", AI_ENABLED="true", AI_GEMINI_ENABLED="true",
                  AI_PROXY_URL="https://proxy.example.com", AI_SECRET_NAME="secret", AI_DEFAULT_LIMIT_USD="0.25", AI_STUDENT_TIMEOUT="25",
                  KNOWLEDGE_ENABLED="true", KNOWLEDGE_URL="https://k.example.com")
here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(here, "..", "lambda"), here]
import ai_proxy  # noqa: E402
import backends  # noqa: E402
import gemini  # noqa: E402
import knowledge  # noqa: E402
import knowledge_api  # noqa: E402
import portal  # noqa: E402
import trace_core as tc  # noqa: E402
import traces  # noqa: E402
from fakes import FakeLambda, FakeLogs, FakeS3  # noqa: E402

site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
NOW = int(time.time())
MAIL = "ravi" + chr(64) + "example.com"  # built, so no address is written in the repo
DAY = portal.DAY

# ---------- 1. the format: levels, masking, ids ----------
text = {"query": "Is " + MAIL + " sick? Call 98765 43210 or use ks_abc.AAAAAAAAAAAAAAAAAAAAAAAA", "score": 0.81, "k": 5,
        "hits": [{"doc": "d1", "vector": 0.7, "section": "Cells"}], "passages": ["Aadhaar 1234 5678 9012 and PAN ABCDE1234F"], "answer": "Yes."}
meta = tc.apply_level(text, "metadata")
assert meta["query_chars"] == len(text["query"]) and meta["passages_count"] == 1 and meta["answer_chars"] == 4, meta
assert "query" not in meta and "passages" not in meta and "answer" not in meta, meta
assert meta["score"] == 0.81 and meta["k"] == 5 and meta["hits"] == [{"doc": "d1", "vector": 0.7, "section": "Cells"}], "numbers and short ids are kept"
masked = tc.apply_level(text, "masked")
flat = json.dumps(masked)
for secret in (MAIL, "98765 43210", "ks_abc.AAAA", "1234 5678 9012", "ABCDE1234F"):
    assert secret not in flat, f"{secret} must be masked"
for label in ("[email]", "[phone]", "[key]", "[id]", "[pan]"):
    assert label in flat, (label, flat)
assert tc.apply_level(text, "full")["query"] == text["query"]
assert tc.apply_level(text, "bogus") == meta, "an unknown level behaves as the safe one"
assert tc.apply_level("not a dict", "full") == {}
long = tc.apply_level({"answer": "x" * 10_000}, "full")["answer"]
assert len(long) < 4100 and long.endswith("[cut]")
assert tc.valid_trace_id(tc.new_trace_id()) and not tc.valid_trace_id("tr_xyz") and not tc.valid_trace_id(None) and not tc.valid_trace_id("tr_" + "G" * 16)
assert tc.trace_id_or_new("tr_0123456789abcdef") == "tr_0123456789abcdef" and tc.trace_id_or_new("junk; drop") != "junk; drop"
span = tc.make_span("x" * 200, "weird", "nobody", "5", -3, "odd", {"answer": "secret"}, "metadata")
assert span["kind"] == "other" and span["source"] == "platform" and span["status"] == "ok" and span["dur_ms"] == 0, span
assert len(span["name"]) == 60 and "answer" not in span["attrs"], span
assert tc.span_sort_key(5, "app", "ab") == "0000000000005#app#ab"
assert tc.normalise_settings(None) == {"enabled": True, "level": "metadata", "retention_days": 90, "student_view": "metadata", "daily_limit": 2000}
assert tc.normalise_settings({"level": "x", "retention_days": 7, "student_view": 3, "daily_limit": -1})["retention_days"] == 90
for bad in ({}, {"level": "all"}, {"retention_days": 45}, {"student_view": "yes"}, {"daily_limit": True}, {"daily_limit": 10 ** 9}, {"enabled": "no"}, "x"):
    try:
        tc.validate_settings(bad)
        raise AssertionError(f"{bad} should be refused")
    except ValueError:
        pass
everything = {"level": "full", "retention_days": 30, "student_view": "off", "daily_limit": 0, "enabled": False}
assert tc.validate_settings(everything) == everything
assert tc.student_can_see("off", "full") is None and tc.student_can_see("metadata", "full") == "metadata" and tc.student_can_see("stored", "masked") == "masked"
assert "answer" not in tc.strip_for_student({"attrs": {"answer": "hi", "model": "m"}}, "metadata")["attrs"]
print("  ok  format: three levels, masking of emails/phones/keys/ids/PAN, caps, ids, settings validation")

# ---------- 2. the platform steps ----------
portal._store = portal.MemoryStore()
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)),
                                                "secret": portal.b64(b"3" * 32)})
logs, s3, lam = FakeLogs(), FakeS3(), FakeLambda()
lam.logs = logs
backends._clients.update({"lambda": lam, "logs": logs, "s3": s3})
gemini._secret.update(value="g-key", at=10**12)
google = []


def fake_google(url, headers, body):
    google.append(body)
    return 200, {"choices": [{"message": {"role": "assistant", "content": "Photosynthesis makes glucose."}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


gemini.transport = fake_google


def call(method, path, body=None, session=None, query=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}}, "queryStringParameters": query,
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else NOW)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]
_, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": "Alice"}, admin)
tid, slug = made["item"]["id"], made["item"]["slug"]
call("POST", f"/api/admin/tokens/{tid}/backend", {"enabled": True}, admin)
call("POST", f"/api/admin/tokens/{tid}/ai", {"enabled": True}, admin)
gkey = portal.db().get(f"tok#{tid}")["ai"]["gkey"]
CHAT = {"messages": [{"role": "user", "content": "Explain photosynthesis to " + MAIL}]}


def chat(trace_id=None, at=None):
    time.sleep(0.004)  # steps are ordered by the millisecond they started
    headers = {"authorization": f"Bearer {gkey}"}
    if trace_id:
        headers["x-trace-id"] = trace_id
    event = {"rawPath": "/v1/chat/completions", "requestContext": {"http": {"method": "POST"}}, "headers": headers, "body": json.dumps(CHAT)}
    result = ai_proxy.handler(event, now=at if at is not None else NOW)
    return result["statusCode"], json.loads(result["body"]), result["headers"]


# off: nothing changes, no header, nothing stored
assert not traces.available()
status, body, headers = chat()
assert status == 200 and "x-trace-id" not in headers and not traces.store().rows, "with traces off the proxy behaves exactly as before"
assert call("GET", "/api/admin/traces", session=admin)[1] == {"available": False, "traces": []}
assert call("POST", f"/api/admin/tokens/{tid}/traces", {"level": "full"}, admin)[0] == 409

os.environ["TRACES_ENABLED"] = "true"
student = call("POST", "/api/student/login", {"token": made["token"]})[1]["session"]
assert call("GET", "/api/student/me", session=student)[1]["token"]["traces"] == \
    {"available": True, "enabled": True, "level": "metadata", "retention_days": 90, "student_view": "metadata", "daily_limit": 2000}

# default privacy: metadata only, no text anywhere
status, body, headers = chat()
trace_id = headers["x-trace-id"]
assert status == 200 and tc.valid_trace_id(trace_id)
status, one = call("GET", f"/api/admin/traces/{slug}/{trace_id}", session=admin)
assert status == 200 and one["trace"]["spans"] == 1 and one["trace"]["tokens"] == 120 and one["trace"]["level"] == "metadata", one
llm = one["spans"][0]
assert llm["name"] == "llm" and llm["kind"] == "llm" and llm["source"] == "platform" and llm["status"] == "ok", llm
assert llm["attrs"]["model"] == "gemini-3.5-flash-lite" and llm["attrs"]["prompt_tokens"] == 100 and llm["attrs"]["finish_reason"] == "stop", llm
assert MAIL not in json.dumps(one) and "glucose" not in json.dumps(one), "metadata level stores no text"
assert llm["attrs"]["answer_chars"] == len("Photosynthesis makes glucose.")
cost = gemini.cost_micro("gemini-3.5-flash-lite", {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
assert one["trace"]["cost_micro"] == cost > 0
rows = [r for (p, sk), r in traces.store().rows.items() if p == traces.pk_of(slug, trace_id)]
assert all(r["ttl"] == NOW + 90 * DAY for r in rows), "every item expires at the retention"
assert [r for r in rows if r["id"] == "summary"][0]["app"] == slug
print("  ok  proxy: one llm step, header with the trace id, tokens and cost on the summary, no text at the default level")

# the same trace gets the knowledge step (joined by the id the app sends); text depends on the level
call("POST", f"/api/admin/tokens/{tid}/traces", {"level": "masked"}, admin)


def fake_search(item, body, now):
    return {"hits": [{"collection": "library/bio", "doc": "d1", "page": 3, "section": "Cells", "text": "Cells are small. Call 98765 43210.",
                      "score": 0.9, "vector": 0.8, "bm25": 3.1, "coverage": 0.7}],
            "confident": True, "collections": {"library/bio": {"confident": True, "version": 2, "documents": 1}}, "tokens": 12}


knowledge.authenticate = lambda headers, now: portal.db().get(f"tok#{tid}")
knowledge.student_owner = lambda item: None
knowledge.search = fake_search
mine = tc.new_trace_id()
event = {"rawPath": "/v1/search", "requestContext": {"http": {"method": "POST"}}, "headers": {"x-trace-id": mine},
         "body": json.dumps({"query": "what is a cell, " + MAIL + "?", "k": 3})}
result = knowledge_api.handler(event, now=NOW + 1)
assert result["statusCode"] == 200 and result["headers"]["x-trace-id"] == mine and json.loads(result["body"])["confident"] is True
chat(mine, at=NOW + 2)
status, one = call("GET", f"/api/admin/traces/{slug}/{mine}", session=admin)
assert sorted(s["name"] for s in one["spans"]) == ["llm", "retrieval"] and one["trace"]["spans"] == 2 and one["trace"]["level"] == "masked", one
flat = json.dumps(one)
assert MAIL not in flat and "98765" not in flat and "[email]" in flat and "[phone]" in flat, "masked text, personal details removed"
first = [s for s in one["spans"] if s["name"] == "retrieval"][0]["attrs"]
assert first["hit_count"] == 1 and first["hits"][0]["doc"] == "d1" and first["confident"] is True
assert "glucose" in flat, "the answer text is stored at the masked level"
call("POST", f"/api/admin/tokens/{tid}/traces", {"level": "full"}, admin)
t3 = chat(at=NOW + 3)[2]["x-trace-id"]
assert MAIL in json.dumps(call("GET", f"/api/admin/traces/{slug}/{t3}", session=admin)[1]), "full level keeps the text"
print("  ok  retrieval + llm steps join by trace id; masked and full levels")

# listing, filtering, deleting
listing = call("GET", "/api/admin/traces", session=admin, query={"slug": slug})[1]["traces"]
assert listing[0]["trace_id"] == t3 and len(listing) == 3 and all(t["slug"] == slug for t in listing), listing
today = call("GET", "/api/admin/traces", session=admin, query={"day": traces.day_id(NOW)})[1]["traces"]
assert len(today) == 3
assert call("GET", "/api/admin/traces", session=admin, query={"slug": "nobody-here"})[1]["traces"] == []
older = call("GET", "/api/admin/traces", session=admin, query={"slug": slug, "before": str(listing[0]["started"])})[1]["traces"]
assert len(older) == 2
assert call("GET", f"/api/admin/traces/{slug}/tr_0000000000000000", session=admin)[0] == 404
assert call("GET", f"/api/admin/traces/{slug}/bad", session=admin)[0] == 404, "not even a route"
assert call("GET", "/api/admin/traces")[0] == 401
assert call("DELETE", f"/api/admin/traces/{slug}/{t3}", session=admin)[1] == {"removed": 2}
assert call("GET", f"/api/admin/traces/{slug}/{t3}", session=admin)[0] == 404
print("  ok  listing by student and day, paging, 404s, teacher login needed, delete removes every step")

# ---------- 3. retention, switches and the daily limit ----------
saved = call("POST", f"/api/admin/tokens/{tid}/traces", {"retention_days": 30, "level": "metadata"}, admin)[1]["token"]["traces"]
assert saved["retention_days"] == 30
t4 = chat(at=NOW + 4)[2]["x-trace-id"]
assert all(r["ttl"] == NOW + 4 + 30 * DAY for (p, _), r in traces.store().rows.items() if p == traces.pk_of(slug, t4))
call("POST", f"/api/admin/tokens/{tid}/traces", {"enabled": False}, admin)
status, body, headers = chat(at=NOW + 5)
assert status == 200 and "glucose" in json.dumps(body), "off for this student: the answer still works"
assert not traces.store().spans(traces.pk_of(slug, headers.get("x-trace-id", "tr_none"))), "and nothing is recorded"
call("POST", f"/api/admin/tokens/{tid}/traces", {"enabled": True, "daily_limit": 2}, admin)
before = len(traces.store().summaries(app=slug, limit=100))
for i in range(5):
    assert chat(at=NOW + 10 + i)[0] == 200, "the limit never blocks an answer"
assert len(traces.store().summaries(app=slug, limit=100)) - before <= 2, "at most the daily limit of new traces"
after_limit = len(traces.store().summaries(app=slug, limit=100))
assert chat(at=NOW + 3 * DAY)[0] == 200 and len(traces.store().summaries(app=slug, limit=100)) > after_limit, "a new day starts a new allowance"
print("  ok  retention follows the setting, per-student off, daily limit stops recording but never an answer")


# ---------- 4. tracing never breaks an answer ----------
class Broken:
    def __getattr__(self, name):
        raise RuntimeError("table unavailable")


traces._store[:] = [Broken()]
status, body, headers = chat(at=NOW + 4 * DAY)
assert status == 200 and body["choices"][0]["message"]["content"], "a broken trace table cannot break the answer"
traces._store[:] = [traces.MemoryTraces()]
assert traces.record(None, None, "x") is None
assert traces.record({"slug": "a"}, "tr_0123456789abcdef", "x", now=NOW) == "tr_0123456789abcdef"
print("  ok  a broken trace store never breaks an answer")

# ---------- 5. steps the student's app reports (the helper's answer and agent steps) ----------
traces._store[:] = [traces.MemoryTraces()]
call("POST", f"/api/admin/tokens/{tid}/traces", {"level": "masked", "enabled": True, "daily_limit": 100}, admin)
knowledge.authenticate = lambda headers, now: portal.db().get(f"tok#{tid}")
app_id = tc.new_trace_id()


def report(body):
    event = {"rawPath": "/v1/trace", "requestContext": {"http": {"method": "POST"}}, "headers": {}, "body": json.dumps(body)}
    result = knowledge_api.handler(event, now=NOW + 7)
    return result["statusCode"], json.loads(result["body"])


status, body = report({"trace_id": app_id, "spans": [
    {"name": "answer", "kind": "chain", "attrs": {"question": "mail " + MAIL, "answer": "ok", "confident": True, "slug": "someone-else"}, "dur_ms": 900, "status": "ok", "ts": 1700000000000},
    {"name": "tool:save", "kind": "tool", "attrs": {"arguments": {"note": "call 98765 43210"}}, "status": "skipped"},
    "junk", {"name": "x" * 99, "kind": "nonsense", "status": "weird"}]})
assert status == 200 and body == {"trace_id": app_id, "recorded": 3}, body
one = call("GET", f"/api/admin/traces/{slug}/{app_id}", session=admin)[1]
assert one["trace"]["slug"] == slug and one["trace"]["spans"] == 3 and all(s["source"] == "app" for s in one["spans"]), one
flat = json.dumps(one)
assert MAIL not in flat and "98765" not in flat and "[email]" in flat and "[phone]" in flat, "app text is masked like platform text"
assert [s for s in one["spans"] if s["name"] == "answer"][0]["kind"] == "chain" and [s for s in one["spans"] if s["name"] == "tool:save"][0]["status"] == "skipped"
assert [s for s in one["spans"] if s["name"].startswith("xxx")][0]["kind"] == "other", "unknown values are made safe"
assert report({"trace_id": "bad", "spans": []})[0] == 400 and report({"trace_id": app_id, "spans": "x"})[0] == 400
os.environ["TRACES_ENABLED"] = "false"
assert report({"trace_id": app_id, "spans": [{"name": "answer"}]}) == (200, {"trace_id": app_id, "recorded": 0}), "traces off: accepted and ignored, the app never sees an error"
os.environ["TRACES_ENABLED"] = "true"
print("  ok  app steps: joined by trace id, source=app, slug from the key, masked, unknown values made safe, bad input refused, off means ignored")

# ---------- 6. the student's own view (as the teacher allows) ----------
traces._store[:] = [traces.MemoryTraces()]
call("POST", f"/api/admin/tokens/{tid}/traces", {"level": "full", "student_view": "metadata", "enabled": True, "daily_limit": 100}, admin)
mine_id = chat(at=NOW + 20)[2]["x-trace-id"]
_, bob = call("POST", "/api/admin/tokens", {"days": 30, "student_name": "Bob"}, admin)
bob_item = portal.db().get(f"tok#{bob['item']['id']}")
call("POST", f"/api/admin/tokens/{bob['item']['id']}/traces", {"level": "full", "student_view": "stored"}, admin)
bob_session = call("POST", "/api/student/login", {"token": bob["token"]})[1]["session"]
traces.record(portal.db().get(f"tok#{bob['item']['id']}"), None, "llm", "llm", {"answer": "bob private answer", "model": "m"}, now=NOW + 21)
bob_trace = traces.list_traces(bob_item["slug"])["traces"][0]["trace_id"]

status, listing = call("GET", "/api/student/traces", session=student)
assert status == 200 and listing["view"] == "metadata" and [t["trace_id"] for t in listing["traces"]] == [mine_id], listing
assert all("slug" not in t for t in listing["traces"])
status, one = call("GET", f"/api/student/traces/{mine_id}", session=student)
assert status == 200 and one["view"] == "metadata" and one["spans"][0]["attrs"]["model"] == "gemini-3.5-flash-lite", one
flat = json.dumps(one)
assert MAIL not in flat and "glucose" not in flat, "even though full text was stored, the student sees numbers only at the default view"
assert call("GET", f"/api/student/traces/{bob_trace}", session=student)[0] == 404, "another app's trace is simply not found"
assert call("GET", f"/api/student/traces/{mine_id}", session=bob_session)[0] == 404
assert call("GET", "/api/student/traces/bad", session=student)[0] == 404
assert call("GET", "/api/student/traces")[0] == 401
status, bobs = call("GET", f"/api/student/traces/{bob_trace}", session=bob_session)
assert status == 200 and bobs["view"] == "stored" and bobs["spans"][0]["attrs"]["answer"] == "bob private answer", "'as stored' shows the stored text to its own student"
call("POST", f"/api/admin/tokens/{tid}/traces", {"student_view": "off"}, admin)
status, body = call("GET", "/api/student/traces", session=student)
assert status == 403 and "teacher" in body["error"], body
assert call("GET", f"/api/student/traces/{mine_id}", session=student)[0] == 403
os.environ["TRACES_ENABLED"] = "false"
assert call("GET", "/api/student/traces", session=student)[0] == 409
os.environ["TRACES_ENABLED"] = "true"
print("  ok  student view: own app only, off means nothing, numbers-only hides stored text, as-stored shows it, other apps are not found")

# ---------- 7. DynamoDB hands back Decimal numbers (found on the first real student-view call): every view must turn them into plain numbers ----------
from decimal import Decimal  # noqa: E402

view_row = traces._summary_view({"s": "t#alice#tr_0123456789abcdef", "slug": "alice", "day": "2026-11-07", "started": Decimal("1793986800000"), "level": "metadata",
                                 "spans": Decimal("3"), "tokens": Decimal("1055"), "cost_micro": Decimal("277"), "errors": Decimal("0")})
json.dumps(view_row)
assert view_row["spans"] == 3 and view_row["started"] == 1793986800000 and view_row["trace_id"] == "tr_0123456789abcdef", view_row
print("  ok  Decimal numbers from DynamoDB are turned into plain numbers")
