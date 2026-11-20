# T2 plan: the worker replies to Chatwoot (DRAFT, approval pending, no code)

**Date:** 2026-11-19. **Teacher repo:** untouched until you approve. **Testing:** you will test everything together after all implementation; so T2 is built and checked only with stubbed HTTP (no live Chatwoot call by me).

## 1. What T2 does (and does not)
The worker (today it only records) calls Chatwoot's **Create New Message** API once per accepted message and records `replied` (with Chatwoot's reply message id) or `failed` (with a short reason). The reply is a **placeholder** (see Q1): the real answer comes in T4. Not in T2: AI, books, handoff to a human (T5), inbox-to-agent mapping UI (T3), Studio link (T6).

## 2. Facts (Chatwoot source, 2026-11-19; develop branch, not a tagged release)
- `POST {base}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages`, JSON `{"content", "message_type": "outgoing", "private": false}`; success 200 with the message (`id`). `conversation_id` is the dashboard number (the webhook's `conversation.id`).
- An agent bot posts with **its own token**: header `Authorization: Bearer <token>` (Chatwoot 4.19+) or the older `api_access_token: <token>`. `messages#create` is on the list of endpoints an agent-bot token may call. A bot passes the conversation policy without an inbox check (it can write in any conversation of its account whose id it knows), so **the platform must check the account itself** (Q8).
- The reply fires a `message_created` webhook for the bot's own message (the loop risk). T1's guard (`message_type` outgoing, sender `agent_bot`) already ignores it. A bot's outgoing message does **not** change the conversation status (it stays `pending`).
- **No deduplication** in Chatwoot (`echo_id` and `source_id` are not unique). So a repeated POST after an unknown outcome would double-reply: no blind retries (Q5).
- Rate limits: 3,000 requests a minute per IP (global); a per-conversation message-flood guard exists (limit value unconfirmed).
- Unconfirmed (to check on the real server at the final test): the exact 401/422/429 response bodies; whether Chatwoot below 4.19 accepts Bearer.

## 3. Intent capture (reply with e.g. "all recommended" or per number)

**Scope**
1. Placeholder reply text? A) Fixed acknowledgement with the message number, e.g. "Received (message 501)": customer text is never handled **(recommended)** B) Echo of the customer's text (first 200 characters; the text travels only in the async event, never stored) C) A fixed text per agent kept in the secret.
2. Which message type? A) Public reply (`outgoing`, `private: false`) **(recommended)** B) Private note only (safe for a first live test, the customer sees nothing).

**Depth**
3. Authentication header? A) Bearer only (needs Chatwoot 4.19+) B) A deployment setting `bearer` (default) or `api_access_token` for older servers **(recommended: your Chatwoot version is still unknown)**.
4. Where does the Chatwoot address come from? A) One template parameter `ChatwootBaseUrl` (https only; one Chatwoot, as decided in D2; the worker can reach only that host) **(recommended)** B) Per agent inside its secret (flexible, but the platform would call an address read from data).

**Constraints**
5. If the Chatwoot call fails or times out? A) No automatic retry: record `failed` with a reason (`http_401`, `http_404`, `http_422`, `http_429`, `http_5xx`, `timeout`, `network`), log it, let T5 hand the conversation to a person **(recommended: no dedupe in Chatwoot, a retry could reply twice)** B) One retry only on HTTP 429 C) Retry on timeouts too (may double-reply).
6. Check that the conversation is still `pending` just before replying? A) No, T1 checked at webhook time; T5 covers a human taking over **(recommended)** B) Yes, with a GET first (one more call, more latency, one more permission to confirm).

**Edge cases**
7. Account safety: the bot token can write in any conversation of its account. A) The secret holds `account_id`; the worker refuses (records `failed: account_mismatch`) when the message's account differs **(recommended)** B) Skip until T3 (inbox mapping).
8. What is stored after a reply? A) Status `replied`, the reply's message id, time; nothing else **(recommended)**.

**Non-goals (confirm):** no AI, no books, no human handoff, no attachments, no buttons or cards, no typing indicator. A) Confirmed.

