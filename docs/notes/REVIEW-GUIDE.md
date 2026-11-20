# Review guide: Chatwoot adapter (teacher repo `D:\Github_Codes\class-hosting-platform-github`)

Date: 2026-11-19. Branch `main`, **local commits only** (nothing pushed, deployed or tried against a real Chatwoot or AWS). Untracked `study-guide/` in that repo is not mine.
Review one commit at a time: `git show --stat <hash>` then the files in the order below. All tests: `node tests/run.mjs` (62 steps, no AWS needed). One adapter test alone: `python tests/test_chatwoot_api.py` (set `AWS_DEFAULT_REGION=us-east-1` first).

## The five commits (oldest first)

| # | Hash | What it adds | Read first | Tests that prove it |
|---|---|---|---|---|
| T1 | `a8dfe3a` | Webhook receiver: signature check, loop guard, dedupe by message id, 200 at once, async worker records the message. Off by default (`ChatwootEnabled`). | `lambda/chatwoot_core.py`, then `chatwoot_api.py` (`handler`, `worker`), then the Chatwoot block of `template.yaml` | `test_chatwoot_core.py`, `test_chatwoot_api.py`, `test_template_chatwoot.py` |
| T2 | `ebb0ff8` | Worker posts ONE placeholder reply with the agent bot's own token; account check; no retry; failure codes. Parameters `ChatwootBaseUrl`, `ChatwootAuthStyle`. | `chatwoot_core.py` (reply functions), `chatwoot_api.py` (`post_json`, `answer`, `worker`) | `test_chatwoot_api.py` (worker part), `test_chatwoot_http.py` (real client vs a local server) |
| T3a | `fab21cf` | Registry (tenant, agent, version items); the receiver and worker serve only a registered, enabled agent for its account and inboxes; table code moved to `chatwoot_store.py`; IAM tightened to message items. | `lambda/chatwoot_registry.py` (`check_mapping`, `Registry`), `chatwoot_store.py` | `test_chatwoot_registry.py`, enforcement matrix in `test_chatwoot_api.py` |
| T3b | `3895be5` | Studio-facing admin API: service key per business, agents, immutable versions, live version; third function that can touch registry items only. | `lambda/chatwoot_admin.py` (`authenticate`, `put_agent`, `put_version`, `route`) | `test_chatwoot_admin.py`, admin block of `test_template_chatwoot.py` |
| T3c | `2b03f7e` | Teacher's local command `tools/chatwoot_ctl.py` (create tenant, show key once, rotate, enable, list, agent-set); docs, spec 4.18, CHANGELOG. | `tools/chatwoot_ctl.py`, `docs/CHATWOOT_ADAPTER.md` | `test_chatwoot_ctl.py` |

## What each test file proves (one line each)
- `test_chatwoot_core.py`: the signature is exactly Chatwoot's (`sha256=` + HMAC of `<timestamp>.<raw body>`), stale/forged/odd calls are refused, only a customer's message in a `pending` conversation is accepted, the reply request has the right path/body/header, Chatwoot's answers map to short codes.
- `test_chatwoot_api.py`: one hand-over and one reply per message, repeats only counted, nothing a customer wrote or any token is stored or logged, 401 for forged and unknown alike, 500 (not 200) when something is down, every failure code, no retry, the registry is checked before storing and again before replying.
- `test_chatwoot_http.py`: the real HTTP client sends the right request, never follows a redirect, caps the answer, times out.
- `test_chatwoot_registry.py`: service-key shape and hashing, validation, the serving rule, the few-second cache, the table's conditional writes and number handling.
- `test_chatwoot_admin.py`: key access (all bad keys are the same 401), tenant isolation (404), name clashes, optimistic concurrency, immutable versions, half-done publish, limits, secrecy.
- `test_chatwoot_ctl.py`: the key is shown once and works, rotation kills the old key, disable kills the tenant, list/show never print a key or a hash.
- `test_template_chatwoot.py`: off by default; each function has exactly the permissions listed in `docs/CHATWOOT_ADAPTER.md`; no other function knows about the adapter.

## Decisions: cheap to change vs expensive to change
**Cheap (a setting, a constant, one function):** placeholder reply text (`REPLY_TEXT` in `chatwoot_core.py`); all limits (`config/settings.json`); cache times; 401 wording; whether an unmapped agent is `ignored` or an error; header style (parameter).
**Medium (a few files + tests):** reason codes; item layout inside the table; version size/number limits; whether `account_id` also stays in the secret (today both the registry and the secret are checked).
**Expensive (touches the data model, IAM and tests):** one table with prefixes `msg#`, `tenant#`, `agent#` (a second table would be cleaner for retention but changes every store call); the list of agent names inside the tenant item (cap 20; replaced by an index if you ever need thousands); per-agent webhook URL instead of one universal URL; the worker having no automatic retry (Chatwoot cannot de-duplicate).

