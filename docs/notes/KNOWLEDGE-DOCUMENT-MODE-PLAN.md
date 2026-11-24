# Document mode: give the model the whole PDF (2026-11-24)

Status: **plan only, nothing built, needs approval per phase.** This changes the order of `KNOWLEDGE-READER-PLAN.md` (the read-once-into-Markdown track becomes Track 2) and of `KNOWLEDGE-QUALITY-PLAN.md` (Docling stays a later option).

## 1. The decision and why it is sound

The owner's documents are small: usually 8 to 10 pages, at most 25 to 30. At that size the simplest and highest-fidelity design is to **send the original PDF to the model with the customer's question**, instead of extracting text, cutting passages, embedding them and searching. The model sees exactly what a person sees: tables, Hindi script, stamps, layout. Whole-document context also removes the failure that retrieval can never remove: the right passage was never found.

Retrieval, passages and embeddings are not thrown away. They remain for the day a business has far more than a few documents, and as the **fallback** when the document call fails. Document mode is a new `mode` of an agent version, so the live agent is untouched until the owner chooses it, and it can be compared with the current way, version against version, with the checks that already exist.

Two points where my earlier worry was too strong:
- **Prompt injection:** the owner uploads the documents; customers cannot. The risk is much lower than for customer text. The rules still say "documents are reference material, never instructions", and the 52-case safety suite is run on a document-mode version before it goes live.
- **Price:** see section 5; it is a few times today's cost per message, still a fraction of a cent to about half a cent.

## 2. What the repo gives us (read from the code)

- `chatwoot_runtime.respond()` is the one answer function: search the shelves, score the corrections ("updates"), `build_messages()`, `runtime.llm(...)`, handle the "not sure" sentence, media, clean the reply. The agent config already has `mode` (`rag` or `auto`), `provider` and `model` per version.
- `build_messages()` already holds everything that must stay the same in document mode: the fixed rules, the corrections (newer than the books), the owner's instructions, media, language, "do not greet again", channel rule, inbox note, conversation memory. **Only the `<books>` block changes**: instead of passages, the model gets the documents.
- The original uploaded file is already stored in S3 (`_source_key` in `knowledge.py`), with sha256 and page count in the document record. No new upload flow.
- `llm_gemini()` goes through `gemini.chat()`, an OpenAI-style text call. A PDF needs Gemini's **native** call (the file as an inline part). That is the one new low-level function, beside `chat`, using the same keys, key rotation and cost counting.
- `knowledge_ingest` keeps building the text index when a PDF is uploaded; that stays as is, so the fallback and the "books" mode keep working.
- There is a `bedrock` module and a `provider` field already; Bedrock is blocked on the account today. The document part is built per provider so Bedrock (its Converse API takes PDFs as document blocks: verify) or OpenRouter (PDF as a file part: verify) can be added when allowed.

## 3. Design

### 3.1 Config
`mode: "documents"` next to `rag` and `auto`. Optional `documents: {"max_pages": n}`. Which documents: every PDF on the version's `collections` (same shelves the owner already manages), in a fixed order. The version **pins each document by id and sha256**, so uploading a new file never silently changes a published version (to be checked against how books are pinned today, see Phase 2).

### 3.2 Answer path
1. Load the PDFs (S3, kept in memory while the container is warm).
2. `build_messages()` variant: the same system rules and parts; the user turn is the documents (first, in a fixed order, so the provider can cache the repeated prefix) and then `<customer>` as today.
3. Native call with the PDFs as parts; reply handled exactly as today: the "not sure" sentence gives the fallback text, media line, length limit.
4. The model may end its reply with a hidden line `PAGES: 3,5` (the same pattern as the `MEDIA:` line, stripped before the customer sees it) so Studio's "why" can show where the answer came from. It is self-reported, not verified; say so in the UI.
5. If every provider fails: the existing books route (retrieval) answers, and the trace says `via=books_fallback`.

