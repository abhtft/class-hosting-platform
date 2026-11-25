"""Books in Studio (lambda/chatwoot_books.py, the admin routes /admin/books...): the owner adds a book (an upload form, then it is read in the background into a shelf), sees the shelves
(also those the teacher's command made), opens a book's passages, asks the shelves a question, removes a book or a shelf, and a shelf an agent's live version reads is not removed. The
books function runs on in-memory fakes with a fake embedder."""
import json
import os
import sys

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(root, "lambda"))

import chatwoot_admin as admin  # noqa: E402
import chatwoot_api as api  # noqa: E402
import chatwoot_books as books  # noqa: E402
import chatwoot_registry as reg  # noqa: E402
import knowledge_core as kc  # noqa: E402
import knowledge_store as ks  # noqa: E402

admin.log = lambda what, **details: None
books.log = lambda what, **details: None
api.log = lambda what, **details: None
NOW = 1_800_000_000
TENANT = {"tenant_id": "acme", "enabled": True, "agents": ["shop"], "rev": 1}


class Raw:
    """The sites bucket's chatwoot-books/ part: bytes by key."""

    def __init__(self):
        self.files = {}

    def get(self, key, limit):
        data = self.files.get(key)
        return data if data is not None and len(data) <= limit else None

    def size(self, key):
        return len(self.files[key]) if key in self.files else None

    def delete(self, key):
        self.files.pop(key, None)

    def presigned_post(self, key, size, seconds):
        return {"url": "https://bucket.example/upload", "fields": {"key": key}, "max_bytes": size, "expires_in": seconds}


def fake_embed(prepared, embedding, cache=None, sleep=None):
    rows = [[0.1] * embedding["dims"] for _ in prepared["passages"]]
    return {**prepared, "vectors": kc.pack_vectors(rows), "tokens": 10, "cost_micro": 7, "cached": 0}


store = api.MemoryStore()
store.claim(reg.tenant_key("acme"), TENANT)
store.claim(reg.agent_key("shop"), {"tenant_id": "acme", "agent_id": "shop", "account_id": 1, "inbox_ids": [3], "enabled": True, "live_version": 0, "versions": 0, "rev": 1})
bucket = ks.MemoryBucket()
shelves = ks.Store(bucket)
raw = Raw()
deps = books.Deps(store, shelves, raw, embed=fake_embed)
calls = []


def fake_books(payload, wait=True):
    """The admin function's call to the books function: run it here (an asynchronous call too, at once)."""
    calls.append((payload["kind"], wait))
    answer = books.handler(payload, now=NOW + 5, deps=deps)
    return answer if wait else {}  # an asynchronous call returns at once, nothing


admin.invoke_books = fake_books


def call(method, path, body=None, files=raw, invoke=None, at=NOW):
    event = {"body": json.dumps(body) if body is not None else "", "isBase64Encoded": False, "queryStringParameters": None}
    try:
        return admin.route(store, TENANT, method, path, event, at, invoke, files)
    except reg.Invalid as error:
        return 400, {"error": "bad_request", "message": str(error)}


# ---- the pure rules
assert books.clean_shelf("library") == "library" and books.safe_filename("Fees list (2026).pdf") == "Fees_list_2026_.pdf"
for bad in ("", "Library", "a b", "pending", "shelves", "search", "x" * 41, 5, None):
    try:
        books.clean_shelf(bad)
        raise AssertionError(bad)
    except books.Invalid:
        pass
assert books.safe_filename("../../etc/passwd") == "etc_passwd" and books.safe_filename("   ") == "book"
print("  ok  shelf names and file names are checked (no path tricks, no names the routes use)")

