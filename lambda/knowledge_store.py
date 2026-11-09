"""Knowledge storage: collections of documents in S3, the search index built from them, and a warm cache so a search reads almost nothing.

Layout, per collection (base = knowledge/<scope>/<owner>/<collection>; scope is "class" for the teacher's library, "student" for a private one):

  <base>/meta.json                         the pointer: version, embedding settings, document summaries, which index directory is current
  <base>/docs/<doc>/passages.json.gz       one document's passages (page, section, text)       } written first, then
  <base>/docs/<doc>/vectors.f16            its vectors (float16, unit length, one row each)    } doc.json LAST: a document exists once doc.json does
  <base>/docs/<doc>/doc.json               title, pages, counts, status
  <base>/index/<version>-<id>/             the search index built from ALL documents: passages.json.gz, vectors.f16, bm25.json.gz

The per-document files are the truth; the index is rebuilt from them after every change (a few thousand passages: well under a second of work and
a few megabytes). The index directory name carries a random id and is written completely before meta.json points at it, so a reader never sees a
half-written index and two rebuilds that overlap never mix their files. After writing, a rebuild checks that the set of documents did not change
underneath it and that its pointer is the one in place; if not it rebuilds again (a document that arrived meanwhile is included the next round).
"""
import gzip
import json
import os
import re
import time
import uuid
from collections import OrderedDict

import embeddings as em
import knowledge_core as kc
import platform_settings

ROOT = "knowledge"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SCOPES = ("class", "student")
CACHE_COLLECTIONS = platform_settings.value("knowledge.cacheShelves")          # collections kept in memory by one warm container
CACHE_PASSAGES = platform_settings.value("knowledge.cachePassages")         # and never more passages than this in total
REBUILD_TRIES = platform_settings.value("knowledge.rebuildTries")
DEFAULT_THRESHOLDS = {"min_similarity": platform_settings.value("knowledge.minSimilarity"), "min_coverage": platform_settings.value("knowledge.minCoverage")}  # starting points: calibrated per collection from an evaluation set on real books


class StoreError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def base_of(scope, owner, collection):
    if scope not in SCOPES or not NAME_RE.match(str(owner)) or not NAME_RE.match(str(collection)):
        raise StoreError(400, "bad_name", "A collection name is lowercase letters, digits, - and _ (up to 40 characters).")
    return f"{ROOT}/{scope}/{owner}/{collection}"


def _gz(value):
    return gzip.compress(json.dumps(value, separators=(",", ":")).encode(), mtime=0)  # mtime 0: the same content is the same bytes


def _ungz(data):
    return json.loads(gzip.decompress(data))


# ------------------------------------------------------------------ buckets

class MemoryBucket:
    """The bucket used by tests (and by a local run): a dict of key -> bytes."""

    def __init__(self):
        self.objects, self.reads = {}, []

    def put(self, key, data):
        self.objects[key] = bytes(data)

    def get(self, key):
        self.reads.append(key)
        return self.objects.get(key)

    def list(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def list_dirs(self, prefix):
        """The names of the folders directly under prefix (prefix ends with "/")."""
        return sorted({k[len(prefix):].split("/")[0] for k in self.objects if k.startswith(prefix) and "/" in k[len(prefix):]})

    def size(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def presigned_post(self, key, max_bytes, expires=300):
        return {"url": "memory://upload", "fields": {"key": key}, "max_bytes": max_bytes, "expires_in": expires}

    def delete_prefix(self, prefix):
        for key in [k for k in self.objects if k.startswith(prefix)]:
            del self.objects[key]


class S3Bucket:
    """The sites bucket under knowledge/ (CloudFront never serves that prefix)."""

    def __init__(self, bucket=None, client=None):
        self.bucket = bucket or os.environ["BUCKET"]
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.s3 = client

    def put(self, key, data):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ServerSideEncryption="AES256")

    def get(self, key):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None

    def list(self, prefix):
        keys, token = [], None
        while True:
            page = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
            keys += [o["Key"] for o in page.get("Contents", [])]
            token = page.get("NextContinuationToken")
            if not token:
                return sorted(keys)

    def list_dirs(self, prefix):
        names, token = [], None
        while True:
            page = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, Delimiter="/", **({"ContinuationToken": token} if token else {}))
            names += [p["Prefix"][len(prefix):].rstrip("/") for p in page.get("CommonPrefixes", [])]
            token = page.get("NextContinuationToken")
            if not token:
                return sorted(names)

    def size(self, key):
        try:
            return int(self.s3.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception as e:  # a missing key is 404 / NoSuchKey / NotFound depending on the client
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def presigned_post(self, key, max_bytes, expires=300):
        post = self.s3.generate_presigned_post(Bucket=self.bucket, Key=key, Fields={}, Conditions=[["content-length-range", 1, max_bytes]], ExpiresIn=expires)
        return {"url": post["url"], "fields": post["fields"], "max_bytes": max_bytes, "expires_in": expires}

    def delete_prefix(self, prefix):
        keys = self.list(prefix)
        for start in range(0, len(keys), 1000):
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys[start:start + 1000]], "Quiet": True})


