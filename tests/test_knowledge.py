"""Class knowledge connector (lambda/knowledge.py, knowledge_api.py, knowledge_worker.py): the teacher's switch and caps, the key and the environment given to
the student's function, the API (collections, upload form, start, documents, search), the background worker reading a real PDF, every limit and failure,
the class library, and that no key is ever exposed. DynamoDB, S3 and Lambda are in-memory fakes; the embedding provider is a fake that hashes words.
"""
import json
import os
import sys
import time
import zlib

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k",
                  ROOT_DOMAIN="example.com", BACKEND_ENABLED="true", SANDBOX_ROLE_ARN="arn:aws:iam::1:role/sandbox", ORIGIN_SECRET="s3cr3t", AI_SECRET_NAME="secret",
                  KNOWLEDGE_ENABLED="true", KNOWLEDGE_URL="https://knowledge.example.com", KNOWLEDGE_WORKER_FUNCTION="kworker", KNOWLEDGE_MONTHLY_CAP_USD="3",
                  KNOWLEDGE_EMBEDDING_DIMS="256")
here = os.path.dirname(os.path.abspath(__file__))
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
from fakes import FakeLambda, FakeLogs, FakeS3, store  # noqa: E402
from pdf_fixture import make_pdf, wrap  # noqa: E402

DAY, MB = portal.DAY, knowledge.MB
site_url = lambda slug: f"https://{slug}.example.com/"  # noqa: E731
salt = b"saltsaltsaltsalt"
NOW = int(time.time())
gemini._secret.update(value="google-test-key", at=time.time() + 10 ** 6)
DIMS = 256
provider_calls = []


def transport(provider, url, headers, body):
    texts = [r["content"]["parts"][0]["text"] for r in body["requests"]]
    provider_calls.append(len(texts))
    rows = []
    for text in texts:
        row = [0.0] * DIMS
        for term in kc.tokenize(text):
            row[zlib.crc32(term.encode()) % DIMS] += 1.0
        rows.append({"values": row})
    return 200, {"embeddings": rows}


em.transport = transport
bucket = ks.MemoryBucket()
knowledge.use_bucket(bucket)


class StrictStore(portal.MemoryStore):
    """DynamoDB refuses Python floats; fail loudly here instead of in production."""

    @staticmethod
    def _no_floats(value, where):
        if isinstance(value, float):
            raise TypeError(f"float {value!r} in {where}: DynamoDB needs int or Decimal")
        if isinstance(value, dict):
            for k, v in value.items():
                StrictStore._no_floats(v, f"{where}.{k}")

    def update(self, pk, fields, remove=()):
        self._no_floats(fields, pk)
        return super().update(pk, fields, remove)


portal._store = StrictStore()
portal._teacher.update(loaded_at=10**12, value={"username": "teacher", "salt": portal.b64(salt), "hash": portal.b64(portal.scrypt_hash("pw", salt)), "secret": portal.b64(b"3" * 32)})
logs, s3, lam = FakeLogs(), FakeS3(), FakeLambda()
lam.logs = logs
backends._clients.update({"lambda": lam, "logs": logs, "s3": s3})
worker_events = []
lam.platform["kworker"] = worker_events.append  # an async invoke is captured; the test decides when the worker runs


def run_worker():
    while worker_events:
        knowledge_worker.handler(worker_events.pop(0), now=NOW)


def call(method, path, body=None, session=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "1.1.1.1"}},
             "headers": {"authorization": f"Bearer {session}"} if session else {}, "body": json.dumps(body or {})}
    result = portal.handle(event, site_url, now=at if at is not None else NOW)
    return result["statusCode"], json.loads(result["body"])


admin = call("POST", "/api/teacher/login", {"username": "teacher", "password": "pw"})[1]["session"]
EXPOSED = []


def seen(status, body):
    EXPOSED.append(json.dumps(body))
    return status, body


def new_student(name):
    _, made = call("POST", "/api/admin/tokens", {"days": 30, "student_name": name}, admin)
    tid = made["item"]["id"]
    call("POST", f"/api/admin/tokens/{tid}/backend", {"enabled": True}, admin)
    return tid, call("POST", "/api/student/login", {"token": made["token"]})[1]["session"], made["item"]["slug"]


def kn_set(tid, **body):
    return seen(*call("POST", f"/api/admin/tokens/{tid}/knowledge", body, admin))


def key_of(tid):
    return (portal.db().get(f"tok#{tid}").get("knowledge") or {}).get("key")


def api(key, method, path, body=None, at=None):
    event = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {"Authorization": f"Bearer {key}"} if key else {}, "body": json.dumps(body) if body is not None else None}
    result = knowledge_api.handler(event, now=at if at is not None else NOW)
    return result["statusCode"], json.loads(result["body"])


