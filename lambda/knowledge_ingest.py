"""Turning an uploaded file into searchable passages: extract the text (PDF, plain text, markdown, HTML), clean it, cut it into passages, embed them.

  bytes + filename --process()--> {"doc": {...}, "passages": [...], "vectors": bytes (float16), "tokens", "cost_micro", "ocr_pages", "skipped_pages"}

This is platform code (it runs in the ingestion worker, never in a student's function). PDF reading uses pypdf, vendored in lambda/vendor/ (pure Python,
BSD licence, every file hashed in pypdf.manifest.json and checked by a test). Pages with almost no text (a scan) are reported; an optional `ocr`
function can read them (a model-based fallback with its own cap), otherwise they are skipped and listed so the teacher knows.

Everything is bounded: file size, pages and passages, so one upload can never run away with time or money.
"""
import html
import io
import os
import re
import sys
from html.parser import HTMLParser

import embeddings as em
import knowledge_core as kc

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 1500
MAX_PASSAGES = 6000
MIN_PAGE_CHARS = 40          # a PDF page with fewer characters than this is treated as a scan
PSEUDO_PAGE_CHARS = 3000     # text, markdown and HTML have no pages: they are cut into pseudo-pages for citation (doc["paged"] is False)
TYPES = ("pdf", "txt", "md", "html")


class IngestError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _pypdf():
    vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import pypdf
    from pypdf import errors
    return pypdf, errors


# ------------------------------------------------------------------ type and text extraction

def detect_type(filename, data):
    name = str(filename or "").lower()
    if data[:5] == b"%PDF-":
        return "pdf"
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == "pdf":
        raise IngestError(422, "bad_pdf", "That file is named like a PDF but is not one.")
    if ext in ("txt", "text"):
        return "txt"
    if ext in ("md", "markdown"):
        return "md"
    if ext in ("htm", "html"):
        return "html"
    raise IngestError(415, "unsupported", "Only PDF, text, Markdown and HTML files can be added.")


def _decode(data):
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = data.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeError:
            continue
    return data.decode("latin-1", "replace")


def _pseudo_pages(text):
    """Cut text without pages into pseudo-pages at paragraph boundaries (about PSEUDO_PAGE_CHARS characters each)."""
    pages, current, size = [], [], 0
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        paragraph = paragraph.strip()
        while len(paragraph) > PSEUDO_PAGE_CHARS:  # one huge paragraph (a text file with no blank lines): cut it at a sentence end or a space
            cut = max(paragraph.rfind(". ", 0, PSEUDO_PAGE_CHARS), paragraph.rfind(" ", 0, PSEUDO_PAGE_CHARS))
            cut = cut + 1 if cut > PSEUDO_PAGE_CHARS // 2 else PSEUDO_PAGE_CHARS
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)
    for paragraph in pieces:
        if current and size + len(paragraph) > PSEUDO_PAGE_CHARS:
            pages.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph) + 2
    if current:
        pages.append("\n\n".join(current))
    return [(number + 1, text) for number, text in enumerate(pages)]


def extract_markdown(text):
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        out.append(f"{kc.HEADING_MARK}{match.group(1)}" if match and match.group(1) else line)
    return "\n".join(out)


class _Html(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "section", "article", "table", "ul", "ol", "blockquote", "pre"}
    SKIP = {"script", "style", "head", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.heading = [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + kc.HEADING_MARK)
            self.heading = True
        elif tag in self.BLOCKS:
            self.out.append("\n\n" if tag in ("p", "div", "section", "article", "table", "ul", "ol", "blockquote") else "\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n")
            self.heading = False

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data) if self.heading else data)


def extract_html(text):
    parser = _Html()
    parser.feed(text)
    parser.close()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in html.unescape("".join(parser.out)).split("\n")]
    return "\n".join(lines)


