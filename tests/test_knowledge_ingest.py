"""Ingestion (lambda/knowledge_ingest.py): real PDF files in, searchable passages out. The PDFs are written by tests/pdf_fixture.py (valid files, no library),
read by the vendored pypdf, cleaned, cut into passages and stored; then questions are asked of the stored book.

Also: the vendored pypdf is exactly the version that was recorded (every file hashed), and every limit and failure gives a plain message.
"""
import hashlib
import json
import os
import sys
import time
import zlib

os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AI_SECRET_NAME="secret", BUCKET="b")
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.path.insert(0, os.path.join(here, "..", "lambda"))
import embeddings as em  # noqa: E402
import gemini  # noqa: E402
import knowledge_core as kc  # noqa: E402
import knowledge_ingest as ki  # noqa: E402
import knowledge_store as ks  # noqa: E402
from pdf_fixture import make_pdf, wrap  # noqa: E402

gemini._secret.update(value="google-test-key", at=time.time() + 10 ** 6)
DIMS = 256
EMBEDDING = {"model": "gemini-embedding-001", "dims": DIMS, "task_types": False}
calls = []


def lexical(text):
    row = [0.0] * DIMS
    for term in kc.tokenize(text):
        row[zlib.crc32(term.encode()) % DIMS] += 1.0
    return row


def transport(provider, url, headers, body):
    texts = [r["content"]["parts"][0]["text"] for r in body["requests"]]
    calls.append(len(texts))
    return 200, {"embeddings": [{"values": lexical(t)} for t in texts]}


em.transport = transport


def fails(fn, status, code):
    try:
        fn()
    except ki.IngestError as e:
        assert e.status == status and e.code == code, (e.status, e.code, str(e))
        return str(e)
    raise AssertionError(f"expected IngestError {status} {code}")


# ---------------------------------------------------------------- the vendored PDF library is exactly what was recorded
vendor = os.path.join(here, "..", "lambda", "vendor")
manifest = json.load(open(os.path.join(vendor, "pypdf.manifest.json"), encoding="utf8"))
on_disk = {}
for folder, _, names in os.walk(os.path.join(vendor, "pypdf")):
    for name in names:
        if "__pycache__" in folder or name.endswith(".pyc"):
            continue
        path = os.path.join(folder, name)
        # line endings are normalised first: a Windows checkout turns LF into CRLF, which must not look like a change to the library
        on_disk["pypdf/" + os.path.relpath(path, os.path.join(vendor, "pypdf")).replace(os.sep, "/")] = hashlib.sha256(open(path, "rb").read().replace(b"\r\n", b"\n")).hexdigest()
assert on_disk == manifest["files"], sorted(set(on_disk.items()) ^ set(manifest["files"].items()))[:4]
assert "Redistribution and use in source and binary forms" in open(os.path.join(vendor, "PYPDF_LICENSE.txt"), encoding="utf8").read()
pypdf, _ = ki._pypdf()
assert pypdf.__version__ == manifest["version"] == "6.21.4"
import logging  # noqa: E402
assert logging.getLogger("pypdf").level == logging.ERROR, "pypdf's long fontTools notices (printed for many textbook fonts) are silenced"
print(f"  ok  vendored pypdf {manifest['version']}: {len(on_disk)} files match the recorded hashes, licence present")

# ---------------------------------------------------------------- a sample book as a real PDF
HEADER = "NCERT Science Class 9"
CHAPTERS = [
    ("Chapter 1: Photosynthesis",
     ["Photosynthesis is the process by which green plants make their own food. The leaves absorb sunlight using chlorophyll. Water from the soil and carbon dioxide from the air are combined. Glucose and oxygen are produced.",
      "The stomata are tiny pores on the surface of a leaf. They open and close to let gases pass in and out. Guard cells control the opening of each stomata pore."]),
    ("Chapter 2: Laws of Motion",
     ["Newton's first law of motion says that an object stays at rest or moves with constant velocity unless a force acts on it. This property is called inertia.",
      "Newton's second law states that force equals mass multiplied by acceleration. A heavier object needs a larger force to get the same acceleration."]),
    ("Chapter 3: The Water Cycle",
     ["Water evaporates from oceans and rivers when the sun heats it. The vapour rises and condenses into clouds. Rain and snow bring the water back to the ground. This continuous movement is the water cycle."]),
]
sample_pages, truth, number = [], {}, 1
for title, paragraphs in CHAPTERS:
    for index, paragraph in enumerate(paragraphs):
        noun = ["roots", "stems", "leaves", "flowers", "fruits", "seeds"][number - 1]
        lines = [HEADER, ""] + ([title, ""] if index == 0 else []) + wrap(paragraph, hyphenate="chlorophyll" if "chlorophyll" in paragraph else None) + \
            ["", "Solution: see the diagram.", f"Remember the {noun} example.", f"Check the {noun} chart carefully.", "", str(number)]  # the label is mid-page, not at an edge
        sample_pages.append(lines)
        truth[number] = title
        number += 1