def count(kind, slug, *extra, scope="student"):
    return int((portal.db().get("#".join(("k" + kind, scope, slug, *extra))) or {}).get("count", 0))


def deploy(session, slug):
    import io
    import zipfile
    lam.polls_until_active = 1
    call("POST", "/api/student/backend/upload-url", session=session)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", "def handler(event, context):\n    return {'statusCode': 200, 'body': 'ok'}\n")
    store[f"uploads/{slug}-backend.zip"] = {"Body": buf.getvalue()}
    assert call("POST", "/api/student/backend/publish", session=session)[0] == 200
    for _ in range(5):
        if call("GET", "/api/student/backend", session=session)[1]["token"]["backend"]["state"] == "ready":
            return
    raise AssertionError("backend never became ready")


def env(slug):
    return (lam.functions[f"sd-{slug}"]["config"].get("Environment") or {}).get("Variables") or {}


def book(chapters=2, pages_per=2, scan=True):
    """A small study book as a real PDF: a header and page numbers on every page, a chapter heading, and (optionally) a last page that is a scan."""
    topics = [("Chapter 1: Photosynthesis", "Photosynthesis is the process by which green plants make their own food. The leaves absorb sunlight using chlorophyll. Water and carbon dioxide are combined to make glucose and oxygen."),
              ("Chapter 2: Laws of Motion", "Newton's second law states that force equals mass multiplied by acceleration. A heavier object needs a larger force to get the same acceleration."),
              ("Chapter 3: The Water Cycle", "Water evaporates from oceans and rivers when the sun heats it. The vapour rises and condenses into clouds. Rain and snow bring the water back to the ground.")]
    pages, number, nouns = [], 1, ["roots", "stems", "leaves", "flowers", "fruits", "seeds", "soil", "rain"]
    for title, paragraph in topics[:chapters]:
        for part in range(pages_per):
            noun = nouns[(number - 1) % len(nouns)]
            pages.append(["NCERT Study Book", ""] + ([title, ""] if part == 0 else []) + wrap(paragraph) + ["", f"Remember the {noun} example.", f"Check the {noun} chart carefully.", "", str(number)])
            number += 1
    if scan:
        pages.append([])
    return make_pdf(pages)


def add_book(key, collection, filename, data, owner="student", start=True):
    """Ask for the upload form, send the bytes, start. Returns (doc_id, the start answer)."""
    status, form = api(key, "POST", f"/v1/collections/{collection}/documents", {"filename": filename, "size": len(data)})
    assert status == 200, form
    bucket.put(form["fields"]["key"], data)
    return form["doc_id"], (api(key, "POST", f"/v1/collections/{collection}/documents/{form['doc_id']}/start") if start else None)


# ---------- 1. the teacher's switch: defaults, a key made once, never exposed ----------
a_tid, a_session, a_slug = new_student("Alice")
b_tid, b_session, b_slug = new_student("Bob")
view = call("GET", "/api/student/me", session=a_session)[1]["token"]["knowledge"]
assert view["available"] and view["enabled"] is False and view["usage"] is None, view
status, body = kn_set(a_tid, enabled=True)
assert status == 200, body
view = body["token"]["knowledge"]
assert view["enabled"] and view["env_names"] == ["CLASS_KNOWLEDGE_URL", "CLASS_KNOWLEDGE_KEY"]
assert {k: view["limits"][k] for k in knowledge.DEFAULTS} == knowledge.DEFAULTS and view["usage"]["documents"] == 0, view
a_key = key_of(a_tid)
assert a_key.startswith(f"ks_{a_slug}.") and len(a_key) > 40
kn_set(a_tid, enabled=False)
kn_set(a_tid, enabled=True)
assert key_of(a_tid) == a_key, "switching off and on keeps the same key"
for bad in ({"documents": 0}, {"documents": True}, {"documents": 10 ** 6}, {"searches_period": "hour"}, {"enabled": "yes"}, {"passages": 5}, {"stored_mb": 1.5}):
    assert kn_set(a_tid, **bad)[0] == 400, bad
assert kn_set(a_tid, collections=2, documents=3)[1]["token"]["knowledge"]["limits"]["documents"] == 3
kn_set(a_tid, collections=5, documents=20)
assert call("POST", "/api/admin/tokens/" + a_tid + "/knowledge", {"enabled": True})[0] == 401, "only the teacher can change it"
print("  ok  first switch-on: defaults, one key per student kept across off/on, bad values refused, teacher login required")

