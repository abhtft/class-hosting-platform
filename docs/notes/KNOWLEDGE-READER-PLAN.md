# Knowledge reader plan: let a model read the document in its own format (2026-11-24)

Status: **plan only, nothing built, needs approval per phase.** Supersedes the Docling-first order of `KNOWLEDGE-QUALITY-PLAN.md` (that note stays as the fallback path: Docling is Phase 8 here).

> **Order changed (2026-11-24):** for small documents the first step is now `KNOWLEDGE-DOCUMENT-MODE-PLAN.md` (send the whole PDF). The reader described here becomes its Track 2.

## 1. The idea and what it really means

Today a book becomes text with a PDF library (`pypdf`), which sees characters, not the page. A modern multimodal model **sees the page**: tables, columns, Hindi script, stamps, photographed price lists. So: **send each document to a model in the form the model reads best (the PDF itself, or the image), let it write clean structured text, and keep everything after that (chunk, embed, search, answer) as it is.**

Two different things this could mean; only the first is the plan:
1. **At reading time (ingest), once per book.** The model turns pages into structured Markdown with the real page numbers. Cost is paid once per book change. **This is the plan.**
2. **At answer time, every question.** Sending the whole PDF with each customer message is slow and expensive for any real book. Not for books. Two narrow uses are kept as later options (Phase 7): a tiny document (a few pages) read whole, and "show the model the original page image" for a table question.

## 2. What the repo already gives us (read from the code)

- `knowledge_ingest.prepare()` calls `extract_pdf()` and wants `(pages [(n, text)], needs_ocr, title)`, headings as `HEADING_MARK` lines. A new reader returning this shape needs **no change** to chunking, embedding, storage or search.
- `process_document(..., ocr=None)` and `prepare(..., ocr=...)` already have a hook for a model that reads pages, but `knowledge_worker.handler` never passes it. The seam exists and is unconnected.
- The worker is already a **background job**: its own Lambda, 900 s, 2 GB, started asynchronously, with a status the owner sees. A model reader fits there; no new service, no container.
- `gemini.py` talks to Google through the OpenAI-compatible `/chat/completions` URL with key rotation, OpenRouter as last resort, cost in micro-dollars, one model offered (`gemini-3.5-flash-lite`, 0.30 / 2.50 USD per million tokens, override by `AI_GEMINI_PRICES`). That path is built for short text chats; reading a PDF needs a **native call** (the `generateContent` API with the PDF as inline data), so we add one small function beside `chat`, reusing the same keys, rotation and cost counting.
- Spend is counted per month (`kspend#<month>`) with a class ceiling; the reader's cost must be counted there too.
- Bedrock is blocked on this account, so Claude models are reachable only through OpenRouter (key already in the secret as the last resort). Which models are available to us has to be checked; I do not know the model named "Luna 6" and will not guess about it.

## 3. Design

### 3.1 A ladder, not one reader
For every document the worker tries, in order, and records which step produced each page:

| Step | When | Cost |
|---|---|---|
| 1. **Model reads the original** (PDF in batches of pages, or the image) | default for PDF and images | cents per book |
| 2. **Text layer** (`pypdf`, as today) | the model failed or was refused for a batch, or the page fails the checks below | free |
| 3. **Skip and report** | no text layer, model unavailable | free, and the owner is told which pages |

Text, Markdown and HTML stay as they are (already text; the model adds nothing).

### 3.2 How a book is read
- Split the PDF into batches of about 8 pages with `PdfWriter` (pypdf is already vendored), so page numbers stay exact, one failed batch is retried alone, and memory and time stay bounded.
- One call per batch with a fixed instruction: output Markdown, one block per page marked `<<<PAGE n>>>`; headings as `#`; tables as Markdown tables with the header row; lists as lists; copy every number, name and unit exactly; do not translate, summarise or add anything; mark unreadable text as `[unclear]`; drop repeated page headers and footers.
- Parse defensively (same pattern as the other model features in the repo): only the expected page blocks are accepted; anything else is ignored; an answer with the wrong number of pages is retried once, then falls to step 2 for that batch.
- The result becomes `pages [(n, text with HEADING_MARK lines)]`, exactly the shape `chunk_pages` already takes.