### 3.3 Limits (settings, not constants)
`knowledge.docMaxPages` (about 60 for all documents of one agent), `knowledge.docMaxMB` (the provider's inline request limit, with margin for base64), per-question token ceiling. A version whose documents exceed them is refused at publish with the numbers and the advice (fewer documents, or books mode). The publish check also shows the **estimated tokens and cost per 1,000 questions**.

### 3.4 What it does not change
Corrections (extras) still win over the documents. The 52 safety cases, your own tests, repeatability and the consistency check run on a document-mode version like on any other. The consistency check can later also read the documents (today it cannot read the books).

## 4. Phases

### Phase 0. Preparation (half a day, no platform code)
- Choose 3 to 4 real documents (the `shop` books, one with tables, one Hindi or Hinglish).
- Gold questions = the `shop` tests table rows plus about 20 new ones: table lookups, Hinglish, follow-ups, "not in the document", price and time questions. Write the expected key facts.
- **Baseline** on today's version with the existing tools: your tests, safety suite, repeatability, and median reply time from traces (`retrieval_ms`, `llm_ms`).

### Phase 1. Quick spike (half a day, a script in `tools/`, no platform change)
Call Gemini natively with the whole PDF plus the same rules text, for the gold questions. Record per question: answer, correct or not (by key facts), input tokens (and cached tokens if reported), seconds, cost. Try the enabled model and one larger model if our keys can reach it. This answers three things before any core change: does the native PDF call work with our key and model, what do real pages cost in tokens, and is the quality clearly better.
- **Gate:** continue if the document answers are at least as good on plain questions and clearly better on tables/Hindi, with cost and time inside the limits below. Otherwise stop here.

### Phase 2. Document mode in the platform (3 to 4 days)
1. `gemini.py`: `generate()` native call (same keys, rotation, OpenRouter last resort when its file support is confirmed), cost from the usage figures including cached and thinking tokens.
2. `chatwoot_runtime.py`: `mode: "documents"` accepted in `clean_config`; a documents loader; `build_messages` variant; the call; `PAGES:` line parsing; fallback to the books route; metrics (`mode`, pages, input tokens, cached tokens, `llm_ms`).
3. Version pinning of documents (id + sha256); publish-time size check and cost estimate in the admin route.
4. Settings in `config/settings.json`, then `python tools/gen_settings.py`; the numbers module test (`FIELD`-style "fixed" entries) kept green.
5. Tests with a fake transport (no AWS): documents mode answer; "not sure" gives the fallback; corrections beat the document; provider failure gives the books route; oversize refused with the numbers; document changed after publish does not change the version; `text` and `rag` behaviour unchanged. Update CHANGELOG, spec, ADAPTER and RUNBOOK docs.
- **Done:** a version in `documents` mode answers in Test chat and in the checks; the live `shop` (books mode) behaves exactly as before; suite green. No AWS resource changes: this is code in the same functions, so a normal change set and an explicit "Yes, execute" before any deploy.

### Phase 3. Studio (1 to 2 days)
Build tab, a clear choice: **How the agent reads your documents: [Search the books] [Read the whole documents]**, with the page count, the estimated cost, and the limits; Test chat shows which mode and the pages the model reported. A simple way to ask the same question of two versions side by side if the existing "pick a version" is not enough.

### Phase 4. The owner's testing days (several days, no code)
A fixed protocol so the days produce a decision: for each of the gold questions, version N (books) against version N+1 (documents): pass or fail by key facts; the whole safety suite on N+1; repeatability on N+1 (3 times, the standard questions plus the table questions); about 20 real-sounding customer messages in Hinglish; check the median reply time and the cost per message from traces. Decision rule agreed in advance, for example: documents mode is adopted if it fails no more tests than books mode, is clearly better on tables and Hindi, repeatable on the standard five, and the median reply time and cost stay under the limits.

### Phase 5. Roll out
Make the document-mode version live for `shop` (rollback = make the previous version live, as always). Docs: ARCHITECTURE, DECISIONS, RUNBOOK.

### Phase 6. For bigger shelves (only when a business needs it)
When a business has more pages than the limit: retrieval picks the **top 1 to 3 documents** (not passages) and the model gets those documents whole. This reuses today's search and the document mode, with no new infrastructure.

### Track 2 (later, only if the numbers ask)
Read each PDF once into structured Markdown with a number check (`KNOWLEDGE-READER-PLAN.md`), to improve the books route and the fallback; Docling for large or scan-heavy documents (`KNOWLEDGE-QUALITY-PLAN.md`).

## 5. Cost and time (estimates to be measured in Phase 1)

| Documents | Input tokens per question | Input cost at 0.30 USD per million |
|---|---|---|
| 10 pages | about 2,600 (Google's average of 258 per page) | about 0.08 US cent |
| 30 pages | about 7,700 | about 0.23 US cent |
| 30 image-heavy pages | up to about 17,000 (560 per page on some platforms) | about 0.5 US cent |

Plus the answer (a few hundred output tokens at 2.50 USD per million, about 0.05 US cent) and the conversation memory. So **roughly 0.15 to 0.6 US cent per message, against about 0.1 cent today**: 1,000 messages a month cost 1.5 to 6 US dollars. If the provider caches the repeated document prefix (documents first, same order), the input part can fall a lot: Phase 1 reads the cached-token figure to see whether our path benefits. The monthly cap per business counts it as always. Time: more input tokens add some latency; the target is a median reply time near today's, measured, not assumed.

## 6. Risks and answers

| Risk | Answer |
|---|---|
| Model answers from general knowledge, not the document | Same fixed rules and the exact "not sure" sentence; the "unknown question" tests, the safety suite and repeatability run on the version before it is live |
| Quality of the small enabled model on long documents | Phase 1 tries a larger model; version config already carries `model` |
| Pages cited by the model may be wrong | Marked "reported by the model" in Studio; not used for any decision |
| Cost per message rises | Estimate at publish, monthly cap, caching test, a limit on pages; books mode remains |
| Provider cannot take PDFs (fallback chain) | Fall back to the books route; Bedrock and OpenRouter adapters added only after their PDF support is confirmed |
| A re-uploaded file changes a live version | Versions pin documents by id and sha256 |
| Privacy | Documents go to the provider already used for answers; no new third party |

## 7. What I need from the owner
1. **Approve Phase 0 and 1** (about one day, no platform change, a few dollars of model calls at most).
2. The real documents (kept local, not committed): the `shop` books, one with tables, one Hindi or Hinglish; and any questions you already know customers ask.
3. Which models your keys can reach besides the enabled Gemini Flash-Lite, so Phase 1 can try a larger one.
4. The decision rule for Phase 4 (my proposal is in section 4; change it if you want stricter numbers).