# ---------- 2. the student's function gets the two variables, and cannot overwrite them ----------
deploy(a_session, a_slug)
call("POST", f"/api/admin/tokens/{a_tid}/knowledge", {"enabled": False}, admin)
assert "CLASS_KNOWLEDGE_KEY" not in env(a_slug)
kn_set(a_tid, enabled=True)
assert env(a_slug)["CLASS_KNOWLEDGE_URL"] == "https://knowledge.example.com" and env(a_slug)["CLASS_KNOWLEDGE_KEY"] == a_key
status, body = call("PUT", "/api/student/backend/env", {"name": "CLASS_KNOWLEDGE_KEY", "value": "mine"}, session=a_session)
assert status == 400 and "class connector" in body["error"], body
assert call("DELETE", "/api/student/backend/env/CLASS_KNOWLEDGE_URL", session=a_session)[0] == 400
assert env(a_slug)["CLASS_KNOWLEDGE_KEY"] == a_key
print("  ok  the function receives CLASS_KNOWLEDGE_URL and KEY while on, loses them when off, and a student cannot overwrite or delete them")

# ---------- 3. the API's key check and isolation ----------
assert api(None, "GET", "/v1/collections")[1]["error"] == "bad_key"
assert api("ks_nobody.aaaaaaaaaaaaaaaaaaaaaaaa", "GET", "/v1/collections")[0] == 401
assert api(a_key[:-3] + "xxx", "GET", "/v1/collections")[0] == 401, "a wrong secret is refused"
kn_set(b_tid, enabled=True)
b_key = key_of(b_tid)
assert api(a_key, "GET", "/v1/collections")[0] == 200 and api(b_key, "GET", "/v1/collections")[0] == 200
assert api(a_key, "GET", "/v1/nonsense")[1]["error"] == "not_found"
kn_set(a_tid, enabled=False)
assert api(a_key, "GET", "/v1/collections")[1]["error"] == "not_enabled"
kn_set(a_tid, enabled=True)
c_tid, c_session, c_slug = new_student("Carol")
kn_set(c_tid, enabled=True)
c_key = key_of(c_tid)
call("POST", f"/api/admin/tokens/{c_tid}/revoke", {}, admin)
assert api(c_key, "GET", "/v1/collections")[1]["error"] == "token_inactive"
print("  ok  API key check: missing, unknown, wrong, switched off and revoked are all refused, with a reason")

# ---------- 4. collections ----------
status, made = api(a_key, "POST", "/v1/collections", {"name": "notes", "title": "My notes"})
assert status == 201 and made["name"] == "notes" and made["documents"] == 0 and made["embedding"] == {"model": "gemini-embedding-001", "dims": DIMS, "task_types": False}
assert api(a_key, "POST", "/v1/collections", {"name": "notes"})[1]["error"] == "exists"
for bad in ("Bad Name", "library/x", "", "../x", "a" * 41):
    assert api(a_key, "POST", "/v1/collections", {"name": bad})[0] == 400, bad
kn_set(a_tid, collections=2)
assert api(a_key, "POST", "/v1/collections", {"name": "second"})[0] == 201
assert api(a_key, "POST", "/v1/collections", {"name": "third"})[1]["error"] == "quota_exceeded"
assert api(a_key, "DELETE", "/v1/collections/second")[0] == 200 and api(a_key, "DELETE", "/v1/collections/second")[0] == 404
kn_set(a_tid, collections=5)
listing = api(a_key, "GET", "/v1/collections")[1]
assert [c["name"] for c in listing["collections"]] == ["notes"] and listing["library"] == []
assert api(b_key, "GET", "/v1/collections")[1]["collections"] == [], "another student sees none of Alice's collections"
assert api(b_key, "GET", "/v1/collections/notes/documents")[1]["error"] == "not_found" and api(b_key, "DELETE", "/v1/collections/notes")[0] == 404
print("  ok  collections: create, duplicate, bad names, the cap, delete, listing, and no view into another student's shelf")

