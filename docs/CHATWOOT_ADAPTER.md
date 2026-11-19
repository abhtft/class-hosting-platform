# Chatwoot Agent Bot adapter

> **Status: step T1 of 6. Off by default.** A customer message from a Chatwoot inbox reaches the platform, is checked, de-duplicated and handed to a worker, and the outcome is recorded. **Nothing is sent back to Chatwoot yet and no AI is used** (replying is step T2). Nothing about students, sites, backends or any existing feature changes.

## What it is for

Chatwoot can send a webhook to an *Agent Bot* for every message in an inbox the bot is connected to (website chat, WhatsApp, Instagram, Facebook). This adapter is the address that webhook points to. It is a platform feature of the teacher's AWS stack, not a student project: a separate Studio application (or anything else) talks to it only over HTTP.

## The flow

```
Chatwoot --POST /hooks/chatwoot/<agent name>--> own HTTP API (own throttle) --> receiver function
   receiver: read the bot secret (Secrets Manager: chatwoot/<agent name>)  ->  check the signature and the time
             ->  ignore what the bot must not answer  ->  write ONE item per message (conditional)
             ->  start the worker asynchronously  ->  answer 200
   worker:   claim the message once  ->  (T1: nothing)  ->  record processed or failed
```

* **Signature.** When a bot has a secret, Chatwoot adds `X-Chatwoot-Timestamp` and `X-Chatwoot-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<raw body>`. The receiver recomputes it over the raw bytes, compares in constant time, and refuses a time more than 5 minutes (setting `chatwoot.timestampToleranceSeconds`) from its own clock. A call with a wrong, missing or old signature, and a call for an agent with no secret, all get the same `401`.
* **What is ignored** (answered `200` with `ignored`, never stored): events other than `message_created`; any message that is not `incoming` (the bot's own replies and the human agents' replies are sent back to the bot too); private notes; messages sent by an agent bot; and conversations that are not `pending` (a human has taken them over).
* **Repeats.** The first delivery of a message writes `msg#<agent name>#<message id>` with a conditional write and starts the worker. A second delivery of the same message (Chatwoot retries on `429` and `500`, and sometimes sends twice) only adds one to a counter. If the worker cannot be started the item is removed and the answer is `500`, so Chatwoot's retry is accepted.
* **One run.** The worker moves the item from `received` to `processing` with a conditional update; an event delivered twice, or a message with no item, does nothing. The platform never retries the worker by itself (`MaximumRetryAttempts: 0`).
* **Timing.** Chatwoot waits 5 seconds and retries only on `429` and `500`; the receiver answers in milliseconds and its function limit is 10 seconds.

## What is stored and logged

Stored (one small item per message, expiring after `chatwoot.dedupeTtlDays`, 7 by default): the message, conversation, account and inbox ids, the agent name, Chatwoot's delivery id, a status and times. **Never** the text of a message, a customer's name, e-mail or any other personal data. Logged: event names, agent names, reasons and ids. **Never** a secret, a signature or a message.

## Turning it on (the teacher)

1. `ChatwootEnabled=true` (installer: `plan` with the Chatwoot feature, then `apply`). It needs no student backends. The stack then has an HTTP API, a table and two functions, and an output `ChatwootWebhookUrl`.
2. For each agent create a Secrets Manager secret named `chatwoot/<agent name>` (lowercase letters, digits and `-`, up to 40) whose text is `{"bot_secret": "<the secret Chatwoot shows for the bot>"}`. The receiver may read secrets with that name prefix only.
3. In Chatwoot: *Settings, Bots, Add bot*, outgoing URL = `ChatwootWebhookUrl` + `/` + the agent name, then connect the bot to an inbox. Copy the bot's secret into the secret above.

A secret that is missing or has no `bot_secret` field makes every call for that agent `401`. A changed secret takes effect within `chatwoot.secretCacheSeconds` (5 minutes by default).

## Limits and settings

All in `config/settings.json` (see [SETTINGS.md](SETTINGS.md)): largest body (256 KB), signature age (300 s), how long a message is remembered (7 days), how long a secret is remembered (300 s), the API's own throttle (20 per second, burst 40), and the memory and time of the two functions.

## Permissions

The receiver may `PutItem`, `UpdateItem` and `DeleteItem` in its own table, start the one worker function, and read secrets named `chatwoot/*`. The worker may `UpdateItem` in the table and nothing else. No other function, no student role and no existing feature gains anything (`tests/test_template_chatwoot.py`).

## Not built yet

T2 replies to the conversation (Chatwoot's Create Message API); T3 maps an inbox to an agent and keeps one set of keys per business; T4 runs the agent (answers from books, the agent loop, traces); T5 hands a conversation to a person when anything fails; T6 connects the Studio application. Each step is approved on its own.

## Tests

`tests/test_chatwoot_core.py` (the signature Chatwoot builds, the loop guard), `tests/test_chatwoot_api.py` (receiver, worker, secret cache and the conditional writes, on in-memory fakes), `tests/test_template_chatwoot.py` (off by default, scoped permissions). All run in `node tests/run.mjs`.