sample_pages.append([])  # a scanned page: nothing to read
BOOK = make_pdf(sample_pages)
assert BOOK.startswith(b"%PDF-") and len(BOOK) > 2000

pages, needs_ocr, meta_title = ki.extract_pdf(BOOK)
assert len(pages) == 6 and [n for n, _ in pages] == [1, 2, 3, 4, 5, 6] and needs_ocr == [6] and meta_title is None
assert "Photosynthesis is the process" in pages[0][1] and HEADER in pages[0][1], "pypdf reads the text; cleaning comes later"
titled = make_pdf([["Some text on a page that is long enough to count as a page of real text."]], title="Science for Class 9")
assert ki.extract_pdf(titled)[2] == "Science for Class 9"
print("  ok  a real PDF is read page by page with its own page numbers; the scanned page is flagged; the PDF title is read")

# ---------------------------------------------------------------- the whole pipeline, then questions asked of the stored book
cache = em.MemoryCache()
result = ki.process(BOOK, "ncert-science_9.pdf", EMBEDDING, cache=cache)
doc, passages = result["doc"], result["passages"]
assert doc["title"] == "ncert-science 9" and doc["type"] == "pdf" and doc["pages"] == 6 and doc["paged"] is True and doc["needs_ocr"] == [6] and result["skipped_pages"] == [6]
assert all(HEADER not in p["text"] for p in passages), "the running header is gone"
assert all(not p["text"].strip().isdigit() for p in passages), "page numbers are gone"
assert any("chlorophyll" in p["text"] for p in passages) and not any("chlor-" in p["text"] for p in passages), "the hyphenated word is whole"
assert any("chlor-" in text for _, text in pages), "(the raw PDF text really did have the word split across lines)"
assert any("Solution: see the diagram." in p["text"] for p in passages), "a label in the middle of a page is kept"
assert {p["section"] for p in passages} >= {t for t, _ in CHAPTERS}, "chapters became sections"
assert len(result["vectors"]) == len(passages) * DIMS * 2 and result["tokens"] > 0 and result["cost_micro"] > 0 and result["cached"] == 0
store = ks.Store(ks.MemoryBucket())
base = ks.base_of("student", "alice", "science")
store.create_collection(base, "Science", dims=DIMS)
store.put_document(base, {**doc, "id": "ncert-science-9"}, passages, result["vectors"])
questions = [("How do plants make their own food?", 1), ("What do stomata do?", 2), ("What is inertia?", 3), ("State Newton's second law", 4), ("Explain the water cycle", 5)]
for question, page in questions:
    found = store.search(base, question, k=3)
    assert found["confident"] and found["hits"][0]["page"] <= page <= found["hits"][0]["page_end"], (question, found["hits"][0]["page"], found["hits"][0]["page_end"])
    assert found["hits"][0]["title"] == "ncert-science 9"
assert store.search(base, "What is on the school canteen lunch menu?")["confident"] is False
report = store.evaluate(base, [{"question": q, "expect": {"pages": [p]}} for q, p in questions] + [{"question": "Who won the football world cup?", "expect": None}], k=3)
assert report["recall"] == 1.0 and report["refusal"] == 1.0, report
print("  ok  PDF to answer: cleaned (header, page numbers, hyphen), sectioned by chapter, stored; 5 questions find the right page, an off-topic one is not confident")

# the same book again costs nothing (the embedding cache), and a scanned page can be read by an OCR function
calls.clear()
again = ki.process(BOOK, "ncert-science_9.pdf", EMBEDDING, cache=cache)
assert calls == [] and again["tokens"] == 0 and again["cost_micro"] == 0 and again["cached"] == len(passages)
reads = []


def ocr(data, wanted):
    reads.append(list(wanted))
    return {6: "The scanned page explains that the mitochondria are the powerhouse of the cell.", 99: "ignored: not a flagged page"}


with_ocr = ki.process(BOOK, "ncert-science_9.pdf", EMBEDDING, cache=cache, ocr=ocr)
assert reads == [[6]] and with_ocr["ocr_pages"] == [6] and with_ocr["skipped_pages"] == [] and with_ocr["doc"]["needs_ocr"] == []
assert any("mitochondria" in p["text"] and p["page"] <= 6 <= p["page_end"] for p in with_ocr["passages"]), "the scanned page's text is searchable and cited as page 6 (a short page joins the passage before it)"
nothing = ki.process(BOOK, "ncert-science_9.pdf", EMBEDDING, cache=cache, ocr=lambda d, w: {})
assert nothing["skipped_pages"] == [6] and nothing["ocr_pages"] == []
print("  ok  re-uploading a book costs nothing; a scanned page is read through the OCR hook; without text it is listed as skipped")

