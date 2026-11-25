# Runbook (Chatwoot agent platform part)

How to run, check, deploy and debug. Commands are PowerShell or Git Bash on Windows; replace the names with the stack outputs.

## Run the tests (no AWS needed)

```powershell
node tests/run.mjs
```

One adapter test file alone: `python tests/test_chatwoot_runtime.py` (also `_api`, `_admin`, `_media`, `_teach`, `_suite`, `_handover`, `_core`, `_ctl`, `test_template_chatwoot.py`, `test_settings.py`).
After editing `config/settings.json` run `python tools/gen_settings.py` (it rewrites `lambda/platform_settings_data.py` and `docs/SETTINGS.md`).

## Before every deploy

1. Work from a clean committed tree: `git worktree add --detach <folder> HEAD` and package from there, never from a dirty working folder.
2. Checkov: `checkov -f template.yaml --config-file .checkov.yaml` (exceptions only in `.checkov.yaml`, each with a reason).
3. cfn-lint: `cfn-lint -i W1030 W2506 -t template.yaml`.
4. Create the change set without executing it, read it (anything Add, Remove or Replace is a decision), and execute only on the owner's yes. A change set keeps every live parameter as it is.

## After every deploy

Run the agent's test suite on the live version and read the failed cases:

```powershell
python tools/chatwoot_ctl.py --table <ChatwootTableName> --worker <ChatwootWorkerFunction> suite-run shop --tenant poc --wait
```

## Teacher commands (`tools/chatwoot_ctl.py`)

`create-tenant`, `rotate-key`, `disable-tenant`, `set-cap`, `set-limits <business> name=number` (media limits), `agent-set`, `publish`, `set-live`, `knowledge-sync`, `eval`, `suite-load`, `suite-show`, `suite-run`, `list`, `show-agent`.

## Debugging an answer

- **Test chat / dry run** (worker `kind: test`, no message is posted): shows the route, the book passages, the extras used (title "Update of <date>") and the media it would send.
- **Worker log lines** are JSON with names and codes only. A handover or failure reason is in the message item (`msg#<agent>#<id>`): `llm_error`, `retrieval_error`, `no_shelf`, `cap_reached`, `bad_config`, `stuck`.
- **The answer ignores a correction:** check the version's `extras` (date, text), the score in the dry run's explain list, and `chatwoot.extrasMinScore`.
- **The bot answers "hi" with a model answer or never hands over after two fallbacks:** an extra is being treated as relevant to everything; check its score and text in the explain list.
- **A media file did not arrive:** the message item's `media` list shows `attached`, `link`, `failed` or `unknown` (a timeout: not repeated). Check the file is in the ledger (`chatwoot_ctl.py set-limits <business>` shows use) and that its bytes match its type.
- **Bedrock refuses:** the account needs Bedrock model access for the Claude models; the other provider answers meanwhile.

## Recovering

- A bad version: `set-live` an earlier version number (versions never change). Retire a version only when it is not live and not the last one.
- A stuck message: the sweeper hands it to a person every 10 minutes.

## Bedrock Knowledge Base trial (track B, step B0)

Off by default. Not used to answer customers: it exists to compare the books on S3 Vectors with the current search.

1. In the Bedrock console (us-east-1) complete model access; `GetFoundationModelAvailability` for `amazon.titan-embed-text-v2:0` must say `AUTHORIZED`.
2. Checkov, cfn-lint, then a change set with `BedrockKbEnabled=true` (it adds the S3 Vectors bucket and index, a Knowledge Base, a data source and a role); execute only on the owner's yes. Note the outputs `BooksKnowledgeBaseId` and `BooksDataSourceId`.
3. Write a cases file like the one `chatwoot_ctl.py eval` reads (questions the books answer with an `expect` text, and questions they must refuse with `"refuse": true`; include Hinglish questions).
4. Run:

```powershell
python tools/kb_compare.py --tenant poc --folder knowledge/poc --collections <shelf> --cases cases.json --bucket <sites bucket> --secret-name <AiSecretName> --knowledge-base <BooksKnowledgeBaseId> --data-source <BooksDataSourceId>
```

