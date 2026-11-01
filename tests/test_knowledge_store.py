"""Knowledge storage (lambda/knowledge_store.py): collections and documents in a bucket, the rebuilt search index, the warm cache, and search.

A MemoryBucket stands in for S3 (and a fake boto client exercises the real S3Bucket code). Embeddings come from a fake provider that hashes words into
a vector, so the questions here find their passages by shared words: this checks the storage and the plumbing, not semantic quality.
"""
import json
import os
import sys
import time
import zlib

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AI_SECRET_NAME="secret", BUCKET="b")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))
import embeddings as em  # noqa: E402
import gemini  # noqa: E402
import knowledge_core as kc  # noqa: E402
import knowledge_store as ks  # noqa: E402

gemini._secret.update(value="google-test-key", at=time.time() + 10 ** 6)
DIMS = 256
provider_calls = []


def lexical(text, dims=DIMS):
    row = [0.0] * dims
    for term in kc.tokenize(text):
        row[zlib.crc32(term.encode()) % dims] += 1.0
    return row


def transport(provider, url, headers, body):
    texts = [r["content"]["parts"][0]["text"] for r in body["requests"]]
    provider_calls.append(texts)
    return 200, {"embeddings": [{"values": lexical(t, body["requests"][0]["outputDimensionality"])} for t in texts]}


em.transport = transport
emb_cache = em.MemoryCache()


def pages_of(topics, first=1):
    out, number = [], first
    for title, paragraphs in topics:
        for paragraph in paragraphs:
            out.append((number, f"Study Book\n{number}\n{title}\n{paragraph}"))
            number += 1
    return out


SCIENCE = [("Chapter 1: Photosynthesis", ["Photosynthesis is the process by which green plants make their own food. The leaves absorb sunlight using chlorophyll. Water from the soil and carbon dioxide from the air are combined. Glucose and oxygen are produced.",
                                           "The stomata are tiny pores on the surface of a leaf. They open and close to let gases pass in and out. Guard cells control the opening of each stomata pore."]),
           ("Chapter 2: Laws of Motion", ["Newton's second law states that force equals mass multiplied by acceleration. A heavier object needs a larger force to get the same acceleration."])]
HISTORY = [("Chapter 1: The Mughal Empire", ["Akbar was the greatest of the Mughal emperors. He ruled from Agra and built Fatehpur Sikri. Akbar followed a policy of religious tolerance.",
                                              "Aurangzeb ruled the Mughal empire for nearly fifty years until 1707. After his death the empire weakened and regional powers grew."])]
RIVERS = [("Chapter 1: The Water Cycle", ["Water evaporates from oceans and rivers when the sun heats it. The vapour rises and condenses into clouds. Rain and snow bring the water back to the ground. This continuous movement is the water cycle."])]


def ingest(store, base, doc_id, title, topics, first_page=1, put=True):
    """What the ingestion worker does, in miniature: clean, chunk, embed (through the cache), pack."""
    passages = kc.chunk_pages(kc.clean_pages(pages_of(topics, first_page)), target_words=60, overlap_words=10, min_words=15)
    vectors = em.embed([kc.embed_text(p) for p in passages], dims=DIMS, cache=emb_cache)["vectors"]
    packed = kc.pack_vectors(vectors)
    doc = {"id": doc_id, "title": title, "type": "pdf", "pages": len(topics), "tokens": sum(kc.estimate_tokens(p["text"]) for p in passages)}
    return (store.put_document(base, doc, passages, packed) if put else (doc, passages, packed))


def fails(fn, status, code=None):
    try:
        fn()
    except ks.StoreError as e:
        assert e.status == status and (code is None or e.code == code), (e.status, e.code, str(e))
        return
    raise AssertionError(f"expected StoreError {status} {code}")