# ---- the teacher's shelves show up (reindex), before any upload
base = ks.base_of("tenant", "acme", "faq")
meta = shelves.create_collection(base, "FAQ", model=books.embedding_choice()[0], dims=books.embedding_choice()[1])
status, got = call("GET", "/admin/books")
assert status == 200 and [s["name"] for s in got["shelves"]] == ["faq"] and got["shelves"][0]["books"] == [] and calls[-1] == ("reindex", True), got
assert got["limits"]["max_books"] == books.MAX_BOOKS and got["limits"]["types"] == ["pdf", "txt", "md", "html"]
print("  ok  the first look builds the list from the shelves that are already there (the teacher's command made them)")

# ---- upload, start, read
TEXT = ("Library fees. A general seat costs Rs 500 per month and an air conditioned seat costs Rs 800 per month. " * 12 + "\n\n" + "Timings. The reading room is open from 8 AM to 8 PM. " * 12).encode()
for bad in ({}, {"shelf": "library"}, {"shelf": "Library", "filename": "a.txt", "size": 10}, {"shelf": "library", "filename": "a.exe", "size": 10}, {"shelf": "library", "filename": "a.txt", "size": 0},
            {"shelf": "library", "filename": "a.txt", "size": 10 ** 9}, {"shelf": "library", "filename": "a.txt", "size": 10, "x": 1}, {"shelf": "library", "filename": "a", "size": 10}, {"shelf": "library", "filename": "a.txt", "size": True}):
    assert call("POST", "/admin/books/upload-url", bad)[0] == 400, bad
status, form = call("POST", "/admin/books/upload-url", {"shelf": "library", "filename": "Library fees.txt", "size": len(TEXT)})
assert status == 200 and form["key"].startswith("chatwoot-books/acme/") and form["key"].endswith("/Library_fees.txt") and form["upload"]["max_bytes"] == len(TEXT)
book = form["book_id"]
seen = call("GET", "/admin/books")[1]
assert [(p["id"], p["status"], p["shelf"]) for p in seen["pending"]] == [(book, "uploading", "library")]
assert call("POST", f"/admin/books/{book}/start")[0] == 409, "the file has not arrived yet"
raw.files[form["key"]] = TEXT
status, started = call("POST", f"/admin/books/{book}/start")
assert status == 202 and calls[-1] == ("ingest", False)
seen = call("GET", "/admin/books")[1]
library = next(s for s in seen["shelves"] if s["name"] == "library")
assert seen["pending"] == [] and len(library["books"]) == 1 and library["books"][0]["name"] == "Library fees.txt" and library["passages"] >= 1 and form["key"] not in raw.files, "read, listed, the uploaded file removed"
assert store.usage(api.usage_key("acme", NOW + 5)) == 7, "the embedding cost counts against the business's month"
assert call("POST", f"/admin/books/{book}/start")[0] == 404, "an upload is started once"
print("  ok  an upload form, the file sent, the book read into a new shelf in the background, listed with its numbers, the uploaded file removed, the cost counted")

# the same file again costs nothing; a changed file with the same name replaces the old one
def add(shelf, name, data):
    status, f = call("POST", "/admin/books/upload-url", {"shelf": shelf, "filename": name, "size": len(data)})
    assert status == 200, f
    raw.files[f["key"]] = data
    r = call("POST", f"/admin/books/{f['book_id']}/start"); assert r[0] == 202, r
    return f["book_id"]


spent = store.usage(api.usage_key("acme", NOW + 5))
add("library", "Library fees.txt", TEXT)
assert store.usage(api.usage_key("acme", NOW + 5)) == spent and len(next(s for s in call("GET", "/admin/books")[1]["shelves"] if s["name"] == "library")["books"]) == 1
add("library", "Library fees.txt", TEXT + b" A day pass costs Rs 50. " * 20)
library = next(s for s in call("GET", "/admin/books")[1]["shelves"] if s["name"] == "library")
assert len(library["books"]) == 1, "a changed file replaces the older book of the same name"
print("  ok  the same file again is free and not doubled; a changed file replaces the older one of the same name")