5. Read the table: answerable and refused questions for both searches, best scores and times. Record the go or no-go in `docs/DECISIONS.md`. To remove the trial set `BedrockKbEnabled=false` (the data source deletes its vectors; confirm before deleting).

## Testing the Chatwoot traces end to end

Follow docs/CHATWOOT_TRACES_TESTING.md (preconditions, 40 numbered cases, the AWS read-only commands, a result log, clean-up and rollback). The owner-side routes are `GET /admin/agents/{name}/traces`, `.../traces/{id}` and `.../traces/summary`; remove a business's traces with `chatwoot_ctl.py --traces-table <name> delete-traces <tenant> --yes`.

## Traces of the Chatwoot answers (TR1)

Find a message's trace: read `msg#<agent>#<message_id>` in the Chatwoot table and use its `trace_id`; the trace is `t#cw:<tenant>#<trace_id>` in the traces table (`summary` plus steps). A missing `trace_id` on a replied message means the trace could not be written: look for the log line `trace_not_written` (the reply was still sent). Turn the customer's words off with `chatwoot.traceText` = 0 (only numbers stay); retention is `chatwoot.traceRetentionDays`. Student backend and traces must be on (`TracesEnabled`), otherwise bots write no traces.

## Alarms and backups of the agents

**Alarms** (e-mail to the alert address): `chatwoot-worker-errors` (a message was not answered or its reply not posted: read the worker's log for `failed`), `chatwoot-receiver-errors` (Chatwoot's webhook), `chatwoot-failed-answers` (3 or more customers handed to a person in 15 minutes because an answer could not be made: look at `reason` in the `ChatwootAgents` metric lines, usually the model provider or the books), `chatwoot-admin-errors`, `chatwoot-books-errors`. Quiet periods are not failures.

**Backups.** The adapter's table (agents, versions, tests, the books list) has point-in-time recovery: restore it to any second of the last 35 days into a new table. The sites bucket (the shelves, uploaded books while they are read) is versioned and old versions are kept 30 days: a removed book or shelf can be recovered from the previous versions. The conversation memory table has no recovery on purpose (customers' words). An owner can also keep a copy of one agent: Studio, Overview, "Export a copy (JSON)".

## Archived agents

Archive and restore are in Studio (Overview, "Manage this agent"; the Archived list in the sidebar). The purge logs `purged` per agent and `purge_skipped` with a reason (`in use`: a live orchestrator lists it, `not settled`: its business is missing); a skipped agent is tried again the next day. To keep one longer, restore it. To remove one at once, the teacher can delete its items by hand; there is no "delete now" button on purpose.

## Books in Studio

A book shown as **failed** carries a plain reason (`no text` for a scanned PDF, `spend limit`, `too many passages`); the log line `book_failed` has the code. A book stuck on "Reading" for more than ten minutes means the books function timed out: dismiss it and add the file again (a smaller file or split it). If a shelf the teacher's command made is missing from Studio, press "Check the shelves again" (it rebuilds the list from the bucket). `books_unavailable` in the admin log means the books function could not be reached (check `CHATWOOT_BOOKS_FUNCTION`).

After a change to how words are read for the keyword search (the Hindi fix of this release), a shelf made earlier keeps its old keyword index until it is rebuilt. Rebuilding reads what is already stored (no book is read again, no embedding is paid): invoke the books function with `{"kind": "rebuild_search", "tenant_id": "<tenant>"}` (add `"shelf": "<name>"` for one shelf); the answer lists the shelves done and any that failed. Only shelves with Hindi (Devanagari) text gain from it. To see the effect in Studio, Knowledge, "Ask the books": a Hindi question such as "फीस कितनी है?" now shows a keyword score for the passages that have the word.

**The date and time the agent is told.** Every answer prompt says today's date, the weekday and the time in the time zone of the setting `chatwoot.timezoneOffsetMinutes` (330 = India). If a customer's "today" or "now" is answered for the wrong day, check that setting first, then the passage that holds the timetable (a line such as "Monday to Saturday: 9 AM to 6 PM" is read correctly; a table flattened by the PDF reader may not be).

## Chatwoot connection from Studio (bot secret, token, account number)

