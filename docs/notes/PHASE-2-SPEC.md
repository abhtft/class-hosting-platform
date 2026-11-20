# Phase 2 (teacher side, T-track): spec with answers, T1 approval pending

**Date:** 2026-11-19. **Code: none.** Teacher repo (`D:\Github_Codes\class-hosting-platform-github`) is untouched.
**Basis:** HANDOFF-agent-studio-v2.md (D1-D10, R2), T0-AUDIT.md (code evidence), memory: Studio = control plane, teacher = runtime + AWS, user deploys AWS.
**Method (Outer Boundary):** 1 Intent capture, 2 Spec table (fill "User's Answer" + "Status"), 3 Boundary document, 4 Checkpoints. I write nothing until you approve.

## 1. Intent capture (numbered; reply as e.g. "1A 2B 3A")

**Scope**
1. First deliverable of the T-track? A) Echo bot only (T1-T2: webhook, verify, dedupe, 200, async, reply "echo") **(recommended)** B) Echo plus RAG answer in one go C) Only design docs, no code yet.
2. How many Chatwoot businesses/inboxes in the first test? A) One inbox (your website widget) **(recommended)** B) Two inboxes to prove isolation.

**Depth**
3. Agent runtime in this phase? A) T4 later: first loop-safe echo, then runtime **(recommended)** B) Runtime from the start.
4. Message storage (D7): A) Store only what dedupe/retry needs (ids, status, timestamps), no message text **(recommended: privacy)** B) Store message text for 30 days (needed for conversation memory) C) Text for 90 days.

**Constraints**
5. Chatwoot version of your server? (Bearer token needs 4.19+, agent-bot signing needs 4.13+.) A) 4.19 or newer B) 4.13 to 4.18 C) I do not know (I will give you a read-only way to check).
6. Reply token: A) Agent-bot access token (message appears as the bot) if Chatwoot allows it **(recommended; I will verify in Chatwoot source first)** B) A dedicated user API token (message appears as that user).
7. Live AWS Lambda account concurrency limit: please run `aws lambda get-account-settings` yourself and tell me only the "ConcurrentExecutions" number. Needed to size the worker.

**Edge cases**
8. Worker fails after we already replied 200 (Chatwoot will not retry): A) Mark the conversation `open` for a human and add a private note **(recommended, matches D6)** B) Retry once, then A.
9. Message while a human has the conversation (`open`): A) Bot stays silent **(recommended)** B) Bot may answer too.

**Intent**
10. Who owns knowledge shelves for tenants (T0 Q5)? A) New "tenant" scope in the teacher knowledge store (clean, changes teacher core, done in T4) **(recommended)** B) A dedicated service-student slug (no teacher-core change, but shared student quotas).
11. Studio to teacher auth (T0 Q6): A) New service key `cs_<tenant>.<secret>` with a `svc#<id>` record **(recommended)** B) Reuse a student `ks_` key (quota capture risk).

**Non-goals (confirm)**
12. Not in Phase 2: WhatsApp/Instagram/Facebook rollout, voice, payments, multi-account Chatwoot auto-onboarding, media analysis. A) Confirmed B) Change: ______.

## 2. Spec table

| Decision Point | Proposed Answer | User's Answer | Status |
|---|---|---|---|
| T-D1 Deploy unit | New `HttpApi` + receiver function + separate worker function, same code bundle `lambda/`; behind a `UseChatwoot` condition (off by default) | | |
| T-D2 Webhook route | `POST /hooks/chatwoot/{agentId}` on its own `HttpApi` (not through the student gateway; no per-IP limit; own throttle and body-size cap) | | |
| T-D3 Verification | HMAC-SHA256 of `<timestamp>.<raw body>` with the bot secret, header `X-Chatwoot-Signature` (`sha256=<hex>`), constant-time compare, reject timestamps older than 5 min | | |
| T-D4 Ack | Return 200 right after verify + dedupe + enqueue (Chatwoot timeout is 5 s, retries only on 429/500) | | |
| T-D5 Dedupe | New small DynamoDB table (not the shared Tokens table): conditional write `attribute_not_exists` on `message_id`, TTL | | |
| T-D6 Async | Receiver invokes worker with `InvocationType=Event`; worker has explicit `EventInvokeConfig` (retries chosen on purpose) and claims the message atomically before replying | | |
| T-D7 Loop safety | Process only `message_created` with `message_type == "incoming"`, `private == false`, `sender.type != "agent_bot"`, conversation status `pending` | | |
| T-D8 Reply | Chatwoot Create New Message API with `Authorization: Bearer` (see Q5, Q6); conversation id uses the dashboard number (to be verified on a live payload in T1) | | |
| T-D9 Secrets | One Secrets Manager secret per business holding bot secret + API token(s); only receiver/worker may read; cached in the container for a few minutes | | |
| T-D10 Mapping | `(chatwoot_account_id, inbox_id)` to agent (published version), stored in the new table; edited only through the Studio API | | |
| T-D11 Studio API | HTTPS + `cs_` service key; versioned agent config in, runtime and trace summaries out; no shared files | | |
| T-D12 Tests/docs | New `tests/test_chatwoot_*.py` steps in `tests/run.mjs`; limits in `config/settings.json` then `gen_settings.py`; update `docs/TECHNICAL_SPECIFICATION.md`, `CHANGELOG.md`, `docs/SETTINGS.md` | | |

