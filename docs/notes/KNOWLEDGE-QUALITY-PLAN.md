# Knowledge quality plan: better books in, better answers out (2026-11-24)

Status: **plan only, nothing built.** Needs the owner's approval per phase. Follows `OPEN-SOURCE-TOOLS-2026-11.md` section 4 and `ARCHITECTURE-REVIEW-2026-11.md` (R3).

## 1. Why this first (and one correction to the order)

The agent answers only from the books, so answer quality is capped by what the books look like *after* we read them. A table flattened into one line, a Hindi PDF read as garbage, a page header repeated 200 times: no prompt or model fixes that. So yes, knowledge before tracing.

One correction: **we cannot call anything an improvement without a small measurement first.** Not tracing (that comes later), only a fixed set of real questions with the known right place in the book. That is Phase 0, one day, and every later phase is judged by it.

## 2. What the code does today (read from the repo)

| Step | Where | How it works | Weak spot |
|---|---|---|---|
| Read PDF | `lambda/knowledge_ingest.py` `extract_pdf` | vendored **pypdf**, `page.extract_text()` per page | No layout understanding: tables become running text, multi-column pages interleave, headers/footers repeat, Devanagari and some fonts can come out wrong |
| Scans | same file, `needs_ocr` + optional `ocr(data, pages)` hook | pages under `MIN_PAGE_CHARS` are model-OCR'd if the hook is on, else skipped and reported | Hook is a model call per page (cost, quality varies); skipped pages are only a message |
| Text/MD/HTML | `extract_markdown`, `extract_html` | headings marked with `HEADING_MARK` | HTML/MD have no pages, so citations are pseudo-pages |
| Clean + chunk | `lambda/knowledge_core.py` `clean_pages`, `chunk_pages` | sentence-aware, ~300 words, 40 overlap, section = last heading, page-break rules | Heading detection for PDFs is heuristic (`is_heading`); a table is just sentences |
| Embed text | `embed_text` | section title + passage | No document title, no path (Chapter > Section) |
| Search | `knowledge_store.search` | BM25 + vectors (hybrid), confidence thresholds | Fine as a base; judged by Phase 0 |

**The seam to use:** `prepare()` calls `extract_pdf()` and expects `(pages [(number, text)], needs_ocr, title)`, with headings as lines starting with `HEADING_MARK`. Anything that returns that shape plugs in without touching chunking, embedding, storage or search.

## 3. What Docling would change (and what it would not)

**Docling** (open source, from IBM Research, now under the `docling-project` GitHub organisation; licence reported as MIT: **verify**) converts PDF, DOCX, PPTX, XLSX, HTML, images into a structured document: reading order, headings, **real tables (rows and columns)**, lists, figures with captions, per-item page numbers, and OCR for scanned pages. It exports Markdown or JSON, and has its own layout-aware chunker.

| Problem today | What Docling gives | Confidence |
|---|---|---|
| Tables flattened (price lists, fee tables, timetables) | Table structure recovered; export as Markdown table | High: this is its main strength |
| Two-column / sidebar pages interleaved | Reading order from a layout model | High |
| Headings guessed by heuristics | Heading levels from layout, so `section` becomes a real path | Medium-high |
| Repeated page headers/footers | Detected and left out | Medium |
| Scanned pages skipped or model-OCR'd | Built-in OCR engines (several; Hindi support depends on the engine: **must test**) | Medium |
| Devanagari text from born-digital PDFs | Often better than pypdf, not guaranteed | **Unknown: test with real client files** |

**Costs and limits (the honest part):**
- It is heavy: Python 3.10+, PyTorch and downloaded models (hundreds of MB to GB). **It cannot be added to the Lambda zip** (250 MB unzipped limit; our `lambda/` is deliberately small, pure Python, vendored and hash-checked).
- It is slower than pypdf: seconds per page on CPU, more on scans (**measure**). Fine for a one-time book read, bad at answer time. So it must stay in ingestion, never in the answer path.
- It does **not** fix wrong books, stale books or missing books, and does not replace retrieval tuning.

## 4. Where Docling can run (decision for Phase 2)