# ---- a book that cannot be read
bad_id = add("library", "empty.txt", b"   \n  ")
seen = call("GET", "/admin/books")[1]
assert [(p["id"], p["status"]) for p in seen["pending"]] == [(bad_id, "failed")] and "text" in seen["pending"][0]["error"].lower()
assert call("DELETE", f"/admin/books/pending/{bad_id}")[0] == 200 and call("GET", "/admin/books")[1]["pending"] == []
print("  ok  a book that cannot be read is shown as failed with a plain reason, and can be dismissed")

# ---- passages and search
doc = library["books"][0]["id"]
status, shown = call("GET", f"/admin/books/library/{doc}/passages")
assert status == 200 and shown["total"] >= 1 and "seat" in shown["passages"][0]["text"] and len(shown["passages"]) <= books.PREVIEW
assert call("GET", "/admin/books/library/dnotthere/passages")[0] == 404
asked = []


def worker(payload):
    asked.append(payload)
    return {"result": {"hits": [{"title": "Library fees", "page": 1, "score": 0.81, "text": "A general seat costs Rs 500"}], "confident": True, "missing": [], "spent_micro": 1}}


status, found = call("POST", "/admin/books/search", {"shelves": ["library"], "query": "seat fees?"}, invoke=worker)
assert status == 200 and found["confident"] and asked[-1] == {"kind": "books_search", "tenant_id": "acme", "shelves": ["library"], "query": "seat fees?"}
for bad in ({"shelves": [], "query": "x"}, {"shelves": ["library"]}, {"shelves": ["Library"], "query": "x"}, {"shelves": ["library"], "query": ""}, {"shelves": ["library"], "query": "x", "tenant_id": "other"}):
    assert call("POST", "/admin/books/search", bad, invoke=worker)[0] == 400, bad
print("  ok  a book's first passages are shown; a question to the shelves is forwarded with the tenant of the key and only valid shelf names")

# the worker side of the search: which passages, how sure, what it cost, and the refusals
class Runtime:
    def search(self, tenant, collections, query):
        return {"hits": [{"title": "Library fees", "page": 1, "section": "", "vector": 0.8123, "text": "seat Rs 500", "collection": "library"}], "confident": True, "cost_micro": 3, "missing": []}


wdeps = api.Deps(store, api.Secrets(lambda n: None), runtime=Runtime())
out = api.worker({"kind": "books_search", "tenant_id": "acme", "shelves": ["library"], "query": "seat"}, None, now=NOW, deps=wdeps)["result"]
assert out["hits"][0]["score"] == 0.812 and out["confident"] and out["spent_micro"] == 3
for bad in ({"tenant_id": "acme", "shelves": [], "query": "x"}, {"tenant_id": "acme", "shelves": ["a"], "query": " "}, {"tenant_id": 5, "shelves": ["a"], "query": "x"}):
    assert api.worker({"kind": "books_search", **bad}, None, now=NOW, deps=wdeps) == {"error": "bad_event"}, bad
assert api.worker({"kind": "books_search", "tenant_id": "ghost", "shelves": ["a"], "query": "x"}, None, now=NOW, deps=wdeps) == {"error": "not_found"}
store.items[api.usage_key("acme", NOW)] = {"micro": 10 ** 9}
assert api.worker({"kind": "books_search", "tenant_id": "acme", "shelves": ["a"], "query": "x"}, None, now=NOW, deps=wdeps) == {"error": "cap_reached"}
store.items.pop(api.usage_key("acme", NOW))
print("  ok  the search reports passages with their scores, whether the books are sure and what it cost; unknown business, odd input and a business at its cap are refused")