## 3. Boundary document (draft)

**Hard constraints**
1. No edit, commit or deploy in the teacher repo before you approve T1. After approval: local code and tests only; **you** run every AWS deploy.
2. Never touch existing behaviour: `app.py`, `portal.py`, `gateway.py`, `knowledge.py` and `student/*` stay unchanged (kit-version test would fail).
3. No secret in code, logs, tests or traces. Webhook secret and tokens never reach student code.
4. Every new limit goes in `config/settings.json`, no numeric literals in `lambda/`.
5. New IAM is narrow; the guard tests (`test_template_*`) are updated deliberately, never bypassed.
6. Studio stays HTTP-only: no shared files, no runtime code in `studio/`.
7. No personal data stored beyond what Question 4 allows.

**Implementation plan (new flat modules in `lambda/`)**
`chatwoot_core.py` (pure: parse, verify, loop guard), `chatwoot_api.py` (receiver + admin routes), `chatwoot_agents.py` (tenant, mapping, versions), `chatwoot_runtime.py` (T4), plus `template.yaml` resources.

## 4. Checkpoints (pause and report at each)

| # | Work | Gate |
|---|---|---|
| T1 | Webhook + signature verify + dedupe + 200 + async invoke; **echo bot only, no AI** | `node tests/run.mjs` passes; you run the AWS deploy; a test message from your widget is recorded once as processed, a duplicate delivery is recorded as duplicate and not processed again (no reply yet; the reply is T2) |
| T2 | Reply via Create Message with loop safety | no loop, no double reply on forced retry |
| T3 | Inbox to agent mapping; per-business secrets | two inboxes isolated |
| T4 | Agent runtime (RAG + loop) behind the versioned API; tenant knowledge scope | eval set passes incl. refusals |
| T5 | Handoff and failure handling | killed worker leads to `open` + note |
| T6 | Studio UI connected to this API | end-to-end from Studio |

Studio C3-C8 continue separately (sign-in, CRUD, publish, wizard, editor, deploy), each with its own checkpoint.

## 5. What I need from you
Answers to the 12 questions above (or "all recommended") and the Status column. Then I write the final Boundary Document and stop for your approval of T1.

---

## 6. Answers (2026-11-19: "all recommended") and what is still open

| Q | Answer used | Note |
|---|---|---|
| 1 | A: echo bot only first | T1 = no outbound call yet, T2 adds the echo reply |
| 2 | A: one inbox (your website widget) | |
| 3 | A: runtime later (T4) | |
| 4 | A: store no message text, only ids/status/timestamps | conversation memory is therefore a later, separate decision |
| 5 | A: Chatwoot 4.19 or newer | **ASSUMPTION, not checked.** T1 only needs signing (4.13+). Please confirm the real version (Chatwoot admin: Settings, bottom-left version) before T2 |
| 6 | A: agent-bot access token | I verify in Chatwoot source that a bot token may post to a conversation before T2; fallback B |
| 7 | pending | **You run `aws lambda get-account-settings` and tell me only ConcurrentExecutions.** Not blocking T1, blocking T4 sizing |
| 8 | A: on failure set conversation `open` + private note | built in T5 |
| 9 | A: bot silent while a human has the conversation (`open`) | |
| 10 | A: new "tenant" scope in the knowledge store | built in T4 (changes teacher core, separate approval then) |
| 11 | A: `cs_<tenant>.<secret>` service key + `svc#<id>` record | built in T3/T4 |
| 12 | A: non-goals confirmed | |

Spec table: all T-D1 to T-D12 proposals are accepted as proposed (Status = Accepted), except the values in 7 below, which you approve with T1.

## 7. Final Boundary Document for T1