## What is NOT verified (be sceptical here)
- Nothing ran against real Chatwoot or AWS. `cfn-lint` and `checkov` (CI tools) were not installed here: run them on `template.yaml`.
- Chatwoot facts come from its source on the `develop` branch (not a tagged release) and public docs: the 401/422/429 response bodies, whether Bearer works below 4.19, the per-conversation message-flood limit, and whether an agent bot token may post in a conversation of an inbox it is not connected to (the source suggests it may: hence the account and inbox checks) are unconfirmed.
- `conversation.id` in the webhook is used as the conversation number in the API path (the source says it is the dashboard number): confirm on a live payload.
- Lambda concurrency of your account (`aws lambda get-account-settings`) is unknown: needed before the runtime (T4).
- A `processing` item whose worker crashed after the POST is left as is (T5 will sweep).

## Your combined live test (suggested order, smallest first)
1. `cfn-lint -i W1030 W2506 -t template.yaml` and `checkov -f template.yaml --config-file .checkov.yaml`; `node tests/run.mjs`.
2. Deploy the stack with `ChatwootEnabled=true`, `ChatwootBaseUrl=https://<your Chatwoot>` (and `ChatwootAuthStyle=api_access_token` if Chatwoot is older than 4.19). Note the outputs `ChatwootWebhookUrl` and `ChatwootTableName`.
3. `python tools/chatwoot_ctl.py --table <ChatwootTableName> create-tenant <id>`: keep the printed key.
4. Secrets Manager: `chatwoot/<agent>` = `{"bot_secret": "...", "api_token": "<the agent bot token>", "account_id": <number>}`.
5. `python tools/chatwoot_ctl.py --table <...> agent-set <agent> --tenant <id> --account <number> --inboxes <inbox number>`.
6. Chatwoot: add an Agent Bot with outgoing URL `<ChatwootWebhookUrl>/<agent>`, copy its secret into step 4, connect it to that inbox.
7. Send a message from the website widget. Expect: one reply "Received (message N)" in the conversation; the table item `msg#<agent>#<N>` with status `replied` and a `reply_id`. Send the same payload again (or let Chatwoot retry): `duplicates` goes up, no second reply.
8. Negative checks: switch the agent off (`agent-set <agent> --tenant <id> --enabled false`), wait 15 s, send a message: no reply, log line `ignored not_mapped`. Wrong `api_token`: item `failed` with `http_401`. Wrong `account_id` in the secret: `account_mismatch`. A `ChatwootBaseUrl` typo: `network` or `timeout`.
9. Admin API (with the tenant key): `curl -H "Authorization: Bearer <key>" <ChatwootWebhookUrl without /hooks/chatwoot>/admin/agents` should list the agent; another tenant's key must not see it.
Where to look when something fails: the Lambda logs of `ChatwootReceiverFunction`, `ChatwootWorkerFunction`, `ChatwootAdminFunction` (lines start with `{"chatwoot": ...}` or `{"chatwoot_admin": ...}`; they hold reason codes and ids, never secrets), and the item's `reason` field in the table.

## Still to build (each needs its own approval)
T4 the runtime (answers from books and the agent loop using the live version; needs the tenant knowledge scope and your Lambda limit), T5 hand-over to a person on failure plus a sweeper for stuck items, T6 the Studio screens that call the admin API. In `D:\Github_Codes\Test\studio` the Studio side stands at C2 (empty shell, committed, push to the platform still waiting for your yes); C3 sign-in to C8 are not started.

---

# T4 additions (2026-11-19): the worker answers from the business's books

Commits (teacher repo, local, on top of `2b03f7e`): **T4a** `98ead0a`, **T4b** `6522033`, **T4c** `e604922`, **T4d** `3c78a16` (docs included). Full suite 62 steps green after T4a, T4b and (at the end) T4d; T4c was checked with the Chatwoot tests, the spec/settings/guard tests and the CLI test only (it changes just the tool and its test).

