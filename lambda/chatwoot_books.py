"""Books for a business's agents (plan: docs/notes/KNOWLEDGE-TAB-PLAN.md): the owner adds, reads and removes the PDFs and text files an agent answers from, in Studio, without the teacher's command.

The shelves are the same ones the teacher's `knowledge-sync` writes (knowledge/tenant/<tenant>/<shelf>/ in the sites bucket), so what is already there simply shows up. Three parts:

  * the INDEX, one item `books#<tenant>` in the adapter's table: for every shelf its title, passage count and books (id, name, type, pages, passages), and `pending` books that are being added
    (uploading, queued, reading, failed). The admin function reads and writes only this item (and signs upload forms); it never touches the shelves, so its permissions stay small.
  * the BOOKS FUNCTION (ChatwootBooksFunction, `handler` below): the only writer of the shelves. It reads an uploaded file, cuts it into passages, embeds them (the cost counts against the
    business's month), stores them and updates the index; it also removes a book or a shelf, builds the index from the shelves that are already there (`reindex`) and returns a book's first passages.
  * the pure rules here: names, types and limits, the index changes, what an upload may be.

A file the owner uploads goes to chatwoot-books/<tenant>/<book id>/<name> first and is deleted as soon as it was read (or refused). Nothing here keeps a customer's words.
"""
import hashlib
import json
import os
import re
import time
import uuid

import platform_settings

MAX_BOOKS = platform_settings.value("chatwoot.bookMaxBooks")
MAX_SHELVES = platform_settings.value("chatwoot.bookMaxShelves")
PREVIEW = platform_settings.value("chatwoot.bookPreviewPassages")
STALE_UPLOAD_SECONDS = platform_settings.value("chatwoot.bookStaleUploadMinutes") * 60
PREFIX = "chatwoot-books/"
EXTENSIONS = ("pdf", "txt", "md", "markdown", "html", "htm", "text")
UPLOAD_SECONDS = 900
SHELF = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class Invalid(ValueError):
    pass


def index_key(tenant_id):
    return f"books#{tenant_id}"


RESERVED = ("pending", "shelves", "search", "refresh", "upload-url")  # words the routes use


def clean_shelf(name):
    if not isinstance(name, str) or not SHELF.fullmatch(name) or name in RESERVED:
        raise Invalid("A shelf name is lowercase letters, digits, - and _ (up to 40 characters), and not " + ", ".join(RESERVED) + ".")
    return name


def safe_filename(name):
    """The file name as it is kept: letters, digits, dots, dashes (the original is shown to the owner as given, cut)."""
    return (_NAME_UNSAFE.sub("_", str(name)).strip("_.") or "book")[:100]


# ---------- the index ----------

def change_index(store, tenant_id, change, now):
    """Apply change(index) -> result to the business's index with a conditional write, a few tries on a lost race; Invalid from `change` stops it. Returns what `change` returned."""
    key = index_key(tenant_id)
    for _ in range(6):
        item = store.get(key)
        index = {"shelves": dict((item or {}).get("shelves") or {}), "pending": dict((item or {}).get("pending") or {})}
        result = change(index)
        updated = {"tenant_id": tenant_id, "shelves": index["shelves"], "pending": index["pending"], "rev": (item["rev"] + 1) if item else 1, "updated_at": now}
        if (store.replace_if(key, updated, "rev", item["rev"]) if item else store.claim(key, updated)):
            return result
    raise Invalid("The books of this business were changed at the same moment. Try again.")


def live_pending(pending, now):
    """The pending books worth showing: an upload that never started is dropped after the stale time; the rest stay until they are done or removed."""
    return {k: v for k, v in pending.items() if not (v.get("status") == "uploading" and now - int(v.get("at") or 0) > STALE_UPLOAD_SECONDS)}


def shelf_summary(meta):
    """What the index keeps of one shelf, from its meta.json."""
    docs = [{"id": d["id"], "name": d.get("source") or d.get("title") or d["id"], "title": d.get("title") or d["id"], "type": d.get("type"), "pages": d.get("pages"), "passages": d.get("passages"), "added": d.get("added")}
            for d in meta.get("docs", []) if isinstance(d, dict) and d.get("id")]
    return {"title": meta.get("title") or "", "passages": int(meta.get("passages") or 0), "docs": docs, "updated": int(meta.get("updated") or 0)}


