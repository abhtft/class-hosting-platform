# Architecture review of the agent platform (2026-11-22)

A fresh review of the Chatwoot agent platform (teacher repo `lambda/chatwoot_*`, `template.yaml`) and Agent Studio (`Test/studio`), done as if seeing it for the first time. The goal it is judged against: **many kinds of business agents (support, sales, lead generation, marketing) that are robust, reliable, fast and cheap**, run by a small team.

Each finding has a short "why it matters" and a recommendation. Priority: **P1** = do before calling the platform finished; **P2** = do before more businesses or agent types; **P3** = later.

---

## 1. Verdict in one paragraph

The **core is sound and better tested than most agent products**: a webhook that answers in milliseconds and hands work to an asynchronous worker; one claim record per message so a customer is never answered twice; immutable versions with a separate "make live"; answers grounded only in the business's books, with a "not sure" path and a human hand-over; corrections that beat the books; four kinds of checks (your tests, a central safety suite, repeatability, consistency) and a gate before make-live; per-business monthly spend caps; traces. What it is **not yet** is a platform for *different kinds* of agents: today there is exactly one shape (a question-answering support bot), the "auto" agent mode is weaker than the main path, the runtime shares the student-hosting stack and its limits, and two large modules carry most of the logic. The work to conclude is mostly **consolidation and one new layer (agent types with actions)**, not more checks.

## 2. What is done right (keep it)

| Area | Why it is right |
|---|---|
| Receiver → async worker, claim per message, retries off, sweeper for stuck items | The 5-second Chatwoot timeout is never at risk; no double replies; a stuck message ends in a hand-over, not silence. |
| Versions are immutable; publish ≠ make live; roll back by choosing a version | The safest release model there is for prompts and configs. |
| Grounded answers: fenced passages, "not sure" → fallback, two misses → hand-over | The main defence against invented prices and promises. |
| Corrections (extras) retrieved by meaning, newest wins, ahead of the books | Teaching changes behaviour at once without re-indexing books. |
| Checks: own tests with key facts, 52-case safety suite (OWASP LLM), repeatability, consistency, make-live gate with a typed reason | Deterministic checks decide; models only advise. Rare and valuable. |
| Spend: per-business monthly cap, every model call counted | A runaway agent cannot run up a bill. |
| Settings registry, IaC only, Checkov and cfn-lint, change sets shown before execute | Reproducible and auditable. |
| Traces with privacy levels; Studio Activity tab | You can see what the agent did and why. |

## 3. Findings

### Robustness (the agent says the right thing)

**R1 (P1) The "auto" agent mode is a weaker second path.** It runs only when the books are not sure and no correction matches (a matching correction already sends the question down the main path). But the agent loop (`chatwoot_agent.answer`) gets no conversation memory, no inbox note, no channel rule and no media, and it ignores `followup_fallback_text`. So in `auto` a follow-up question loses its context and the welcome can repeat. *Recommendation:* either build the auto prompt from the same parts as the books route (`build_messages`), or hide `auto` in Studio until agent types (section 3, Product) need tools. Today `shop` runs `rag`, so customers are not affected.

**R2 (P2) One fact lives in many places.** Hours, prices and phone numbers are written in the instructions, the fixed texts, the notes, the media descriptions, the corrections and the books. The new consistency check catches drift; it does not prevent it. *Recommendation:* the "business facts" card (a few named facts, `{hours}`-style placeholders in fixed texts, injected first in the prompt), as planned in `CONSISTENCY-PLAN.md`, once the check shows how often it happens.

**R3 (P2) Retrieval is single-shot.** One hybrid search (BM25 plus vectors) of the lightly history-aware question, top-k, fixed confidence thresholds. That is a good baseline. Short Hinglish follow-ups ("aur iska rate?") and mixed-script questions are the likely misses. *Recommendation:* measure first: tag fallbacks in traces by cause (no hit vs low score vs model said not sure). Add a cheap query rewrite only if low-score misses dominate.

**R4 (P2) Safety gaps that are known.** No hand-over reason for despair or self-harm words (the suite case is only a "should"); `agency-delete-data` is flaky. *Recommendation:* add a `distress` hand-over trigger (keyword list in Hindi, Hinglish and English, no model) and make the case a "must".

### Reliability (it always answers)

**L1 (P1) The agent runs inside the student-hosting stack and shares its limits.** The account Lambda concurrency (the template notes 10), the deploy pipeline, alarms and quotas are shared with student sites. A burst of WhatsApp traffic can throttle students, and a student burst can delay customers. *Recommendation:* ask AWS to raise the account concurrency now (free); give the worker a reserved concurrency; in P2, move the agent product into its own stack (same code, own `template`), which is also what the migration audit (G1 to G10) prepares.

**L2 (P1) No alarm watches the agent.** The alert topic exists, but no alarm is on the worker's errors, the receiver's 5xx, failed message items, hand-overs caused by failures (`llm_error`, `retrieval_error`) or a spend jump. Failures are seen only when the owner looks. *Recommendation:* four CloudWatch alarms on existing metrics and log filters, to the existing e-mail topic (cents a month).

**L3 (P2) One model provider in practice.** Gemini with key rotation and OpenRouter as last resort; Bedrock is blocked by the account. A Gemini outage means hand-overs for every message. *Recommendation:* keep OpenRouter's fallback model tested (a monthly repeatability run through it), and finish the Bedrock support case.

**L4 (P3) The reply post is not retried.** A timeout or network error when posting to Chatwoot ends the item as failed (correctly, to avoid a double reply), but the customer may get nothing. *Recommendation:* use Chatwoot's `echo_id`/`source_id` to make one retry safe, then allow it.