ks.clear_cache()
bucket = ks.MemoryBucket()
store = ks.Store(bucket, now=lambda: 1_700_000_000)
base = ks.base_of("student", "alice", "books")
assert base == "knowledge/student/alice/books" and ks.base_of("class", "library", "ncert-9") == "knowledge/class/library/ncert-9"
for bad in [("other", "alice", "x"), ("student", "Alice", "x"), ("student", "alice", "../x"), ("student", "alice", ""), ("student", "a/b", "x")]:
    fails(lambda b=bad: ks.base_of(*b), 400, "bad_name")

# ---------------------------------------------------------------- a collection and its first document
meta = store.create_collection(base, "Alice's books", dims=DIMS)
assert meta["version"] == 0 and meta["embedding"] == {"model": "gemini-embedding-001", "dims": DIMS, "task_types": False} and meta["docs"] == []
fails(lambda: store.create_collection(base, "again", dims=DIMS), 409, "exists")
fails(lambda: store.create_collection(ks.base_of("student", "alice", "x"), "bad size", dims=10), 400)
assert store.search(base, "photosynthesis") == {"hits": [], "confident": False, "version": 0, "tokens": 0, "cost_micro": 0}, "an empty collection finds nothing"
meta = ingest(store, base, "science", "Science Class 9", SCIENCE)
assert meta["version"] == 1 and meta["passages"] >= 3 and [d["id"] for d in meta["docs"]] == ["science"] and meta["docs"][0]["title"] == "Science Class 9"
assert meta["bytes"] == meta["passages"] * DIMS * 2
found = store.search(base, "How do plants make their own food?")
assert found["confident"] and found["hits"][0]["title"] == "Science Class 9" and found["hits"][0]["section"] == "Chapter 1: Photosynthesis" and found["hits"][0]["page"] == 1
assert found["tokens"] > 0 and found["cost_micro"] > 0 and found["version"] == 1
print("  ok  a collection with one document: indexed, searchable, hits carry title, section and page, the question's cost is reported")

# ---------------------------------------------------------------- two documents, one index
meta = ingest(store, base, "history", "History Class 9", HISTORY)
assert meta["version"] == 2 and {d["id"] for d in meta["docs"]} == {"science", "history"}
answer = store.search(base, "Who built Fatehpur Sikri?")
assert answer["confident"] and answer["hits"][0]["doc"] == "history" and answer["hits"][0]["title"] == "History Class 9"
assert store.search(base, "State Newton's second law")["hits"][0]["doc"] == "science"
assert store.search(base, "What is on the school canteen lunch menu?")["confident"] is False
assert len({k.split("/")[-2] for k in bucket.list(f"{base}/index/")}) <= 2, "only the current and the previous index directory exist"
print("  ok  two documents share one index: each question finds its own book; an off-topic question is not confident")

# ---------------------------------------------------------------- the warm cache
ks.clear_cache()
bucket.reads.clear()
store.search(base, "What is inertia force?")
first = list(bucket.reads)
bucket.reads.clear()
provider_calls.clear()
store.search(base, "What is inertia force?", cache=emb_cache)
store.search(base, "What is inertia force?", cache=emb_cache)
assert any(k.endswith("passages.json.gz") for k in first) and any(k.endswith("vectors.f16") for k in first), "the first search loads the index"
assert bucket.reads and all(k.endswith("meta.json") for k in bucket.reads), bucket.reads
assert len(provider_calls) == 1, "the same question twice, with the cache, reaches the provider once"
before = len(bucket.reads)
ingest(store, base, "rivers", "Geography", RIVERS)
bucket.reads.clear()
cold = store.search(base, "Explain the water cycle")
assert cold["hits"][0]["doc"] == "rivers" and any(k.endswith("vectors.f16") for k in bucket.reads), "a changed collection is reloaded, not served stale"
print("  ok  warm cache: a repeat search reads only meta.json, the same question costs no provider call, a new version reloads")

# ---------------------------------------------------------------- old indexes are dropped, deletes work
for n in range(3):
    ingest(store, base, f"extra{n}", f"Extra {n}", RIVERS)