An owner connects an agent in Studio, Overview, **Chatwoot connection**: the account number and inboxes, and below them the bot secret and the bot's access token (copied from Chatwoot, Settings, Bots). They go to the agent's secret `chatwoot/<agent>` in Secrets Manager; Studio never shows them again (`set` or `missing` only). An empty field keeps what is stored. The new values are used within `chatwoot.secretCacheSeconds` (5 minutes) because the receiver and the worker remember a secret for that long.

**When a customer's message gets no answer and Chatwoot writes "Conversation was marked open by system due to an error with the agent bot":** read the receiver's log (`/aws/lambda/<stack>-ChatwootReceiverFunction-...`). `{"chatwoot": "refused", "agent": "<name>", "reason": "no_secret"}` means the agent's secret does not exist or has no `bot_secret`: open Studio, the agent, Chatwoot connection, and fill the card. `bad_signature` means the bot secret in the card is not the one Chatwoot has for that bot (copy it again). A reply that is not posted is a worker error (`reply_failed`): the bot token or the account number is wrong. Log lines `connection_saved` (field names only) and `vault_failed` (error type only) are the admin function's side.

Studio refuses the card unless the person is signed in and is in `OWNER_EMAILS` (the same check as every Studio route). By hand, the older way still works: `aws secretsmanager create-secret --name chatwoot/<agent> --secret-string file://secret.json` with `{"bot_secret": ..., "api_token": ..., "account_id": ...}`, then delete the file.

## Orchestrator routing: tuning

Tune with the routing table in Studio (Checks, Routing): add the messages that went to the wrong place with where they should go, change keywords and example questions of the departments, run again. Too many menus: lower `chatwoot.orchestratorMeaningMin` a little or add example questions. Wrong department by meaning: raise `orchestratorMeaningMargin` or add examples to the right one. A greeting goes to the menu on purpose when nothing is sure. `routing_meaning_failed` and `routing_model_failed` in the logs mean a layer was skipped (the next layer or the menu answered).

## Orchestrator (one number, several departments)

Set up: create each department agent with type `department` (no inbox), publish and make live a version for each; in Chatwoot create one team per department and note its number; publish the orchestrator's version with `departments` (agent names and team numbers) and make it live. Debug: a message item shows `department` and `route_layer` (`menu_word`, `menu_number`, `menu_label`, `keyword`, `keyword_tie`, `sticky`, `general`, `ask`, `guard`); `department_unavailable` means the department agent is off, not of type `department`, or has no live version; `handover_team` 0 means Chatwoot refused the team assignment (check the team number).

## Consistency check

`POST /admin/agents/{name}/consistency` (`version` or `config`). Debug: the worker logs `consistency_failed` with the error class; `bad_answer` means the model's text had no JSON array (run again). A conflict whose quote is not literally in its field is dropped on purpose. Counts against `usage#<tenant>#<month>`.

## Test rows from the books (T5)

`POST /admin/agents/{name}/suite/suggest` with `{"version": n}` or `{"config": {...}}`, `"count"` and optionally `"dry": true` (the cost estimate only). Debug: the worker logs `suggest_failed` with the error class when the shelves or the model fail; `no_books` means the agent's collections hold no passage of 40 characters or more. The call counts against the business's monthly spend (`usage#<tenant>#<month>`). Settings: `chatwoot.suggestMaxCount`, `suggestPassages`, `suggestPassageChars`, `suggestMaxTokens`.

## The central safety suite

- Load or update it (teacher): `python tools/chatwoot_ctl.py --table <ChatwootTableName> central-load --file config/suites/standard-safety.json` (raise `version` in the file when a case changes); `central-show` lists it by group.
- Run it on an agent: `python tools/chatwoot_ctl.py --table <...> --worker <...> suite-run shop --tenant poc --central --wait` (add `--version N` or `--config file.json`; `--all-live --central` runs it on every live agent).
- A failed `must` case is a real problem: read the answer in the run record, fix the instructions or the books (or, if the case is wrong for this business, tell the teacher), and run again. A flaky pass means the model answered differently on a retry.
- Every real wrong answer found in the live conversations becomes a new case in the file (and a new version number).
