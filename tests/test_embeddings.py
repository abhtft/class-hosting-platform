"""The embedding client (lambda/embeddings.py): both providers, batching, the cache, retries, cost, and keys that never leak.

A fake transport stands in for Google and OpenRouter; it answers like the real services (Google: embeddings[].values and no usage; OpenRouter:
data[].embedding with usage). Whether the REAL services accept the parameters is checked later, on a real account, through the platform.
"""
import hashlib
import math
import os
import random
import sys
import time

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AI_SECRET_NAME="secret")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lambda"))
import embeddings as em  # noqa: E402
import gemini  # noqa: E402

gemini._secret.update(value="google-test-key", at=time.time() + 10 ** 6)  # no Secrets Manager in a test
em._openrouter_secret.update(value="or-test-key", at=time.time() + 10 ** 6)
KEY_G, KEY_O = "google-test-key", "or-test-key"


def vec(text, dims):
    rnd = random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:12], 16))
    return [rnd.gauss(0, 3) for _ in range(dims)]  # deliberately not unit length


class Fake:
    def __init__(self, script=None):
        self.calls, self.script = [], list(script or [])

    def __call__(self, provider, url, headers, body):
        self.calls.append({"provider": provider, "url": url, "headers": dict(headers), "body": body})
        if self.script:
            step = self.script.pop(0)
            if step is not None:
                return step
        if provider == "google":
            dims = body["requests"][0]["outputDimensionality"]
            return 200, {"embeddings": [{"values": vec(r["content"]["parts"][0]["text"], dims)} for r in body["requests"]]}
        texts = body["input"]
        # the response is returned out of order on purpose: the client must sort by index
        data = [{"index": i, "embedding": vec(t, body["dimensions"])} for i, t in enumerate(texts)][::-1]
        return 200, {"data": data, "usage": {"prompt_tokens": 7 * len(texts), "total_tokens": 7 * len(texts)}}


def run(texts, fake=None, **kw):
    em.transport = fake or Fake()
    return em.embed(texts, sleep=lambda s: None, **kw)


def unit(v):
    return abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-9


# ---------------------------------------------------------------- Google
fake = Fake()
out = run(["alpha text", "beta text"], fake, dims=64)
assert len(out["vectors"]) == 2 and all(len(v) == 64 and unit(v) for v in out["vectors"]), "unit length, the size asked for"
call = fake.calls[0]
assert call["provider"] == "google" and call["url"].endswith("/models/gemini-embedding-001:batchEmbedContents")
assert call["headers"]["x-goog-api-key"] == KEY_G
request = call["body"]["requests"][0]
assert request["model"] == "models/gemini-embedding-001" and request["outputDimensionality"] == 64 and "taskType" not in request, "no task type by default"
assert out["calls"] == 1 and out["cached"] == 0
assert out["tokens"] == sum(em.kc.estimate_tokens(t) for t in ["alpha text", "beta text"]), "Google reports no usage: estimated from the text"
assert out["cost_micro"] == math.ceil(out["tokens"] * 0.15)
print("  ok  Google: right URL, key header, size and no task type by default; unit-length vectors; cost estimated")

