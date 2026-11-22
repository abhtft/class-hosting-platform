# TR0: traceability for Chatwoot agents (read-only audit and boundary)

Status: audit done 2026-11-22, no code changed. Decisions D1 to D5 were accepted by the owner (see section 6). Next: TR1 after the owner reads this.

## 1. What already exists (teacher platform, live: `TracesEnabled=true`)

| Piece | Where | Reusable for bots? |
|---|---|---|
| Trace format: summary + spans, OpenTelemetry/OpenInference shape | `lambda/trace_core.py`, `docs/TRACE_SCHEMA.md` | Yes, as is (`make_span`, `apply_level`, `mask_text`, `span_sort_key`) |
| Privacy levels `metadata` / `masked` / `full`; Indian PII masking (phone, Aadhaar, PAN, UPI, IFSC, GSTIN, email) | `trace_core.apply_level` | Yes |
| Store: `TracesTable` (`s` = `t#<slug>#<trace_id>`, sort key `summary` or `<ts>#<source>#<salt>`; GSIs `by-app`, `by-day`; per-item TTL) | `lambda/traces.py` (`DynamoTraces`), `template.yaml` | Yes: the table is generic, `app` is just a string |
| Daily rollup per `app` and a drift check (refusal-rate jump, error rate) that emails one alert a day | `traces.compute_day`, `check_drift`, `daily` (called from `app.sweep`) | Yes, automatically, once bots write summaries with an `app` value |
| Teacher routes: list, one trace, delete, export, daily view | `lambda/portal.py` lines 631 to 662 | Partly (see 3.3) |

## 2. What the Chatwoot adapter keeps today

Per customer message the worker writes `msg#<agent>#<message_id>` in `ChatwootTable` with `status`, `reason` and the answer's `metrics`: route, confident, hits, tokens, cost_micro, retrieval_ms, llm_ms, `sources` ([doc, page]), media and hand-over flags (`chatwoot_api.worker`, `store.finish`). Expires after `chatwoot.dedupeTtlDays`. No customer text, no passage scores, no extras that matched, no model name, no per-step timing. `explain` (passages with scores) exists only in Studio's dry run.

## 3. Coupling points that stop "just call `traces.record`"