# ---------- 5. a real PDF through the whole path: form, upload, start, worker, documents, search ----------
pdf = book()
status, form = api(a_key, "POST", "/v1/collections/notes/documents", {"filename": "my book.pdf", "size": len(pdf)})
assert status == 200 and form["doc_id"].startswith("my-book-") and form["fields"]["key"].endswith("/source.pdf") and form["max_bytes"] == len(pdf), form
assert f"/student/{a_slug}/notes/docs/" in form["fields"]["key"], "the owner and path come from the key, never from the request"
docs = api(a_key, "GET", "/v1/collections/notes/documents")[1]["documents"]
assert [d["state"] for d in docs] == ["uploading"] and count("doc", a_slug) == 1
assert api(a_key, "POST", f"/v1/collections/notes/documents/{form['doc_id']}/start")[1]["error"] == "no_file", "nothing was uploaded yet"
bucket.put(form["fields"]["key"], pdf)
status, started = api(a_key, "POST", f"/v1/collections/notes/documents/{form['doc_id']}/start")
assert status == 200 and started["state"] == "queued" and worker_events == [{"scope": "student", "owner": a_slug, "collection": "notes", "doc_id": form["doc_id"]}], (started, worker_events)
assert api(a_key, "POST", f"/v1/collections/notes/documents/{form['doc_id']}/start")[1]["error"] == "bad_state", "a document starts once"
before_tokens, before_spend = count("tok", a_slug, knowledge.month_id(NOW)), int((portal.db().get(f"kspend#{knowledge.month_id(NOW)}") or {}).get("count", 0))
run_worker()
doc = api(a_key, "GET", "/v1/collections/notes/documents")[1]["documents"][0]
assert doc["state"] == "ready" and doc["title"] == "my book" and doc["pages"] == 5 and doc["paged"] is True and doc["passages"] >= 2, doc  # two short chapters: one passage each
assert "scan" in doc["message"] and doc["needs_ocr"] == [5], "the scanned last page is skipped and the owner is told"
assert count("pass", a_slug) == doc["passages"] and count("bytes", a_slug) > 0 and count("doc", a_slug) == 1
spent = count("tok", a_slug, knowledge.month_id(NOW)) - before_tokens
assert spent > 0 and int(portal.db().get(f"kspend#{knowledge.month_id(NOW)}")["count"]) > before_spend, "embedding is counted for the student and for the class"
status, found = api(a_key, "POST", "/v1/search", {"query": "How do plants make their own food?", "k": 3})
top = found["hits"][0]
assert status == 200 and found["confident"] and top["collection"] == "notes" and top["title"] == "my book" and top["section"] == "Chapter 1: Photosynthesis" and top["page"] == 1, found
assert "NCERT Study Book" not in top["text"], "the running header was cleaned"
assert api(a_key, "POST", "/v1/search", {"query": "State Newton's second law"})[1]["hits"][0]["section"] == "Chapter 2: Laws of Motion"
assert api(a_key, "POST", "/v1/search", {"query": "What is on the school canteen menu today?"})[1]["confident"] is False
tokens_once = found["tokens"]
assert tokens_once > 0 and api(a_key, "POST", "/v1/search", {"query": "How do plants make their own food?", "k": 3})[1]["tokens"] == 0, "the same question embeds once (cache)"
assert api(b_key, "POST", "/v1/search", {"query": "photosynthesis"})[1]["error"] == "bad_request", "Bob has nothing to search yet, and cannot reach Alice's collection"
assert api(b_key, "POST", "/v1/search", {"query": "photosynthesis", "collections": ["notes"]})[1]["error"] == "not_found"
print("  ok  a real PDF: form, upload, start, background read, page and chapter in the answer, header cleaned, scan reported, cost counted, repeat question free, no leak between students")

# the same book again costs nothing to embed (cache), and deleting a book gives everything back
api(a_key, "POST", "/v1/collections", {"name": "again"})
spend_before = int(portal.db().get(f"kspend#{knowledge.month_id(NOW)}")["count"])
second_id, _ = add_book(a_key, "again", "my book copy.pdf", pdf)
run_worker()
assert api(a_key, "GET", "/v1/collections/again/documents")[1]["documents"][0]["state"] == "ready"
assert int(portal.db().get(f"kspend#{knowledge.month_id(NOW)}")["count"]) == spend_before, "an already embedded book costs the class nothing again"
assert count("doc", a_slug) == 2
assert api(a_key, "DELETE", f"/v1/collections/again/documents/{second_id}")[0] == 200
assert count("doc", a_slug) == 1 and count("pass", a_slug) == doc["passages"], "deleting gives back the slot, the passages and the bytes"
assert api(a_key, "DELETE", "/v1/collections/again")[0] == 200
print("  ok  re-uploading a book costs nothing; deleting a document or a collection returns every counter")

# ---------- 6. limits and failures ----------
kn_set(a_tid, documents=1)
status, body = api(a_key, "POST", "/v1/collections/notes/documents", {"filename": "second.pdf", "size": 1000})
assert status == 413 and body["error"] == "quota_exceeded" and count("doc", a_slug) == 1, body
kn_set(a_tid, documents=20, upload_mb=1)
assert api(a_key, "POST", "/v1/collections/notes/documents", {"filename": "big.pdf", "size": 2 * MB})[1]["error"] == "too_large"
kn_set(a_tid, upload_mb=25)
for filename, size, error in [("../evil.pdf", 10, "bad_request"), ("noext", 10, "bad_request"), ("movie.mp4", 10, "unsupported"), ("ok.pdf", 0, "bad_request"), ("ok.pdf", True, "bad_request"), ("ok.pdf", "9", "bad_request")]:
    assert api(a_key, "POST", "/v1/collections/notes/documents", {"filename": filename, "size": size})[1]["error"] == error, (filename, size)
