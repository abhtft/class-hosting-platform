# Sprint 1 plan: make the agent fit for a real client

Status: PLAN ONLY. No code, no deploy until the user approves. Written after the POC passed (real message in inbox 16 answered; cost about 160 micro-USD per book answer).

## Decisions already made by the user
| Decision | Answer |
|---|---|
| Production tenant | `margdarshak-chatwoot` (the user wrote margdarshak_chatwoot; an underscore is not allowed: tenant ids are 1 to 30 lowercase letters, digits, `-`) |
| Production agent | `digital-margdarshakss` (same rule; agent names up to 40 characters) |
| Chatwoot address | `https://chatwoot.margdarshakss.com` (already deployed as ChatwootBaseUrl). The webhook address stays the API Gateway address; a custom domain for it is optional and later |
| Memory | keep the customer conversation for 24 hours, set in the settings registry (the config file), discuss later |
| Inbox | stay on inbox 16 until the answer quality is validated |

## What Sprint 1 builds (four commits in the teacher repo, then one deploy)

### S1a. Conversation memory (24 hours)
- **What:** the worker remembers the last few turns of each conversation, so it can ask one question at a time, not repeat itself, and understand follow-ups ("and for 80 seats?").
- **Where it lives:** items `mem#<agent>#<conversation>` with an expiry. Each holds the last N customer and bot messages (each cut to a length). The worker reads it before answering and writes it after replying.
- **Settings (all in `config/settings.json`):** `chatwoot.memoryHours` = 24 (0 switches memory off, and nothing is read or stored), `chatwoot.memoryTurns` (proposed 6), `chatwoot.memoryMessageChars` (proposed 400).
- **Use:** the history goes into the model prompt as earlier turns (fenced as data like the passages). The book search uses the previous customer message together with a short new one, so "80 seater?" still finds the classroom passages.
- **Privacy change (the user accepted for now):** until now the customer's words were never stored. From now on they are kept up to 24 hours in the memory item only, never in message items, logs or metrics. The expiry is checked on read too, because DynamoDB deletes expired items late (up to about 2 days).
- **Docs:** CHATWOOT_ADAPTER.md, spec section 4.18, SETTINGS.md and CHANGELOG say plainly that the policy changed.

### S1b. Hand-over to a person (T5), with a lead note
- **What:** when a person should take over, the worker (1) posts a private note in the conversation for the team (reason, and the last turns from memory), (2) changes the conversation from `pending` to `open` (it then appears in the team's list and notifies assignees), (3) sends the customer one fixed polite sentence from the agent's configuration (`handover_text`).
- **Mechanism: Chatwoot's own hand-over.** An agent bot hands a conversation to people by setting its status from `pending` to `open` (Chatwoot's documented bot hand-off); open conversations appear in the team's list and notify. We use that, and add a private note so the team sees why. We do not invent a second system.
- **Triggers (final, decided by Claude as the recommended policy; the user asked for the ideal):**
  1. the customer asks for a person (word list in English, Hinglish and Hindi: "agent", "human", "person", "insaan", "baat karni", "call me", "कॉल", "इंसान"): at once;
  2. a **warm lead**: the customer shares a mobile number (Indian mobile pattern, optional +91), or says they want to go ahead or visit ("interested", "book", "visit", "aana hai", "karna hai"): speed matters, so the team is told with the last turns in the note;
  3. the books were not sure **twice in a row** in the same conversation (the bot is stuck; needs memory);
  4. a complaint or angry or money-problem word ("refund", "cheat", "fraud", "complaint", "dhokha", "paisa wapas"): a person must answer;
  5. a technical failure of the answer (model, search, spend cap, reply failed) and the sweeper's stuck items, so the customer is never left in silence.
  Not handed over: answered price questions, FAQ, small talk (the bot handles those; handing over everything would drown the team).
  After the hand-over the bot is silent in that conversation (it is no longer `pending`). A team member can put the conversation back to `pending` to give it back to the bot.
  Chatwoot also has its own **Automation rules** (Settings, Automation) that can assign or label by keyword. They are an optional extra on the user's side; the bot does not depend on them (whether they run on `pending` conversations is unverified).
- **After a hand-over** the bot stays silent in that conversation (it is no longer `pending`: the existing rule already does this).
- **Chatwoot calls:** `POST .../conversations/{id}/messages` with `private: true`, and `POST .../conversations/{id}/toggle_status` with `{"status": "open"}`. **Unverified:** whether an agent bot's own token is allowed to call toggle_status on Chatwoot 4.18. It will be tested live first; if refused, the fallback is the private note plus a label (the note itself still reaches the team).
- **New agent settings** (in the version configuration, all optional): `handover_text`, `handover_on` (list of the triggers above).
- **Sweeper (stuck items):** a small scheduled function (every 10 minutes) finds message items stuck in `processing` for longer than the worker's time limit, marks them failed and, when no reply was posted, hands the conversation to a person with a note. It reads only `msg#` items of the adapter's table.

