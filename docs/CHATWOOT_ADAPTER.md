# Chatwoot Agent Bot adapter

> **Status: steps T1 and T2 of 6. Off by default.** A customer message from a Chatwoot inbox reaches the platform, is checked, de-duplicated and handed to a worker, and the worker posts **one placeholder reply** ("Received (message 501)") to the conversation and records the outcome. **No AI is used yet**: the agent runtime (answers from books, the agent loop) is a later step. Nothing about students, sites, backends or any existing feature changes.

## What it is for

Chatwoot can send a webhook to an *Agent Bot* for every message in an inbox the bot is connected to (website chat, WhatsApp, Instagram, Facebook). This adapter is the address that webhook points to. It is a platform feature of the teacher's AWS stack, not a student project: a separate Studio application (or anything else) talks to it only over HTTP.

## The flow

```
Chatwoot --POST /hooks/chatwoot/<agent name>--> own HTTP API (own throttle) --> receiver function
   receiver: read the bot secret (Secrets Manager: chatwoot/<agent name>)  ->  check the signature and the time
             ->  ignore what the bot must not answer  ->  write ONE item per message (conditional)
             ->  start the worker asynchronously  ->  answer 200
   worker:   claim the message once  ->  read the bot's token and account id (same secret)  ->  refuse another account
             ->  POST Create New Message to the one Chatwoot address  ->  record replied (reply id) or failed (reason)
```

* **Signature.** When a bot has a secret, Chatwoot adds `X-Chatwoot-Timestamp` and `X-Chatwoot-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<raw body>`. The receiver recomputes it over the raw bytes, compares in constant time, and refuses a time more than 5 minutes (setting `chatwoot.timestampToleranceSeconds`) from its own clock. A call with a wrong, missing or old signature, and a call for an agent with no secret, all get the same `401`.
* **What is ignored** (answered `200` with `ignored`, never stored): events other than `message_created`; any message that is not `incoming` (the bot's own replies and the human agents' replies are sent back to the bot too, so the reply of step T2 is ignored when it comes back); private notes; messages sent by an agent bot; and conversations that are not `pending` (a human has taken them over).
* **Repeats.** The first delivery of a message writes `msg#<agent name>#<message id>` with a conditional write and starts the worker. A second delivery of the same message (Chatwoot retries on `429` and `500`, and sometimes sends twice) only adds one to a counter. If the worker cannot be started the item is removed and the answer is `500`, so Chatwoot's retry is accepted.
* **One run, one reply.** The worker moves the item from `received` to `processing` with a conditional update; an event delivered twice, or a message with no item, does nothing. The platform never retries the worker by itself (`MaximumRetryAttempts: 0`).
* **Timing.** Chatwoot waits 5 seconds and retries only on `429` and `500`; the receiver answers in milliseconds and its function limit is 10 seconds.

## The reply (step T2)

The worker posts `POST <ChatwootBaseUrl>/api/v1/accounts/<account>/conversations/<conversation>/messages` with `{"content": "Received (message <id>)", "message_type": "outgoing", "private": false}`, using **the agent bot's own token**: `Authorization: Bearer <token>` (Chatwoot 4.19 and later) or the older `api_access_token: <token>` header (parameter `ChatwootAuthStyle`). `<conversation>` is the number Chatwoot shows in its dashboard (the webhook's `conversation.id`).

* **One address only.** The worker calls the address in `ChatwootBaseUrl` (https, no path) and nothing taken from a webhook or from the secret. Redirects are not followed, so the token can never travel to another address. An answer is read only up to a small cap and never logged.
* **Account check.** A bot token may write in any conversation of its Chatwoot account. The secret therefore holds the account id, and the worker refuses (`account_mismatch`) a message of any other account before sending anything.
* **No automatic retry.** Chatwoot does not de-duplicate messages (`echo_id` and `source_id` are not unique), so a second POST after an unknown outcome could answer a customer twice. A failure is recorded and raised so it is visible: `http_401`, `http_403`, `http_404`, `http_422`, `http_429`, `http_5xx`, `http_other`, `timeout`, `network`, or a refusal before sending: `bad_base_url`, `bad_setting`, `no_secret`, `secret_unavailable`, `no_token`, `bad_secret`, `account_mismatch`, `error` (a bug). A `processing` item that never finishes (a crash between the POST and the record) is left for a later step to sweep.
* **Nothing leaks.** The token is read only by the worker (the receiver never reads it, the worker never reads `bot_secret`) and never appears in a log line, an item or an event.

