# T3 plan: tenants, agents, inbox mapping, per-business keys (DRAFT, approval pending, no code)

**Date:** 2026-11-19. **Teacher repo:** untouched by this plan (T1 `a8dfe3a` and T2 `ebb0ff8` are local commits). **Testing:** you review step by step after everything is built; tests here are local (fakes and stubbed HTTP) only.

## 1. Why T3, in one paragraph
T1 and T2 serve one agent per URL (`/hooks/chatwoot/<agent>`), trusting that Chatwoot is configured correctly. T3 adds the **registry** that makes it safe and multi-business: who owns an agent (a *tenant* = a business owner), which Chatwoot account and inboxes an agent may serve, which **published version** of the agent is live, and the **service key** with which Studio (and only Studio, for its own tenant) manages all this over HTTP. The registry is what T4 (the runtime) reads to answer, and what T6 (Studio UI) writes.

## 2. Facts from T0-AUDIT and the code so far
- Decision D2: one Chatwoot, each business has its own inbox/admin; mapping key `(chatwoot_account_id, inbox_id)` to an agent (its published version); keys (bot secret, API token) per client.
- A Chatwoot agent bot may be connected to several inboxes of one account, and a bot token may write in any conversation of its account (T2 research). So the platform must itself check **account and inbox** against the registry.
- Studio talks to the platform only by HTTP and token (D4); T0 Q6 proposed a service key `cs_<tenant>.<secret>` with a `svc#<id>` record, verified in a new function (constant-time, enabled flag), never reusing a student key.
- Today the secret `chatwoot/<agent>` holds `bot_secret`, `api_token`, `account_id`. The table `ChatwootTable` holds only message items (`msg#...`), 7-day TTL.

## 3. What T3 builds (proposal)
**Data (same `ChatwootTable`, new key prefixes, no TTL on these):**
- `tenant#<tenant id>`: `{name, key_hash (SHA-256 of the service key), enabled, created_at}`.
- `agent#<agent name>`: `{tenant_id, account_id, inbox_ids: [..], enabled, type, purpose, live_version, updated_at}`. One agent = one Chatwoot account and a list of inboxes.
- `agent#<agent name>#v<n>`: an immutable version `{config, published_at}` (the instructions and settings Studio publishes; size cap 100 KB, items never overwritten). `live_version` points at one of them.
- Optional later: `map#<account>#<inbox>` to an agent (only if the universal URL is chosen, Q1 B).

**Enforcement in the receiver (after the signature is verified):** load `agent#<name>`; refuse (200 `ignored: not_mapped`, nothing stored) unless it exists, is enabled, its tenant is enabled, the payload's account equals `account_id` and the inbox is in `inbox_ids`. The worker re-checks account and inbox before replying (the secret's `account_id` check from T2 stays as defence in depth).

**Studio-facing admin API** (own route prefix on the same HTTP API, `Authorization: Bearer cs_<tenant>.<secret>`; the tenant is taken from the key, never from the body):
`PUT /admin/agents/{name}` (create or update config: account, inboxes, enabled), `GET /admin/agents`, `GET /admin/agents/{name}`, `PUT /admin/agents/{name}/versions/{n}` (publish a version, immutable), `POST /admin/agents/{name}/live` (choose the live version), `DELETE /admin/agents/{name}` (disable, keep history). Every call checks that the agent belongs to the key's tenant (another tenant's agent answers 404).

**Per-business keys:** the secret per agent stays `chatwoot/<agent name>`. Tenant service keys are created by a **local command** (`tools/chatwoot_admin.py create-tenant <id>`, run by you with your AWS credentials, prints the key once, stores only its hash), never by a web route.

## 4. Questions (reply "all recommended" or per number)
1. **URL model.** A) Keep one webhook URL per agent; the registry only **checks** account and inbox **(recommended: simplest, every business creates its own Chatwoot bot with its own secret, no payload lookup before the signature)** B) One universal URL `/hooks/chatwoot`; the platform finds the agent from the payload's account and inbox (one Chatwoot bot per account can serve several agents, but the secret to verify with is chosen from unsigned data) C) A now, B later.
2. **Who creates the Secrets Manager secrets** (`bot_secret`, `api_token`, `account_id`)? A) You, by hand or with the same local command **(recommended: the web API never gets permission to write secrets)** B) The admin API writes them (Studio could onboard a business alone; needs `CreateSecret/PutSecretValue` on `chatwoot/*` for a function reachable from the internet).
3. **Admin API now or in T6?** A) Build it in T3, T6 then only builds the Studio screens **(recommended)** B) T3 builds the data and the receiver check, the API comes with T6.
4. **Version content.** A) Studio sends the whole published configuration (instructions, type, purpose, language, knowledge references) as JSON up to 100 KB per version; the platform stores it untouched and T4 reads it **(recommended)** B) Only a version number; the instructions stay in Studio and the runtime asks Studio at answer time (adds a dependency in the live path, not recommended).
5. **What does an agent that is not mapped do?** A) Ignore silently (200 `ignored`) and log **(recommended; Chatwoot is not told to retry)** B) Answer 403.
6. **Several inboxes per agent?** A) Yes, a list **(recommended)** B) Exactly one.