### 3.3 Trust: a model may drop or alter a number
A price list is the worst place for a silent error. Controls:
- **Number check per page** when the PDF has a text layer: every number found in the text layer must appear in the model's text, and the model's text must not add numbers the page lacks. A page that fails uses its text layer (step 2) and is flagged.
- **Scans have no text layer to compare with.** Two options, decided by the spike: read twice with two different models and flag every page where the numbers differ; or flag every page that contains numbers for owner review in Studio. Never trust a single model pass on a price silently.
- **The document is data, not instructions.** A book can contain "ignore your rules". The reader prompt says so, the model has no tools, its output is only text for the shelf, and answers still fence passages as today.
- **Length sanity:** output far shorter or longer than the text layer (when there is one) is a flag.
- **Safety net:** `knowledge.reader` is a setting; `pypdf` remains available per collection. Rollback = flip the setting and re-read.

### 3.4 Cost and limits
Rough, from the price in the repo and Google's stated average of about 258 input tokens per page (image-heavy or scanned pages count more): input is small; **output is the cost** (a page of Markdown is a few hundred to a thousand tokens). My estimate for the current model: **about 0.1 to 0.3 US cents per page, so roughly 20 to 60 cents for a 200-page book**, once per book change. This is my arithmetic, not a measurement: Phase 1 measures it. Controls: a page cap per book (existing `knowledge.maxPages`), a per-book estimate shown before reading, the cost added to `kspend`, and a reader-specific monthly ceiling so a runaway upload cannot spend the credits.

### 3.5 More input types become possible
- **Images** (a photographed price list, a menu, a poster): a new type `image` (jpg, png) read by the model into text. For small Indian businesses this is probably the most common "document" they actually have.
- **DOCX, XLSX, PPTX:** not read natively by the model API; later, a plain-Python reader of the file's XML (no model) or conversion to PDF. Not in the first release.

### 3.6 Settings and records
Everything numeric or choosable goes in `config/settings.json` (then `python tools/gen_settings.py`): `knowledge.reader` (`text`, `model`, `auto`), `knowledge.readerModel`, `knowledge.readerBatchPages`, `knowledge.readerMonthlyCapMicro`, the number-check thresholds. Each document records `reader`, `model`, per-page route (model / text / skipped), and flags, so a re-read is explainable and Studio can show it.

## 4. Phases

Each phase has a done criterion. The spike decides everything after it.

### Phase 0. Baseline and gold set (1 day, no platform code)
1. About 4 real files kept local and uncommitted: the `shop` books, one with price or fee tables, one Hindi or Hinglish, one scanned, plus 2 or 3 photographed price lists or menus.
2. 30 to 40 gold questions with the expected page and key fact (seed from the `shop` tests table): plain facts, table lookups, Hindi, "not in the books", price questions.
3. `tools/knowledge_eval.py` (offline, platform side): ingest with the current pipeline, search, report **hit@1, hit@3, hit@5**, "not sure" correctness, and a **number fidelity** score per page (numbers in the extracted text vs the text layer).
4. Extraction audit by hand: list the defects of the current reader per file.
- **Done:** baseline table and defect list in `docs/notes/`.

### Phase 1. Spike: which reader, which model (1 day, on a laptop, no platform change)
1. Readers: (a) `pypdf` now, (b) model reads the PDF, with 2 or 3 models (the enabled `gemini-3.5-flash-lite`, one larger Gemini, and one Claude or other model through OpenRouter if available to us), (c) Docling, only as a reference point, not built.
2. Same gold set, same documents. Report: hit@1/3/5, table and Hindi questions, number fidelity, **cost and seconds per page**, how often the output was malformed, and what the models did with an injected "ignore your rules" line placed in a test PDF.
3. Decide: the model, whether the ladder's first step runs on every page or only on pages that need it (low text, tables, columns, Devanagari), and the scan policy (double read or owner review).
- **Gate:** continue only if the model reader beats the baseline by a clear margin on the owner's real documents, with acceptable fidelity and cost. Otherwise stop and go to Docling (Phase 8).
- **Done:** a comparison note with numbers and a go or no-go.