assert count("doc", a_slug) == 1, "refused requests reserve nothing"
kn_set(a_tid, stored_mb=1)
assert api(a_key, "POST", "/v1/collections/notes/documents", {"filename": "wide.pdf", "size": MB - 10})[1]["error"] == "quota_exceeded", "a full shelf"
kn_set(a_tid, stored_mb=50)

# a file that is not what its name says: the worker fails it, says why, and frees the slot
junk_id, _ = add_book(a_key, "notes", "fake.pdf", b"this is plain text pretending to be a pdf")
run_worker()
junk = [d for d in api(a_key, "GET", "/v1/collections/notes/documents")[1]["documents"] if d["id"] == junk_id][0]
assert junk["state"] == "failed" and "PDF" in junk["message"] and count("doc", a_slug) == 1, junk
assert api(a_key, "DELETE", f"/v1/collections/notes/documents/{junk_id}")[0] == 200 and count("doc", a_slug) == 1, "deleting a failed one refunds nothing twice"

# too many passages for the shelf: refused before any money is spent on embedding
def big_page(n):
    lines = []
    for k in range(4):
        lines += wrap((f"Topic {n}.{k} explains that the substance number {n * 10 + k} behaves in a particular way when heated, cooled or mixed with water. "
                       f"Students observe the change, record the temperature and write down what they notice about substance {n * 10 + k}. ") * 2) + [""]
    return ["Big Book", ""] + lines + [str(n)]


big = make_pdf([big_page(n) for n in range(1, 121)])  # about 130 words a page: a passage per page, so well over the cap of 100
kn_set(a_tid, passages=100)
calls_before = len(provider_calls)
big_id, _ = add_book(a_key, "notes", "huge.pdf", big)
run_worker()
huge = [d for d in api(a_key, "GET", "/v1/collections/notes/documents")[1]["documents"] if d["id"] == big_id][0]
assert huge["state"] == "failed" and "allows 100" in huge["message"], huge
assert len(provider_calls) == calls_before, "no embedding call was made for a book that does not fit"
assert count("doc", a_slug) == 1 and count("pass", a_slug) == doc["passages"], "the failed upload gave everything back"
kn_set(a_tid, passages=5000)

# budgets: the student's monthly embedding allowance, and the class-wide ceiling
kn_set(a_tid, embed_tokens=0)
extra_id, _ = add_book(a_key, "notes", "late.pdf", pdf, start=False)
assert api(a_key, "POST", f"/v1/collections/notes/documents/{extra_id}/start")[1]["error"] == "allowance_used"
kn_set(a_tid, embed_tokens=2_000_000)
os.environ["KNOWLEDGE_MONTHLY_CAP_USD"] = "0.0000001"
assert api(a_key, "POST", f"/v1/collections/notes/documents/{extra_id}/start")[1]["error"] == "class_budget"
os.environ["KNOWLEDGE_MONTHLY_CAP_USD"] = "3"
assert api(a_key, "DELETE", f"/v1/collections/notes/documents/{extra_id}")[0] == 200
print("  ok  limits: documents, upload size, bad names and types, a full shelf, a fake PDF, too many passages (no money spent), the student's monthly budget, the class ceiling")

# abandoned uploads and stuck work are cleaned up and their slots freed
ghost_id = api(a_key, "POST", "/v1/collections/notes/documents", {"filename": "ghost.pdf", "size": 100})[1]["doc_id"]
assert count("doc", a_slug) == 2
listed = api(a_key, "GET", "/v1/collections/notes/documents", at=NOW + 3700)[1]["documents"]
ghost = [d for d in listed if d["id"] == ghost_id][0]
assert ghost["state"] == "failed" and "Never finished" in ghost["message"] and count("doc", a_slug) == 1
api(a_key, "DELETE", f"/v1/collections/notes/documents/{ghost_id}")
stuck_id, _ = add_book(a_key, "notes", "stuck.pdf", pdf)  # queued, and the worker never runs
worker_events.clear()
assert api(a_key, "DELETE", f"/v1/collections/notes/documents/{stuck_id}")[1]["error"] == "busy", "work in progress cannot be deleted"
stuck = [d for d in api(a_key, "GET", "/v1/collections/notes/documents", at=NOW + 1300)[1]["documents"] if d["id"] == stuck_id][0]
assert stuck["state"] == "failed" and "too long" in stuck["message"] and count("doc", a_slug) == 1
api(a_key, "DELETE", f"/v1/collections/notes/documents/{stuck_id}", at=NOW + 1300)
from botocore.exceptions import ClientError  # noqa: E402


def refuse(event):
    raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no such function"}}, "Invoke")


