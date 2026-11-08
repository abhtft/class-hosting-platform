# Critical issues: what stands between today and a safe wider release

> Written 2026-11-08 at release 2.3.1 from a read of the code, the template, the tests and the documentation, plus a few read-only checks and one small load test on the owner's own platform. Each item says **what is wrong, the evidence, who it hurts, and the fix** (no fix is built yet). Severity: **P0** blocks a public announcement or risks real harm; **P1** will be hit by the first real class; **P2** matters at scale or for trust; **P3** polish. Context: [STATUS.md](STATUS.md); order of work: [ROADMAP.md](ROADMAP.md).

## Summary

| # | Issue | Severity | Hurts | Fix size |
|---|---|---|---|---|
| 1 | The installer creates no alerts and no budget | P0 | Teacher: outages and cost spikes go unseen | Small |
| 2 | A clean install of the current version has never been run | P0 | Every new teacher | Medium (needs a verified AWS account) |
| 3 | Backend capacity: 3 requests in flight for the whole platform | P0 for classes using AI | Students: "busy" errors | Medium |
| 4 | Backup and restore leave out books and traces | P0 | Teacher: data loss on a move or rebuild | Small to medium |
| 5 | No automated checks on real AWS for the newer features, and CI runs on Linux only | P1 | Maintainers: regressions found by hand | Medium |
| 6 | No privacy guidance and no "erase a student" action | P1 | Teacher (schools, minors), students | Medium |
| 7 | No abuse path and no traffic or content controls for public sites | P1 | Teacher: legal and cost exposure | Medium |
| 8 | The live platform runs on a Free plan account that will end | P1 (owner) | The owner's class | Small, but dated |
| 9 | Answer quality is not measured; a wrongful refusal was seen | P1 | Students and teacher trust | Large (the RAG roadmap) |
| 10 | Cold starts take about 10 seconds on the first request | P2 | Students: a slow first answer | Small to medium |
| 11 | Teacher login lockout is per IP | P2 | Teacher on a shared school network | Small |
| 12 | Hard-coded AI models and prices | P2 | Cost caps drift; AI breaks when a model is retired | Small |
| 13 | A vendored PDF reader with no update tracking | P2 | Security of book reading | Small |
| 14 | The old permanent-key deploy path is still shipped | P2 | Attack surface, confusion | Medium |
| 15 | One teacher, no audit log, no class-wide defaults, no notifications | P2 | Teacher at scale | Large |
| 16 | No Content-Security-Policy on the portal | P3 | Defence in depth | Small |
| 17 | Documentation was stale and bloated (fixed in this change) | done | Everyone | done |

---

## P0: fix before any public announcement

### 1. The installer creates no alerts and no budget

- **What is wrong.** The platform can email one address about crashes, throttling, student-backend errors and a failed daily sweep. The address is read from an AWS parameter whose name is given to the stack as `AlertEmailParameter`. **The installer never sets it** (`installer/engine.mjs` `parametersFor` passes only the features and the domain), so a teacher who installs the documented way has no alerts at all, and no AWS Budget either. The documentation used to describe the manual route that only the author used.
- **Evidence.** `AlertEmailParameter` appears only in `template.yaml`; `grep` finds no mention in `installer/`, `setup.mjs` or the teacher tools. The author's stack has it because it was set by hand months ago, and updates keep it.
- **Why it matters.** A stranger's class can break or run up a bill (AI use, traffic) and nobody is told.
- **Fix.** The install asks for an alert email, stores it in the parameter, passes the parameter name to the stack, creates an AWS Budget with that address, and `verify` checks both. Add `--alert-email` for non-interactive installs. Document it in the first page of the teacher guide.

### 2. A clean install of the current version has never been run

- **What is wrong.** Every release since 2.1 was exercised on the author's platform by **upgrading in place**. The only second account is blocked by AWS CloudFront verification, so nobody has watched the current template create a platform from nothing. New features add conditions, tables, a KMS key, an EC2 role and more; a first-install failure (ordering, a permission, a name clash, an account restriction) is likely to be found only by a stranger.
- **Evidence.** `HANDOFF.md` (before this rewrite) said the test account was blocked; the AWS Free-plan refusal of the first compute worker instance type showed that account-level limits surface only on real runs.
- **Fix.** Get the second AWS account verified (support case), then run the clean-room test from [ROADMAP.md](ROADMAP.md) phase 1: install with the README quickstart in CloudShell, no domain, as a stranger would; run `verify`; switch every feature on; then `destroy --everything`. Fix what hesitates. Repeat with a domain.

### 3. Backend capacity: 3 requests in flight for the whole platform