| # | What it adds | Read first | Tests |
|---|---|---|---|
| T4a | `lambda/chatwoot_runtime.py`: config check, fenced passages, prompt, cleaned reply, multi-shelf search over `knowledge/tenant/<tenant>/<shelf>`, the books route, the fallback; the ONE edit to existing platform code: `"tenant"` in `knowledge_store.SCOPES` | `chatwoot_runtime.py` (`clean_config`, `build_messages`, `respond`) | `test_chatwoot_runtime.py` |
| T4b | Worker integration: the customer's words go to the worker in the async event only; live version read; monthly cap per business (`usage#<tenant>#<yyyymm>`, default 10 USD); numbers-only metrics on the message item; failure codes; template: worker 1024 MB, read-only `knowledge/tenant/*`, the AI secret | `chatwoot_api.py` (`compose`, `answer`, `worker`), the worker block of `template.yaml`, `chatwoot_store.py` (`usage`, `usage_add`) | `test_chatwoot_api.py` (new sections), `test_template_chatwoot.py` |
| T4c | `tools/chatwoot_ctl.py`: `knowledge-sync` (folder to shelves), `eval` (real searches), `set-cap` | `tools/chatwoot_ctl.py` | `test_chatwoot_ctl.py` |
| T4d | `lambda/chatwoot_agent.py`: the budgeted agent loop (port of the kit's `class_agent`, read-only tools `search_books` and `calculate`), the router hook (`make_runtime`), docs, spec, changelog | `chatwoot_agent.py` (`run`, `answer`), `chatwoot_runtime.py` (`respond`, `make_runtime`) | `test_chatwoot_agent.py`, the agent test in `test_chatwoot_api.py` |

## Decisions you can still change (T4)
**Cheap:** the model's rules text (`build_messages` in `chatwoot_runtime.py`); every limit (`config/settings.json`, group Chatwoot adapter: 37 settings); the default cap (10 USD); reply cleaning; the fallback behaviour for an image-only message.
**Medium:** the live-version configuration fields (`clean_config`); the metrics fields on the message item; when the agent loop starts (today: books not sure AND mode `auto`); the two agent tools.
**Expensive:** books in S3 under `knowledge/tenant/...` with the platform's ranking (changing the store changes every student shelf too); the cap and spend counters living in the adapter's own table; the customer's words travelling in the async event (the alternative is fetching the message from Chatwoot); the single model (Gemini flash-lite with the platform key: there is no per-tenant model choice).

## Not verified (T4), on top of the earlier list
- Never run against a real bucket, a real Google key (embeddings and Gemini) or a real Chatwoot. The tests use a hash-based fake embedding: they prove the plumbing and the privacy rules, **not the quality of answers** on real books. Run `eval` on your real shelves before trusting any answer.
- The confidence gate (`min_similarity` 0.55, `min_coverage` 0.6) is the English-tuned default. For Hindi (Devanagari) the keyword half of the search finds nothing (the tokenizer only knows a-z and 0-9) and the meaning half decides alone; Hinglish in Roman letters is treated as English. Use `eval` cases in your languages; a Hindi-aware keyword search is a separate step.
- Short customer messages ("hi", "price?") will often fall below the gate and get the fallback text. There is no greeting policy yet.
- A cold worker may need several seconds to load a shelf (an unmeasured cold start of about 10 s is noted in the platform's own docs); the worker has 120 s.
- Concurrency of your AWS account is still unknown; every message holds a worker for the whole model wait.
- Costs: Gemini flash-lite prices are fixed in code (0.30 input, 2.50 output USD per million tokens); query embeddings are priced at 0.15 USD per million tokens. The 10 USD cap is a per-business ceiling checked before each answer (one answer can overshoot it).

## Additions to your live test (after step 4 of the earlier list)
1. Put a few real books in `D:\Github_Codes\Test\studio\knowledge\<tenant>\<shelf>\` (PDF, text, Markdown or HTML; keep that folder out of git and out of `frontend/`).
2. `python tools/chatwoot_ctl.py --table <ChatwootTableName> --bucket <sites bucket> --secret-name <AiSecretName> knowledge-sync --tenant <id> --dry-run`, then without `--dry-run`.
3. `eval` with a `cases.json` such as `[{"question": "What are your opening hours?", "expect": "nine"}, {"question": "Can I return a used item?", "expect": "return"}, {"question": "Explain quantum physics", "refuse": true}]` and your own Hindi/Hinglish questions; read the `best=` scores to decide whether the gate needs tuning.
4. Publish a version through the admin API (or any JSON body via `curl -X PUT -H "Authorization: Bearer <key>" -d '{"config": {...}}' <admin url>/admin/agents/<agent>/versions/1`) with a configuration such as `{"instructions": "Answer like a polite shop assistant.", "language": "hinglish", "collections": ["<shelf>"], "fallback_text": "Sorry, I will ask our team to help you.", "max_answer_chars": 500, "mode": "auto"}`, then `POST /admin/agents/<agent>/live` with `{"version": 1}`.
5. Send a message the books answer, one they do not, and a two-part question ("opening hours and the price of two shirts and a belt"): look at the reply and at the message item's `route`, `confident`, `cost_micro`, `sources`. Set a tiny cap (`set-cap <tenant> 0`) and check you get `cap_reached` and no reply.