lam.platform["kworker"] = refuse  # the worker cannot be started: the document goes back to "uploading" so the same call can be repeated
unstarted = api(a_key, "POST", "/v1/collections/notes/documents", {"filename": "x.pdf", "size": len(pdf)})[1]
bucket.put(unstarted["fields"]["key"], pdf)
status, body = api(a_key, "POST", f"/v1/collections/notes/documents/{unstarted['doc_id']}/start")
assert status == 503 and body["error"] == "not_ready", body
assert [d["state"] for d in api(a_key, "GET", "/v1/collections/notes/documents")[1]["documents"] if d["id"] == unstarted["doc_id"]] == ["uploading"]
lam.platform["kworker"] = worker_events.append
assert api(a_key, "POST", f"/v1/collections/notes/documents/{unstarted['doc_id']}/start")[1]["state"] == "queued", "the same call works once the worker can be started"
worker_events.clear()
api(a_key, "POST", f"/v1/collections/notes/documents/{unstarted['doc_id']}/start")  # (still queued: refused as bad_state, nothing changes)
api(a_key, "DELETE", f"/v1/collections/notes/documents/{unstarted['doc_id']}", at=NOW + 1300)
print("  ok  an abandoned upload and stuck work are failed after their time, free their slots, cannot be deleted while genuinely running, and a worker that cannot be started leaves the upload retryable")

# ---------- 6b. only a few books are read at once, so a class uploading together cannot starve the portal ----------
portal.db().put({"pk": "kwork#global", "count": 0, "updated_at": NOW})  # the stuck-work cases above started workers that never ran, and so held their slots
worker_events.clear()
os.environ["KNOWLEDGE_WORKERS"] = "1"
slot = lambda: int((portal.db().get("kwork#global") or {}).get("count", 0))  # noqa: E731
one_id, first_start = add_book(a_key, "notes", "slot one.pdf", pdf)
two_id, second_start = add_book(a_key, "notes", "slot two.pdf", pdf)
assert first_start[1]["state"] == "queued" and second_start[0] == 429 and second_start[1]["error"] == "busy", second_start
assert [d["state"] for d in api(a_key, "GET", "/v1/collections/notes/documents")[1]["documents"] if d["id"] == two_id] == ["uploading"], "the refused one stays retryable"
assert slot() == 1
run_worker()
assert slot() == 0, "a finished book gives its slot back"
assert api(a_key, "POST", f"/v1/collections/notes/documents/{two_id}/start")[1]["state"] == "queued"
run_worker()
assert slot() == 0
failed_id, _ = add_book(a_key, "notes", "bad slot.pdf", b"not a pdf at all")
run_worker()
assert slot() == 0, "a failed book gives its slot back too"
portal.db().put({"pk": "kwork#global", "count": 1, "updated_at": NOW - 5000})  # a worker that died: its slot heals after the worker's own time limit
healed_id, healed_start = add_book(a_key, "notes", "slot three.pdf", pdf)
assert healed_start[1]["state"] == "queued" and slot() == 1
run_worker()
for doc_id_ in (one_id, two_id, failed_id, healed_id):
    api(a_key, "DELETE", f"/v1/collections/notes/documents/{doc_id_}")
os.environ["KNOWLEDGE_WORKERS"] = "2"
assert slot() == 0 and count("doc", a_slug) == 1
print("  ok  reader slots: one book at a time when set to one, a refused upload stays retryable, finished and failed books free their slot, a dead worker's slot heals")

