# Action plan: what to fix before announcing this as open source, in priority order

> Written 2026-11-13 at release **2.10.0**. Sources: `docs/CRITICAL_ISSUES.md` and `docs/ROADMAP.md` (both written 2026-11-08, at 2.3.1) re-checked against the repository today, plus things that appeared after them (sign-in for student apps, add-ons, higher limits).
> **Honesty note:** I checked the repo (files, template, changelog), not your live AWS account. For each item, "status" says what the repo shows. Anything marked *unverified* needs you to confirm on a real account. This is my judgement, not a specification: reorder it if your goals differ.

## 0. The one-paragraph answer

The code is strong (security design, tests, signed releases). What is missing is **proof and paperwork**: nobody but you has installed it, the safety nets built in 2.3.2 (alerts, budget, backups of books, capacity) were never run on a clean account, there is **no privacy guidance, no "erase a student" action and no abuse/report path**, and the repository's community settings are not finished. Fix those, run a pilot with 3 outside teachers, then announce. Do not add features until then (except the answer-quality track).

## 1. Priority key

| Level | Meaning | Rule |
|---|---|---|
| **P0** | Blocks a public announcement or can cause real harm (data loss, cost shock, legal exposure, install failure) | Fix and **prove on real AWS** before any pilot |
| **P1** | The first real class will hit it | Fix before announcing; may overlap with the pilot |
| **P2** | Matters at scale or for trust | Schedule after the pilot; write down the risk now |
| **P3** | Polish | Whenever convenient |

Effort: **S** under a day, **M** 2–4 days, **L** about a week or more.

## 2. P0: do these first

| # | Item | Status in the repo today | What to do | Done when | Effort |
|---|---|---|---|---|---|
| P0-1 | **Clean-room install has never been run** | Unchanged since the issues list. The second AWS account (teacher-test) was blocked by CloudFront verification | (a) Reopen the AWS Support case for the second account (request id is in your notes), explain it is a legitimate verified account. (b) Avoid repeated create/delete cycles on that account. (c) Follow **only the README quickstart** in CloudShell, no domain; then again with a domain. Switch every feature on, run `verify`, then `destroy --everything`. (d) Fix every hesitation in the installer and the guide | Two clean installs in a row, no manual edits, under 45 minutes of your time (excluding AWS waits) | L (mostly waiting) |
| P0-2 | **Alerts + budget never run live** | Coded and tested in 2.3.2 (`setupAlerts`, budget); your own notes say "alerts not run live" | On the clean install: give an alert email, confirm the subscription mail arrives, cause one deliberate error (e.g. invoke a function with bad input) and confirm an email; confirm the AWS Budget exists and `verify` checks it | A real alarm email and a Budget visible in the console | S |
| P0-3 | **Backup/restore of books and student data never proven on real AWS** | Code covers `knowledge/` and PITR (2.3.2); "not yet tested on a real account" | On a throwaway class: add a book, `export`, `destroy --everything`, `install`, `import`, confirm the book answers a question. Also test restore of one deleted S3 file (delete marker) from the guide | A restored class answers from its book | M |
| P0-4 | **Privacy guidance missing** (`docs/PRIVACY.md` does not exist) | Missing | Write `docs/PRIVACY.md` for teachers: what is stored where and for how long (tokens, names, site files, backend data, books, traces, project files the teacher can read), what leaves the account (prompts to OpenRouter/Google, passages to the embedding provider, Google sign-in for visitors), roles of each provider, minors and consent, retention, a template notice for parents/students. Link it from README and the teacher guide | A teacher can answer "where is my child's data and how do I delete it" from documents alone | M |
| P0-5 | **No "Erase this student" action** | No erase route found in `lambda/portal.py` (only per-feature clear actions) | One teacher action that removes: token record and slug lock, site and archive, backend function(s) (+ job function) and log groups, storage data and files, books, traces, AI key (disable + delete), project files, sign-in client. Return a report of what was removed. Add tests (fake) and one live run on `test_claude` | After the action, nothing about the student remains (a listing of every prefix/key proves it) | M |
| P0-6 | **No abuse/report path, no traffic control for public sites** | Not built. SECURITY.md covers vulnerabilities only | (a) Teacher-settable abuse contact shown on error pages and on a `/report` page. (b) Per-site traffic estimate on the Usage tab plus an alarm threshold. (c) Guidance in the guide/PRIVACY.md: acceptable use, what to do on a complaint (take offline, preserve, revoke). A public site on the teacher's domain is the teacher's legal exposure | A stranger can report a site and the teacher knows how to act | M |
| P0-7 | **Sign-in for student apps is built but not proven live** | 2.8.0–2.10.0 released; per `AUTH_PLAN` live test (A5) and Google client setup were still open when last noted *(unverified: check)* | Complete A5 on `test_claude` with a real Google account **you** sign in with: sign in, sign out, second account sees only its own records, forged token refused, app deletion removes its client. Check Google's consent-screen publishing rules. Add the visitor-data lines to PRIVACY.md (shared pool means a Google identity is recognised across apps) | A real login works end to end and the privacy text matches reality | M |
| P0-8 | **Free-plan AWS account (your live platform) will end** | Dated item for the owner | Put the credit/plan end date in your calendar now; upgrade a month before; keep a recent `export` | Calendar entry + a fresh export stored off the account | S |