# ---------------------------------------------------------------- text, markdown and HTML
notes = ("# Photosynthesis\n\nPlants make food from sunlight. The green pigment chlorophyll absorbs the light.\n\n## Stomata\n\nStomata let gases in and out of the leaf.\n"
         "Guard cells open and close every pore.\n").encode()
md = ki.process(notes, "my notes.md", EMBEDDING)
assert md["doc"]["type"] == "md" and md["doc"]["paged"] is False and md["doc"]["title"] == "my notes"
assert {p["section"] for p in md["passages"]} == {"Photosynthesis", "Stomata"} and not any("#" in p["text"] for p in md["passages"])
page = ("<html><head><title>x</title><style>p{color:red}</style></head><body><h1>The Water Cycle</h1><p>Water evaporates &amp; rises. Clouds form.</p>"
        "<script>evil()</script><h2>Rain</h2><p>Rain falls.<br>Snow too.</p></body></html>").encode()
html_doc = ki.process(page, "water.html", EMBEDDING)
text = " ".join(p["text"] for p in html_doc["passages"])
assert {p["section"] for p in html_doc["passages"]} == {"The Water Cycle", "Rain"}
assert "Water evaporates & rises." in text and "evil" not in text and "color:red" not in text and "<" not in text
plain = ki.process(("Alpha beta gamma. " * 400).encode("utf-16"), "long.txt", EMBEDDING)
assert plain["doc"]["type"] == "txt" and plain["doc"]["pages"] >= 2 and max(p["page"] for p in plain["passages"]) >= 2, "text without pages is cut into pseudo-pages"
assert ki._decode("café text".encode("latin-1")).startswith("caf") and ki._decode("﻿BOM text".encode("utf-8")) == "BOM text"
print("  ok  markdown and HTML headings become sections; scripts, styles and tags are dropped; plain text in other encodings is read")

# ---------------------------------------------------------------- limits and failures say what is wrong
assert "empty" in fails(lambda: ki.process(b"", "a.pdf", EMBEDDING), 400, "empty")
fails(lambda: ki.process(b"x", "movie.mp4", EMBEDDING), 415, "unsupported")
fails(lambda: ki.process(b"just text pretending", "fake.pdf", EMBEDDING), 422, "bad_pdf")
fails(lambda: ki.process(b"%PDF-1.4\nthis is not a real pdf at all", "broken.pdf", EMBEDDING), 422, "bad_pdf")
fails(lambda: ki.process(make_pdf([[], []]), "scan.pdf", EMBEDDING), 422, "no_text")
fails(lambda: ki.process(b"   \n  ", "blank.txt", EMBEDDING), 422, "no_text")
ki.MAX_FILE_BYTES = 1000
try:
    assert "MB" in fails(lambda: ki.process(BOOK, "big.pdf", EMBEDDING), 413, "too_large")
finally:
    ki.MAX_FILE_BYTES = 50 * 1024 * 1024
ki.MAX_PAGES = 3
try:
    fails(lambda: ki.process(BOOK, "long.pdf", EMBEDDING), 413, "too_many_pages")
finally:
    ki.MAX_PAGES = 1500
ki.MAX_PASSAGES = 2
try:
    fails(lambda: ki.process(BOOK, "dense.pdf", EMBEDDING), 413, "too_many_passages")
finally:
    ki.MAX_PASSAGES = 6000
em.transport = lambda provider, url, headers, body: (401, {"error": {"message": "API key google-test-key is invalid"}})
try:
    ki.process(BOOK, "x.pdf", EMBEDDING)
    raise AssertionError("expected IngestError")
except ki.IngestError as e:
    assert e.code == "embedding_failed" and "google-test-key" not in str(e), "an embedding failure is reported without the key"
finally:
    em.transport = transport
print("  ok  failures: empty, wrong type, not a PDF, broken PDF, scan only, too big, too many pages or passages, embedding errors: each says what is wrong")

# ---------------------------------------------------------------- a real-sized book
big_pages = []
for n in range(1, 151):
    body = []
    for k in range(4):
        body += wrap(f"Topic {n}.{k} explains that the substance number {n * 10 + k} behaves in a particular way when heated, cooled or mixed with water. "
                     f"Students observe the change, record the temperature and write down what they notice about substance {n * 10 + k}. " * 2)
        body.append("")
    big_pages.append([HEADER, ""] + ([f"Chapter {n // 10 + 1}: Unit {n // 10 + 1}", ""] if n % 10 == 1 else []) + body + [str(n)])
big = make_pdf(big_pages)
started = time.time()
calls.clear()
made = ki.process(big, "textbook.pdf", EMBEDDING)
elapsed = time.time() - started
assert made["doc"]["pages"] == 150 and len(made["passages"]) > 100 and elapsed < 60, (len(made["passages"]), elapsed)
print(f"  ok  a 150-page book ({len(big) // 1024} KB): {len(made['passages'])} passages, {sum(calls)} texts embedded in {len(calls)} calls, whole pipeline {elapsed:.1f} s")

print("test_knowledge_ingest.py: all passed")