def view(item, now):
    """What Studio gets: {"shelves": [{"name", "title", "passages", "books": [...]}], "pending": [{"id", "shelf", "name", "status", "error"?}], "limits": {...}} (nothing but names and numbers)."""
    item = item or {}
    shelves = [{"name": name, "title": s.get("title", ""), "passages": s.get("passages", 0), "books": s.get("docs", [])} for name, s in sorted((item.get("shelves") or {}).items())]
    pending = [{"id": bid, **{k: p[k] for k in ("shelf", "name", "status", "error", "size", "at") if k in p}} for bid, p in sorted(live_pending(item.get("pending") or {}, now).items(), key=lambda kv: kv[1].get("at", 0))]
    return {"shelves": shelves, "pending": pending, "limits": {"max_books": MAX_BOOKS, "max_shelves": MAX_SHELVES, "max_file_mb": max_file_mb(), "types": ["pdf", "txt", "md", "html"]}}


def max_file_mb():
    return platform_settings.value("knowledge.maxFileMB")


def count_books(index, now):
    return sum(len(s.get("docs", [])) for s in index["shelves"].values()) + len(live_pending(index["pending"], now))


# ---------- an upload ----------

def upload_form(store, files, tenant, body, now):
    """Where the owner's browser may send one book: {"book_id", "upload": {url, fields...}, "max_bytes"}, or Invalid. The book is recorded as `uploading` first (so it is counted and
    shown), the form is limited to the size announced."""
    tenant_id = tenant["tenant_id"]
    if not isinstance(body, dict) or set(body) - {"shelf", "filename", "size"}:
        raise Invalid('Send {"shelf": "library", "filename": "fees.pdf", "size": bytes}.')
    shelf = clean_shelf(body.get("shelf"))
    filename, size = body.get("filename"), body.get("size")
    ext = filename.rsplit(".", 1)[-1].lower() if isinstance(filename, str) and "." in filename else ""
    if ext not in EXTENSIONS:
        raise Invalid("Only PDF, text, Markdown and HTML files can be added.")
    limit = max_file_mb() * 1024 * 1024
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= limit:
        raise Invalid(f"A book may be at most {max_file_mb()} MB.")
    book_id = uuid.uuid4().hex[:12]
    key = f"{PREFIX}{tenant_id}/{book_id}/{safe_filename(filename)}"

    def record(index):
        known = set(index["shelves"]) | {p["shelf"] for p in live_pending(index["pending"], now).values()}
        if shelf not in known and len(known) >= MAX_SHELVES:
            raise Invalid(f"A business may have at most {MAX_SHELVES} shelves.")
        if count_books(index, now) >= MAX_BOOKS:
            raise Invalid(f"A business may keep at most {MAX_BOOKS} books. Remove one first.")
        index["pending"][book_id] = {"shelf": shelf, "name": str(filename)[:120], "key": key, "size": size, "status": "uploading", "at": now}
    change_index(store, tenant_id, record, now)
    return {"book_id": book_id, "key": key, "upload": files.presigned_post(key, size, UPLOAD_SECONDS)}


def mark(store, tenant_id, book_id, status, now, **extra):
    """Set the state of a pending book (or remove it with status None). Returns the pending record as it was, or None."""
    seen = {}

    def change(index):
        found = index["pending"].get(book_id)
        seen["was"] = dict(found) if found else None
        if not found:
            return
        if status is None:
            index["pending"].pop(book_id)
        else:
            index["pending"][book_id] = {**found, "status": status, "at": now, **extra}
    change_index(store, tenant_id, change, now)
    return seen["was"]


# ---------- the books function ----------

class Deps:
    """What the books function needs from outside: the table, the shelves, the raw files, the embedder and the clock."""

    def __init__(self, store, shelves, raw, embed=None, now=time.time):
        self.store, self.shelves, self.raw, self.now = store, shelves, raw, now
        self.embed = embed


def default_deps():
    import boto3
    import chatwoot_media as media
    import chatwoot_store as cs
    import knowledge_store as ks
    table = boto3.resource("dynamodb").Table(os.environ["CHATWOOT_TABLE"])
    return Deps(cs.DynamoStore(table), ks.Store(ks.S3Bucket()), media.S3Files())