| Option | What | Pros | Cons |
|---|---|---|---|
| **A. Offline converter** | A script (`tools/docling_convert.py`) run on a laptop or CI: PDF in, structured JSON/Markdown out, uploaded as the book | No infra, no AWS change, no new service; best for the first 2 to 3 clients; works today | Manual step; the platform never sees the original layout |
| **B. Docling service** | `docling-serve` (the project's REST wrapper) on Railway, called by the ingestion worker over HTTPS; fall back to pypdf if it is down | Automatic for every upload; one place to upgrade; same hosting as Chatwoot | A service to run and secure (token, size limits), CPU cost on Railway, queueing for big books |
| **C. Lambda container image** | Worker as a container image (up to 10 GB) with Docling inside | All on AWS, no extra host | Big image, slow cold starts, ECR cost, our SAM stack and tests assume zip packaging, "student-hosting" stack coupling grows |

**Recommendation:** A first (prove value, zero infra), then B only if uploads become frequent. Skip C. Client documents go only to our own service, never to a third-party parsing API.

## 5. The phases

Each phase has a "done" criterion. Do them in order; stop at any gate where the numbers say it is not worth it.

### Phase 0. Baseline and gold set (1 day, no code in the platform)
1. Pick 3 real documents: the `shop` books, plus two kinds likely next (one with price/fee tables, one Hindi or scanned).
2. Write **30 to 40 gold questions** (mix: plain fact, table lookup, Hindi/Hinglish, "not in the books", follow-up style). For each: the expected page/section and the key fact. The `shop` tests table already holds many; reuse it.
3. Small offline script `tools/knowledge_eval.py`: ingest with the **current** pipeline, run search, report **hit@1, hit@3, hit@5** (the right passage is among the top k) and "not sure" correctness (questions that should miss do miss). The existing `class_quality.evaluate_retrieval` (student kit) is the model for the metric; do not edit kit files, write a platform-side tool.
4. **Extraction audit:** dump the extracted text of each book to a file and list defects by hand (tables, columns, Devanagari, repeated headers, lost headings).
- **Done when:** a table of baseline numbers and a defect list exist in `docs/notes/`.

### Phase 1. Docling spike on the laptop (1 day)
1. Install Docling in a throw-away virtualenv (not in the repo's runtime).
2. Convert the same 3 documents; save Markdown and JSON.
3. Compare to the Phase 0 audit: tables, order, headings, Devanagari, scans. Note time per page and memory.
4. Quick retrieval test with **no platform change**: feed the Docling Markdown through the existing `.md` path and rerun the gold set (page citations will be pseudo-pages: ignore that here).
- **Gate:** continue only if hit@5 improves by a clear margin (for example 10 points) **or** a named defect class is fixed (tables, Hindi). Otherwise stop here and record why.
- **Done when:** a comparison note with numbers and a go/no-go.

### Phase 2. Plug it in without losing citations (2 to 3 days)
1. Write `extract_pdf_structured()` returning the same `(pages, needs_ocr, title)` shape: per page text in reading order, headings as `HEADING_MARK` lines (with the section path), tables rendered as Markdown tables, repeated headers/footers dropped, **real page numbers kept** (this is why we do not just upload Markdown).
2. Selection by a setting in `config/settings.json` (`knowledge.extractor`: `pypdf` default, `docling`), then `python tools/gen_settings.py`. The document record stores which extractor read it (`doc["extractor"]`) so a re-index is explainable.
3. Option A first: `tools/docling_convert.py` makes a small JSON the platform reads (`pages`, `headings`, `tables`). Option B later swaps the source of that same JSON for an HTTP call to `docling-serve`, with a timeout and fallback to pypdf (and a note on the document "read with the basic reader").
4. Tests: a fixture PDF with a table and two columns, a snapshot of the expected pages, a test that the pypdf path is unchanged, a test that a failed Docling call falls back.
- **Done when:** the gold set run with `extractor=docling` reproduces the Phase 1 gain, the full test suite is green, and `pypdf` stays the default.

### Phase 3. Tables as first-class passages (1 to 2 days)
1. In `chunk_pages`: never split inside a table; if a table is too big, split by rows and **repeat the header row** in each part; the passage text keeps the table caption/section.
2. Add the table's section path to `embed_text` so "fees for class 6" finds the right table.
3. Gold questions: add 10 table lookups ("price of X", "timing on Sunday").
- **Done when:** table-lookup questions hit@3 reaches the agreed target and no regression elsewhere.

### Phase 4. Scans and Hindi (2 days, only if Phase 1 showed a real need)
1. Try Docling's OCR engines on real scanned and Hindi pages; pick one by accuracy on the gold set and by speed/cost.
2. Replace the model-based `ocr` hook for scans with it (cheaper, offline), or keep the hook as a second try for pages Docling could not read.
3. Show skipped or low-confidence pages to the owner (Phase 6).
- **Done when:** a scanned Hindi sample is searchable and the gold questions on it pass.

### Phase 5. Better passage context (1 to 2 days, optional, measure first)
1. `embed_text` gets the **document title and the full section path** (Chapter > Section), not just the last heading. Free, no model call.
2. Optional "contextual retrieval": at ingest, one cheap model call per passage writes a one-line context ("this passage is from the 2026 fee list, about class 6 tuition"); stored with the passage and embedded with it. Cost: cents per book, once. Keep only if the gold set improves.
- **Done when:** hit@5 up, or the idea dropped with the numbers recorded.

### Phase 6. Show book quality to the owner (1 to 2 days)
1. Per book in Studio (Build, books or Checks): pages read, tables found, scans skipped, extractor used, "read quality" note. This makes bad books visible before customers find them.
2. Re-index flow: choose a book, **Read again with the better reader**, new version of the collection, old one kept until the check passes.
- **Done when:** the owner can see and fix a badly read book without asking us.

### Phase 7. Retrieval tuning, only what the numbers ask for
Reranker and query rewrite (R3 in the review). Run only if, after Phases 2 to 5, the gold set still misses mostly because the right passage is in the top 20 but not the top 5 (reranker) or because the question is short and context-free (rewrite). Otherwise skip.

### Phase 8. Roll out
Re-read the live agent's books into a **new collection**, run the gold set and the agent's own test table against both, switch the agent's version to the new collection, keep the old one for rollback. Document in `docs/`: ARCHITECTURE, DECISIONS, RUNBOOK (how to convert, how to re-index, how to roll back).

## 6. Order and effort

| Phase | Effort | Needs infra | Stop condition |
|---|---|---|---|
| 0 Baseline | 1 day | no | none, always do |
| 1 Spike | 1 day | no (laptop) | no clear gain |
| 2 Plug in | 2 to 3 days | no (option A) | gain not reproduced |
| 3 Tables | 1 to 2 days | no | none if tables exist in client books |
| 4 Scans/Hindi | 2 days | maybe (OCR models) | no scanned clients yet |
| 5 Context | 1 to 2 days | no | no gain |
| 6 Owner view | 1 to 2 days | no | none |
| 7 Retrieval | as needed | maybe | numbers say no |
| 8 Roll out | 1 day | no | none |

Phases 0 to 2 are the core: about 4 to 5 days, no new hosting, and each step can be stopped by a number.

## 7. Risks and rules

- **Privacy:** client books stay on our infrastructure (laptop, our Railway, our AWS). No third-party parsing API.
- **Cost:** Docling itself is free; cost is compute time (laptop free, Railway CPU minutes) and any optional model calls (Phase 5), shown before running, as everywhere else.
- **Safety net:** `pypdf` stays the default and the fallback. A setting, not a rewrite. Rollback = switch the setting and the collection version.
- **Licences:** confirm Docling's licence and each model's licence (layout, table, OCR models can have their own terms) before hosting for paying clients.
- **Version drift:** pin the Docling version; a new version can change extraction, so re-run the gold set before upgrading.
- **No kit changes:** nothing in `student/` or `starter/` is touched, so no kit-version churn.

## 8. Resources (verify the current links and versions)

- Docling: `https://github.com/docling-project/docling` and its documentation site (installation, conversion, export to Markdown/JSON, chunking, OCR options, supported formats).
- `docling-serve`: `https://github.com/docling-project/docling-serve` (REST service, container image, async tasks).
- `docling-core` (the document model and the hybrid chunker), same organisation.
- Retrieval measurement ideas: Ragas docs (context precision and recall), and the "contextual retrieval" technique write-up from Anthropic (Phase 5).
- For Hindi OCR: the OCR engines Docling lists (Tesseract with Hindi data, EasyOCR, RapidOCR, others): check each one's Hindi support in its own docs.

## 9. What I need from the owner

1. **Which real documents** to use for the gold set (the `shop` books, and one with tables, one Hindi or scanned from a real or likely client)?
2. **Approve Phase 0 and 1** (about 2 days, no platform change, no cost beyond a few cents of embeddings).
3. Later, at the Phase 2 gate: option A (offline converter) or B (a Docling service on Railway).