**Non-goals (confirm):** no AI (T4), no handoff (T5), no Studio screens (T6), no automatic Chatwoot setup, no billing or quotas per tenant, no user logins for tenants (a tenant has one service key, rotation by creating a new key and disabling the old).

## 5. Spec table (my proposals; fill "User's Answer" and "Status")
| Decision Point | Proposed Answer | User's Answer | Status |
|---|---|---|---|
| T3-D1 Registry store | Same table, prefixes `tenant#`, `agent#`, `agent#...#v<n>`; no TTL on them | | |
| T3-D2 Agent identity | `<agent name>` (lowercase, 1 to 40) is global across tenants; creation refuses a name owned by another tenant | | |
| T3-D3 Check | Receiver refuses unless agent and tenant are enabled and account and inbox match; worker re-checks | | |
| T3-D4 Service key | `cs_<tenant id>.<secret>`, 32 random bytes; only SHA-256 stored; constant-time compare; shown once | | |
| T3-D5 Admin routes | As in section 3; tenant from the key; foreign agent = 404; JSON only, body cap, strict validation (names, ids, sizes) | | |
| T3-D6 Versions | Immutable, `n` increasing by 1, cap 100 KB per version and 50 versions per agent; `live_version` changes by a conditional write | | |
| T3-D7 Local command | `tools/chatwoot_admin.py` (create-tenant, rotate-key, disable-tenant, list) with boto3; never prints an existing key | | |
| T3-D8 Template | New function `ChatwootAdminFunction` on the same `ChatwootApi` (routes `/admin/{proxy+}`), table permissions only (GetItem, PutItem with condition, UpdateItem, Query on `agent#`? see risk), no secrets, no outbound calls; receiver gains `GetItem` | | |
| T3-D9 Limits | Settings for body cap, agents per tenant (20), inboxes per agent (10), version size and count | | |
| T3-D10 Tests | Fakes only: key checks, tenant isolation, every refusal, immutability, conditional writes, receiver mapping enforcement, template guard | | |

## 6. Boundary document (T3)
**Hard constraints:** everything from T1 and T2 (local git, no push or deploy or AWS call, fake secrets, no message text or personal data stored or logged, no edits to `app.py`, `portal.py`, `gateway.py`, `knowledge.py`, `student/`, `starter/`), plus:
1. A tenant can never read, change or even detect another tenant's agent (404, same timing class).
2. The admin function can read and write only the registry prefixes of its table; it cannot read secrets, cannot call Chatwoot, cannot invoke other functions.
3. A service key or secret never appears in a log line, an item (only its hash), an error text or a response (except the one-time creation print of the local command).
4. Listing uses a **query on a key prefix of one partition design**, never a table `Scan` (the existing portal functions already scan other tables: this adds none). *Risk to settle in the build:* the table has a single hash key `pk`, so a list of "my agents" needs a second item per tenant (`tenant#<id>#agents` listing names) or a small GSI; I will use the tenant item holding the agent names (cap 20) to avoid a new index.
5. The receiver stays fast: one extra `GetItem` per accepted message, cached in the container for a few seconds (setting).

**Work:** `lambda/chatwoot_registry.py` (pure validation and key handling), `lambda/chatwoot_admin.py` (admin handler), receiver and worker changes in `chatwoot_api.py`, `template.yaml`, settings, `tools/chatwoot_admin.py`, tests, docs.

## 7. Checkpoints (each its own local commit; you review them in order at the end)
- **T3a** registry data model, receiver and worker enforcement, tests.
- **T3b** service keys, admin API, template function, tests.
- **T3c** local admin command, docs, spec and changelog.
After each: full `node tests/run.mjs` green; commit message states what is unverified against a real stack.

## 8. Review aid for you (since you will review at the end)
I will keep one file `D:\Github_Codes\Test\studio-notes\REVIEW-GUIDE.md` listing, per commit, what to read first, what each test proves, and which decisions you can still change cheaply versus expensively.

---

## 9. Answers (2026-11-19)
| Q | Answer |
|---|---|
| 1 | A: one webhook URL per agent; the registry checks account and inbox |
| 2 | A: you create the Secrets Manager secrets (by hand or with the local command); no web function gets permission to write secrets |
| 3 | A: the admin API is built in T3; T6 only builds the Studio screens |
| 4, 5, 6 | not asked separately: my recommendations stand unless you object (4 A: Studio sends the whole published configuration as JSON up to 100 KB; 5 A: an unmapped agent is ignored with 200 and logged; 6 A: several inboxes per agent) |

All T3-D1 to T3-D10 proposals stand as written. **Waiting for your approval to implement T3a, T3b and T3c** (local code and fake-based tests only; each a separate local commit; no push, no deploy, no AWS call). I will also write REVIEW-GUIDE.md for your step-by-step review.
