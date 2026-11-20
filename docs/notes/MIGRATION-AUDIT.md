# Migration to a new AWS account: audit of the current platform (2026-11-20)

Scope: read-only audit of `D:\Github_Codes\class-hosting-platform-github` (HEAD fa20fa2). Nothing was changed, deployed or run against AWS for this audit.
Source of the existing tooling: commit 949a05d (1.1.0: export/import and transfer), 43e3f00 (backups include class knowledge), b4dca1f (project sync, `projects/` in backups). Code: `installer/transfer.mjs`, `installer/data.mjs`, `installer/engine.mjs`. Docs: `docs/TEACHER_SETUP.md` (backup and transfer), `docs/future/MIGRATION_PLAN.md` is a DIFFERENT thing (a scanner for students' apps), not this.

Honest status of the tooling: `export`, `destroy --everything`, fresh `install` and `import` were rehearsed on a real account; **`transfer` was only tested with simulated accounts** (TEACHER_SETUP says experimental). Nothing below has been rehearsed.

## What the tooling covers today (still valid)
| Item | How it moves |
|---|---|
| TokensTable (students, tokens, per-student `ks_`/`ga_` keys and OpenRouter keys, knowledge counters, job and identity records) | whole table scanned into the backup, written back (`transformForTransfer` drops `domain#` locks, backend state and domain fields) |
| StudentDataTable (class storage) | scanned and written back |
| S3 `sites/ archive/ userdata/ knowledge/ projects/` | `s3 sync` (so `knowledge/tenant/<tenant>/<shelf>` books ARE covered, and Studio's source in `projects/`) |
| SSM: student key map, teacher login | copied |
| Platform itself, features, root domain, certificate | the same `install` with the same features |
| DNS rows for the teacher (`deploy.<domain>`, `*.<domain>`, certificate record, one row per student custom domain) | CSV written by `transfer` |
| Kit signing key (KMS) | new account = new key. Documented (TEACHER_SETUP line about "not signed by the key this project trusts"): students re-trust |

## Gaps found, newest features first (each is something a real move would break or lose)
| # | Gap | Effect on a move | Fix | Size |
|---|---|---|---|---|
| G1 | **The installer does not know the Chatwoot adapter.** `FEATURES` and `parametersFor` have no `ChatwootEnabled`, `ChatwootBaseUrl`, `ChatwootAuthStyle`. | Any installer `plan/apply/upgrade/enable` (and `transfer`'s install step) builds the stack with the Chatwoot switch OFF, so it plans to remove the Chatwoot tables. The STATEFUL guard blocks that plan, which means **installer updates are refused today** unless the three parameters are carried by hand (as I did with a change set). `transfer` would produce a platform WITHOUT the adapter. | add a `chatwoot` feature that carries the three parameters on every update (like the custom-domain ids), `plan --enable chatwoot --chatwoot-url ...`, include it in `transfer`'s feature list | small to medium |
| G2 | **ChatwootTable is not exported.** Tenants (service-key hashes), agents, versions (the agent configurations), spend counters. | After a move every business, agent and version is gone; every Studio service key stops working. | export/import the registry items (`tenant#`, `agent#`, `agent#...#v<n>`; optionally `usage#`); skip `msg#` (7-day records) and the memory table (24-hour conversation memory, expendable by design) | small |
| G3 | **Secrets Manager `chatwoot/<agent>` (bot secret, bot token, account id) and the AI secret (`OPENROUTER_MANAGEMENT_KEY`, `GOOGLE_API_KEY`) are not exported** (the AI secret is re-entered with `secrets`; the Chatwoot ones have no such step). | bots stay silent (`no_secret`) until recreated. | either export them into the backup folder (same sensitivity as the student AI keys already in it, 0600 files, opt-in flag) or a documented re-entry list; the code can print the list of secrets the registry expects | medium |
| G4 | **The webhook address changes** (`https://<apiid>.execute-api.<region>.amazonaws.com/hooks/chatwoot/<agent>`; the id belongs to the account's new API). | EVERY client's Chatwoot bot (outgoing URL) must be edited by that client, and Studio's `CHATWOOT_ADMIN_URL`. This grows with every client. | **give the adapter a stable address on the user's own domain** (for example `chatbot.margdarshakss.com`: API Gateway custom domain + ACM certificate + one DNS record; the move then only repoints one DNS record). Best done BEFORE the S1d rename, because S1d already changes each bot's URL once | medium |
| G5 | **Sign-in state is copied but is account-specific.** The `authpool#platform` item (Cognito pool id, domain, flags) and each student's `auth` field (client id, callback, pool id) are exported unchanged; the Google client in SSM `auth-google` is not exported. | in the new account the code believes the pool exists and skips creating it, so **sign-in fails until the stale state is removed**; Google must also list the new Cognito domain's return address; visitors get new Cognito subs, so any app data keyed on a visitor's id is orphaned (verify: `class_auth` uses the id token's `sub`) | `transformForTransfer` drops `authpool#` items and every `auth` field; documented steps: put the Google client in SSM again, add the new return address in Google's console, each app re-runs `npm run signin -- on`. Check how app data keyed on visitor ids behaves | small plus a decision |
| G6 | **Students' environment variables live only in the old account's Lambda configuration** (`_current_env` reads `get_function_configuration`; the token item keeps only the names, `env_names`). Student backend functions are re-created by a re-publish. | all variable VALUES are lost: Studio's `CHATWOOT_ADMIN_URL`, `CHATWOOT_SERVICE_KEY`, `OWNER_EMAILS`, any student's own keys. | at least list the names per student in the transfer report; better, an opt-in export of the values (sensitive) and a restore after the student's backend is re-published | medium |
| G7 | **Student backends and class-connector URLs.** `sd-<slug>` functions are not moved (documented), and the connector URLs inside them (`CLASS_KNOWLEDGE_URL`, `AI_PROXY_URL`, storage) point at the old account's APIs until re-publish. | students must publish their backend again (documented). Until then their backends call the OLD account. | keep as is; make the transfer report list who has a backend and who has not re-published | docs |
| G8 | **New-account limits** (not code): Lambda concurrent executions (a new account may start at 10; this one has 1000), CloudFront account verification (can take days via AWS Support), Secrets Manager and KMS costs, SES or email for alerts. | a move to an account at 10 concurrent executions would throttle the whole class and the bots. | the user starts these requests early (they have lead time) | user |
| G9 | `verifyInstall` checks sites and backends only. | a transfer would pass its own check with the adapter missing or broken. | add Chatwoot checks (admin route answers 401 without a key, receiver answers 401 for an unknown agent, memory table and sweeper exist) | small |
| G10 | The transfer CSV has no row for Chatwoot. | the user has no per-client list of bot URLs to change. | add rows (client, agent, old URL, new URL), or none at all once G4's stable address exists | small |

## Things that are fine
Class knowledge books (including the `knowledge/tenant/` shelves), `projects/`, class storage, student tokens and their AI keys (OpenRouter keys are account-independent), the Google API key itself (not AWS), per-student limits and counters, teacher login. The platform has no hard-coded account id or bucket name in its code (checked by search for the usual patterns; a full read was not done).

## What the user must do themselves (no code, long lead time: start early)
1. Create the new AWS account and billing; keep the old one until everything is verified (the tooling never deletes it).
2. Ask AWS Support to verify the new account for CloudFront (can take days).
3. Request a higher Lambda concurrency quota in the new account (about a day), and read it with `aws lambda get-account-settings`.
4. Decide the move window: publishing is paused while data is copied (anything published after the copy is lost); announce it to students and clients.
5. Own the DNS: the certificate record, then `deploy.<domain>` and `*.<domain>` at the switch, and the new records for the Chatwoot address.
6. Google console: add the new Cognito return address; Chatwoot clients: update bot URLs (unless G4 is done first).
7. Re-enter keys with `secrets` (OpenRouter management key, Google API key).

## Recommended order
1. **Now (small, independent, removes a present risk):** G1 (installer knows Chatwoot). Without it the installer cannot be used for any update.
2. **With S1d (the rename already changes each bot's URL once):** G4, the stable webhook address on the user's domain. Doing it after clients are onboarded multiplies the work.
3. **After Sprint 1 is tested and the product settles (before choosing a move date):** G2, G3, G5, G6, G9, G10, then docs.
4. **A rehearsal** with a throwaway second account (the docs say `transfer` is untested on real accounts): full transfer, verify, rollback, with a copy of the real data. Only after that, schedule the real move.
5. The real move after Sprint 2 (Studio on the platform with its own env, web upload of books) so the stack stops changing every week: every new table, secret or URL added later needs the same migration thought.

## Rule for every future feature (add to the definition of done)
When a feature adds a table, a bucket prefix, a secret, an SSM parameter, a URL that a third party stores, or state that names an account resource: say in the same commit how it moves (backup, re-entry, or expendable) and add it to `data.mjs` or to the transfer report.

## Not verified in this audit
No transfer or import was run; Cognito `sub` behaviour for visitors; whether `authpool#platform` is in TokensTable (read from the code comment, not from data); a full line-by-line search for account-specific values; the `docs/future/MIGRATION_PLAN.md` scanner (a separate feature).
