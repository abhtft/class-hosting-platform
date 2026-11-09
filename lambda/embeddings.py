"""Embedding client for the class knowledge service (standard library only).

  texts --embed()--> unit-length vectors (one per text, same order) + tokens used + cost in micro-dollars

Two providers behind one function. The model name decides: a plain name (gemini-embedding-001) goes to Google; a name with a provider prefix
(google/gemini-embedding-001) goes to OpenRouter. Keys come from the same Secrets Manager secret as the class AI (GOOGLE_API_KEY, and for OpenRouter
OPENROUTER_EMBEDDING_KEY: a normal inference key, because the management key cannot call models). They are never returned, logged or put in an error.

Compatibility on purpose: by default NO task types are sent. Google can be told "this is a document" or "this is a question" (better retrieval), but
OpenRouter cannot, and vectors made both ways are not comparable. With no task type both providers give vectors that can be searched together, so a
teacher can move a collection from Gemini to OpenRouter (or back) by re-embedding only if they want to. A collection may switch task types on
(`task_types=True`); that changes every cache key and means a re-index.

Caching: a text is embedded once per (model, dimension, task types, text); `cache` is anything with get_many(keys) and put_many(items), and values are
the compact encoded form (encode_vector). Cost: one micro-dollar per token equals one US dollar per million tokens.
"""
import base64
import hashlib
import json
import math
import os
import re
import struct
import time
import urllib.error
import urllib.request

import gemini
import knowledge_core as kc
import platform_settings

DEFAULT_MODEL = platform_settings.default("knowledge.embeddingModel")
DEFAULT_DIMS = platform_settings.default("knowledge.embeddingDims")
MAX_DIMS = platform_settings.maximum("knowledge.embeddingDims")
BATCH = platform_settings.value("knowledge.embedBatch")  # texts per request (Google accepts 100)
MAX_TEXT_CHARS = platform_settings.value("knowledge.embedMaxTextChars")  # about 3000 tokens: a passage is far smaller; anything bigger is a mistake upstream
RETRY_DELAYS = (1, 2, 4)
GOOGLE_URL = os.environ.get("EMBEDDING_GOOGLE_URL", "https://generativelanguage.googleapis.com/v1beta")
OPENROUTER_URL = os.environ.get("EMBEDDING_OPENROUTER_URL", "https://openrouter.ai/api/v1")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,79}$")
# USD per million input tokens. Override or add with EMBEDDING_PRICES, a JSON object such as {"google/gemini-embedding-001": 0.15}.
DEFAULT_PRICES = {"gemini-embedding-001": 0.15, "google/gemini-embedding-001": 0.15}
KEY_CACHE_SECONDS = platform_settings.value("ai.keyCacheSeconds")

transport = None  # tests: fake(provider, url, headers, body) -> (status, json)
_openrouter_secret = {"value": None, "at": 0.0}


class EmbeddingError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def prices():
    table = dict(DEFAULT_PRICES)
    try:
        table.update({k: float(v) for k, v in json.loads(os.environ.get("EMBEDDING_PRICES") or "{}").items()})
    except (ValueError, TypeError, AttributeError):
        pass  # a broken override must not take the service down: the built-in prices stay
    return table


def provider_of(model):
    return "openrouter" if "/" in model else "google"


def cost_micro(model, tokens):
    """Micro-dollars, rounded up (never under-count). An unknown model is refused before anything is sent, so every embedded text has a price."""
    return math.ceil(tokens * prices()[model])


def check_settings(model, dims):
    if not isinstance(model, str) or not MODEL_RE.match(model):
        raise EmbeddingError(400, "The embedding model name is not valid.")
    if model not in prices():
        raise EmbeddingError(400, f"The embedding model {model} has no price on file, so it cannot be used (add it in the EMBEDDING_PRICES setting).")
    if not isinstance(dims, int) or isinstance(dims, bool) or not 64 <= dims <= MAX_DIMS:
        raise EmbeddingError(400, f"The vector size must be a whole number from 64 to {MAX_DIMS}.")


# ------------------------------------------------------------------ cache keys and the compact stored form

def cache_key(model, dims, variant, text):
    """variant is "" without task types, otherwise "document" or "query": a query and a document vector differ only when task types are on."""
    return "emb#" + hashlib.sha256(f"{model}\n{dims}\n{variant}\n{text}".encode()).hexdigest()


def encode_vector(vector):
    """Unit-length float16 as base64 text (a 768 number vector is 2 KB): small enough for one database item, exact enough for retrieval."""
    return base64.b64encode(struct.pack(f"<{len(vector)}e", *kc.normalize(vector))).decode()


def decode_vector(text):
    raw = base64.b64decode(text)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


class MemoryCache:
    """The cache used by tests and by a one-off script; the service uses a database-backed one with the same two methods."""

    def __init__(self):
        self.items, self.reads, self.writes = {}, 0, 0

    def get_many(self, keys):
        self.reads += len(keys)
        return {k: self.items[k] for k in keys if k in self.items}

    def put_many(self, items):
        self.writes += len(items)
        self.items.update(items)


# ------------------------------------------------------------------ providers

def _google_key():
    try:
        return gemini.google_key()
    except gemini.GeminiError as e:
        raise EmbeddingError(e.status, str(e))


