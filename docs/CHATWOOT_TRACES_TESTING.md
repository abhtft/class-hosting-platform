# Chatwoot traces: end-to-end test guide

For the person who tests the traceability feature of the Chatwoot agents from the customer's message to the Studio screen. Plan and reasons: [notes/TR0-TRACEABILITY-AUDIT.md](notes/TR0-TRACEABILITY-AUDIT.md). Format and privacy rules: [TRACE_SCHEMA.md](TRACE_SCHEMA.md). Code: `lambda/chatwoot_trace.py`, `lambda/chatwoot_admin.py` (routes), Studio `frontend/src/Activity.tsx`.

## 0. What the feature does (one paragraph)

Every customer message the worker answers (or tries to answer) leaves **one trace** in the traces table under the owner key `cw:<business>`: a summary (agent, version, route, tokens, cost, ids) and three to four steps (`retrieval`, `llm`, `answer`, `media`). The customer's words and the reply are kept **masked** (phone, Aadhaar, PAN, UPI, email and similar become `[phone]`, `[pan]`...) for 30 days. The owner reads them in Studio's **Activity** tab (read only) and can turn a bad answer into a test row. Nothing changes what the customer receives.

## 1. Before you start

| # | Check | How | Expected |
|---|---|---|---|
| P1 | The platform code with traces is deployed | `aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].StackStatus" --output text` | `UPDATE_COMPLETE` |
| P2 | Traces are switched on | `aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].Parameters[?ParameterKey=='TracesEnabled'].ParameterValue" --output text` | `true` (with `false` nothing is recorded and Activity says traces are not switched on) |
| P3 | Studio is the new version | open https://studio.margdarshakss.com/ and hard refresh (Ctrl+F5) | an **Activity** tab between Try and Checks |
| P4 | The agent is live and on | Studio > `shop` > Overview: "On", "Live: version N" | both true |
| P5 | The Chatwoot test conversation is **Pending** | In Chatwoot a conversation that is *Open* has a person on it and the bot stays silent (this is the designed behaviour, not a bug). Use a new conversation or set it to Pending. | the bot replies |
| P6 | AWS CLI can read DynamoDB | `aws sts get-caller-identity` | your account |

Set these once in your terminal (PowerShell) and use them in the commands below:

```powershell
$env:AWS_REGION = "us-east-1"
$T  = aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].Outputs[?OutputKey=='TracesTableName'].OutputValue" --output text
$CW = aws cloudformation describe-stacks --stack-name student-hosting --query "Stacks[0].Outputs[?OutputKey=='ChatwootTableName'].OutputValue" --output text
$TENANT = "poc"; $AGENT = "shop"
```

Useful read-only helpers:

```powershell
# the newest 5 traces of the business (index by-app)
aws dynamodb query --table-name $T --index-name by-app --key-condition-expression "app = :a" --expression-attribute-values "{\":a\":{\"S\":\"cw:$TENANT\"}}" --no-scan-index-forward --limit 5 --output json
```

```powershell
# one message item (replace 501 with the Chatwoot message id)
aws dynamodb get-item --table-name $CW --key "{\"pk\":{\"S\":\"msg#$AGENT#501\"}}" --output json
```

```powershell
# every item of one trace (replace the id)
aws dynamodb query --table-name $T --key-condition-expression "s = :s" --expression-attribute-values "{\":s\":{\"S\":\"t#cw:$TENANT#tr_XXXXXXXXXXXXXXXX\"}}" --output json
```

## 2. Test cases

Mark every case Pass / Fail in section 4. "Studio" means Activity unless stated; open a row by clicking it. A trace appears a few seconds after the reply.

### A. A message becomes a trace (customer side, in Chatwoot)

| # | Send in Chatwoot | Expected trace (Studio > Activity) | Expected in AWS |
|---|---|---|---|
| A1 | A normal question the books answer, e.g. "Social media ka rate kya hai?" | Route chip **From the books**; steps: Customer (your words), passages with scores and "shown to the model", model call with tokens, cost and ms, the reply | summary `route=rag`, `status=replied`, `spans=3`, `version` = the live version |
| A2 | A question containing a PAN: "My PAN is ABCDE1234F, what are the prices?" | The customer line shows `[pan]`, never the real PAN | `ABCDE1234F` appears **nowhere** in the trace items (search the JSON of the query above) |
| A3 | A message with a mobile number: "Call me on 9876543210" | Route **To a person**; hand-over reason `warm lead`; the customer line shows `[phone]` | summary `route=handover`; no `llm` step (the model is not asked) |
| A4 | "I want to talk to a person" | Route **To a person**, reason `person asked` | `route=handover` |
| A5 | A question the books do not answer: "Who is the CEO of Google?" | Route **Fallback**; the reply is the agent's fallback text | `route=fallback`; the summary has `refused=1` |
| A6 | A question that makes the agent send a brochure/photo | Route **From the books** and a note "A file was sent with the reply" | a `media` step with `count` and `together` |
| A7 | A Hinglish/Hindi question | Same as A1; Devanagari is shown correctly | `status=replied` |
| A8 | Send **only an image** (no words) | Route **To a person**, reason `customer media`; the customer line says the words are not kept (there are none) | `route=handover` |
| A9 | A question that matches a client correction (an "extra") | In the retrieval line "corrections used" is not 0 (visible in the AWS span `corrections_used`) | span `retrieval` attrs `corrections_used` > 0 |
| A10 | Two messages in two different conversations within a few seconds | Two separate traces, each with its own conversation id | two summaries, different `conversation_id` |