1. **`record()` needs a student token item and a slug.** It reads settings from `item["traces"]` and counts the daily limit with `portal.db().counter_add` on `TokensTable`. The Chatwoot worker has neither (its table is `ChatwootTable`; its role cannot touch `TokensTable`). Fix: split `record` into a pure writer taking `(owner, settings, limiter)`; the student path stays as it is.
2. **Namespace safety.** Student slugs match `^[a-z0-9][a-z0-9-]{0,29}$` (`portal.SLUG_RE`). A bot owner key `cw:<tenant>` contains a colon, so no student can ever own it, and `student_list` / `student_get` (which build the partition from the signed-in student's slug) cannot reach it. This holds only if nothing else normalises or builds keys from user input: verified for `traces.py`; `portal.py` line 654 (teacher route) uses the slug pattern and would 404 on `cw:` (see 3.3).
3. **Teacher routes.** `GET /api/admin/traces/{slug}/{id}` and `DELETE` accept only the slug pattern; `?slug=` on the list is free text. Bots' traces would list (via `?slug=cw:poc`) but their detail would 404. Fix: one extra branch for `cw:` ids (teacher-only). The teacher's Traces page then shows bots without further change.
4. **Drift alert includes everything.** `check_drift` judges every `app`, so bots would start emailing at once. Owner decision D4: bots are excluded from the email for now, rollups stay. Fix: a prefix filter in `check_drift` (setting `traces.driftSkipPrefixes`, default `cw:`).
5. **IAM.** The worker role has no `TracesTable` access and no `TRACES_TABLE` variable. Needs: `dynamodb:PutItem` and `UpdateItem` on `TracesTable`, `LeadingKeys` `t#cw:*`, only `!If UseTraces`. `UseTraces` requires `UseBackend`; Chatwoot can run without it, so tracing for bots is silently off in that case (acceptable; logged once). The admin (read) role needs `Query`/`GetItem` on the table and `by-app` index.
6. **Settings per tenant.** Level and retention have no home for a tenant. Proposal: `tenant["traces"] = {enabled, level, retention_days}` on the tenant item, set by the teacher (`chatwoot_ctl set-traces`), default `masked` and 30 days (D2, D3), validated with `trace_core.validate_settings`.
7. **Daily limit.** The student limit counts new traces on `TokensTable`. For bots use a counter in `ChatwootTable` (same atomic pattern as `usage_add`); limit per tenant, new setting `chatwoot.traceDailyLimit`.
8. **Privacy duties.** Masked level stores customer text. A tenant-wide "delete all traces of this business" (query `by-app`, delete each) and a note in the Studio Activity tab ("customer messages are kept for N days, phone numbers and similar are hidden") are required before turning `masked` on.
9. **Backups leave out traces by design** (`docs/STATUS.md`); the same applies to bots.

## 4. What a bot trace holds (proposal)

Owner key `cw:<tenant>`; trace id `tr_` + 16 hex, also saved in the `msg#` item so a message links to its trace.

| Span | kind | Attributes (text keys only at masked and full) |
|---|---|---|
| `retrieval` | retriever | `query`*, shelves, `hit_count`, `confident`, `hits` (doc, page, section, score), ms |
| `extras` | retriever | how many corrections matched and their scores; text* |
| `llm` | llm | model, provider, prompt/completion tokens, cost_micro, finish_reason, latency, `messages`*, `answer`* |
| `guard` | guard | route (rag, fallback, handover), hand-over reason, `not_sure` |
| `media` | tool | ids sent, together-with-text or separate |
| `chatwoot` | other | post status and latency (no token, no secret) |

Summary fields beyond today's: `agent`, `version`, `channel`, `route`. They live on the summary only (so the indexes never hold text). Never stored: bot secret, API token, signature, Chatwoot account details beyond ids.

## 5. Phases and checkpoints (stop after each)

| Phase | Work | Done when | Needs approval |
|---|---|---|---|
| TR1 | Refactor `record` into a pure writer; worker writes the spans above (never raises, never delays the reply); tenant `traces` settings; counter; `msg#` links the trace id; tests (levels, masking of a phone number, daily limit, failure of the store does not break the reply) | A fake message produces the six spans at `masked` and only numbers at `metadata`; a broken store still sends the reply | Change set (IAM, env var): yes, before executing |
| TR2 | Admin API for the owner: `GET /admin/agents/{name}/traces` (day, route filter, paging), `GET .../traces/{id}`, tenant isolation; teacher route branch for `cw:`; `check_drift` skip prefix; tenant "delete all traces" command | A second tenant's key gets 404 for the first tenant's trace; the teacher's page shows a bot trace | Deploy: yes |
| TR3 | Studio **Activity** tab: list by day with route chips, one trace with its steps, "Make a test row from this answer" | A bad answer becomes a row in Your tests in two clicks | Studio deploy: yes |
| TR4 | Rollup per agent and version (fallback and hand-over rate, cost per day), shown on Overview | Version 9 and version 10 rates side by side | Deploy: yes |

Cost: on-demand DynamoDB, well under USD 5 a month per 100,000 answers (teacher doc). The checkpoint list repeats the owner's rule: no deploy without "Yes, execute".

## 6. Owner decisions (accepted 2026-11-22)

| # | Decision | Answer |
|---|---|---|
| D1 | Where bot traces live | Same `TracesTable`, owner key `cw:<tenant>` |
| D2 | Default privacy level for a business | `masked` |
| D3 | Retention | 30 days |
| D4 | Drift alert email for bots | Rollups on, email off for now |
| D5 | Studio tab | "Activity" |

## 7. Open questions found by this audit (to confirm at TR1 start)

1. Is `UseTraces` true while the Chatwoot worker needs it (`BackendEnabled=true` today: yes)? If the owner ever turns the student backend off, bot traces stop.
2. The worker's reply path must stay fast: the plan writes spans after the Chatwoot POST succeeded, in the same invocation, with a short timeout. Is one extra DynamoDB round trip (about 6 small writes) acceptable? Alternative: one batched write at the end (recommended).
3. Should `full` level be selectable by a tenant owner in Studio, or only by the teacher? Proposal: teacher only.