**Goal of T1:** a Chatwoot webhook that verifies the signature, dedupes by `message_id`, answers 200 at once and hands the message to a worker asynchronously. The worker only records the result. No AI, no Chatwoot API call, no student code.

**Work (teacher repo, local only):**
1. `lambda/chatwoot_core.py` (pure, no AWS imports): parse payload, check `X-Chatwoot-Signature` over `<timestamp>.<raw body>` (constant-time, timestamp window), loop guard (`message_type == "incoming"`, `private == false`, `sender.type != "agent_bot"`, status `pending`).
2. `lambda/chatwoot_api.py`: receiver handler (`POST /hooks/chatwoot/{agentId}`) and worker handler (claims the message atomically, records `processed`).
3. `template.yaml`: `UseChatwoot` condition (default off), own `HttpApi` with its own throttle, receiver function, worker function with explicit `EventInvokeConfig`, new DynamoDB table (TTL, PITR, conditional dedupe), narrow IAM (receiver may invoke only the worker; both read only the `chatwoot/*` secrets), output with the webhook URL.
4. `config/settings.json` entries, then `python tools/gen_settings.py`. **Proposed values (you approve them with T1):** body cap 256 KB, timestamp window 300 s, dedupe TTL 7 days, API throttle 20 rps burst 40, worker 512 MB and 120 s, worker retries 0 (own claim record decides).
5. Tests: `tests/test_chatwoot_core.py`, `tests/test_chatwoot_api.py`, `tests/test_template_chatwoot.py`, new steps in `tests/run.mjs`. The `secretsmanager:` count pinned in `tests/test_template_ai.py:45` is updated on purpose (new statement for `chatwoot/*`).
6. Docs: `docs/TECHNICAL_SPECIFICATION.md`, `CHANGELOG.md`, `docs/SETTINGS.md`.

**Hard constraints for T1:** everything in section 3 above, plus: only new files and additive edits to `template.yaml`, `config/settings.json`, `tests/run.mjs`, the two guard tests and the docs; no change to `app.py`, `portal.py`, `gateway.py`, `knowledge.py`, `student/`, `starter/`. Local git only in the teacher repo: commits on its current branch with the trailer lines, no push, no tag, no release, no AWS call. The signature secret in tests is a fake constant.

**Verified before T1 code (read-only):** re-read `webhook_job.rb` for the exact retry line and the live payload shape (`conversation.id` vs `display_id`) from Chatwoot source; confirm `node tests/run.mjs` is green on the untouched repo first (baseline).

**T1 gate:** `node tests/run.mjs` fully green; `cfn-lint` and `checkov` are CI-only (run by you if installed); you deploy the stack yourself with `UseChatwoot` on and create the `chatwoot/<tenant>` secret; then: a test message from your widget is recorded once as processed, a re-sent delivery is recorded as duplicate. I stop and report.

**Not in T1:** reply to Chatwoot (T2), mapping and per-business secrets UI (T3), runtime (T4), handoff (T5), Studio link (T6).

**Open before T2:** real Chatwoot version (Q5), ConcurrentExecutions (Q7).

---

## 8. T1 result (2026-11-19)

Built in the teacher repo as ONE local commit `a8dfe3a` on `main` (not pushed, not deployed, no AWS call). 15 files: `lambda/chatwoot_core.py`, `lambda/chatwoot_api.py`, `template.yaml` (switch `ChatwootEnabled`, table, HttpApi, receiver, worker, output), 11 settings, 3 new tests, docs/CHATWOOT_ADAPTER.md, spec 4.18 and F26, CHANGELOG (Unreleased).
Tests: untouched baseline 61 steps green; after T1 62 steps green (`node tests/run.mjs`). `cfn-lint` and `checkov` (CI only) are NOT installed here: the user runs them.
Verified from Chatwoot source before coding: retry_on 3 attempts every 3 s only for 429/500 on the agent-bot job; timeouts and connection errors are not retried; signature `sha256=<hex>` of `<timestamp>.<body>` sent only when the bot has a secret; webhook `message_type` is a word; **`conversation.id` is the dashboard number (display id)** and `conversation.status` is present, so T-D8 uses `conversation.id` as `conversation_id` in the Create Message path (confirm on a live payload in T2).
Deliberate choices to review: unknown agent and wrong signature both answer 401; T1 does not pass message text to the worker; the worker has no retries.
User steps for the T1 gate: deploy with `ChatwootEnabled=true`, create Secrets Manager secret `chatwoot/<agent>` = `{"bot_secret": "..."}`, point the Chatwoot bot webhook at `<ChatwootWebhookUrl>/<agent>`.
