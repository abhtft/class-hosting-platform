# Extras as retrieved facts that win over the books, and the move of the book search to Bedrock Knowledge Bases (final plan v3)

Status: approved by the owner on 2026-11-21 (idea confirmed; D1 and D2 take the recommended answer A). Track E (extras) is implemented first, locally; Track B (Bedrock KB) is planned below and each AWS step waits for the owner's yes because it creates resources. Testing phase: no customer is served, so no live stopgap is needed.

## 1. Objective (owner's words, confirmed)

- The **books are facts that change over time**. The client corrects them in teach sessions; each correction is stored as an **extra** of the agent version.
- Extras are **embedded like the books** and **retrieved** for a question. A retrieved extra has **higher priority than any book passage**.
- Conflict between an extra and a book: the extra wins. Conflict between two extras: the **latest** wins.
- When no extra is relevant, the answer is exactly what the books alone would give, including "the books do not say", the welcome message, the off-topic refusal and the hand-over after two fallbacks in a row.

## 2. Why the current code (989896f) does not meet it

| # | Problem | Consequence (seen in the live suite: 32/32 became 28/32) |
|---|---|---|
| 1 | All extras (up to 40) are put in every prompt: no retrieval. | Cost and noise on every message; grows with the list. |
| 2 | A version with extras is always "confident". | The books-or-fallback gate is bypassed: `welcome-first`, `off-topic-poem` and `handover-stuck` fail. |
| 3 | An extra is a plain string: no date, no source. | "Latest wins" among extras cannot be applied; nothing to audit or undo. |
| 4 | The older word-matching missed other wording ("timing" vs "hours"). | The reason for embeddings; the answer is retrieval by meaning, not "always include". |
| 5 | The standard suite still expects the book's value where the client corrected it. | A correct new answer is reported as a failed case. |
| 6 | The old book passage is retrieved next to the correction. | The model must be told, clearly, which one is newer. |

## 3. Design

**3.1 The record.** An extra becomes `{"text", "at", "source"}`: `at` is a unix time, `source` is `{"session": id}` (teach) or `{"by": "owner"}` (typed in the editor). A plain string (older versions) stays valid and means `at` = the version's publish time, no source. Limits stay settings (`chatwoot.maxExtras`, `chatwoot.extraChars`).

**3.2 Embedding.** The worker embeds the version's extras on first use, in one batch call (at most `maxExtras` short texts), and keeps the vectors in its warm container keyed by a hash of the extras list; a new container does it again. No new IAM, no new storage: the worker already holds the embedding key and the same embedding model as the books. The customer's question is embedded once and that vector is reused for the books search (no second call).

**3.3 Retrieval.** Score = cosine of the question and the extra. An extra is **relevant** when its score is at least `chatwoot.extrasMinScore`; the best `chatwoot.extrasTopK` relevant ones are used. If two used extras say different things, the one with the later `at` is listed first and the rules name it the winner.

**3.4 The prompt.** The rules say: *the updates are newer than the books; on any conflict the update is right and the book is out of date; between updates the later date is right; use an update only if it answers the question.* The block `<updates>` (each line with its date) comes **before** `<books>`. The business's own instructions and the language rules stay as they are.

**3.5 The gate.** `confident` = the books are confident **or** at least one extra is relevant. Everything else is unchanged, so with no relevant extra the behaviour is identical to a version without extras. A relevant extra with unsure books answers from the extra alone.

**3.6 What the owner sees.** The dry run and the trace list the extras that were relevant (text, date, score) next to the book passages, and say which ones the model was shown.

**3.7 The teach flow.** A confirmed fact from a teach session is written with its date and session. A proposal that contradicts an existing extra shows the older one next to it, so the owner can remove the old one (removal is a new version, like every change).

## 4. Phases, step by step (each phase ends with its own "done")