### B. What is stored (AWS side, read only)

| # | Check | How | Expected |
|---|---|---|---|
| B1 | The message links to its trace | `get-item` of the `msg#` item | field `trace_id` = `tr_` + 16 hex; **no** fields named `_text`, `_tenant` or `explain` |
| B2 | The partition is the business's own | `query` the trace id with key `t#cw:<tenant>#<id>` | one `summary` item and the steps; every item has `slug` = `cw:<tenant>` |
| B3 | Retention | in any item `ttl` | about now + 30 days (epoch seconds; `date -d @<ttl>`) and the same `ttl` on every item of the trace |
| B4 | Privacy level | the `summary` item field `level` | `masked` |
| B5 | The list index works | the by-app helper above | newest first, only `cw:<tenant>` |
| B6 | Cost fields | summary `tokens`, `cost_micro` | numbers; `cost_micro` of a `rag` answer > 0; of a fallback with no model call = the search cost only |
| B7 | A student cannot read it | the key starts with `cw:`, which no student slug can contain (a student slug is lowercase letters, digits and `-`). Automated: `tests/test_chatwoot_api.py` (TR1) and `tests/test_traces.py`. Manual (optional): ask the teacher page `GET /api/admin/traces?slug=cw:poc` while logged in as teacher: the bot's traces are listed. | teacher sees them; students have no route to them |
| B8 | Bots are not in the class numbers or the drift e-mail | teacher page `GET /api/admin/traces/summary`: the "class" totals do not include `cw:` answers; no alert e-mail arrives for the bot (even after many fallbacks) | totals exclude bots; no mail |

### C. Studio (the owner's screens)

| # | Screen | Steps | Expected |
|---|---|---|---|
| C1 | Overview | open the agent's Overview | a card **Last 7 days** with answers, fell back %, handed to a person %, errors, cost |
| C2 | Activity list | open Activity | summary on top, then the newest answers; each row: time, route chip, version, tokens, cost, ms |
| C3 | Filter by what happened | choose "Fell back" / "Handed to a person" / "Answered from the books" | the list shows only that route; "Everything" restores it |
| C4 | Filter by day | choose today's date (UTC) | only answers of that UTC day; an empty day says "No answers found" |
| C5 | Detail | click a row | Customer line, shelves searched, passages with a score bar (green border = shown to the model), model call line, the reply, Chatwoot conversation and message ids |
| C6 | Make a test row | in a detail click **Make a test row from this** | Studio opens Checks > Your tests with a new unsaved row holding the customer's question; write the expected answer and Save; reloading the page does **not** add the row again |
| C7 | Version comparison | publish and make live a second version, send 5+ messages, open Activity | the summary shows a per-version table (answers, fell back %, to a person %, cost) |
| C8 | Paging | with more than 50 answers press **Show older** | the next page continues without repeats (to test without 50 messages use the API: add `?limit=3` to `/admin/agents/<name>/traces` with the business key) |
| C9 | Refresh | send a new message, press **Refresh** | the new answer appears at the top |
| C10 | Mobile | open Studio at phone width (375 px) | rows wrap, the detail is readable, no horizontal scrolling of the page |
| C11 | Read only | look for any button that changes or deletes a trace | none exists |
| C12 | Wrong agent / expired | open an old bookmark or an expired trace | a plain message ("No such trace (it may have expired)"), no blank page |

### D. Privacy and safety