## 3. P1: before announcing (can overlap with the pilot)

| # | Item | Status | What to do | Effort |
|---|---|---|---|---|
| P1-1 | **Real-AWS regression suite is partial and manual** | `tests/live_smoke.py` covers backends/storage; not knowledge, traces, jobs, worker, sign-in; CI is Linux only | Add live sections (knowledge upload/search/refuse, traces joined, a job, worker status, sign-in); keep `tests/live_load.py`; add Windows and macOS to the CI matrix for no-AWS tests; add an **upgrade test** (install previous release, upgrade, verify). Run the live suite on a schedule against a test platform | L |
| P1-2 | **Repository settings and community files** | `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, issue templates exist. **No `CODE_OF_CONDUCT.md`**. Branch protection, private vulnerability reporting, secret scanning/push protection are GitHub settings I cannot see | Turn on: private vulnerability reporting, branch protection on `main` (required checks), Dependabot alerts, secret scanning with push protection. Add a Code of Conduct. Create 5–8 `good first issue`/`help wanted` issues. Read Scorecard and CodeQL results and fix serious ones before adding badges | S–M |
| P1-3 | **Answer quality is not measured; a wrongful refusal was seen** | Not started per the roadmap (benchmark needs your approval of a document list) | Follow `docs/RAG_ROADMAP.md`: benchmark + baseline report → fix wrongful refusals measured against it → per-shelf thresholds. Do **not** claim "accurate answers" publicly until a number exists | L |
| P1-4 | **Capacity on a *new* account** | Caps follow the quota (2.3.2); a new account has a Lambda limit of ~10, so only ~3 requests run at once until AWS raises it | Document it in the first page of the teacher guide ("request a Lambda concurrency increase (free) before a class"), and make `verify` warn clearly. Re-run `tests/live_load.py` with a student token on your platform | S |
| P1-5 | **Supply-chain: MCP package and portal pages are not signed** | Kit is KMS-signed; MCP tarball and browser pages are not (`design-kit-signing.md` phase 2) | At minimum document the limit in SECURITY.md. Next: have `setup` verify the MCP tarball hash against a signed `mcp-version.json` before writing the editor entry | M |
| P1-6 | **Teacher AWS permissions** | Docs assume AdministratorAccess | State clearly in the guide that the install key is powerful and should be created for the install and deactivated/rotated afterwards; later publish a least-privilege policy or SSO path | S (doc) / L (policy) |
| P1-7 | **Support expectations** | README says issues on GitHub; no explicit statement | Add a "support: GitHub issues, best effort, response time" statement and a status of "beta" at the top of README. Pin an issue "Tried the install? Tell us" (template exists) | S |
| P1-8 | **Cold start (~10 s first request)** | Measured on 2026-11-08; limits were raised since | Re-measure; if still slow on the books/AI routes, add a scheduled warm ping or accept and document it | S–M |

## 4. P2: schedule after the pilot, but write down the risk now

| # | Item | Why it can wait | Action now |
|---|---|---|---|
| P2-1 | Teacher login lockout is per IP (a student on the school network can lock the teacher out) | Needs a login redesign | Document the CLI reset (`teacher <name>`); plan per-username lockout with backoff |
| P2-2 | Hard-coded AI model names and prices (`lambda/gemini.py`) | Works today | Move to a data file + a test that fails when the provider rejects a model; add a teacher-visible "model retired" message |
| P2-3 | Vendored `pypdf` has no update tracking | Reading is bounded (pages, size, separate function) | Add a scheduled check that opens an issue when a newer pypdf exists |
| P2-4 | Old permanent-key deploy path still shipped | Confusing, bigger surface | Mark deprecated in docs/CHANGELOG now; remove in the next major version with notice |
| P2-5 | One teacher, no audit log, no class-wide defaults | Pilot will tell you what teachers really click | Add "new students start with these features" defaults first; then bulk actions; audit log later |
| P2-6 | Security hardening items deferred on purpose (the F2/F3 notes) | You decided to focus on features | Keep them written with a trigger: "re-open at first outside teacher" |
| P2-7 | Student backends have open internet egress | Accepted for a class | Say it plainly in PRIVACY.md and SECURITY.md |
| P2-8 | Multi-region / DR | Out of shape for one install per org | Document RPO/RTO honestly (export frequency, PITR 35 days) |

## 5. P3: polish

- Content-Security-Policy on the portal (the page code avoids `innerHTML`, so risk is low; `template.yaml` has no CSP today). Add and test against real pages.
- Make `npm`-published command optional after the pilot (`npx class-platform-teacher`), only with a name check and provenance.
- Two-minute demo recording; a one-page "run a class in a week" plan; README quickstart verified by a stranger.
- Re-read all docs for stale numbers (the spec's machine-checked facts help; limits changed in 2.5.x).

## 6. Suggested order (about 4–6 weeks of part-time work)

| Week | Do | Why this order |
|---|---|---|
| **Today** | Reopen the AWS Support case (P0-1a); put the Free-plan end date in your calendar (P0-8); take a fresh `export` | Longest wait and cheapest insurance |
| **Week 1** | P0-4 PRIVACY.md; P0-6 abuse contact + `/report` + guidance; P1-2 repository settings + Code of Conduct + good-first-issues | Paperwork and settings need no AWS verification |
| **Week 2** | P0-5 "Erase this student" (build + test + live on `test_claude`); P0-7 finish sign-in live test; P0-3 backup/restore proof on your platform | Mostly your existing test bed |
| **Week 3** (when AWS verifies) | P0-1 clean-room installs ×2 (no domain, then domain); P0-2 alerts and Budget live; P1-4 capacity note | The real proof |
| **Week 4** | P1-1 live regression + CI matrix + upgrade test; P1-5 document/sign MCP; P1-7 support statement | Stops future regressions |
| **Weeks 5–8** | **Pilot** with 3–5 outside teachers (see gates); run the answer-quality track (P1-3) in the gaps; fix pilot findings; release | Real use finds what you cannot |
| After pilot | Gate 2 review → announce (section 8) | |

## 7. Gates (when to move on)

| Gate | Must be true |
|---|---|
| **Gate 1: safe to pilot** | All P0 items done and proven on real AWS; two clean installs; the live suite is green; `PRIVACY.md` published; "Erase this student" works |
| **Gate 2: safe to announce** | ≥3 teachers outside you each ran a real class ≥2 weeks; no author intervention on their accounts; no data loss; no unexplained cost; every pilot problem fixed or listed as a known limitation; P1 done |
| **Gate 3: drop "beta"** | A year of releases without a breaking upgrade; an outside contributor merged; least-privilege installer permissions or SSO; a third-party security review |

Measures to track during the pilot: minutes from "I have an AWS account" to "first student's site is live"; times you had to intervene per install; "busy"/error answers per class session; support questions per teacher per week.

## 8. Announcement checklist (only after Gate 2)

- README: beta label, one-paragraph description, verified quickstart, cost/alerts page, support statement.
- Repository: items in P1-2 done; Scorecard/CodeQL read.
- Documents: `PRIVACY.md`, teacher guide cost + recovery pages, `SECURITY.md` with the verification steps and the known limits (MCP package unsigned, open egress).
- Post to the pilot teachers first; then wider communities **only when someone can answer questions the same day**. One tagged release a month; a patch within days for any P0.

## 9. What not to do before the pilot

- Do not add new student-facing features, new providers or a hosted service.
- Do not publish to npm yet.
- Do not make claims about answer quality, "safe for minors", or "GDPR/COPPA compliant". You have no evidence or legal review for those; say what the platform does, and tell teachers to check their own rules.
- Do not run many create/delete cycles on the new AWS account (it triggered the verification block).
- Do not ever put real tokens, account numbers or keys in docs or chat; the repo has a test for this, keep it.

## 10. Decisions only you can make

| # | Decision | My recommendation |
|---|---|---|
| 1 | Which 3–5 teachers to invite for the pilot | People you know who can open an AWS account and will tell you the truth |
| 2 | Approve the benchmark document list (for the quality track) | Approve a small, freely licensed set so measuring can start |
| 3 | Visitor sign-in privacy stance (shared pool across apps) | Keep, but state it in PRIVACY.md; offer per-app erase |
| 4 | Whether the first announcement says "beta" | Yes, until Gate 3 |
| 5 | npm publishing | After the pilot |