## 4. Spec table (my proposals; fill "User's Answer" and "Status")
| Decision Point | Proposed Answer | User's Answer | Status |
|---|---|---|---|
| T2-D1 Reply text | Fixed acknowledgement with the message number | | |
| T2-D2 Message kind | Public `outgoing`, `private: false` | | |
| T2-D3 Token | The agent bot's own token, in the existing secret `chatwoot/<agent>` as `api_token` (plus `account_id`); never logged, never sent anywhere but Chatwoot | | |
| T2-D4 Header | Setting `ChatwootAuthStyle`: `bearer` (default) or `api_access_token` | | |
| T2-D5 Address | Parameter `ChatwootBaseUrl`, https only, no path, no credentials in it; the worker builds the path itself and encodes ids as integers | | |
| T2-D6 HTTP client | Python standard library `urllib.request`, own timeout setting (10 s), no redirects followed, response body read up to a small cap, never logged | | |
| T2-D7 Failure | No automatic retry; status `failed` plus a short reason code; Lambda still raises so the failure is visible | | |
| T2-D8 Account check | Refuse when the message's `account_id` differs from the secret's `account_id` | | |
| T2-D9 Idempotence | The worker's atomic claim (T1) is the only guard: one claim, one POST. A crash between the POST and the status write can leave `processing`; T5 will sweep these | | |
| T2-D10 Statuses | `received`, `processing`, `replied`, `failed` (T1's `processed` is replaced; T1 has not been deployed yet, so no data to migrate) | | |
| T2-D11 Template | Worker gets `secretsmanager:GetSecretValue` on `chatwoot/*` only (guard-test count 6 to 7) and the two environment variables; no VPC; no other permission | | |
| T2-D12 Settings | New registry entries: HTTP timeout (10 s), auth style, reply length cap (1000 characters), base URL parameter | | |
| T2-D13 Tests | Stubbed HTTP only: success, each error class, timeout, wrong account, missing token, header style, no redirect, no secret in log or in the stored item, one POST per message even if the worker event arrives twice | | |

## 5. Boundary document (T2)
**Hard constraints:** everything from the T1 boundary (no edits to `app.py`, `portal.py`, `gateway.py`, `knowledge.py`, `student/`, `starter/`; local git only; no push, tag, release or AWS call; fake secrets in tests; no message text or personal data stored or logged), plus:
1. The Chatwoot token is read only inside the worker, only from `chatwoot/<agent>`, and never appears in a log line, an exception text, an item or an event payload.
2. The platform calls exactly one host (the `ChatwootBaseUrl`), over https, never a URL taken from a webhook or a secret.
3. At most one POST per accepted message.
4. A reply never goes to a conversation of another account (T2-D8).
5. The T1 guard test for the worker is widened deliberately to exactly: UpdateItem on the table, GetSecretValue on `chatwoot/*`.

**Work:** `lambda/chatwoot_api.py` (a small client plus the worker body), `lambda/chatwoot_core.py` (build the request, classify the response), `template.yaml` (worker permission, two environment variables, one parameter), registry entries and regenerated files, tests, docs (`CHATWOOT_ADAPTER.md`, spec 4.18, CHANGELOG).

**Checkpoint T2 gate (local):** `node tests/run.mjs` fully green; `cfn-lint` and `checkov` run by you. The live round trip is part of your one combined test at the end.

## 6. About testing everything at the end (my advice, your call)
Deferring all live tests means a wrong assumption in T2 can be copied into T3 to T6. To keep that cheap: every step stays its own local commit, every external shape (Chatwoot's request and response) is written once in a test fixture taken from the source above, and the final test starts with the smallest path (T1 signature and dedupe, then T2 reply as a **private note** (Q2 B), then the rest). If you would rather have a 10-minute live check after T2 only (deploy, one widget message, one reply), say so; otherwise I implement T2 to T6 in order, one approval each, with local tests only.

---

## 7. Answers (2026-11-19)
| Q | Answer |
|---|---|
| 1 | A: fixed acknowledgement with the message number |
| 3 | B: setting `bearer` (default) or `api_access_token` |
| 4 | A: template parameter `ChatwootBaseUrl` (https, one Chatwoot) |
| 5 | A: no automatic retry; `failed` plus a reason code |
| 2, 6, 7, 8 and the non-goals | not asked separately: my recommendations stand unless you object (2 A public reply, 6 A no pre-check, 7 A account check against the secret, 8 A store reply id and time only) |

All T2-D1 to T2-D13 proposals above are therefore as written. **Waiting for your approval to implement T2** (local code and stubbed tests only; no push, no deploy, no live call). Your live test stays for the end, after T2 to T6.


---

## 8. T2 result (2026-11-19)
Built in the teacher repo (see its git log): the worker now posts one placeholder reply and records `replied` / `failed`. Local code and tests only: no push, no deploy, no live call.
Differences from the plan, on purpose: (1) no reply-length setting, because the reply is a fixed short text; (2) added `tests/test_chatwoot_http.py` (the real HTTP client against a local server: headers, body, no redirect, answer cap, timeout), because no live call is made before your final test; (3) the receiver and the worker each read only their own fields of the one secret (`bot_secret` vs `api_token` and `account_id`).
New for the final test: parameters `ChatwootBaseUrl` (https, required) and `ChatwootAuthStyle` (`bearer` default, or `api_access_token` for Chatwoot older than 4.19); the secret `chatwoot/<agent>` must now be `{"bot_secret": "...", "api_token": "<the agent bot's token>", "account_id": <number>}`.
Still unconfirmed against a real Chatwoot: 401/422/429 bodies, whether Bearer works below 4.19, and whether the per-conversation flood guard limit affects a reply.