assert len({k.split("/")[-2] for k in bucket.list(f"{base}/index/")}) <= 2, "only the current and the previous index are kept"
meta = store.delete_document(base, "history")
assert "history" not in {d["id"] for d in meta["docs"]} and not [k for k in bucket.list(f"{base}/docs/history/")]
assert store.search(base, "Who built Fatehpur Sikri?")["confident"] is False, "a deleted book is no longer found"
for doc_id in [d["id"] for d in store.meta(base)["docs"]]:
    meta = store.delete_document(base, doc_id)
assert meta["passages"] == 0 and meta["index"] == "" and store.search(base, "plants")["hits"] == []
store.delete_collection(base)
assert store.meta(base) is None and not bucket.list(base) and base not in ks._cache
fails(lambda: store.search(base, "x"), 404, "no_collection")
print("  ok  old index versions are dropped, a deleted book is gone from search, an emptied or deleted collection is clean")

# ---------------------------------------------------------------- an upload that finishes during a rebuild is not lost
class Racy(ks.MemoryBucket):
    inject = None

    def put(self, key, data):
        super().put(key, data)
        if self.inject and "/index/" in key and key.endswith("passages.json.gz"):
            action, self.inject = self.inject, None
            action()


racy = Racy()
rstore = ks.Store(racy)
rbase = ks.base_of("class", "library", "shared")
rstore.create_collection(rbase, "Shared", dims=DIMS)
ingest(rstore, rbase, "first", "First book", SCIENCE)


def late_upload():  # another worker finishes its document while this rebuild is writing
    doc, passages, packed = ingest(rstore, rbase, "late", "Late book", HISTORY, put=False)
    folder = f"{rbase}/docs/late"
    racy.objects[f"{folder}/passages.json.gz"] = ks._gz([{k: p[k] for k in ("page", "page_end", "section", "text", "words")} for p in passages])
    racy.objects[f"{folder}/vectors.f16"] = packed
    racy.objects[f"{folder}/doc.json"] = json.dumps({**doc, "passages": len(passages), "status": "ready", "added": 1}).encode()


racy.inject = late_upload
ingest(rstore, rbase, "second", "Second book", RIVERS)
final = rstore.meta(rbase)
assert {d["id"] for d in final["docs"]} == {"first", "second", "late"}, "the rebuild noticed the document that arrived meanwhile and included it"
loaded = rstore.load(rbase)
assert len(loaded.passages) == len(loaded.rows) == final["passages"] and loaded.meta["index"] == final["index"], "passages, vectors and pointer agree"
assert rstore.search(rbase, "Who built Fatehpur Sikri?")["hits"][0]["doc"] == "late"
print("  ok  two uploads finishing together: the second rebuild includes the document that arrived during it; index files agree")

# ---------------------------------------------------------------- bad input and damage
fails(lambda: rstore.put_document(rbase, {"id": "Bad Id", "title": "x"}, [], b""), 400, "bad_name")
fails(lambda: rstore.put_document(rbase, {"id": "ok", "title": "x"}, [{"page": 1, "page_end": 1, "section": "", "text": "t", "words": 1}], b"\x00\x00"), 400, "bad_vectors")
fails(lambda: rstore.put_document(ks.base_of("class", "library", "missing"), {"id": "ok", "title": "x"}, [], b""), 404, "no_collection")
fails(lambda: rstore.delete_document(rbase, "../x"), 400, "bad_name")
racy.objects[f"{rbase}/docs/first/vectors.f16"] = racy.objects[f"{rbase}/docs/first/vectors.f16"][:-2]  # damage: one number short
fails(lambda: rstore.rebuild(rbase), 500, "corrupt")
print("  ok  bad ids, mismatched vectors and a missing collection are refused; a damaged vector file is reported, not indexed")