### S1c. Infrastructure for S1a and S1b
- **Memory storage (D1, decided):** a separate small DynamoDB table with expiry and **no point-in-time recovery** (the existing table has it switched on, which would keep expired customer text in backups for up to 35 days).
- **Permissions:** the worker gets GetItem and PutItem on memory items only; the sweeper gets read on `msg#` and its own write on message items; nothing else is widened. Tests in `test_template_chatwoot.py` pin all of it.
- New function `ChatwootSweeperFunction` and its schedule, off when Chatwoot is off.

### S1d. Real names (migration, with the user's steps)
1. Create tenant `margdarshak-chatwoot` (`create-tenant`); the new service key is saved by the user (it is for Studio).
2. Put the books on its shelves (`knowledge-sync --tenant margdarshak-chatwoot`; a few cents).
3. Register agent `digital-margdarshakss` (account 1, inbox 16), publish version 1 from the current configuration (+ `handover_text`), make it live.
4. In Chatwoot: **reset the bot's secret** (it was printed in the Railway logs) and change the bot's outgoing URL to `<webhook address>/digital-margdarshakss`.
5. Create the AWS secret `chatwoot/digital-margdarshakss` with the new secret and the bot's token (the user pastes into a local file, as before).
6. Test in inbox 16. Then switch the old agent `shop` off, and later remove tenant `poc`, its shelf and the secret `chatwoot/shop`.
Between 4 and 5 the bot is silent for a short moment: do it when no customers are writing.

## Order, checkpoints and what each one proves
| Step | Local proof (no AWS) | Live proof (after the single deploy) |
|---|---|---|
| S1a memory | tests: history is stored, cut, expires, off at 0, never reaches logs or message items; the prompt fences it | a 3-message conversation where message 3 depends on message 1 |
| S1b hand-over | tests: each trigger, one hand-over per conversation, failure of one Chatwoot call does not lose the other, notes hold no token | give a phone number in inbox 16: a private note appears, the conversation becomes open |
| S1c infra | template guard tests, `cfn-lint`/`checkov` (the user runs them) | change set read before it is executed (as before) |
| S1d rename | n/a | the same test conversation on the new names |
**Checkpoint 1** after S1a and S1b with the full test suite green and a summary for review. **Checkpoint 2** before the deploy, with the change set. **Checkpoint 3** after the live test.

## Decisions (all four settled)
| # | Question | Final answer | Why |
|---|---|---|---|
| D1 | Where does memory live? | **A separate DynamoDB table** (`ChatwootMemoryTable`): key `mem#<agent>#<conversation>`, expiry (ttl), no point-in-time recovery, on-demand billing | one small item per conversation that is read and replaced on every message; DynamoDB does this in a few milliseconds with an expiry. S3 is the wrong tool (no per-item expiry in hours: its lifecycle rules work in whole days; slower; no conditional update) and the existing table keeps backups for 35 days |
| D2 | When do we hand over? | the five triggers in S1b (person asked, warm lead, stuck twice, complaint, technical failure) | the ideal policy: speed to lead and never leave a customer in silence, without sending every price question to people |
| D3 | What does the team see? | private note + the conversation becomes `open` | recommended; assigning to a named agent can come later |
| D4 | The customer sentence | one fixed `handover_text` in the version configuration, default Hinglish: "Dhanyavaad! Aapki jankari hamari team ko mil gayi hai. Team jaldi aapse sampark karegi (Mon-Sat, 9 AM se 6 PM). Tab tak aap yahin likh sakte hain." (the user can change it in a new version) | a fixed sentence cannot invent a promise |

Defaults for the numbers: `memoryHours` 24, `memoryTurns` 6, `memoryMessageChars` 400. All in the settings registry.

## Risks and unknowns
- toggle_status with the bot's token on Chatwoot 4.18: unverified (see S1b).
- Memory raises the cost of each answer (more prompt tokens): expected small, measured in the live test.
- A phone-number trigger can fire on a number that is not a lead (an order number). The note says why it was handed over, so the team can ignore it.
- DynamoDB expiry is late; the read-time check covers it, but the item stays until DynamoDB removes it.
- Nothing in this sprint is tested against the real Chatwoot until the deploy.
