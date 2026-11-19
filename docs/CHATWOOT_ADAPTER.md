# Chatwoot Agent Bot adapter

> **Status: steps T1 to T3 of 6. Off by default.** A customer message from a Chatwoot inbox reaches the platform, is checked against the registry of businesses and agents, de-duplicated and handed to a worker, and the worker posts **one placeholder reply** ("Received (message 501)") and records the outcome. A business's Studio manages its agents and their published versions over an admin API. **No AI is used yet**: the agent runtime (answers from books, the agent loop), the hand-over to a person and the Studio screens are later steps. Nothing about students, sites, backends or any existing feature changes.

## What it is for

Chatwoot can send a webhook to an *Agent Bot* for every message in an inbox the bot is connected to (website chat, WhatsApp, Instagram, Facebook). This adapter is the address that webhook points to. It is a platform feature of the teacher's AWS stack, not a student project: a separate Studio application talks to it only over HTTP, with a service key that belongs to one business.

## The pieces

| Piece | What it is |
|---|---|
| **Tenant** | A business owner. One item `tenant#<id>` holds the hash of its service key, whether it is enabled and the names of its agents. Created only by the teacher's local command. |
| **Agent** | `agent#<name>`: its tenant, **one Chatwoot account**, a list of **inboxes**, enabled or not, how many versions exist and which one is live. The name is global (it is part of the webhook address). |
| **Version** | `agent#<name>#v<n>`: an immutable copy of the configuration Studio published (up to 100 KB). The platform stores it untouched; the runtime of a later step reads the live one. |
| **Service key** | `cs_<tenant id>.<secret>`: lets one business's Studio call the admin API. Only the SHA-256 of the secret is stored; it is shown once, when it is made. |
| **Secret** | Secrets Manager `chatwoot/<agent name>`: `bot_secret` (verifies Chatwoot's webhook), `api_token` (the agent bot's own token) and `account_id`. Created by the teacher. |
| **Message item** | `msg#<agent name>#<message id>`: ids, status and times of one accepted message; expires by itself. |

## The flow

```
Chatwoot --POST /hooks/chatwoot/<agent name>--> own HTTP API (own throttle) --> receiver function
   receiver: read the bot secret  ->  check the signature and the time  ->  ignore what the bot must not answer
             ->  ask the registry: agent and tenant enabled, this account, this inbox?  (else ignored: not_mapped)
             ->  write ONE item per message (conditional)  ->  start the worker asynchronously  ->  answer 200
   worker:   claim the message once  ->  ask the registry again (no cache)  ->  read the bot's token and account id
             ->  refuse another account  ->  POST Create New Message to the one Chatwoot address  ->  record replied or failed

Studio --Authorization: Bearer cs_<tenant>.<secret>--> /admin/agents ...  --> admin function (registry items only)
```

* **Signature.** When a bot has a secret, Chatwoot adds `X-Chatwoot-Timestamp` and `X-Chatwoot-Signature: sha256=<hex>`, the HMAC-SHA256 of `<timestamp>.<raw body>`. The receiver recomputes it over the raw bytes, compares in constant time, and refuses a time more than 5 minutes (setting `chatwoot.timestampToleranceSeconds`) from its own clock. A call with a wrong, missing or old signature, and a call for an agent with no secret, all get the same `401`.
* **What is ignored** (answered `200` with `ignored`, never stored): events other than `message_created`; any message that is not `incoming` (the bot's own replies and the human agents' replies are sent back to the bot too, so the reply of step T2 is ignored when it comes back); private notes; messages sent by an agent bot; conversations that are not `pending` (a human has taken them over); and (T3) anything for an agent that is not registered, is switched off, whose tenant is switched off, or whose account or inbox is not the message's (`not_mapped`: one reason for every cause, so nothing tells a caller which it was).
* **Repeats.** The first delivery of a message writes its item with a conditional write and starts the worker. A second delivery (Chatwoot retries on `429` and `500`, and sometimes sends twice) only adds one to a counter. If the worker cannot be started the item is removed and the answer is `500`, so Chatwoot's retry is accepted.
* **One run, one reply.** The worker moves the item from `received` to `processing` with a conditional update; an event delivered twice, or a message with no item, does nothing. The platform never retries the worker by itself.
* **Timing.** Chatwoot waits 5 seconds and retries only on `429` and `500`; the receiver answers in milliseconds. The registry is read (two single items) at most once per `chatwoot.registryCacheSeconds` per agent (15 s): a new agent, a change or a switch-off takes effect within that time. The worker always reads fresh.