- **What is wrong.** The gateway lets at most **3** student-backend requests run at once on the whole platform and 2 per student (`INFLIGHT_GLOBAL`, `INFLIGHT_STUDENT` in `template.yaml`), and the backend API allows about 10 requests a second. These were set for a new AWS account's limit of 10 concurrent executions. A class asking the AI or the books at the same moment mostly gets "busy" (HTTP 429).
- **Evidence (measured on the owner's platform, 2026-11-08).** The same cached question sent in parallel: 1 at a time, all fine; **6 at once: 3 answered, 3 refused**; **12 at once: 2 answered, 10 refused**. The owner's account has a raised limit of 1,000, so the cap is the platform's own setting, not AWS's.
- **Why it matters.** The AI and books features are the platform's main attraction, and a demo to a class is the first thing a teacher does.
- **Fix.** (a) Read the account's real concurrency quota at install and set the caps from it, as parameters; (b) raise the defaults on accounts whose limit allows it; (c) make the helpers wait and retry with a short, visible "busy, trying again" path instead of failing; (d) document the capacity ("about N simultaneous students") in the teacher guide; (e) re-run the measurement above as a permanent test (item 5).

### 4. Backup and restore leave out books and traces

- **What is wrong.** `export` copies tokens, the teacher login, stored student data and the folders `sites/`, `archive/` and `userdata/`. It does **not** copy `knowledge/` (every student's books, the class library, the search indexes) or traces. After a move or a rebuild the books are simply gone. The student-data table has no point-in-time recovery either (the tokens table does).
- **Evidence.** `installer/data.mjs`: `const FOLDERS = ['sites', 'archive', 'userdata']`; `template.yaml` has `PointInTimeRecoverySpecification` only on the tokens table.
- **Why it matters.** The one safety net for a class does not cover its newest and most valuable data, and the guide used to imply it did.
- **Fix.** Add `knowledge/` to the export and import (the data is plain files, indexes are rebuilt or copied), decide on traces (include the last 90 days, or document them as expendable), turn on point-in-time recovery for the student-data table, and add a test that exports and imports a class with books.

---

## P1: the first real class will hit these

### 5. No automated checks on real AWS for the newer features; CI runs on Linux only

- **What is wrong.** The suite uses fake AWS, so it cannot see real-service behaviour. Nearly every feature had a bug found only on the real account (DynamoDB `Decimal` numbers in traces, a Free-plan instance refusal, wrongful refusals, and a refusal count that missed searches the books could not answer). The live smoke test (`tests/live_smoke.py`, 74 checks) covers backends and storage; **it does not cover knowledge, traces, background jobs or the compute worker**, and it is run by hand. CI runs on Ubuntu only, while students and teachers use Windows and macOS.
- **Fix.** Add live sections for knowledge (upload, search, refuse), traces (an answer produces a joined trace and the teacher routes answer), jobs and the worker status; add the load measurement from item 3; run the live suite against a dedicated test platform on a schedule (a GitHub Action using a short-lived role, or the owner's nightly run). Add Windows and macOS to the CI matrix for the tests that need no AWS, and an upgrade test (install the previous release, upgrade, verify).

### 6. No privacy guidance and no "erase a student" action

- **What is wrong.** The platform stores student names, tokens, backend data, books and (optionally) the text of questions in traces; AI calls send prompts to Google or OpenRouter. There is **no data-protection guidance for schools** (minors, consent, retention, a student's right to have their data removed, the AI providers' roles). There are per-feature clear actions (storage, books, traces) but **no single action that erases everything about one student**.
- **Evidence.** `grep` of `lambda/portal.py` and `app.py` finds no purge or erase route; each feature has its own clear route.
- **Fix.** Write `docs/PRIVACY.md` for teachers (what is stored where, for how long, who sees it, what leaves the account, a template notice for parents and students). Add one teacher action, "Erase this student", that removes tokens, site and archive, backend, storage data and files, books, traces, and AI keys, with a report, and add it to `destroy`/`export` tests.

### 7. No abuse path and no traffic or content controls for public sites

- **What is wrong.** Sites are served from the teacher's domain and the teacher is responsible for their content, but the public has **no way to report** a site, there is **no per-site bandwidth cap** (a popular or abusive site raises the teacher's CloudFront bill), and there is no content scanning. The teacher can take a site offline from the page; nobody tells them to.
- **Fix.** A teacher-settable abuse contact shown on every student site's error pages and on a `/report` page; a per-site traffic estimate on the Usage tab and an alarm threshold; guidance in the teacher guide and `PRIVACY.md` about acceptable use and what to do on a complaint (take offline, preserve, revoke).

### 8. The live platform runs on a Free plan account that will end

- **What is wrong.** The owner's platform runs on an AWS account on the **Free plan** with about $200 of credits for six months. When the credits or the period end, the account must be upgraded to a paid plan or services can be restricted and the account closed, which would take the class offline.
- **Evidence.** The Free plan refused the compute worker's first instance type (only free-tier types are allowed), which is how the plan was discovered.
- **Fix (owner).** Put the end date in a calendar, upgrade to a paid plan **before** it, set an AWS Budget (item 1), and keep a recent `export` (item 4). Teachers on a Free plan account should be told in the guide that some AWS services are restricted.

### 9. Answer quality is not measured, and a wrongful refusal was seen

- **What is wrong.** There is no benchmark, so nobody can say how good the books feature is, whether a change helped, or how it does in Hindi. A real trace showed "The books do not say." although the search had found the right passage with confidence. The settings that decide this (confidence thresholds, passage size) cannot be changed by anyone.
- **Fix.** Follow [RAG_ROADMAP.md](RAG_ROADMAP.md): the benchmark (the document list needs the owner's approval before any download), the refusal fix measured against it, per-shelf settings ([CONFIGURATION_PLAN.md](CONFIGURATION_PLAN.md) C1), Hindi support, then the guards.

---

## P2: matters at scale or for trust

### 10. Cold starts take about 10 seconds on the first request

- **Evidence.** The first request to the books route after a quiet period took **10.7 s** against about **1.4 s** warm (same question, 2026-11-08). The student backend limit is 10 s (25 s with the class AI), so a cold first answer can time out.
- **Fix.** Measure where the time goes (the student function, the knowledge function loading its index, the embedding call), keep the knowledge function warm with a small scheduled ping or provisioned concurrency only if the cost is acceptable, and say in the student guide that a first call after a pause can be slow.

### 11. Teacher login lockout is per IP

- **What is wrong.** Five wrong passwords from one IP lock that IP out for 15 minutes (`fail#<ip>` in `lambda/portal.py`). A student on the same school network can lock the teacher out by guessing wrongly five times.
- **Fix.** Lock per username with an increasing delay instead of a hard IP lock, keep the per-IP limit high, and always allow the command-line `teacher` reset.

### 12. Hard-coded AI models and prices

- **What is wrong.** The Gemini model name and the per-token prices are in `lambda/gemini.py`; the spend cap is only as right as the table. A price change makes the cap drift; a retired model breaks the AI for every student at once.
- **Fix.** Make the model list and prices a small data file with a test that fails when the provider rejects a model, and show "AI unavailable: model retired" to the teacher.

### 13. A vendored PDF reader with no update tracking

- **What is wrong.** Books are read with `pypdf` 6.21.4, copied into `lambda/vendor/` and verified by hash. It parses files students upload; Dependabot does not watch vendored code. (The reading is limited to 1,500 pages, 50 MB and a separate 2 GB function with a 900 s limit, so damage is contained.)
- **Fix.** A scheduled check that compares the vendored version with the latest release and opens an issue.

### 14. The old permanent-key deploy path is still shipped

- **What is wrong.** The original per-student permanent deploy keys (`admin.mjs add`, the `/deploy` route in `lambda/app.py`, `student/deploy.mjs`) remain alongside the token-based portal. They add attack surface, a 4 KB parameter limit on the number of students, and documentation to explain.
- **Fix.** Mark deprecated now, remove in the next major version, and delete the related documentation then.

### 15. One teacher, no audit log, no class-wide defaults, no notifications

- **What is wrong.** Known limits (README): tokens one at a time, features per student (about 150 clicks for 30 students), a single teacher login, no log of who changed what, regeneration requests visible only on the page.
- **Fix.** "New students start with these features" defaults first, then bulk actions, then a notification email for requests; co-teachers and an audit log after.

---

## P3: polish

### 16. No Content-Security-Policy on the portal

- The portal answers with `X-Frame-Options`, `Referrer-Policy`, `X-Content-Type-Options` and HSTS but no CSP (checked on the live portal). The page code builds elements without `innerHTML`, so the risk is low; add a strict CSP once it is tested against the real uploads.

### 17. Documentation was stale and bloated (done in this change)

- The README claimed "public beta 2.0.x" and omitted knowledge, traces and the worker; the teacher guide had no alerts or recovery section; the API reference lacked the newer routes; nine historical documents (about 300 KB) described superseded designs. This change removed the obsolete ones, rewrote the README, the documentation map and the handoff, added the missing sections, and added STATUS, this file and ROADMAP.

---

## What is good and should not be disturbed

The security design (students never hold AWS credentials, a permission boundary on student functions, tokens stored only as hashes, kit signing with KMS), the plan-then-apply update model, the refusal to replace data-holding resources, the per-feature off-by-default switches enforced on the server, the test culture (46 groups, machine-checked specification) and the release process are all strong. The work ahead is **closing gaps, not redesigning**.