| Step | Work | Done when |
|---|---|---|
| E0 | Remove the always-include code path of `989896f` (the unconditional `<updates>` block and the forced `confident`), keeping the rule wording that updates are newer than the books. | The old behaviour of versions with extras is back; the existing unit tests pass. |
| E1 | The extra record with `at` and `source`; `clean_config` accepts both shapes and returns records; the teach flow and the draft builder write them; settings and generated docs updated. | Unit tests for old strings, new records, limits; a version published earlier still loads and answers. |
| E2 | Embedding and retrieval (3.2, 3.3); the `<updates>` block and rules (3.4); the gate (3.5); settings `extrasMinScore`, `extrasTopK`. Embeddings are injected in tests. | Unit tests: scoring, threshold, newest first, no relevant extra changes nothing, unsure books plus a relevant extra answers, one question embedding only. |
| E3 | Dry run and trace (3.6); suite cases: correction beats the book, later correction beats an earlier one, unrelated question ignores extras, and `welcome-first`, `off-topic-poem`, `handover-stuck` pass again; the stale `office-hours` case is updated after the owner confirms the real hours. Docs: first entries in `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/RUNBOOK.md`, plus `CHATWOOT_ADAPTER.md` and the changelog. | All local tests pass; `node tests/run.mjs` passes. |
| E4 | Checkov and cfn-lint, change set, deploy; then tune `extrasMinScore` on a draft with the real embeddings (a list of true and false pairs, scores printed), set the defaults; run the live suite once. | The live suite has no failed `must` case, all new cases pass, the chosen thresholds are recorded in `DECISIONS.md`. |
| E5 (Studio, after E4) | The agent editor lists the extras with date and source, lets the owner delete one, and shows in the test chat which extras were used. | Studio build and tests pass; the list matches the worker's view. |

## 5. Decisions for the owner

- **D1 Where to embed.** A (recommended): lazily in the worker, cached per container (simple, no new permissions). B: at publish time, stored in S3 (slightly faster first answer, needs a write path and new permissions).
- **D2 "Latest wins".** A (recommended): by the extra's date at answer time, and the owner may remove an old one. B: an explicit "replaces extra N" link set in the teach flow (more exact, more to build).
- **D3 Office hours.** The books say 9 to 6 and the client's extra says 10 to 6. The owner confirms which is true; the standard suite changes accordingly (not a code decision).

## 6. Out of scope

Merging old extras into the books automatically (the owner does it by hand for now, keeping the extras list short), conflicts between two books, and detecting which book passage an extra replaces.

## 7. Track B: the book search on Amazon Bedrock Knowledge Bases with S3 Vectors (after Track E; B0 needs the owner's yes)

Why: managed ingestion (chunking, PDF parsing) and a store that scales without our own index code. Cost: S3 Vectors has no idle cost (about $0.06 per GB-month of vectors and about $2.5 to $2.7 per million queries, from third-party pages: check the AWS pricing page); OpenSearch Serverless (about $345 to $700 a month idle) is **not** used. Risks to measure first: retrieval quality on Hinglish, the "is the book sure" decision (our own thresholds are tuned on our hybrid score), page citations, and one Knowledge Base for many businesses (metadata filters on `tenant` and `collection`). Everything stays behind the existing `search` function, so going back costs a setting.

**Blocker found earlier:** this AWS account has not completed Bedrock model access (`NOT_AUTHORIZED` for Claude). A Knowledge Base needs access to its embedding model too. The owner completes the use-case form and model access first.

| Step | Work | Done when |
|---|---|---|
| B0 (owner's yes: creates AWS resources, a few cents a month) | Template (infrastructure as code, behind a switch that is off by default): an S3 Vectors bucket and index, one Knowledge Base, a data source on the sources prefix with metadata files for `tenant` and `collection`. Ingest one real shelf of `poc`. Run the same questions through the current store and through the Knowledge Base (`eval` cases plus the suite questions): recall@k, MRR, refusals, Hinglish, page numbers, latency, cost. | A written comparison with numbers and a go or no-go. |
| B1 | A setting `chatwoot.searchBackend` (`own` or `bedrock_kb`); `search_shelves` gets a Knowledge Base implementation (Retrieve with a metadata filter, scores mapped to our `confident` rule); a shadow mode that answers from the current store and records the Knowledge Base's hits for comparison. The worker gets `bedrock:Retrieve` on that one Knowledge Base only. | Unit tests with a fake client; shadow mode shows the same top passage on at least the agreed share of questions. |
| B2 | `chatwoot_ctl.py knowledge-sync` also copies the sources to the data-source prefix and starts an ingestion job (and shows its status). Removing a book removes it from the Knowledge Base. | A new book is searchable through the Knowledge Base after one command. |
| B3 | Switch one business by setting; run the suite; keep the current store as the fallback for one release; then decide whether to remove it. Docs: `ARCHITECTURE.md`, `DECISIONS.md`, `RUNBOOK.md`. | Suite has no failed `must` case on the Knowledge Base; the decision is recorded. |
| B4 | The students' knowledge connector (a separate decision, later). | Not planned yet. |

**Extras and Track B.** Extras keep their own small embedding step (3.2) behind a function, so they can move to the Knowledge Base's embedding model later without touching the answer logic; priority over the books does not depend on which store the books use.
