# Roadmap: a generic, trustworthy RAG and agent platform

> **Status: the agreed plan (2026-11-05).** It merges four threads that were discussed separately: the **benchmark pack**, the **per-shelf settings file and profiler**, **guardrails and groundedness**, and **stability and drift**. Nothing in this document is built yet unless it says so. The facts about the current system are in [RAG_GUIDE.md](RAG_GUIDE.md) and [AGENT_GUIDE.md](AGENT_GUIDE.md); the limits and permissions are in [KNOWLEDGE_CONNECTOR.md](KNOWLEDGE_CONNECTOR.md).

---

## 1. Goal and decisions

**Goal.** One generic framework that gives **correct, grounded, safe** answers from documents of **any domain** (school, medical, finance, legal, manuals), in **English and Hindi**, that can later be used for **business**, with **one install per organisation** (each in its own AWS account), and that **stays good over time** (no silent decay).

**Decisions made by the owner**

| Question | Decision |
|---|---|
| Breadth or depth | **Breadth first** (a strong general core; depth added only where measurements show a failure) |
| Business model | **One install per organisation.** No hosted multi-tenant service for now |
| Regulation | No special rules stated yet; **Tier C** (regulated) is designed in anyway |
| Languages | **English and Hindi** (including Hinglish attacks and questions) |
| Which error is worse | **Answering wrongly** is worse than refusing. Thresholds and judges lean toward refusing |
| Logging | By default **only guard decisions and hashes**; flagged items keep their text for review, for a limited time (section 5) |
| Managed guard | **Amazon Bedrock Guardrails is allowed as an option to test** |
| Latency and cost | **Fine** for guard overhead (targets in section 6 stay as limits) |
| Flagged answers | Saved in **one place** (the review table, section 5) |
| Benchmark documents | **Yes** to downloading openly licensed documents; the exact list (source, size, licence) is shown **before** anything is downloaded |

**Principles**
1. **No source, no claim.** An unsupported answer becomes "The books do not say.", never a guess.
2. **Deterministic checks before model checks before humans.** Each step is slower and costs more.
3. **Choose from tested strategies**; never let a model pick free numbers.
4. **Measure, then change one thing.** Every change is gated by the benchmark.
5. **Light in production, heavy in testing.** Heavy open-source tools run in the test pipeline; the functions stay small.
6. **Fail safe and visible.** A guard that fires is recorded; a provider outage degrades to showing passages.

---

## 2. Risk tiers (set per shelf)

| Tier | For | Guards |
|---|---|---|
| **A. Study and FAQ** | School books, notes, public FAQs | Input flags, citation checks, the confidence gate |
| **B. Business internal** | Company documents | Plus: personal-data scan, claim-level groundedness check, review queue, audit log |
| **C. Regulated** | Medical, finance, legal | Plus: stricter thresholds, **quote-only mode** option, required disclaimer, review of flagged answers, longer retention |

The tier lives in the shelf settings file (phase 1) and switches the guards on.

---

## 3. The layered design

| Layer | What it does |
|---|---|
| **0. Ingest scan** | Flags hidden or invisible text, instruction-like text, odd repetition, personal data, scanned pages. Records provenance (source, version, date) |
| **1. Input guard** | Normalises text (invisible characters, look-alike letters, Devanagari and Roman mixes), flags injection patterns, applies per-shelf denied topics. **Flags and logs by default; blocks in Tier C** |
| **2. Retrieval guard** | The confidence gate (exists), plus dropping outdated versions, flagging or down-ranking passages that look like instructions, a minimum amount of evidence |
| **3. Constrained generation** | A structured answer: the answer, the passage numbers behind each claim, an explicit "cannot answer" flag; low temperature |
| **4. Output guard** | Citations valid; numbers, names and quotes really present in the cited passages; a small-model check that each claim is supported; secret and personal-data scan; policy check. On failure: refuse, or show quotes only |
| **5. Agent guard** | Allowlist, approval, budgets (exist), plus filtering what tools return |
| **6. Observability** | Guard decisions, golden probes, drift metrics, the review table |

---

## 4. Evaluation: four sets, run at different times

| Set | Purpose | Notes |
|---|---|---|
| **Quality** | Answerable questions with the right page | The benchmark documents |
| **Refusal** | Must refuse, including near-misses | Counted double in the score, because a wrong answer is worse |
| **Adversarial (English, Hindi, Hinglish)** | Attack success rate | Injection in the question, in a document, in a tool result; prompt extraction; personal data; look-alike and zero-width characters |
| **Regression** | Every real failure becomes a permanent case | Seeded with two real ones: the wrong "chief advisor" pick and the duplicate passages |

**Metrics:** groundedness (share of claims supported), citation accuracy, retrieval recall and MRR, refusal precision, false-refusal rate, attack success rate, personal-data leak rate, latency and cost per answer.

**When:** a small set on every change; the full set plus attacks weekly; a full run after any model, prompt, chunk or threshold change; results kept per version to show a trend. Open-source red-team and metric tools (promptfoo, garak, PyRIT, RAGAS, DeepEval) run **only in the test pipeline**, not in production.

---

## 5. The review table (where flagged answers are saved)

**Place:** a dedicated small on-demand DynamoDB table in the customer's AWS account, with automatic expiry, shown on the teacher page as a **Review** tab.