def extract_pdf(data):
    """Returns (pages [(number, text)], needs_ocr [page numbers], title or None). Page numbers are the PDF's own (the first page is 1)."""
    pypdf, errors = _pypdf()
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:  # an AES-protected file needs a library we do not ship
                ok = 0
            if not ok:
                raise IngestError(422, "encrypted", "That PDF is password protected. Remove the protection and upload it again.")
        count = len(reader.pages)
        if count > MAX_PAGES:
            raise IngestError(413, "too_many_pages", f"That PDF has {count} pages; the limit is {MAX_PAGES}. Split it into parts.")
        pages, needs_ocr = [], []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # one broken page must not lose the whole book
                text = ""
            if len(text.strip()) < MIN_PAGE_CHARS:
                needs_ocr.append(number)
            pages.append((number, text))
        title = None
        try:
            title = (reader.metadata.title or "").strip() if reader.metadata else None
        except Exception:
            title = None
    except IngestError:
        raise
    except (errors.PdfReadError, errors.PdfStreamError, errors.DependencyError, ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError) as e:
        raise IngestError(422, "bad_pdf", f"That PDF could not be read ({type(e).__name__}). Try saving it again from the program that made it.")
    return pages, needs_ocr, title if title and len(title) <= 120 else None


def title_from_filename(filename):
    stem = os.path.splitext(os.path.basename(str(filename or "document")))[0]
    return re.sub(r"[\s_]+", " ", stem).strip()[:100] or "Document"


# ------------------------------------------------------------------ the whole pipeline

def process(data, filename, embedding, cache=None, ocr=None, sleep=None):
    """Extract, clean, chunk and embed one file. `embedding` is the collection's {"model", "dims", "task_types"}; `cache` an embedding cache.
    `ocr(data, page_numbers) -> {page_number: text}` reads scanned pages (optional). Raises IngestError (a message fit to show the teacher)."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise IngestError(400, "empty", "The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise IngestError(413, "too_large", f"That file is {len(data) // (1024 * 1024)} MB; the limit is {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    kind = detect_type(filename, data)
    needs_ocr, ocr_done, title = [], [], None
    if kind == "pdf":
        pages, needs_ocr, title = extract_pdf(data)
        if needs_ocr and ocr is not None:
            reads = ocr(data, needs_ocr) or {}
            ocr_done = [p for p in needs_ocr if str(reads.get(p, "")).strip()]  # only pages that were asked for: anything else the function returns is ignored
            pages = [(n, reads[n] if n in ocr_done else t) for n, t in pages]
            needs_ocr = [p for p in needs_ocr if p not in ocr_done]
        paged = True
    else:
        text = _decode(bytes(data))
        text = extract_markdown(text) if kind == "md" else extract_html(text) if kind == "html" else text
        pages, paged = _pseudo_pages(text), False
    if not any(t.strip() for _, t in pages):
        raise IngestError(422, "no_text", "No text could be read from that file. If it is a scan of printed pages, text recognition has to be switched on for it first.")
    passages = kc.chunk_pages(kc.clean_pages(pages))
    if not passages:
        raise IngestError(422, "no_text", "No text could be read from that file.")
    if len(passages) > MAX_PASSAGES:
        raise IngestError(413, "too_many_passages", f"That file makes {len(passages)} passages; the limit is {MAX_PASSAGES}. Split it into parts.")
    try:
        done = em.embed([kc.embed_text(p) for p in passages], model=embedding["model"], dims=embedding["dims"], task="document",
                        task_types=embedding["task_types"], cache=cache, **({"sleep": sleep} if sleep else {}))
    except em.EmbeddingError as e:
        raise IngestError(e.status if e.status in (400, 429, 503) else 502, "embedding_failed", str(e))
    doc = {"title": title or title_from_filename(filename), "type": kind, "pages": len(pages), "paged": paged, "source": os.path.basename(str(filename))[:120],
           "tokens": sum(kc.estimate_tokens(p["text"]) for p in passages), "needs_ocr": needs_ocr}
    return {"doc": doc, "passages": passages, "vectors": kc.pack_vectors(done["vectors"]), "tokens": done["tokens"], "cost_micro": done["cost_micro"],
            "cached": done["cached"], "ocr_pages": ocr_done, "skipped_pages": needs_ocr}
