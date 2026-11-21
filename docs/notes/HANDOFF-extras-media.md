# Hand-off: extras retrieval and the media library (state at 2026-11-21, code commit 0973d28)

For the owner's own testing. Nothing here needs a decision before you start; the open items are listed at the end.

## What is live

- Stack `student-hosting` (us-east-1) runs the code of commit `0973d28` (UPDATE_COMPLETE). Local commits are **not pushed** to any git remote.
- Agent `shop` (business `poc`): **version 9 is live** (= version 7 without the vague "exact address is on the website" extra). Version 7 is the one before; version 8 is a **not-live test version** with two media items (a test picture and a map link).
- Live suite on version 9: **31 of 32** pass. The one failure is `office-hours` (see open items).
- Checks done before deploy: the whole `node tests/run.mjs` passes, Checkov 35 passed / 0 failed, cfn-lint clean.

## What was built

1. **Extras as retrieved corrections.** Dated records, scored by meaning against each question, the relevant ones ahead of the books, newest first, an update beats the books and a later update beats an earlier one. No relevant extra: nothing changes. Details: `docs/notes/EXTRAS-RETRIEVAL-PLAN.md`, `docs/CHATWOOT_ADAPTER.md`.
2. **Media library** (platform side only): a version lists photos, videos, documents, links and places with descriptions; the model picks by description; the worker sends the text, then each item. A customer's own photo/voice note/file without words goes to a person (`customer_media`). Per-business limits, a ledger, retire-a-version, a daily janitor. Details: `docs/notes/MEDIA-LIBRARY-PLAN.md`.
3. Gemini keys in turns with OpenRouter last, and Amazon Bedrock as a selectable provider (code is live; Bedrock cannot answer until the account's model access is completed).

## Your testing points

Use the test chat / dry run where it says so (nothing is sent to Chatwoot); use a real conversation only where it says so.

**A. Extras (dry run on the live version)**
- Ask about something a correction covers: "digital marketing class ka plan kitne se start hota hai", "sunday ko office khula hai kya", "digital classroom kya hai". Expect an answer from the correction; the explain list shows `Update of <date>` with its score (0.6 to 0.9 is normal).
- Ask things no extra covers: "hi", "what is the capital of france", "tell me a joke". Expect the welcome / fallback text, no update in the list.
- Conflict: the book says the office opens at 9, the extra says 10. Expect 10 (the client wins). Ask "office kab khulta hai" and "refund policy kya hai"; the second may show a weak, harmless update (0.61 to 0.62 is known).
- Two extras that disagree: a later `at` must win (unit-tested; try it by publishing a draft with two dated extras).
- Stuck hand-over: two unanswerable questions in a row in a real test conversation should end with the hand-over (suite case `handover-stuck` covers it).

**B. Media (dry run on version 8; version 8 is not live)**
- Test chat on version 8: "office ka address kya hai? raasta bhi dikhao" shows the map link; "mujhe offer ki photo dikhao" shows the picture and a short friendly sentence (not "the books do not say"); "what are your office hours" shows no media.
- **Not verified yet: a real attachment in Chatwoot.** To try it: `python tools/chatwoot_ctl.py --table <ChatwootTableName> set-live shop --tenant poc --version 8`, send "mujhe offer ki photo dikhao" from a test contact on the test inbox, look at the conversation for the picture; then set the live version back (`--version 9`). If the picture does not arrive as an attachment, the conversation should show a link instead, and the message item's `media` list says `attached`, `link`, `failed` or `unknown`.
- Send a photo with no text from a test contact: expect the hand-over sentence, a private note ("the customer sent a photo, voice note, file or location...") and the conversation set to open.

**C. Limits and cleanup**
- `python tools/chatwoot_ctl.py --table <...> set-limits poc` shows the business's media limits and use; `set-limits poc total_mb=5` then try an upload larger than that (the upload form needs a business service key; there is no Studio screen yet, so use the admin API `POST /admin/agents/shop/media/upload-url`).
- The janitor runs once a day; its log line is `chatwoot_janitor swept` with `removed` and `skipped`. An upload that no version uses is removed after 24 hours.

**D. Regression**
- Run the suite any time: `python tools/chatwoot_ctl.py --table <...> --worker <ChatwootWorkerFunction> suite-run shop --tenant poc --wait`.
- Rollback: `set-live shop --tenant poc --version 7` (or 6). A version never changes; retiring one is `DELETE /admin/agents/shop/versions/<n>` (not the live or last one).

## Open items

1. **Office hours (decision for you):** the extras say Monday to Saturday 10 AM to 6 PM, the standard suite case says 9. Confirm with the client. If 10 is right: change the case `office-hours` in `config/suites/margdarshak-digital.json` to expect "10" and reload it with `suite-load`. If 9 is right: remove that extra (publish version 10 without it).
2. **Not built:** Studio screens for media (upload, list, descriptions) and for extras (list with date and source, delete); the edit of the test chat's dry run to use a business's own per-reply limit; Track B (book search on Bedrock Knowledge Bases with S3 Vectors; needs Bedrock model access first and your yes for the AWS resources).
3. **Bedrock is parked.** The Model access page is retired; the real blocker is account-level (see `docs/DECISIONS.md`: Operation not allowed in us-east-1, account verification elsewhere, quotas 0). The owner opens an AWS Support case. Until then a version that names Bedrock falls back to Gemini.
4. **Uncommitted work of another session** in this folder: `docs/AI_CONNECTOR.md`, `docs/README.md`, `chatwoot-host/neutralise-replica.sql`, `study-guide/`. They were never part of these commits or deploys.
5. The Chatwoot bot secret that appeared in Railway logs earlier should still be rotated.