def _openrouter_key():
    if _openrouter_secret["value"] is None or time.time() - _openrouter_secret["at"] > KEY_CACHE_SECONDS:
        import boto3
        try:
            raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["AI_SECRET_NAME"])["SecretString"]
            value = json.loads(raw)["OPENROUTER_EMBEDDING_KEY"]
        except Exception:  # not saved (or not readable): said plainly, with no detail
            raise EmbeddingError(503, "Embeddings through OpenRouter need a key the teacher has not saved yet (OPENROUTER_EMBEDDING_KEY). Use a Google embedding model instead, or ask your teacher.")
        _openrouter_secret["value"], _openrouter_secret["at"] = value, time.time()
    return _openrouter_secret["value"]


def _send(provider, url, headers, body, key, timeout):
    if transport is not None:
        status, data = transport(provider, url, headers, body)
    else:
        request = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, text = response.status, response.read().decode().strip()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode().strip()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise EmbeddingError(502, f"Could not reach the embedding service ({type(e).__name__}).")
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
    if status >= 400:
        err = data[0] if isinstance(data, list) and data else data
        detail = (err or {}).get("error") if isinstance(err, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else (detail if isinstance(detail, str) else None)
        raise EmbeddingError(status, str(message or f"The embedding service answered HTTP {status}.").replace(key, "***"))
    return data


def _request(model, dims, texts, task, task_types, timeout):
    """One batch to the right provider. Returns (vectors, tokens or None)."""
    if provider_of(model) == "google":
        key = _google_key()
        requests = []
        for text in texts:
            item = {"model": f"models/{model}", "content": {"parts": [{"text": text}]}, "outputDimensionality": dims}
            if task_types:
                item["taskType"] = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
            requests.append(item)
        data = _send("google", f"{GOOGLE_URL}/models/{model}:batchEmbedContents", {"x-goog-api-key": key, "Content-Type": "application/json", "User-Agent": "student-deploy/1"},
                     {"requests": requests}, key, timeout)
        rows = [e.get("values") for e in data.get("embeddings", [])]
        tokens = None  # Google does not report usage for embeddings
    else:
        key = _openrouter_key()
        data = _send("openrouter", f"{OPENROUTER_URL}/embeddings", {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "student-deploy/1"},
                     {"model": model, "input": texts, "dimensions": dims, "encoding_format": "float"}, key, timeout)
        rows = [e.get("embedding") for e in sorted(data.get("data", []), key=lambda e: e.get("index", 0))]
        usage = data.get("usage") or {}
        tokens = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None
    if len(rows) != len(texts) or any(not isinstance(r, list) or len(r) != dims for r in rows):
        raise EmbeddingError(502, "The embedding service answered with vectors of an unexpected shape.")
    return [kc.normalize(r) for r in rows], tokens


def _with_retries(call, sleep):
    for attempt, delay in enumerate(RETRY_DELAYS + (None,)):
        try:
            return call()
        except EmbeddingError as e:
            if delay is None or not (e.status == 429 or e.status >= 500):
                raise
            sleep(delay)


# ------------------------------------------------------------------ the one entry point

def embed(texts, model=DEFAULT_MODEL, dims=DEFAULT_DIMS, task="document", task_types=False, cache=None, sleep=time.sleep, timeout=25):
    """Embed `texts`. Returns {"vectors": [unit-length list per text, same order], "tokens", "cost_micro", "cached", "calls"}.

    `task` ("document" or "query") only matters when task_types is True. Identical texts in one call are sent once. With a `cache`, texts already
    embedded for the same (model, dims, task types) cost nothing; `tokens` and `cost_micro` count only what was really sent."""
    check_settings(model, dims)
    if task not in ("document", "query"):
        raise EmbeddingError(400, "task must be document or query.")
    clean = []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingError(400, "Every text to embed must be a non-empty string.")
        if len(text) > MAX_TEXT_CHARS:
            raise EmbeddingError(400, f"A text is longer than {MAX_TEXT_CHARS} characters; split it first.")
        clean.append(text.strip())
    variant = task if task_types else ""
    keyed = {t: cache_key(model, dims, variant, t) for t in set(clean)}
    found = {}
    if cache is not None and keyed:
        found = {key: decode_vector(value) for key, value in cache.get_many(list(keyed.values())).items()}
    pending = [t for t in dict.fromkeys(clean) if keyed[t] not in found]
    tokens_used, calls, fresh = 0, 0, {}
    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        vectors, reported = _with_retries(lambda: _request(model, dims, batch, task, task_types, timeout), sleep)
        calls += 1
        tokens_used += reported if reported is not None else sum(kc.estimate_tokens(t) for t in batch)
        for text, vector in zip(batch, vectors):
            fresh[keyed[text]] = vector
    if cache is not None and fresh:
        cache.put_many({key: encode_vector(vector) for key, vector in fresh.items()})
    by_key = {**found, **fresh}
    return {"vectors": [by_key[keyed[t]] for t in clean], "tokens": tokens_used, "cost_micro": cost_micro(model, tokens_used),
            "cached": sum(1 for t in clean if keyed[t] in found), "calls": calls}