def embedding_choice():
    return platform_settings.value("knowledge.embeddingModel"), platform_settings.value("knowledge.embeddingDims")


def log(what, **details):
    print(json.dumps({"chatwoot_books": what, **details}))


def ingest(event, deps, now):
    """Read one uploaded book into its shelf. The pending record (made by the upload form) is the instruction: nothing in the event but ids is trusted. Returns {"status": "done" | "failed"}."""
    import knowledge_ingest as ki
    import knowledge_store as ks
    tenant_id, book_id = event.get("tenant_id"), event.get("book_id")
    pending = (deps.store.get(index_key(tenant_id)) or {}).get("pending", {}).get(book_id)
    if not pending or pending.get("status") != "queued":
        return {"status": "skipped"}
    shelf, name, key = pending["shelf"], pending["name"], pending["key"]
    mark(deps.store, tenant_id, book_id, "reading", now)
    started = time.monotonic()

    def fail(message, code):
        mark(deps.store, tenant_id, book_id, "failed", now, error=message[:300])
        log("book_failed", tenant=tenant_id, book=book_id, code=code)
        return {"status": "failed", "error": message}
    try:
        data = deps.raw.get(key, max_file_mb() * 1024 * 1024)
        if data is None:
            return fail("The file did not arrive or is too large. Upload it again.", "no_file")
        import chatwoot_api as api
        tenant = deps.store.get(f"tenant#{tenant_id}") or {}
        usage_key = api.usage_key(tenant_id, now)
        if deps.store.usage(usage_key) >= api.monthly_cap_micro(tenant):
            return fail("This business has used its spend limit for the month.", "cap_reached")
        base = ks.base_of("tenant", tenant_id, shelf)
        meta = deps.shelves.meta(base)
        if meta is None:
            model, dims = embedding_choice()
            meta = deps.shelves.create_collection(base, shelf, model=model, dims=dims)
        doc_id = "d" + hashlib.sha256(data).hexdigest()[:20]
        if doc_id in {d["id"] for d in meta.get("docs", [])}:
            result_meta, cost = meta, 0  # the same content is already on the shelf: nothing to read or pay
        else:
            embed = deps.embed or ki.embed_prepared
            prepared = ki.prepare(data, name)
            done = embed(prepared, meta["embedding"])
            result_meta = deps.shelves.put_document(base, {**done["doc"], "id": doc_id}, done["passages"], done["vectors"])
            cost = int(done.get("cost_micro") or 0)
            older = next((d["id"] for d in meta.get("docs", []) if d.get("source") and d.get("source") == done["doc"].get("source") and d["id"] != doc_id), None)
            if older:  # a changed file with the same name replaces the older one
                result_meta = deps.shelves.delete_document(base, older)
        if cost:
            deps.store.usage_add(usage_key, cost, now)
        summary = shelf_summary(result_meta)
        change_index(deps.store, tenant_id, lambda index: (index["shelves"].__setitem__(shelf, summary), index["pending"].pop(book_id, None)), now)
        log("book_added", tenant=tenant_id, shelf=shelf, book=book_id, passages=len(result_meta.get("docs") or []), cost_micro=cost, seconds=round(time.monotonic() - started, 1))
        return {"status": "done"}
    except ki.IngestError as error:
        return fail(str(error), error.code)
    except ks.StoreError as error:
        return fail(str(error), error.code)
    except Exception as error:  # an embedding service error or anything unforeseen: said plainly, the details stay in the log
        name_of = type(error).__name__
        log("book_error", tenant=tenant_id, book=book_id, error=name_of)
        return fail("The book could not be read right now. Try again in a moment." if name_of == "EmbeddingError" else "The book could not be read.", name_of)
    finally:
        try:
            deps.raw.delete(key)
        except Exception as error:
            log("raw_not_deleted", error=type(error).__name__)


def refresh_shelf(deps, tenant_id, shelf, now):
    """Put one shelf's present state in the index (or take it out when the shelf is gone)."""
    import knowledge_store as ks
    meta = deps.shelves.meta(ks.base_of("tenant", tenant_id, shelf))
    change_index(deps.store, tenant_id, lambda index: index["shelves"].__setitem__(shelf, shelf_summary(meta)) if meta else index["shelves"].pop(shelf, None), now)