# ------------------------------------------------------------------ loaded collections (warm cache)

class Loaded:
    def __init__(self, meta, passages, rows, bm25):
        self.meta, self.passages, self.rows, self.bm25 = meta, passages, rows, bm25


_cache = OrderedDict()  # base -> (index directory, Loaded)


def clear_cache():
    _cache.clear()


def _evict():
    while _cache and (len(_cache) > CACHE_COLLECTIONS or sum(len(v[1].passages) for v in _cache.values()) > CACHE_PASSAGES):
        _cache.popitem(last=False)


# ------------------------------------------------------------------ the store

class Store:
    def __init__(self, bucket, now=time.time):
        self.bucket, self.now = bucket, now

    # -- collections
    def meta(self, base):
        data = self.bucket.get(f"{base}/meta.json")
        return json.loads(data) if data else None

    def _write_meta(self, base, meta):
        self.bucket.put(f"{base}/meta.json", json.dumps(meta, separators=(",", ":")).encode())

    def create_collection(self, base, title, model=em.DEFAULT_MODEL, dims=em.DEFAULT_DIMS, task_types=False):
        try:
            em.check_settings(model, dims)
        except em.EmbeddingError as e:
            raise StoreError(400, "bad_settings", str(e))
        if self.meta(base):
            raise StoreError(409, "exists", "That collection already exists.")
        stamp = int(self.now())
        meta = {"version": 0, "index": "", "title": str(title)[:100], "embedding": {"model": model, "dims": dims, "task_types": bool(task_types)},
                "thresholds": dict(DEFAULT_THRESHOLDS), "docs": [], "passages": 0, "bytes": 0, "created": stamp, "updated": stamp}
        self._write_meta(base, meta)
        return meta

    def delete_collection(self, base):
        self.bucket.delete_prefix(base + "/")
        _cache.pop(base, None)

    def collections(self, scope, owner):
        """The names of one owner's collections (a student's, or the class library's), read from the folders: no database scan."""
        base_prefix = f"{ROOT}/{scope}/{owner}/"
        return [name for name in self.bucket.list_dirs(base_prefix) if NAME_RE.match(name) and self.bucket.size(f"{base_prefix}{name}/meta.json")]

    # -- documents
    def put_document(self, base, doc, passages, vector_bytes):
        """doc: {"id", "title", "type", "pages", "tokens", ...}. passages: records from kc.chunk_pages. vector_bytes: kc.pack_vectors of their embeddings."""
        meta = self.meta(base)
        if not meta:
            raise StoreError(404, "no_collection", "That collection does not exist.")
        if not NAME_RE.match(str(doc.get("id", ""))):
            raise StoreError(400, "bad_name", "A document id is lowercase letters, digits, - and _.")
        dims = meta["embedding"]["dims"]
        if len(vector_bytes) != len(passages) * dims * 2:
            raise StoreError(400, "bad_vectors", "The vectors do not match the passages or the collection's vector size.")
        folder = f"{base}/docs/{doc['id']}"
        self.bucket.put(f"{folder}/passages.json.gz", _gz([{k: p[k] for k in ("page", "page_end", "section", "text", "words")} for p in passages]))
        self.bucket.put(f"{folder}/vectors.f16", vector_bytes)
        record = {**{k: v for k, v in doc.items() if k != "passages"}, "passages": len(passages), "status": "ready", "added": int(self.now())}
        self.bucket.put(f"{folder}/doc.json", json.dumps(record, separators=(",", ":")).encode())  # last: the document exists from here on
        return self.rebuild(base)

    def delete_document(self, base, doc_id):
        if not NAME_RE.match(str(doc_id)):
            raise StoreError(400, "bad_name", "A document id is lowercase letters, digits, - and _.")
        self.bucket.delete_prefix(f"{base}/docs/{doc_id}/")
        return self.rebuild(base)

    def _doc_ids(self, base):
        return sorted({k.split("/")[-2] for k in self.bucket.list(f"{base}/docs/") if k.endswith("/doc.json")})

    # -- the index
    def rebuild(self, base):
        for _ in range(REBUILD_TRIES):
            meta = self.meta(base)
            if not meta:
                raise StoreError(404, "no_collection", "That collection does not exist.")
            dims = meta["embedding"]["dims"]
            ids = self._doc_ids(base)
            passages, parts, docs = [], [], []
            for doc_id in ids:
                folder = f"{base}/docs/{doc_id}"
                record = json.loads(self.bucket.get(f"{folder}/doc.json"))
                items, vectors = self.bucket.get(f"{folder}/passages.json.gz"), self.bucket.get(f"{folder}/vectors.f16")
                if items is None or vectors is None:
                    continue  # deleted while we were reading: the check below sees the change and rebuilds
                items = _ungz(items)
                if len(vectors) != len(items) * dims * 2:
                    raise StoreError(500, "corrupt", f"The stored vectors of document {doc_id} do not match its passages.")
                passages += [{**p, "doc": doc_id, "title": record.get("title", doc_id)} for p in items]
                parts.append(vectors)
                docs.append({k: record[k] for k in ("id", "title", "type", "pages", "passages", "tokens", "added", "needs_ocr", "source") if k in record})
            version = meta["version"] + 1
            directory = f"{version}-{uuid.uuid4().hex[:8]}"
            vectors_all = b"".join(parts)
            index_base = f"{base}/index/{directory}"
            self.bucket.put(f"{index_base}/passages.json.gz", _gz(passages))
            self.bucket.put(f"{index_base}/vectors.f16", vectors_all)
            self.bucket.put(f"{index_base}/bm25.json.gz", _gz(kc.build_bm25([kc.tokenize(kc.embed_text(p)) for p in passages])))
            fresh = {**meta, "version": version, "index": directory if passages else "", "docs": docs, "passages": len(passages),
                     "bytes": len(vectors_all), "updated": int(self.now())}
            self._write_meta(base, fresh)
            if self._doc_ids(base) == ids and (self.meta(base) or {}).get("index") == fresh["index"]:
                self._drop_old_indexes(base, version)
                _cache.pop(base, None)
                return fresh
        raise StoreError(409, "busy", "The collection is being changed by another upload. Try again in a moment.")

    def _drop_old_indexes(self, base, version):
        seen = {k.split("/")[-2] for k in self.bucket.list(f"{base}/index/")}
        for directory in seen:
            try:
                number = int(directory.split("-")[0])
            except ValueError:
                continue
            if number < version - 1:  # keep the previous one: a reader may still be loading it
                self.bucket.delete_prefix(f"{base}/index/{directory}/")

    def load(self, base):
        meta = self.meta(base)
        if not meta:
            raise StoreError(404, "no_collection", "That collection does not exist.")
        if not meta.get("index"):
            return Loaded(meta, [], [], kc.build_bm25([]))
        hit = _cache.get(base)
        if hit and hit[0] == meta["index"]:
            _cache.move_to_end(base)
            return Loaded(meta, hit[1].passages, hit[1].rows, hit[1].bm25)
        folder = f"{base}/index/{meta['index']}"
        raw = [self.bucket.get(f"{folder}/{name}") for name in ("passages.json.gz", "vectors.f16", "bm25.json.gz")]
        if any(r is None for r in raw):
            raise StoreError(503, "index_missing", "The search index is being rebuilt. Try again in a moment.")
        loaded = Loaded(meta, _ungz(raw[0]), kc.unpack_vectors(raw[1], meta["embedding"]["dims"], compact=True), _ungz(raw[2]))
        _cache[base] = (meta["index"], loaded)
        _evict()
        return loaded

    # -- search
    def _query_vector(self, loaded, query, cache):
        emb = loaded.meta["embedding"]
        done = em.embed([query], model=emb["model"], dims=emb["dims"], task="query", task_types=emb["task_types"], cache=cache)
        return done["vectors"][0], done

    def search(self, base, query, k=5, cache=None):
        """The best passages for a question, with where each came from. `confident` False means the books do not seem to answer it."""
        loaded = self.load(base)
        result = {"hits": [], "confident": False, "version": loaded.meta["version"], "tokens": 0, "cost_micro": 0}
        if not loaded.passages or not str(query).strip():
            return result
        vector, done = self._query_vector(loaded, str(query).strip(), cache)
        limits = {**DEFAULT_THRESHOLDS, **(loaded.meta.get("thresholds") or {})}
        found = kc.hybrid_search(str(query), vector, loaded.bm25, loaded.rows, loaded.passages, k=max(1, min(int(k), 20)),
                                 min_similarity=limits["min_similarity"], min_coverage=limits["min_coverage"])
        for hit in found["hits"]:
            p = loaded.passages[hit["n"]]
            result["hits"].append({"doc": p["doc"], "title": p["title"], "page": p["page"], "page_end": p["page_end"], "section": p["section"], "text": p["text"],
                                   "score": hit["score"], "vector": hit["vector"], "bm25": hit["bm25"], "coverage": hit["coverage"]})
        result.update(confident=found["confident"], tokens=done["tokens"], cost_micro=done["cost_micro"])
        return result

    def evaluate(self, base, cases, k=5, cache=None):
        """Recall@k, MRR and refusal rate of this collection on a set of {"question", "expect"} cases (see knowledge_core.evaluate)."""
        loaded = self.load(base)
        limits = {**DEFAULT_THRESHOLDS, **(loaded.meta.get("thresholds") or {})}

        def run(question):
            vector, _ = self._query_vector(loaded, question, cache)
            return kc.hybrid_search(question, vector, loaded.bm25, loaded.rows, loaded.passages, k=k, min_similarity=limits["min_similarity"], min_coverage=limits["min_coverage"])
        return kc.evaluate(cases, run, loaded.passages, k=k)