# ---- limits
old_books, old_shelves = books.MAX_BOOKS, books.MAX_SHELVES
books.MAX_BOOKS = 1
assert call("POST", "/admin/books/upload-url", {"shelf": "library", "filename": "c.txt", "size": 100})[0] == 400, "one book is already kept: no second"
assert books.count_books({"shelves": {"library": {"docs": [1, 2]}}, "pending": {}}, NOW) == 2
books.MAX_BOOKS = old_books
books.MAX_SHELVES = 2
status, _ = call("POST", "/admin/books/upload-url", {"shelf": "third", "filename": "c.txt", "size": 100})
assert status == 400, "faq and library already: no third shelf"
books.MAX_SHELVES = old_shelves
# an upload that was never finished disappears from the list after a while
status, stale = call("POST", "/admin/books/upload-url", {"shelf": "library", "filename": "x.txt", "size": 100})
assert len(call("GET", "/admin/books", at=NOW + 60)[1]["pending"]) == 1 and call("GET", "/admin/books", at=NOW + books.STALE_UPLOAD_SECONDS + 100)[1]["pending"] == []
print("  ok  at most the allowed number of books and shelves; an upload that was never finished is not shown after a while")

# ---- a business at its cap cannot add books
store.items[api.usage_key("acme", NOW + 5)] = {"micro": 10 ** 9}
capped = add("library", "more.txt", b"More text about the library. " * 40)
assert [(p["id"], p["status"]) for p in call("GET", "/admin/books", at=NOW + 5)[1]["pending"] if p["id"] == capped] == [(capped, "failed")]
store.items.pop(api.usage_key("acme", NOW + 5))
call("DELETE", f"/admin/books/pending/{capped}", at=NOW + 5)
print("  ok  a business at its spend limit cannot add a book (it is shown as failed with the reason)")

# ---- removing
status, _ = call("DELETE", f"/admin/books/library/{doc}")
assert status == 200
library = next(s for s in call("GET", "/admin/books", at=NOW + 5)[1]["shelves"] if s["name"] == "library")
assert library["books"] == [] and shelves.meta(ks.base_of("tenant", "acme", "library"))["docs"] == []
store.items[reg.agent_key("shop")] = {**store.items[reg.agent_key("shop")], "live_version": 1, "versions": 1}
store.items[reg.version_key("shop", 1)] = {"config": {"collections": ["library"]}}
status, body = call("DELETE", "/admin/books/shelves/library")
assert status == 409 and body["error"] == "shelf_in_use" and body["agents"] == ["shop"], "a shelf the live version reads is not removed"
store.items[reg.agent_key("shop")]["live_version"] = 0
assert call("DELETE", "/admin/books/shelves/library")[0] == 200 and shelves.meta(ks.base_of("tenant", "acme", "library")) is None
assert "library" not in [s["name"] for s in call("GET", "/admin/books", at=NOW + 5)[1]["shelves"]]
print("  ok  a book and a shelf are removed from the shelf and the list; a shelf the live version of an agent reads is refused, naming the agent")

# ---- the books function refuses what it should
for bad in ({}, {"kind": "ingest"}, {"kind": "nope", "tenant_id": "acme"}, {"kind": "ingest", "tenant_id": 5}):
    assert books.handler(bad, now=NOW, deps=deps) == {"error": "bad_event"}, bad
assert books.handler({"kind": "ingest", "tenant_id": "acme", "book_id": "0" * 12}, now=NOW, deps=deps) == {"status": "skipped"}, "no pending record, nothing to read"
assert books.handler({"kind": "delete_shelf", "tenant_id": "acme", "shelf": "pending"}, now=NOW, deps=deps)["error"] == "bad_event"
assert books.handler({"kind": "passages", "tenant_id": "acme", "shelf": "faq", "doc_id": "../x"}, now=NOW, deps=deps)["error"] == "bad_event"
# other businesses' shelves are never touched: the shelf path is built from the tenant of the event
other = ks.base_of("tenant", "other", "faq")
shelves.create_collection(other, "x", model=books.embedding_choice()[0], dims=books.embedding_choice()[1])
books.handler({"kind": "delete_shelf", "tenant_id": "acme", "shelf": "faq"}, now=NOW, deps=deps)
assert shelves.meta(other) is not None and shelves.meta(ks.base_of("tenant", "acme", "faq")) is None
print("  ok  the books function refuses odd events by name and works only on the tenant of the event")