def delete_book(event, deps, now):
    import knowledge_store as ks
    tenant_id, shelf, doc_id = event.get("tenant_id"), event.get("shelf"), event.get("doc_id")
    base = ks.base_of("tenant", tenant_id, clean_shelf(shelf))
    if not deps.shelves.meta(base):
        return {"status": "gone"}
    deps.shelves.delete_document(base, doc_id)
    refresh_shelf(deps, tenant_id, shelf, now)
    log("book_deleted", tenant=tenant_id, shelf=shelf)
    return {"status": "done"}


def delete_shelf(event, deps, now):
    import knowledge_store as ks
    tenant_id, shelf = event.get("tenant_id"), clean_shelf(event.get("shelf"))
    deps.shelves.delete_collection(ks.base_of("tenant", tenant_id, shelf))
    refresh_shelf(deps, tenant_id, shelf, now)
    log("shelf_deleted", tenant=tenant_id, shelf=shelf)
    return {"status": "done"}


def reindex(event, deps, now):
    """Build the index from the shelves that are really there (the ones the teacher's command made, too). Pending books are kept."""
    import knowledge_store as ks
    tenant_id = event.get("tenant_id")
    found = {}
    for shelf in deps.shelves.collections("tenant", tenant_id):
        meta = deps.shelves.meta(ks.base_of("tenant", tenant_id, shelf))
        if meta:
            found[shelf] = shelf_summary(meta)
    change_index(deps.store, tenant_id, lambda index: (index["shelves"].clear(), index["shelves"].update(found)), now)
    return {"status": "done", "shelves": len(found)}


def rebuild_search(event, deps, now):
    """Build the search index (keyword and vector files) of a business's shelves again from the passages and vectors already stored: no book is read again and nothing is paid for
    to the embedding model. A shelf's keyword index is made when its books are added, so it has to be done once after the tokenizer changes (the Hindi fix). `shelf` limits it to one."""
    import knowledge_store as ks
    tenant_id = event.get("tenant_id")
    wanted = clean_shelf(event["shelf"]) if event.get("shelf") else None
    done, failed = [], []
    for shelf in deps.shelves.collections("tenant", tenant_id):
        if wanted and shelf != wanted:
            continue
        try:
            deps.shelves.rebuild(ks.base_of("tenant", tenant_id, shelf))
            done.append(shelf)
        except ks.StoreError as error:
            failed.append(shelf)
            log("rebuild_failed", tenant=tenant_id, shelf=shelf, code=error.code)
    log("search_rebuilt", tenant=tenant_id, shelves=len(done), failed=len(failed))
    return {"status": "done" if not failed else "partial", "shelves": done, "failed": failed}


def passages(event, deps, now):
    """The first passages of one book (cut), to show the owner what the agent actually reads."""
    import knowledge_store as ks
    tenant_id, shelf, doc_id = event.get("tenant_id"), clean_shelf(event.get("shelf")), event.get("doc_id")
    if not isinstance(doc_id, str) or not ks.NAME_RE.match(doc_id):
        return {"error": "bad_event"}
    data = deps.shelves.bucket.get(f"{ks.base_of('tenant', tenant_id, shelf)}/docs/{doc_id}/passages.json.gz")
    if data is None:
        return {"error": "not_found"}
    items = ks._ungz(data)
    return {"total": len(items), "passages": [{"page": p.get("page"), "section": str(p.get("section") or "")[:80], "text": str(p.get("text") or "")[:700]} for p in items[:PREVIEW]]}


KINDS = {"ingest": ingest, "delete_book": delete_book, "delete_shelf": delete_shelf, "reindex": reindex, "rebuild_search": rebuild_search, "passages": passages}


def handler(event, context=None, now=None, deps=None):
    """The books function's entry: one event of `kind`, from the admin function only. Anything odd is refused by name, never guessed."""
    now = int(now if now is not None else time.time())
    kind = event.get("kind") if isinstance(event, dict) else None
    if kind not in KINDS or not isinstance(event.get("tenant_id"), str):
        log("bad_event")
        return {"error": "bad_event"}
    deps = deps or default_deps()
    try:
        return KINDS[kind](event, deps, now)
    except Invalid as error:
        return {"error": "bad_event", "message": str(error)}
    except Exception as error:
        log("books_failed", kind=kind, error=type(error).__name__)
        return {"error": "failed"}