### Speed

**S1 (P2) Time is not yet measured end to end.** The path is: embed the question (books, cached per warm container), score the corrections (the question may be embedded again in a different variant), the model, then the post to Chatwoot; traces hold `retrieval_ms` and `llm_ms` but no total from webhook to reply. *Recommendation:* add the webhook-to-reply time to the trace, look at the median and the slowest 5 percent for a week, then act: reuse the question vector for the corrections if that call shows up, provisioned concurrency only if cold starts exceed 2 s.

**S2 (P3) Every collection is loaded into the worker's memory.** Fine for dozens of documents; it will not stay fine at thousands of passages per business. *Recommendation:* nothing now; S3 Vectors or a Knowledge Base once a business crosses about 20,000 passages (Track B is built and switched off).

### Cost

**C1 (P2) The model call is already cheap; the extras are not counted per business per feature.** A message costs about a tenth of a cent. Checks (suite, safety, repeatability, consistency, fill from books) cost cents and count against the same monthly cap as customer messages, so a busy testing day can stop a live agent at its cap. *Recommendation:* two buckets per business: "customers" and "testing", each with its own cap; the live agent never stops because of testing.

**C2 (P3) Cache repeated answers.** Many customer questions are the same ("price?", "timing?"). A cache keyed on (version, normalised question, corrections hash) with a short TTL saves the model call for the top questions. Only after traces show the repeat rate.

### Product: different kinds of agents (the business objective)

**P1 (P1 for the business goal) There is one agent shape.** Everything assumes a support bot that answers from books. Sales, lead generation and marketing agents need *goals and actions*: collect name, phone and need; qualify; book a visit; send a brochure; follow up. *Recommendation:* **agent types as templates**, not new code per agent:
- a type = default instructions + which hand-over reasons are on + which **actions** are allowed + which suite cases apply + which fields Studio shows;
- start with three: **Support** (today), **Lead capture** (asks for and records name, phone, need; hands over a warm lead with a summary), **Booking** (offers slots from a simple list, records the request);
- actions are small, deterministic tools with approval rules (record a lead, tag the conversation in Chatwoot, add a private note, send a media item), never free-form API calls.

**P2 (P1) Leads are only a hand-over today.** A mobile number triggers "warm_lead", but no structured lead is stored or exported. *Recommendation:* a `lead#<tenant>` record (name, phone, need, conversation id, time) written by the lead action, a Leads card in Studio and a CSV export; Chatwoot contact attributes updated through its API.

**P3 (P2) No proactive messages.** Marketing and follow-up need outbound messages (WhatsApp templates). *Recommendation:* out of scope until the lead record exists; then a "follow up after N days" action that uses approved WhatsApp templates only.

### Engineering hygiene

**E1 (P2) Two modules hold most of the logic.** `chatwoot_api.py` (1,086 lines: receiver, worker, every check) and `chatwoot_admin.py` (1,065 lines: every route). Each new check adds to both. *Recommendation:* split by concern without changing behaviour: `worker_answer`, `worker_checks` (test, suite, repeat, suggest, consistency share one shape: owned_pair → dry_target → cap → one model call → parse), `admin_agents`, `admin_checks`. The shared shape becomes one helper.

**E2 (P2) Too many plan notes.** 28 files in `docs/notes`, several superseded (PHASE-1-SPEC, -v2, -FINAL). *Recommendation:* one `docs/AGENT-PLATFORM.md` (what exists, how it fits, where the code is), move finished plans to `docs/notes/archive/`.

**E3 (P2) Studio has no tests for its screens.** Backend routes are tested; the React screens are checked by hand. *Recommendation:* a handful of Playwright smoke tests in practice mode (open each tab, publish, conflict review, make live), run before each Studio deploy.

**E4 (P1) Open security items.** The Chatwoot bot secret that appeared in Railway logs is not rotated yet; Studio sign-in was off at launch. *Recommendation:* rotate the secret; confirm Studio sign-in and the owner list are on.

## 4. Proposed conclusion plan

| Phase | Work | Done when |
|---|---|---|
| **A. Close the gaps (P1)** | R1 auto mode (fix or hide); L2 four alarms; L1 concurrency raise + worker reserve; E4 secret rotation and Studio sign-in check | A broken worker or a Gemini outage e-mails the owner within 5 minutes; auto mode either gets memory, notes and channel rules or is not offered; secret rotated |
| **B. Cost and speed (P2)** | C1 two spend buckets; S1 measure reply time end to end, then fix the slowest step; R4 distress hand-over | Testing cannot stop a live agent; median reply time measured before and after; despair words hand over |
| **C. Agent types (business goal)** | P1 types as templates; P2 lead record, Leads card, export; actions: record lead, tag, private note | A "Lead capture" agent is made in Studio from a template, collects name and phone in a real conversation, and the lead appears in Studio and Chatwoot |
| **D. Consolidate (P2)** | E1 split modules; E2 one platform doc; E3 Studio smoke tests; L1 own stack (with the migration audit) | No behaviour change; tests green; a new person reads one doc |
| Later | R2 facts card, R3 query rewrite, C2 answer cache, L3 Bedrock, P3 follow-ups | Only when traces show the need |

## 5. Decisions for the owner

1. **Auto mode:** fix it now (a day) or hide it until agent types (recommended: hide now, rebuild inside agent types).
2. **Which second agent type first:** Lead capture (recommended: it uses what exists and has clear money value) or Booking.
3. **Own stack for agents:** in phase D (recommended) or now, before more businesses.
4. **Spend buckets:** separate testing from customers (recommended) or one cap as today.