# ---------- 7. the class library: the teacher adds books, students search them read-only ----------
assert call("GET", "/api/admin/knowledge")[0] == 401
made = seen(*call("POST", "/api/admin/knowledge/library/collections", {"name": "science-9", "title": "Science, class 9"}, admin))
assert made[0] == 201 and made[1]["name"] == "science-9"
assert call("POST", "/api/admin/knowledge/library/collections", {"name": "science-9"}, admin)[0] == 409
status, form = call("POST", "/api/admin/knowledge/library/collections/science-9/documents", {"filename": "ncert science.pdf", "size": len(pdf)}, admin)
assert status == 200 and "/class/library/science-9/docs/" in form["fields"]["key"]
bucket.put(form["fields"]["key"], pdf)
assert call("POST", f"/api/admin/knowledge/library/collections/science-9/documents/{form['doc_id']}/start", {}, admin)[1]["state"] == "queued"
assert worker_events[-1]["scope"] == "class" and worker_events[-1]["owner"] == "library"
run_worker()
library_docs = call("GET", "/api/admin/knowledge/library/collections/science-9/documents", session=admin)[1]["documents"]
assert [d["state"] for d in library_docs] == ["ready"] and "reserved" not in library_docs[0]
listing = api(a_key, "GET", "/v1/collections")[1]
assert [c["name"] for c in listing["library"]] == ["science-9"] and listing["library"][0]["documents"] == 1
found = api(a_key, "POST", "/v1/search", {"query": "How do plants make their own food?"})[1]
assert {h["collection"] for h in found["hits"]} <= {"notes", "library/science-9"} and "library/science-9" in found["collections"], found
only_library = api(b_key, "POST", "/v1/search", {"query": "Explain Newton's second law", "collections": ["library/science-9"]})[1]
assert only_library["confident"] and only_library["hits"][0]["collection"] == "library/science-9" and only_library["hits"][0]["title"] == "ncert science"
assert api(b_key, "POST", "/v1/search", {"query": "x", "collections": ["library/missing"]})[0] == 404
assert api(b_key, "DELETE", "/v1/collections/science-9")[0] == 404 and api(b_key, "POST", "/v1/collections/science-9/documents", {"filename": "a.pdf", "size": 10})[0] == 404, "students cannot change the library"
overview = call("GET", "/api/admin/knowledge", session=admin)[1]
assert overview["library"]["usage"]["documents"] == 1 and overview["spent_micro"] > 0 and overview["ceiling_micro"] == 3_000_000
assert [c["name"] for c in overview["library"]["collections"]] == ["science-9"]
print("  ok  class library: the teacher adds a book through the same worker, students search it (and their own) read-only, the overview shows spending against the ceiling")

# ---------- 8. search rules ----------
for body in ({}, {"query": ""}, {"query": "   "}, {"query": "x" * 501}, {"query": "ok", "k": 0}, {"query": "ok", "k": 11}, {"query": "ok", "k": True}, {"query": "ok", "collections": []},
             {"query": "ok", "collections": ["a", "b", "c", "d", "e"]}, {"query": "ok", "collections": [1]}, {"query": "ok", "collections": ["BAD NAME"]}):
    assert api(a_key, "POST", "/v1/search", body)[0] in (400, 404), body
today = knowledge.backends.period_info("day", NOW)[0]
used = count("search", a_slug, today)
assert used > 0, "the searches made above are counted"
kn_set(a_tid, searches_limit=used + 2, searches_period="day")  # two more are allowed today
assert api(a_key, "POST", "/v1/search", {"query": "plants"})[0] == 200
assert api(a_key, "POST", "/v1/search", {"query": "plants"})[0] == 200
status, limited = api(a_key, "POST", "/v1/search", {"query": "plants"})
assert status == 429 and limited["error"] == "allowance_used", limited
assert count("search", a_slug, today) == used + 2, "a refused search is not counted"
assert api(a_key, "POST", "/v1/search", {"query": "plants"}, at=NOW + DAY + 10)[0] == 200, "and a new day renews it"
kn_set(a_tid, searches_limit=500)
usage = api(a_key, "GET", "/v1/usage")[1]
assert usage["enabled"] and usage["usage"]["documents"] == 1 and usage["usage"]["searches_used"] >= 1
print("  ok  search: bad queries, k and collection lists refused; the allowance ends and renews")

# ---------- 9. clearing a student, and nothing secret is ever shown ----------
assert api(a_key, "GET", "/v1/collections")[1]["collections"], "Alice has a shelf"
status, cleared = seen(*call("POST", f"/api/admin/tokens/{a_tid}/knowledge/clear", {}, admin))
assert status == 200 and cleared["cleared"]["removed"] == ["notes"]
assert api(a_key, "GET", "/v1/collections")[1]["collections"] == [] and count("doc", a_slug) == 0 and count("pass", a_slug) == 0 and count("bytes", a_slug) == 0
assert not [k for k in bucket.list(f"knowledge/student/{a_slug}/")], "everything of hers is gone from storage"
assert len(bucket.list("knowledge/class/library/science-9/")) > 0, "the class library is untouched"
for text in EXPOSED:
    assert a_key not in text and b_key not in text and "ks_" not in text, "no key in any teacher-visible answer"
assert all(a_key not in json.dumps(v) for v in [call("GET", "/api/student/me", session=a_session)[1]]), "nor in the student's own view"
print("  ok  clearing a student removes her books and counters but not the library; no key appears in any answer")

# ---------- 10. the student's own shelf through the portal (the deploy tool's folder sync) ----------
import hashlib  # noqa: E402