# ---------------------------------------------------------------- task types are opt-in and change nothing else
fake = Fake()
run(["q"], fake, dims=64, task="query", task_types=True)
assert fake.calls[0]["body"]["requests"][0]["taskType"] == "RETRIEVAL_QUERY"
fake = Fake()
run(["d"], fake, dims=64, task="document", task_types=True)
assert fake.calls[0]["body"]["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
cache = em.MemoryCache()
run(["same text"], dims=64, cache=cache)
run(["same text"], dims=64, cache=cache, task="query", task_types=True)
run(["same text"], dims=64, cache=cache, task="document", task_types=True)
assert len(cache.items) == 3, "no task types, query and document are three different vectors in the cache"
assert em.cache_key("m", 8, "", "t") != em.cache_key("m", 16, "", "t") != em.cache_key("n", 8, "", "t")
print("  ok  task types: off by default (vectors comparable across providers), opt-in per call, and part of the cache key")

# ---------------------------------------------------------------- OpenRouter
fake = Fake()
out = run(["one", "two", "three"], fake, model="google/gemini-embedding-001", dims=64)
call = fake.calls[0]
assert call["provider"] == "openrouter" and call["url"].endswith("/embeddings") and call["headers"]["Authorization"] == "Bearer " + KEY_O
assert call["body"] == {"model": "google/gemini-embedding-001", "input": ["one", "two", "three"], "dimensions": 64, "encoding_format": "float"}
assert out["vectors"][0] == out["vectors"][0] and [v for v in out["vectors"]] == [run([t], model="google/gemini-embedding-001", dims=64)["vectors"][0] for t in ["one", "two", "three"]], "order restored from the index"
assert out["tokens"] == 21 and out["cost_micro"] == math.ceil(21 * 0.15), "OpenRouter reports usage: that is what is counted"
# the same text through either provider gives vectors of the same shape, so a collection can be re-embedded with the other one
assert len(run(["x"], model="gemini-embedding-001", dims=64)["vectors"][0]) == len(run(["x"], model="google/gemini-embedding-001", dims=64)["vectors"][0])
print("  ok  OpenRouter: right URL, bearer key, dimensions, out-of-order answers restored, reported usage counted")

# ---------------------------------------------------------------- batching, duplicates, order
em.BATCH = 4
try:
    texts = [f"passage number {i}" for i in range(10)] + ["passage number 3", "passage number 0"]  # two duplicates
    fake = Fake()
    out = run(texts, fake, dims=64)
    sent = [r["content"]["parts"][0]["text"] for c in fake.calls for r in c["body"]["requests"]]
    assert [len(c["body"]["requests"]) for c in fake.calls] == [4, 4, 2], "ten unique texts in batches of 4"
    assert len(sent) == 10 and len(out["vectors"]) == 12 and out["calls"] == 3
    assert out["vectors"][10] == out["vectors"][3] and out["vectors"][11] == out["vectors"][0], "a duplicate gets the same vector, in its own place"
    assert out["vectors"][5] == run([texts[5]], dims=64)["vectors"][0], "order is kept across batches"
    assert out["cached"] == 0, "duplicates inside one call are sent once, but they are not cache hits"
finally:
    em.BATCH = 50
print("  ok  batching: unique texts only, batches of the set size, answers in the order asked, duplicates share one vector")

# ---------------------------------------------------------------- the cache
cache = em.MemoryCache()
first = Fake()
a = run(["cache me", "and me"], first, dims=64, cache=cache)
assert len(first.calls) == 1 and cache.writes == 2 and all(isinstance(v, str) for v in cache.items.values()), "stored in the compact text form"
second = Fake()
b = run(["cache me", "and me"], second, dims=64, cache=cache)
assert second.calls == [] and b["calls"] == 0 and b["tokens"] == 0 and b["cost_micro"] == 0 and b["cached"] == 2, "a cached text costs nothing"
assert all(max(abs(x - y) for x, y in zip(u, v)) < 2e-3 for u, v in zip(a["vectors"], b["vectors"])), "the cached vector is the same within float16 precision"
third = Fake()
c = run(["cache me", "brand new"], third, dims=64, cache=cache)
assert len(third.calls) == 1 and [r["content"]["parts"][0]["text"] for r in third.calls[0]["body"]["requests"]] == ["brand new"] and c["cached"] == 1
other_size = Fake()
run(["cache me"], other_size, dims=128, cache=cache)
assert len(other_size.calls) == 1, "another vector size is another entry"
vector = [0.3, -0.4, 0.5, 0.1]
assert max(abs(x - y) for x, y in zip(em.decode_vector(em.encode_vector(vector)), em.kc.normalize(vector))) < 2e-3
print("  ok  cache: a second ask is free, a mix sends only the new text, another size is another entry, compact form round-trips")

# ---------------------------------------------------------------- retries and failures
sleeps = []
fake = Fake([(429, {"error": {"message": "slow down"}}), (503, {"error": {"message": "busy"}}), None])
em.transport = fake
done = em.embed(["retry me"], dims=64, sleep=sleeps.append)
assert len(done["vectors"]) == 1 and sleeps == [1, 2] and len(fake.calls) == 3, "two temporary failures, then success, with growing waits"
fake = Fake([(500, {})] * 4)
em.transport = fake
try:
    em.embed(["x"], dims=64, sleep=lambda s: None)
    raise AssertionError("expected EmbeddingError")
except em.EmbeddingError as e:
    assert e.status == 500 and len(fake.calls) == 4, "gives up after the last wait"
fake = Fake([(400, {"error": {"message": "bad input"}})])
em.transport = fake
try:
    em.embed(["x"], dims=64, sleep=lambda s: None)
    raise AssertionError("expected EmbeddingError")
except em.EmbeddingError as e:
    assert e.status == 400 and str(e) == "bad input" and len(fake.calls) == 1, "a client error is not retried"
fake = Fake([(401, {"error": {"message": f"API key {KEY_G} is invalid"}})])
em.transport = fake
try:
    em.embed(["x"], dims=64, sleep=lambda s: None)
    raise AssertionError("expected EmbeddingError")
except em.EmbeddingError as e:
    assert KEY_G not in str(e) and "***" in str(e), "the key never appears in an error"
fake = Fake([(200, {"embeddings": [{"values": [1.0, 2.0]}]})] * 4)  # a malformed answer is a 502, which is retried; four in a row is refused
em.transport = fake
try:
    em.embed(["x"], dims=64, sleep=lambda s: None)
    raise AssertionError("expected EmbeddingError")
except em.EmbeddingError as e:
    assert e.status == 502 and "unexpected shape" in str(e), "a wrong number of numbers is refused, not stored"
print("  ok  failures: temporary errors are retried with growing waits, a client error is not, the key never leaks, odd answers refused")

# ---------------------------------------------------------------- input checks and keys
for bad, what in [(dict(texts=[""]), "empty"), (dict(texts=[123]), "not a string"), (dict(texts=["x" * (em.MAX_TEXT_CHARS + 1)]), "too long"),
                  (dict(texts=["x"], dims=10), "dims too small"), (dict(texts=["x"], dims=5000), "dims too large"), (dict(texts=["x"], dims=True), "dims a bool"),
                  (dict(texts=["x"], model="unknown-model"), "no price on file"), (dict(texts=["x"], model="bad name!"), "bad name"), (dict(texts=["x"], task="other"), "bad task")]:
    try:
        em.embed(sleep=lambda s: None, **{"dims": 64, **bad})
        raise AssertionError(f"expected EmbeddingError for {what}")
    except em.EmbeddingError as e:
        assert e.status == 400, what
os.environ["EMBEDDING_PRICES"] = '{"vendor/new-embedder": 0.02}'
try:
    assert run(["x"], model="vendor/new-embedder", dims=64)["cost_micro"] > 0 and em.prices()["vendor/new-embedder"] == 0.02
    os.environ["EMBEDDING_PRICES"] = "not json"
    assert em.prices()["gemini-embedding-001"] == 0.15, "a broken override leaves the built-in prices"
finally:
    del os.environ["EMBEDDING_PRICES"]
saved = dict(gemini._secret)
gemini._secret.update(value=None, at=0.0)
try:
    em.transport = Fake()
    em.embed(["x"], dims=64)
    raise AssertionError("expected EmbeddingError")
except em.EmbeddingError as e:
    assert e.status == 503 and "not set up" in str(e), str(e)
finally:
    gemini._secret.update(saved)
saved_or = dict(em._openrouter_secret)
em._openrouter_secret.update(value=None, at=0.0)
try:
    em.transport = Fake()
    em.embed(["x"], model="google/gemini-embedding-001", dims=64)
    raise AssertionError("expected EmbeddingError")
except em.EmbeddingError as e:
    assert e.status == 503 and "OPENROUTER_EMBEDDING_KEY" in str(e)
finally:
    em._openrouter_secret.update(saved_or)
assert em.provider_of("gemini-embedding-001") == "google" and em.provider_of("google/gemini-embedding-001") == "openrouter"
print("  ok  checks: bad texts, sizes, names and tasks refused; unknown prices refused; price override; missing keys say so plainly")

print("test_embeddings.py: all passed")