## The reply (step T2)

The worker posts `POST <ChatwootBaseUrl>/api/v1/accounts/<account>/conversations/<conversation>/messages` with `{"content": "Received (message <id>)", "message_type": "outgoing", "private": false}`, using **the agent bot's own token**: `Authorization: Bearer <token>` (Chatwoot 4.19 and later) or the older `api_access_token: <token>` header (parameter `ChatwootAuthStyle`). `<conversation>` is the number Chatwoot shows in its dashboard (the webhook's `conversation.id`).

* **One address only.** The worker calls the address in `ChatwootBaseUrl` (https, no path) and nothing taken from a webhook or from the secret. Redirects are not followed, so the token can never travel to another address. An answer is read only up to a small cap and never logged.
* **Account check.** A bot token may write in any conversation of its Chatwoot account, so the account is checked twice: against the registry (the agent's account) and against the secret's `account_id`; a message of any other account is refused (`not_mapped` or `account_mismatch`) before anything is sent.
* **No automatic retry.** Chatwoot does not de-duplicate messages, so a second POST after an unknown outcome could answer a customer twice. A failure is recorded and raised so it is visible: `http_401`, `http_403`, `http_404`, `http_422`, `http_429`, `http_5xx`, `http_other`, `timeout`, `network`, or a refusal before sending: `not_mapped`, `registry_unavailable`, `bad_base_url`, `bad_setting`, `no_secret`, `secret_unavailable`, `no_token`, `bad_secret`, `account_mismatch`, `error` (a bug). A `processing` item that never finishes is left for a later step to sweep.
* **Nothing leaks.** The token is read only by the worker (the receiver never reads it, the worker never reads `bot_secret`) and never appears in a log line, an item or an event.

## The admin API (step T3)

For a business's Studio, on the same HTTP API, `Authorization: Bearer cs_<tenant>.<secret>`. The tenant is the key's: it is never taken from the body or the address. Every error is JSON `{"error": "<code>", "message": "..."}`.

| Route | What it does |
|---|---|
| `GET /admin/agents` | the tenant's agents |
| `PUT /admin/agents/{name}` | create or update: `account_id`, `inbox_ids` (1 to 10 different numbers), `enabled`, `type`, `purpose` (short lowercase words), and `expected_rev` to refuse an overwrite of a newer change (`409 changed`). Creating needs the account and the inboxes. A name used by another business is `409 name_taken`. |
| `GET /admin/agents/{name}` | one agent |
| `DELETE /admin/agents/{name}` | switch the agent off (nothing is deleted) |
| `PUT /admin/agents/{name}/versions/{n}` | publish version `n` (it must be the next number): `{"config": {...}}`, up to 100 KB. A version never changes: the same configuration again is a harmless repeat, a different one is `409 version_exists`. |
| `GET /admin/agents/{name}/versions/{n}` | one version |
| `POST /admin/agents/{name}/live` | `{"version": n}` chooses the live version (0 = none); only an existing version |

* An agent of another business is `404 not_found` on every route. A wrong, unknown, rotated or switched-off key is the same `401` for all of them.
* Two callers cannot silently overwrite each other: every change is a conditional write on the item's revision (`rev`). A publish that stopped half way is completed by sending the same request again.
* The function can only read and write the registry items of the one table. It cannot read secrets, call Chatwoot or start another function.

## The teacher's local command

`python tools/chatwoot_ctl.py --table <ChatwootTableName> <command>`, run on your computer with your AWS credentials (the stack output `ChatwootTableName`). The web API can never create a tenant or a key.

| Command | What it does |
|---|---|
| `create-tenant <id> [--name ...]` | creates a business and **prints its service key once** (only the hash is stored) |
| `rotate-key <id>` | a new key; the old one stops at once |
| `disable-tenant <id>` / `enable-tenant <id>` | switch a business off or on (its key and its agents stop within the cache time) |
| `list` | tenants and their agents (never a key or a hash) |
| `agent-set <name> --tenant <id> --account N --inboxes 3,4 [--enabled true] [--type ...] [--purpose ...]` | creates or changes an agent with the admin API's own validation (the way to register the first agent before Studio exists) |
| `show-agent <name>` | one agent |

## What is stored and logged

Stored: the registry items above (no TTL), and one small item per message, expiring after `chatwoot.dedupeTtlDays` (7 by default): the message, conversation, account and inbox ids, the agent name, Chatwoot's delivery id, a status (`received`, `processing`, `replied`, `failed`), the reply's message id or a reason code, and times. **Never** the text of a message, a customer's name, e-mail, a token or a service key. Logged: event names, tenant and agent names, reason codes and ids. **Never** a secret, a token, a key, a signature or a message.

## Turning it on (the teacher)

1. `ChatwootEnabled=true` (installer: `plan` with the Chatwoot feature, then `apply`), with `ChatwootBaseUrl=https://<your Chatwoot server>` and, for a Chatwoot older than 4.19, `ChatwootAuthStyle=api_access_token`. It needs no student backends. The stack then has an HTTP API, a table, three functions, and outputs `ChatwootWebhookUrl` and `ChatwootTableName`.
2. For each business: `python tools/chatwoot_ctl.py --table <ChatwootTableName> create-tenant <id>`; keep the printed key for that business's Studio.
3. For each agent: create a Secrets Manager secret named `chatwoot/<agent name>` (lowercase letters, digits and `-`, up to 40) whose text is `{"bot_secret": "<the secret Chatwoot shows for the bot>", "api_token": "<the agent bot's token>", "account_id": <the Chatwoot account number>}`, and register the agent (its Studio does it with `PUT /admin/agents/<name>`, or you with `agent-set`). Both receiver and worker may read secrets with the `chatwoot/` prefix only.
4. In Chatwoot: *Settings, Bots, Add bot*, outgoing URL = `ChatwootWebhookUrl` + `/` + the agent name, then connect the bot to an inbox that is in the agent's `inbox_ids`.

A secret that is missing or has no `bot_secret` makes every call for that agent `401`; an unregistered agent is ignored (`not_mapped`); a secret without a usable `api_token` or `account_id` makes the reply fail with a reason. A changed secret takes effect within `chatwoot.secretCacheSeconds` (5 minutes by default).

## Limits and settings

All in `config/settings.json` (see [SETTINGS.md](SETTINGS.md)): largest body (256 KB), signature age (300 s), how long a message is remembered (7 days), how long a secret (300 s) and a registration (15 s) are remembered, how long the worker waits for Chatwoot (10 s), the Chatwoot address, the header style, version size (100 KB) and count (50), agents per business (20), inboxes per agent (10), the API's own throttle (20 per second, burst 40), and the memory and time of the three functions.

## Permissions

The receiver may write message items (`msg#...`), read the registry (`agent#...`, `tenant#...`), start the one worker function and read secrets named `chatwoot/*`. The worker may update message items, read the registry and read `chatwoot/*` secrets, and it makes calls out to the one Chatwoot address. The admin function may read and write registry items and remove an agent item (to take back an agent that could not be listed), and nothing else. No other function, no student role and no existing feature gains anything (`tests/test_template_chatwoot.py`).

## Not built yet

T4 runs the agent (answers from books, the agent loop, traces) from the live version instead of the placeholder; T5 hands a conversation to a person when anything fails and sweeps stuck items; T6 connects the Studio screens to the admin API. Each step is approved on its own. Nothing here has been tried against a real Chatwoot yet: the tests use stand-ins (see below), and the exact 401, 422 and 429 answers of Chatwoot and whether a Chatwoot older than 4.19 accepts the Bearer header are to be confirmed on a real server.

## Tests

`tests/test_chatwoot_registry.py` (service keys, validation, the serving rule, the cached reader, the table's conditional writes), `tests/test_chatwoot_admin.py` (the admin API: key access, agents, immutable versions, tenant isolation, concurrency, secrecy), `tests/test_chatwoot_ctl.py` (the local command), `tests/test_chatwoot_core.py` (the signature Chatwoot builds, the loop guard, the reply request), `tests/test_chatwoot_api.py` (receiver and worker on in-memory fakes, the registry in every flow), `tests/test_chatwoot_http.py` (the real HTTP client against a local server), `tests/test_template_chatwoot.py` (off by default, scoped permissions). All run in `node tests/run.mjs`.