| # | Check | Expected |
|---|---|---|
| D1 | The list and the summary contain **no customer text** (read the JSON of `GET .../traces` in the browser's network tab) | only numbers and labels |
| D2 | Text appears only inside the steps of one trace, masked | PAN, phone, Aadhaar, email, UPI id are replaced |
| D3 | No secret in any trace: search the items of a trace for `Bearer`, the bot token, the service key (`cs_`), `api_access_token` | none found |
| D4 | The customer's real number/PAN in the *Chatwoot* conversation is unchanged (traces only copy, masked) | unchanged |
| D5 | (Teacher, optional) numbers-only mode: set `chatwoot.traceText` default to 0 in `config/settings.json`, `python tools/gen_settings.py`, deploy, send a message | the Activity detail says the customer's words are not kept; the summary and steps hold numbers only; set it back to 1 afterwards |

### E. Failure behaviour (mostly automated)

| # | Case | How it is covered |
|---|---|---|
| E1 | The traces table is unavailable: the customer still gets the reply | automated (`tests/test_chatwoot_api.py`, "a broken store never stops a reply"). Live: not recommended. If a trace is missing, the log has `trace_not_written`. |
| E2 | Traces switched off (`TracesEnabled=false`) | no traces are written; Studio's Activity shows "Traces are not switched on for this platform." |
| E3 | Refusals before the model side leave no trace (agent not live, agent switched off, business over its monthly cap, unusable configuration, or the search or the model failing so that the customer gets the hand-over sentence) | expected: no trace for these messages; the `msg#` item says why |
| E4 | Test chat, suite runs, repeatability runs and "Fill from the books" write **no** traces (they are not customers) | send a Test chat message in Studio and confirm Activity does not show it |

### F. Cost and size

| # | Check | Expected |
|---|---|---|
| F1 | Items are small | one trace is about 6 items of 1 to 4 KB; a day of 100 answers is well under 1 MB |
| F2 | Money | on-demand DynamoDB: a few cents for thousands of answers; check "Cost Explorer > DynamoDB" after a week |
| F3 | Reply speed is unchanged | the trace is written after the reply was sent; compare the model's `llm_ms` with earlier answers: no increase |

## 3. Automated tests (run before and after a change)

```powershell
node tests/run.mjs                                     # everything; ends with "All local tests passed"
python tests/test_chatwoot_api.py                      # TR1: one trace per message, masking, batch, failure
python tests/test_chatwoot_traces_api.py               # TR2/TR4: list, detail, summary, isolation, delete command
python tests/test_traces.py                            # shared trace engine and the teacher route for cw: traces
python tests/test_template_chatwoot.py                 # IAM: the worker may only WRITE t#cw:*, the admin function only READ t#cw:*
python tests/test_template_traces.py
```

Studio (in its own folder): `python tests/test_agents_route.py`, `npm run check`, and `npx tsc --noEmit` in `frontend/`.

## 4. Result log (copy and fill)

| Case | Pass / Fail | Message id / trace id | Notes |
|---|---|---|---|
| P1 to P6 | | | |
| A1 | | | |
| A2 | | | |
| A3 | | | |
| A4 | | | |
| A5 | | | |
| A6 | | | |
| A7 | | | |
| A8 | | | |
| A9 | | | |
| A10 | | | |
| B1 to B8 | | | |
| C1 to C12 | | | |
| D1 to D4 (D5 optional) | | | |
| E2, E3, E4 | | | |
| F1 to F3 | | | |

## 5. If something does not work

| Symptom | Likely cause and what to do |
|---|---|
| The bot replied but there is no trace | Open the `msg#` item: no `trace_id`? Then read the worker log for `trace_not_written` (`aws logs tail /aws/lambda/<ChatwootWorkerFunction> --since 15m`). Check `TracesEnabled=true` (P2) and that the change set with the traces write permission was executed (P1). |
| Activity says "Traces are not switched on" | `TracesEnabled` is `false`, or the admin function is an older version (deploy the platform first, then Studio). |
| Activity is empty but traces exist in AWS | The agent name in the URL differs (a trace belongs to one agent), or the day filter excludes them (days are UTC). |
| Studio shows an error "try again" | The admin function could not read the table: check the log of `ChatwootAdminFunction` for `failed`, and that the read permission (`t#cw:*`, index `by-app` for `cw:*`) is in the deployed template. |
| The customer's text shows a real PAN or number | Stop: this is a defect. Note the trace id, set `chatwoot.traceText` to 0, and delete the traces (section 6). |
| The bot is silent | The conversation is *Open* (P5), or the agent is off or has no live version. Not a trace problem. |

## 6. Clean up and rollback

```powershell
# count first (nothing is removed without --yes)
python tools/chatwoot_ctl.py --traces-table $T delete-traces $TENANT
python tools/chatwoot_ctl.py --traces-table $T delete-traces $TENANT --agent $AGENT --yes
```

- Stop recording customer words: set `chatwoot.traceText` to 0 (numbers only) and deploy.
- Stop all recording: `TracesEnabled=false` (this also stops the students' traces).
- Traces are **not** part of backups (they expire after `chatwoot.traceRetentionDays`, 30 days).

## 7. Known limits (not defects)

- The list shows no customer text on purpose; open a row to see it.
- Summary and filtered lists look through at most `chatwoot.traceScanMax` (2,000) answers and say so when they stop early.
- Days are UTC. The Activity day filter offers the last 7 days.
- Only answers that reached the model's side have traces; the stuck-message sweeper's hand-overs have none.
- The per-version table needs at least two versions with answers.
- Bots are left out of the class numbers and the drift e-mail for now (rollups are stored under `cw:<tenant>`).