### Phase 2. The reader, behind a setting (2 to 3 days)
1. `gemini.py`: a native-call function beside `chat` (same key rotation, OpenRouter last resort, cost counted).
2. New `lambda/doc_reader.py` (pure and testable): split into batches, build the instruction, parse the page blocks, route per page along the ladder, number check, returns `(pages, flags, usage)`.
3. `knowledge_ingest.prepare()` uses it when `knowledge.reader` says so; falls back to `pypdf` on any failure; `doc` gets `reader`, `model`, `flags`.
4. Cost and counters: add to `kspend` and the owner's token counter; refuse before reading if the estimate exceeds the cap.
5. Tests (fake transport, no AWS): well-formed answer, wrong page count (retry then fallback), numbers mismatch (falls to text layer, flagged), model unavailable (fallback), injected instruction in the PDF text, cap reached, unchanged behaviour when the setting is `text`. Update the settings tests, the spec and CHANGELOG.
- **Done:** gold set reproduces the Phase 1 gain through the real code path; full suite green; `text` stays the default until Phase 5.

### Phase 3. Tables and structure into passages (1 to 2 days)
A table is never split inside; a table too big is split by rows with the header row repeated; the section path (chapter, section, table caption) goes in front of the passage for the embedding. Gold set gets 10 more table lookups.
- **Done:** table questions hit@3 reaches the agreed target, no regression elsewhere.

### Phase 4. Images and scans (2 days)
New type `image`; scanned PDFs go through the model with the chosen verification policy; the pages with numbers that could not be verified are flagged.
- **Done:** a photographed price list and a scanned Hindi page are searchable and the gold questions on them pass.

### Phase 5. Show the owner how the book was read, and switch on (2 days)
Studio (Build, books): per book the reader and model, pages read by the model, pages that fell back, pages flagged "please check", skipped pages, the estimated and real cost. A **Read again** action creates a new collection version; the old one stays until the owner's check passes. Then default `knowledge.reader` to `auto`.
- **Done:** the owner can see and fix a badly read book without asking us.

### Phase 6. Roll out to the live agent (1 day)
Re-read the `shop` books into a new collection, run the gold set and the agent's own tests against old and new, switch the agent's version, keep the old collection for rollback. Docs: ARCHITECTURE, DECISIONS, RUNBOOK. Change set shown before any deploy, Checkov first, deploy only on an explicit "Yes, execute".

### Phase 7. Optional, only if numbers ask for it
Small documents read whole at answer time (prompt cached); the original page image sent with the question for table answers; reranker and query rewrite.

### Phase 8. Docling, if still needed
If the spike or real use shows a class the model reader does not handle (very long books, cost, fidelity, offline needs), add Docling as a second reader, run as a background container job (see the revision in `KNOWLEDGE-QUALITY-PLAN.md`).

## 5. Effort and gates

| Phase | Effort | Infra change | Stop condition |
|---|---|---|---|
| 0 Baseline | 1 day | none | always do |
| 1 Spike | 1 day | none | no clear gain, or fidelity too low |
| 2 Reader | 2 to 3 days | none (same worker) | gain not reproduced |
| 3 Tables | 1 to 2 days | none | none if clients have tables |
| 4 Images, scans | 2 days | none | no such clients yet |
| 5 Owner view | 2 days | Studio deploy | none |
| 6 Roll out | 1 day | change set only if settings or template change | none |

Phases 0 to 2 are the core, about 4 to 5 days, with **no new hosting, no container, no AWS resource**.

## 6. Risks and rules
- **Fidelity** is the main risk (numbers, prices, phone numbers, times). Hence the number check, the fallback to the text layer, owner review of unverified pages, and never a silent single pass on scans with prices.
- **Privacy:** client documents go to the model provider already used for answers (Google, OpenRouter as last resort). State it in the owner terms; no new third party.
- **Cost:** shown before reading, capped per book and per month, counted in `kspend`.
- **Model drift:** a model update can change the output; pin the model name, rerun the gold set before changing it.
- **No kit changes:** nothing in `student/` or `starter/` is touched.
- **Provider limits:** the current key path offers one small model; a larger one needs a price entry and a check that our keys can call it.

## 7. What I need from the owner
1. Approve **Phase 0 and 1** (about 2 days, no platform change, a few dollars of model calls at most).
2. The files: `shop` books, a document with tables, a Hindi or Hinglish PDF, a scanned PDF, 2 or 3 photos of price lists or menus, and about 30 questions if you have them. Tell me the folder; they stay local and uncommitted.
3. Which models your keys can reach besides the enabled Gemini Flash-Lite (and what "Luna 6" is, so I can include it correctly).