## What is stored and logged

Stored (one small item per message, expiring after `chatwoot.dedupeTtlDays`, 7 by default): the message, conversation, account and inbox ids, the agent name, Chatwoot's delivery id, a status (`received`, `processing`, `replied`, `failed`), the reply's message id or a reason code, and times. **Never** the text of a message, a customer's name, e-mail, a token or any other personal data. Logged: event names, agent names, reason codes and ids. **Never** a secret, a token, a signature or a message.

## Turning it on (the teacher)

1. `ChatwootEnabled=true` (installer: `plan` with the Chatwoot feature, then `apply`), with `ChatwootBaseUrl=https://<your Chatwoot server>` and, for a Chatwoot older than 4.19, `ChatwootAuthStyle=api_access_token`. It needs no student backends. The stack then has an HTTP API, a table and two functions, and an output `ChatwootWebhookUrl`.
2. For each agent create a Secrets Manager secret named `chatwoot/<agent name>` (lowercase letters, digits and `-`, up to 40) whose text is `{"bot_secret": "<the secret Chatwoot shows for the bot>", "api_token": "<the agent bot's token>", "account_id": <the Chatwoot account number>}`. Both functions may read secrets with that name prefix only.
3. In Chatwoot: *Settings, Bots, Add bot*, outgoing URL = `ChatwootWebhookUrl` + `/` + the agent name, then connect the bot to an inbox. Copy the bot's secret and its token into the secret above.

A secret that is missing or has no `bot_secret` field makes every call for that agent `401`; one without a usable `api_token` or `account_id` makes the reply fail with a reason. A changed secret takes effect within `chatwoot.secretCacheSeconds` (5 minutes by default).

## Limits and settings

All in `config/settings.json` (see [SETTINGS.md](SETTINGS.md)): largest body (256 KB), signature age (300 s), how long a message is remembered (7 days), how long a secret is remembered (300 s), how long the worker waits for Chatwoot (10 s), the Chatwoot address, the header style, the API's own throttle (20 per second, burst 40), and the memory and time of the two functions.

## Permissions

The receiver may `PutItem`, `UpdateItem` and `DeleteItem` in its own table, start the one worker function, and read secrets named `chatwoot/*`. The worker may `UpdateItem` in the table and read secrets named `chatwoot/*`, and it makes calls out to the one Chatwoot address; nothing else. No other function, no student role and no existing feature gains anything (`tests/test_template_chatwoot.py`).

## Not built yet

T3 maps an inbox to an agent and keeps one set of keys per business; T4 runs the agent (answers from books, the agent loop, traces) instead of the placeholder; T5 hands a conversation to a person when anything fails and sweeps stuck items; T6 connects the Studio application. Each step is approved on its own. Nothing here has been tried against a real Chatwoot yet: the tests use stand-ins (see below), and the exact 401, 422 and 429 answers of Chatwoot and whether a Chatwoot older than 4.19 accepts the Bearer header are to be confirmed on a real server.

## Tests

`tests/test_chatwoot_core.py` (the signature Chatwoot builds, the loop guard, the reply request and how its answer is read), `tests/test_chatwoot_api.py` (receiver, worker, secret cache and the conditional writes, on in-memory fakes), `tests/test_chatwoot_http.py` (the real HTTP client against a server on the same computer: headers, body, no redirect, answer cap, timeout), `tests/test_template_chatwoot.py` (off by default, scoped permissions). All run in `node tests/run.mjs`.
