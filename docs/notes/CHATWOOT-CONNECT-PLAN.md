# Connect an agent to Chatwoot from Studio (plan, nothing built yet)

Date: 2026-11-27. Status: **plan for approval**. Written after the live test of `mds` failed with `no_secret`: the agent's secret `chatwoot/mds` did not exist, and the only way to make it was a command in the teacher's terminal.

## 1. The problem
An agent answers only when three things from Chatwoot are known to the platform: the **bot secret** (checks that a webhook really comes from Chatwoot), the **bot access token** (lets the agent post its reply) and the **account number**. Today they live in the Secrets Manager secret `chatwoot/<agent>`, made by hand with the AWS CLI. An owner cannot do that, and a mistake shows up only as silence (the customer gets "marked open due to an error with the agent bot"; the cause is only in CloudWatch).

## 2. The target (what the owner sees)
In Studio, Overview, next to the webhook address that is already there, a card **Connect to Chatwoot**:
- The webhook address with Copy (exists) and three short steps: "In Chatwoot: Settings, Bots, open your bot. Copy the address above into *Outgoing URL*. Copy the *secret* and the *access token* here."
- Three fields: **Account number**, **Bot secret**, **Bot access token**. Each shows `set` or `missing`. A value is never shown again (write only); typing a new one replaces it; an empty field keeps what is there.
- **Save** and a status line: "Connected (changed 27 Nov, 3:40 PM, by you)" or "Not connected: the bot secret is missing".
- **Connection health** (second step): "Last message from Chatwoot: 2 minutes ago", or "Last message refused: the bot secret is missing / the signature is wrong (the secret here is not the one Chatwoot has)". This is the line that would have told us the problem in one second today.

Nothing about this card is secret except the three values, and those only travel from the owner's browser to the vault.

## 3. How it works
```
Studio card --PUT /api/agents/<name>/connection--> Studio backend --(tenant key)--> admin function
                                                                         |  writes (never returns)
                                                                         v
                                                       Secrets Manager  chatwoot/<agent>  {bot_secret, api_token, account_id}
                                                                         ^ read as today
                                                     receiver (checks the signature) and worker (posts the reply)
```
- **The receiver and the worker do not change.** The same secret name and fields, so the live agent `shop` keeps working and an agent connected by hand can be edited in the card.
- **Admin routes (platform):** `GET /admin/agents/<name>/connection` returns `{bot_secret: "set"|"missing", api_token: ..., account_id: 7|null, changed_at, changed_by, webhook_url}`, never a value. `PUT` accepts any of the three (a missing or empty one is kept), validates them (account number a positive integer; secret and token printable text of sane length, no spaces or line breaks), checks the agent belongs to the key's business, and writes the secret (`CreateSecret` the first time, `PutSecretValue` after). It writes `changed_at` and `changed_by` (the signed-in owner's e-mail) on the agent record, never a value. It logs the field NAMES changed, never values.
- **Studio backend:** two pass-through routes with only the allowed fields; the request body is never logged. A practice-mode stub keeps `npm run dev` working.
- **Cache:** the receiver and worker remember a secret for a few minutes (`chatwoot.secretCacheSeconds`); after an edit the old value can be used for that long. The card says so ("takes effect within a few minutes").

## 4. Decisions to confirm
| # | Decision | Recommendation |
|---|---|---|
| D1 | Where the values live | **Secrets Manager** (as today; about 0.40 USD a month per agent, accepted). Not a table. |
| D2 | May the admin function read the secret (to change one field without retyping the others)? | **Yes**, `GetSecretValue` on `chatwoot/*` only, used inside the function and never returned. The alternative (always retype all three) is error-prone. |
| D3 | A form that changes bot credentials must not be open to anyone with the Studio address | **Hard gate: Studio sign-in must be ON** (owner list). With sign-in off, the card shows "Turn on sign-in first" and the routes refuse. Today sign-in is off, so the owner must turn it on first (`npm run signin -- on`; the teacher must have set up Google once). |
| D4 | Connection health line | **Yes, as step 2**: the receiver keeps one small item per agent with the last accepted webhook time and the last refusal (reason and time), overwritten, no growth. |

## 5. Phases and what "done" means
| Phase | Work | Done when |
|---|---|---|
| **C1 (platform)** | Admin routes above; template: admin role may `CreateSecret`, `PutSecretValue`, `DescribeSecret` and `GetSecretValue` on `chatwoot/*` only; unit tests (validation, partial update, never returns a value, other business's agent refused, a log without values); the pinned IAM tests updated on purpose; docs (ADAPTER, RUNBOOK, CHANGELOG). Template change: cfn-lint, Checkov, a change set, your "Yes, execute". | Tests green; with a real call the secret appears in Secrets Manager with the three fields and a `GET` shows `set` and no value. |
| **C2 (Studio)** | The card, the two backend routes, the sign-in guard, the practice stub, route tests, the testing guide section. Deploy with the class-platform tool after "publish now?". | In Studio, an owner connects `mds` by typing three values; Chatwoot's next message is answered; no value ever appears in a response, a log or the page after saving. |
| **C3 (health)** | The receiver's refusal and last-seen item (reason codes only), shown in the card; tests; template IAM for that one item. | A wrong secret shows "the signature is wrong"; a missing one "the bot secret is missing"; a good one "last message 2 minutes ago". |

C1 and C2 together are about a day and a half; C3 about half a day more. After C2, `mds` is connected from Studio and the live test (Hindi question, "aaj class hai?", menu, hand-over) starts.

## 6. Risks
- **Sign-in off** is the real risk (D3): whoever has the address could replace the bot secret. Hence the gate.
- **A wrong paste** (extra space, the wrong token): validated for spaces and length, and C3 shows the result at once.
- **Bot token scope** in Chatwoot (what the bot token may call) is not confirmed from the code; the worker already posts replies with it for `shop`, so the same kind of token works.
- **Values pass through Studio's backend memory** on the way to the vault: never logged, never stored there, HTTPS only.

## 7. Until it exists
The manual command (Secrets Manager, a JSON file removed after use) still works for `mds` today; the card replaces it, and an agent connected by hand shows as connected in the card.