| Item | Decision |
|---|---|
| What is saved | Only **flagged** answers: time, shelf, tier, the question and answer (cut to a safe length), the passage numbers, the guard reasons, the model ids, the scores, any thumbs-down |
| What is **not** saved | Anything unflagged: those leave only counters and hashes |
| Retention | Default **30 days** (Tier C longer, set by the teacher); items expire by themselves |
| Who can see it | **The teacher only** (teacher login). Students see nothing |
| Statuses | New, confirmed wrong, false alarm, fixed, ignored; plus a reviewer note |
| Actions | **Add to the regression set** (turns a confirmed failure into a permanent test case), export, delete |
| Why not S3 or the tokens table | S3 cannot filter or update status easily; the tokens table is deliberately designed so no function can scan it |
| Privacy | Students are told in the docs that flagged answers are kept for review for a limited time |

---

## 6. Stability and drift

| Risk | Detection | Response |
|---|---|---|
| Generation model changes silently | Model id recorded on every answer and run; **golden probes** (about 20 fixed questions) on a schedule | Alert on a drop; pin a dated version where available |
| Embedding model retired | Model and size stored per shelf; watch provider notices | Build a new index **alongside** (the index is versioned), compare on the benchmark, then switch |
| Documents change | Content-hash sync (exists); version and date fields | Re-read changed files; expire outdated ones |
| Score drift | Track the share of refusals and the percentiles of top similarity over time | Alert beyond a tolerance; recalibrate thresholds |
| Question drift | Sample new questions (with consent) | Add to the evaluation set |
| Provider behaviour breaks the prompt | Live smoke probes, scripted tests | Fix, add a regression case |
| Provider down or slow | Timeouts, error rate | Retry, then a **fallback model**, then a **retrieval-only mode** (passages without a generated answer) |

**Gates and targets**
- **No change ships if any benchmark document gets worse beyond a tolerance.**
- Attack success: **under 5% after the deterministic guards, under 2% after the groundedness judge** (and the managed guard if it wins).
- Wrong or unsupported claims presented as answers: **at most 2%**; groundedness **at least 95%** of claims supported.
- Refusal precision **at least 95%**; false refusals tolerated up to about **10%** (the owner prefers refusing to answering wrongly).
- A drop in quality is caught **within a day** by the probes.
- Typical guard overhead about **1.5 s** and **30% cost** per answer is acceptable; rules run first, model checks only where the tier needs them.

---

## 7. The roadmap

Each phase is a separate release step: tests first, then `plan` and `apply` for anything on AWS (shown to the owner for approval), then `publish`, then a testing list for the owner. A release tag only when the owner says "release x".

| Phase | Deliverable | Gate to pass |
|---|---|---|
| **0. Foundation and baseline** | The **benchmark pack** (about 15 to 20 openly licensed documents in English and Hindi, varied types, including one with an old non-Unicode font), the question and attack sets, a runner and a report; a **baseline on the current version** | The owner approves the document list (sources, sizes, licences) **before** any download |
| **1. Shelf settings** | A per-shelf settings file and teacher screen: tier, thresholds, `k`, passage size, notes, with safe ranges for students; the evaluation numbers shown next to the settings | Changing a threshold visibly moves the benchmark |
| **2. Deterministic guards and the review table** | Ingest scan, input flags, passage scan, citation and number checks, secret and personal-data rules (including common Indian identifiers), the guard-decision log, the **review table and Review tab** | Attack success under 5%; overhead small |
| **3. Profiler, strategies and Hindi support** | A rule-based document profiler choosing from 3 or 4 tested strategies; Hindi-aware word search, sentence splitting and stop words; Hinglish handling | Improves at least two very different documents, regresses none |
| **4. Groundedness judge and quote-only mode** | A claim-level check with a small model, calibrated on the benchmark; quote-only mode for Tier C | At least 95% claims supported; wrong answers at most 2% |
| **5. Stability** | Golden probes on a schedule, model recording, drift metrics and alerts on the teacher page, the fallback chain | A deliberate quality drop is detected within a day |
| **6. Managed guard test** | Bedrock Guardrails behind an interface, compared with our layers on the adversarial set **in Hindi too** (feature, region and price verified first; needs an AWS permission change shown in a plan) | Adopt only if it **measurably wins** |
| **7. Auto-tune** | Synthetic questions, preset choice and threshold calibration, with a report the owner approves (about 10 to 20 cents per book) | Beats the rule-based choice on the benchmark |
| **8. Business readiness** | Audit log and retention settings, **document-level permissions** (a search never returns a passage the user may not see), document versions and effective dates, a storage backend swap when a customer's corpus passes about 100,000 passages per shelf | Before any real business data is used |

**Order rationale:** measure first (0), make settings storable (1), cut risk early with cheap guards (2), then quality (3, 4), then stability (5), then the options that need an AWS change or cost (6, 7), then business features (8).

---

## 8. What the owner does at each step
- **Phase 0:** approve the document list; run the smoke test of the current version (already listed).
- **Every phase:** approve any `plan` before `apply`; approve a `publish`; run `verify`; follow the testing list I provide; review the benchmark report.
- **Phase 2 onwards:** use the Review tab on real flagged items and mark them.
- **Anytime:** say "release x" when you want a tag.

## 9. Open items I will confirm with the owner as they come up
- The exact benchmark document list (shown first, then downloaded).
- Retention for Tier C, and who reviews flagged items when more than one teacher exists.
- Whether to keep a fallback model through a second provider.
- The price and Hindi support of managed guards (verified at phase 6).