BASE = "/api/student/knowledge"
c_tid, c_session, c_slug = new_student("Cara")
d_tid, d_session, d_slug = new_student("Dan")
assert call("GET", BASE)[0] == 401, "a login is needed"
assert call("GET", BASE, session=c_session)[0] == 403, "switched off: no shelf"
kn_set(c_tid, enabled=True)
kn_set(d_tid, enabled=True)
status, listing = call("GET", BASE, session=c_session)
assert status == 200 and listing["collections"] == [] and listing["knowledge"]["limits"]["documents"] == 20 and listing["knowledge"]["enabled"], listing
assert call("POST", f"{BASE}/collections", {"name": "physics", "title": "Physics"}, c_session)[0] == 201
assert call("POST", f"{BASE}/collections", {"name": "physics"}, c_session)[0] == 409
assert call("POST", f"{BASE}/collections", {"name": "Bad Name"}, c_session)[0] == 400
assert call("POST", f"{BASE}/nothing", {}, c_session)[0] == 404, "an unknown path under the prefix"

book = b"Ohm's law says the current through a conductor is proportional to the voltage across it. The constant is the resistance, measured in ohms."
digest = hashlib.sha256(book).hexdigest()
status, form = call("POST", f"{BASE}/physics/documents", {"filename": "ohm.txt", "size": len(book), "sha256": digest}, c_session)
assert status == 200 and form["doc_id"] and form["fields"]["key"].startswith(f"knowledge/student/{c_slug}/physics/"), form
assert call("POST", f"{BASE}/physics/documents", {"filename": "x.txt", "size": 5, "sha256": "nothex"}, c_session)[0] == 400, "a bad hash is refused"
bucket.put(form["fields"]["key"], book)
assert call("POST", f"{BASE}/physics/documents/{form['doc_id']}/start", {}, c_session)[0] == 200
run_worker()
status, docs = call("GET", f"{BASE}/physics/documents", session=c_session)
assert status == 200 and [(d["filename"], d["state"], d["sha256"]) for d in docs["documents"]] == [("ohm.txt", "ready", digest)], docs
assert all("reserved" not in d for d in docs["documents"]), "internal bookkeeping is not shown"
assert api(key_of(c_tid), "GET", "/v1/collections/physics/documents")[1]["documents"][0]["sha256"] == digest, "the function's own API shows the same hash"
print("  ok  the student's shelf through the portal: switch, create, upload form, start, the reader stores and shows the content hash")

# a file that changed while it was being sent is refused by the reader, with a plain message, and its reservation is given back
used_before = count("doc", c_slug)
status, form2 = call("POST", f"{BASE}/physics/documents", {"filename": "other.txt", "size": 10, "sha256": hashlib.sha256(b"what the tool hashed").hexdigest()}, c_session)
bucket.put(form2["fields"]["key"], b"different!")
call("POST", f"{BASE}/physics/documents/{form2['doc_id']}/start", {}, c_session)
run_worker()
failed = [d for d in call("GET", f"{BASE}/physics/documents", session=c_session)[1]["documents"] if d["id"] == form2["doc_id"]][0]
assert failed["state"] == "failed" and "changed while it was being sent" in failed["message"], failed
assert count("doc", c_slug) == used_before, "a failed book does not keep its slot"
print("  ok  a file that changed during the upload fails with a plain message and keeps no slot")

# isolation: Dan sees nothing of Cara's, cannot reach her shelf by name, and the class library is not addressable here
assert call("GET", BASE, session=d_session)[1]["collections"] == []
assert call("GET", f"{BASE}/physics/documents", session=d_session)[0] == 404
assert call("POST", f"{BASE}/physics/documents", {"filename": "x.txt", "size": 5}, d_session)[0] == 404
assert call("DELETE", f"{BASE}/physics/documents/{form['doc_id']}", session=d_session)[0] == 404
assert call("GET", f"{BASE}/science-9/documents", session=d_session)[0] == 404, "the class library's shelf is not reachable as a student shelf"
assert call("POST", f"{BASE}/physics/documents/{form['doc_id']}/start", {}, d_session)[0] == 404
print("  ok  another student cannot list, upload to, start or delete from this shelf, and the class library is not reachable through it")

# delete, and the limits of the shelf are the same ones the API enforces
assert call("DELETE", f"{BASE}/physics/documents/{form['doc_id']}", session=c_session)[0] == 200
assert [d["state"] for d in call("GET", f"{BASE}/physics/documents", session=c_session)[1]["documents"]] == ["failed"], "only the failed upload is left"
assert count("pass", c_slug) == 0 and count("bytes", c_slug) == 0, "deleting the book gives its space back"
kn_set(c_tid, documents=1)
call("POST", f"{BASE}/physics/documents", {"filename": "a.txt", "size": 5}, c_session)
status, full = call("POST", f"{BASE}/physics/documents", {"filename": "b.txt", "size": 5}, c_session)
assert status == 413 and "up to 1 documents" in json.dumps(full), full
kn_set(c_tid, enabled=False)
assert call("GET", BASE, session=c_session)[0] == 403, "switched off again: closed at once"
print("  ok  delete gives space back; the document limit applies here too; switching off closes the routes at once")

print("test_knowledge.py: all passed")