# ---------------------------------------------------------------- per-collection thresholds and evaluation
ks.clear_cache()
tbucket = ks.MemoryBucket()
tstore = ks.Store(tbucket)
tbase = ks.base_of("student", "bob", "notes")
tstore.create_collection(tbase, "Notes", dims=DIMS)
ingest(tstore, tbase, "science", "Science", SCIENCE)
ingest(tstore, tbase, "history", "History", HISTORY)
assert tstore.search(tbase, "plants make food")["confident"] is True
tmeta = tstore.meta(tbase)
tmeta["thresholds"] = {"min_similarity": 0.99, "min_coverage": 2.0}  # unreachable on both paths: a hit with every query word would otherwise still count
tstore._write_meta(tbase, tmeta)
ks.clear_cache()
assert tstore.search(tbase, "plants make food")["confident"] is False, "a stricter collection threshold is used"
tmeta["thresholds"] = dict(ks.DEFAULT_THRESHOLDS)
tstore._write_meta(tbase, tmeta)
cases = [{"question": "How do plants make their own food?", "expect": {"pages": [1]}}, {"question": "What do stomata do?", "expect": {"pages": [2]}},
         {"question": "Who built Fatehpur Sikri?", "expect": {"contains": "fatehpur sikri"}}, {"question": "What is on the canteen menu?", "expect": None}]
report = tstore.evaluate(tbase, cases, k=3)
assert report["recall"] == 1.0 and report["refusal"] == 1.0, report
print("  ok  thresholds are per collection; the collection can be scored on an evaluation set")

# ---------------------------------------------------------------- the cache never grows without bound
ks.clear_cache()
ks.CACHE_COLLECTIONS = 2
try:
    for name in ("one", "two", "three"):
        b = ks.base_of("student", "carol", name)
        tstore.create_collection(b, name, dims=DIMS)
        ingest(tstore, b, "science", "Science", SCIENCE)
        tstore.load(b)
    assert list(ks._cache) == [ks.base_of("student", "carol", "two"), ks.base_of("student", "carol", "three")], "the least recently used collection is dropped"
    ks.CACHE_COLLECTIONS, ks.CACHE_PASSAGES = 6, 3
    ks.clear_cache()
    tstore.load(ks.base_of("student", "carol", "one"))
    tstore.load(ks.base_of("student", "carol", "two"))
    assert len(ks._cache) <= 1, "and never more passages than the budget allows"
finally:
    ks.CACHE_COLLECTIONS, ks.CACHE_PASSAGES = 6, 60000
    ks.clear_cache()
print("  ok  warm cache is bounded by collections and by passages, least recently used first")


# ---------------------------------------------------------------- the real S3 code, against a fake boto client
class NoSuchKey(Exception):
    pass


class FakeS3:
    class exceptions:
        NoSuchKey = NoSuchKey

    def __init__(self):
        self.objects, self.page = {}, 2

    def put_object(self, Bucket, Key, Body, ServerSideEncryption=None):
        assert Bucket == "b" and ServerSideEncryption == "AES256"
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NoSuchKey()
        data = self.objects[Key]
        return {"Body": type("Body", (), {"read": lambda self_: data})()}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        chunk = keys[start:start + self.page]
        more = start + self.page < len(keys)
        return {"Contents": [{"Key": k} for k in chunk], **({"NextContinuationToken": str(start + self.page)} if more else {})}

    def delete_objects(self, Bucket, Delete):
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)


fake = FakeS3()
s3 = ks.S3Bucket(client=fake)
s3.put("a/one", b"1")
s3.put("a/two", b"2")
s3.put("a/three", b"3")
s3.put("b/other", b"4")
assert s3.get("a/one") == b"1" and s3.get("a/missing") is None
assert s3.list("a/") == ["a/one", "a/three", "a/two"], "all pages are followed"
s3.delete_prefix("a/")
assert s3.list("a/") == [] and s3.get("b/other") == b"4"
sbase = ks.base_of("student", "dave", "s3test")
sstore = ks.Store(s3)
sstore.create_collection(sbase, "S3", dims=DIMS)
ingest(sstore, sbase, "science", "Science", SCIENCE)
assert sstore.search(sbase, "stomata")["hits"][0]["doc"] == "science", "the whole flow works through the S3 bucket class"
print("  ok  S3Bucket: put, get (missing is None), listing over pages, prefix delete, and a full collection through it")

print("test_knowledge_store.py: all passed")
