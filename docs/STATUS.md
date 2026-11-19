# Project status (release 2.11.0)

> This document says **what is built** and **how well each part is proven**. What is not built yet is in [FUTURE.md](FUTURE.md). The release-by-release record is [CHANGELOG.md](../CHANGELOG.md); the exact facts are in [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md). Updated at every release.

## 1. Summary

- **Version 2.11.0**, public beta. One version number covers the platform, the student kit and the editor package (`VERSION`).
- **Built and released:** the install and update tooling, student sites with custom domains, student backends, class AI, class storage, class knowledge (books), background jobs, traces, project sync, add-ons, sign in with Google for student apps, the settings registry, the optional compute worker, and the signed student kit that keeps itself in step with the teacher's version.
- **Proven:** the author installed, updated, deployed and used every feature on a real AWS account, and the test suite (`node tests/run.mjs`, 55 groups) runs without AWS. The live smoke test (`tests/live_smoke.py`) covers sites, backends, storage and the portal, but **not yet** knowledge, traces, jobs or the compute worker.
- **Not proven:** use by anyone other than the author, classes of more than a few dozen students, regions other than `us-east-1`, a clean install of the current version on a new account, and moving a class to another account.

## 2. What is built, by area

| Area | What it does | Where it is documented | Released in |
|---|---|---|---|
| **Install and update** | One command installs the platform in the teacher's AWS account; `plan` and `apply` update it; `verify` checks it from outside; `destroy` removes it. The install asks for an alert e-mail and a monthly budget and sets the capacity caps from the account's Lambda limit | [TEACHER_SETUP.md](TEACHER_SETUP.md), [TEACHER_ARCHITECTURE.md](TEACHER_ARCHITECTURE.md) | 1.0 to 2.3.2 |
| **Backup and move** | `export` and `import` (including books and projects; not traces), `transfer` between accounts (rehearsed with simulated accounts only), point-in-time recovery for stored student data | [TEACHER_SETUP.md](TEACHER_SETUP.md) | 1.1, 2.3.2 |
| **Teacher portal** | Tokens, extend, take a site offline, feature switches per student, Usage, Requests, Library, Traces, Projects, Worker | [TEACHER_SETUP.md](TEACHER_SETUP.md), [API_REFERENCE.md](API_REFERENCE.md) | 1.0 to 2.3.2 |
| **Student sites** | Publish a site to `https://<name>.<domain>/` or a `cloudfront.net` address; security headers; install as an app (add to home screen) | [STUDENT_KIT.md](STUDENT_KIT.md) | 1.0, 2.0.0 (security headers 2.1.2), 2.6.0 |
| **Custom domains** | A student's own domain, switched on per student | [TEACHER_SETUP.md](TEACHER_SETUP.md) | 1.2.1 |
| **Student backends** | One Python or Node function per student behind `/api/...`; 27 s per request, 3,008 MB memory, 50 MB zipped; monthly run allowance; logs | [STUDENT_KIT.md](STUDENT_KIT.md), [../student/BACKEND.md](../student/BACKEND.md) | 1.0, 2.5.2 |
| **Class AI** | A capped, revocable AI key per student (OpenRouter or Google Gemini); the teacher sets the dollar cap | [AI_CONNECTOR.md](AI_CONNECTOR.md) | 1.0, 1.1 |
| **Class storage** | Capped records and files per student, reached through one helper | [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md) | 1.x (live 2026-10-24) |
| **Class knowledge** | Books (PDF, text, Markdown, HTML) a student's app answers from, with page and source; "The books do not say." when unsure; a class library; a class-wide monthly ceiling | [KNOWLEDGE_CONNECTOR.md](KNOWLEDGE_CONNECTOR.md), [RAG_GUIDE.md](RAG_GUIDE.md) | 2.2.0 |
| **Agent and quality helpers** | `class_agent` (budgeted, guarded, human approval for sensitive tools), `class_quality` (answer cache, feedback, evaluation), `PATTERNS.md` | [AGENT_GUIDE.md](AGENT_GUIDE.md) | 2.2.0 |
| **Background jobs** | Work longer than a request (up to 15 minutes), a second function per student, off by default | [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md) | 2.1.2 |
| **Traces** | A step-by-step record of each answer (model calls, passages, cost). Numbers only by default; masked or full text per student; retention 30, 60 or 90 days; Traces tab; daily summaries and e-mail alerts; archive download; student view with `npm run traces` | [TRACE_SCHEMA.md](TRACE_SCHEMA.md) | 2.3.1 |
| **Compute worker** (optional) | One small teacher-only EC2 machine, off until started, monthly hour cap, watchdog. **No tasks yet** | [COMPUTE_WORKER.md](COMPUTE_WORKER.md) | 2.3.1 |
| **Project sync** | `npm run push`, `pull`, `project`; team projects made by the teacher; automatic saving; content-addressed storage with checksums | [PROJECT_SYNC.md](PROJECT_SYNC.md) | 2.3.2 |
| **Add-ons** (phase 1) | Six skills and six modules from a teacher-approved catalogue, pinned and checked; `add` and `remove` with the AI's confirm-first rule | [ADDONS.md](ADDONS.md) | 2.4.0 |
| **Settings registry** (steps S0 and S1) | About 170 platform limits in one file, `config/settings.json`, read through one loader; `docs/SETTINGS.md` is generated from it | [SETTINGS.md](SETTINGS.md), [SETTINGS_PLAN.md](SETTINGS_PLAN.md) | 2.5.0 |
| **Terminal setup** | `npx <package> init` sets up a class project without an AI editor | [STUDENT_KIT.md](STUDENT_KIT.md) | 2.5.1 |
| **Sign in with Google for student apps** | One Cognito user pool for the class, one app client per student app (PKCE, no secret in the app), the student's switch, a helper, page parts, checks, and an add-on for apps made before 2.9.0 | [SIGN_IN.md](SIGN_IN.md), [AUTH_PLAN.md](AUTH_PLAN.md) | 2.8.0, 2.9.0, 2.10.0 |
| **Kit signing and self-sync** | The kit is signed with a KMS key; students' projects accept only signed, not older kits; the kit follows the teacher's version; replaced files go to `.kit-backup/` | [TEACHER_SETUP.md](TEACHER_SETUP.md), [STUDENT_KIT.md](STUDENT_KIT.md) | 2.0.0, 2.1.0 |
| **Operations** | Alarms and an AWS Budget (alerts e-mail), `verify` that checks every published file, daily sweeper, S3 versioning (30 days) | [TEACHER_SETUP.md](TEACHER_SETUP.md) | 1.1, 2.1.0, 2.3.2 |
| **Release and supply chain** | Versions in one place, signed releases (Sigstore), CodeQL, Scorecard, checkov, Dependabot | [../SECURITY.md](../SECURITY.md) | 2.0.3 |

## 3. How well each part is proven

"Live" means exercised on the author's real AWS account, not only by tests with fake AWS.

| Part | Test suite (no AWS) | Live smoke test | Known gap |
|---|---|---|---|
| Install, update, verify, destroy | yes | yes (upgrade in place) | **A clean install of the current version has not been run** on a new account |
| Student sites and portal | yes | yes | Capacity for many simultaneous visitors not measured |
| Custom domains | yes | yes (real domain) | Partly outside CloudFormation |
| Student backends and gateway | yes | yes | Capacity follows the account's Lambda limit (see section 5) |
| Class AI | yes | yes | Model names and prices are hard-coded |
| Class storage | yes | yes (section J) | Throttle not load-tested |
| Class knowledge | yes | **no** | No benchmark: answer quality is not measured |
| Traces | yes | **no** | The student web page panel is not built |
| Background jobs | yes | **no** (checked by hand, 2.1.2) | Few concurrent jobs by design |
| Project homes and people by e-mail ([PROJECT_HOME.md](PROJECT_HOME.md)) | yes | yes (2.11.0: owner checked cards, invites, pairing, removal, pull on a fresh kit) | Catalogue, direct request, Reveal and pull-and-merge are in FUTURE section 14 |
| Sign in for student apps | yes | yes (2.9.0: real Google sign-in, sign-out, second account, private route) | Teacher view of monthly users per app not built |
| Project sync | yes | push and pull exercised by the author | Team projects need more real use |
| Add-ons | yes | each module built in a fresh kit with real npm (2.4.0) | Teacher tab not built |
| Compute worker | yes | partly (boot, install, watchdog) | No tasks; Start and Stop from the page not yet tried by a person |
| Teacher editor tools (VS Code, Antigravity) | yes | confirmed by the owner | Depends on each editor's own behaviour |

## 4. What a class experiences today

- **Teacher:** installs in about 15 minutes (days if AWS must verify a new account first); creates tokens one at a time; switches features on per student; watches the Usage, Traces and Projects tabs. There are no class-wide defaults and no audit log.
- **Student:** gets a token, runs one command that connects an AI editor and sets up a project, asks the editor to build and publish. Needs Node 22, git and an AI editor. Projects stay in step with the teacher's kit automatically.
- **Everyone at once:** a new AWS account allows 10 concurrent Lambda executions, so about 3 backend requests run at once. After the teacher asks AWS for a higher limit (free, usually within a day) and runs `plan` and `apply`, the installer sets the caps from the limit (for example 247 requests at once for a limit of 1,000, 8 per student). `verify` warns until the account can serve a class of 30.

## 5. Current limits (the defaults; the teacher can change most in the portal or `config/settings.json`)

| What | Default | Where to change |
|---|---|---|
| Student backend request time | 27 seconds (the AWS front door ends every request at 30) | `backend.timeoutSeconds` |
| Student backend memory | 3,008 MB | `backend.memoryMB` |
| Background job time | 5 minutes (at most 15) | per student, `jobs.minutes` |
| Requests in flight, whole platform / per student | 3 / 2 for a new account; set from the Lambda limit after an install | `gateway.inflightGlobal`, `gateway.inflightPerStudent`, install |
| Files in storage | 5 MB a file, 100 files, 50 MB in total | per student; `storage.*` |
| Books | 50 MB a file on the platform (25 MB per student by default), 1,500 pages, class-wide ceiling 3 dollars a month | per student and `knowledge.*` |
| Traces | kept 90 days (30, 60 or 90 per student); a daily limit per student (default 2,000 traces) | per student; `traces.*` |
| Sign-in session | 7 days (`auth.sessionDays`) | `auth.sessionDays` |
| Teacher login | 8 hours; locked for 15 minutes after 5 wrong passwords from one address | `sessions.*` |

## 6. Decided on purpose (do not reopen without a reason)

| Decision | Why |
|---|---|
| One install per organisation, in the teacher's own AWS account | Ownership, cost and privacy stay with the teacher |
| Students never hold AWS credentials; the teacher's portal is the only version authority | Safety and one consistent kit |
| Every feature is installed once and off per student until switched on | Cost and risk stay under the teacher's control |
| Student code runs only in the student's own function; no worker access and no job queue for students | Untrusted code stays in the sandbox that already exists |
| Plain HTML, JavaScript and standard-library Python | Few dependencies, easy to audit |
| DynamoDB and S3 for students, no SQL database | On-demand, near-zero idle cost |
| The student's token lives only in the project (`.env.local`) | Two editor windows on one computer must not share one student |
| Traces store numbers only by default | Cheap, private, and the teacher chooses what text is kept |

## 7. Known limitations (current)

- **Built for classes of a few dozen.** Tokens are created one at a time; features are switched on per student; there are no class-wide defaults.
- **One teacher login** per platform; no co-teachers and no audit log.
- **One class per platform.** Do not import a backup into a platform that already has students.
- **Backups leave out traces** by design (download an archive from the Traces tab to keep them).
- **`transfer`** (moving a class to another AWS account) was only rehearsed with simulated accounts.
- **Student backends:** one function, standard library only, no SQL database.
- **Projects are whole files, not Git:** a file two people changed is never overwritten silently, but there is no merge.
- **No third-party security review** has been done. No Content-Security-Policy on the teacher portal.
- **Regions:** only `us-east-1` is tested.

Everything still to build, and the order it is in, is in [FUTURE.md](FUTURE.md).

## 8. Documents that describe this release

- [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md): the source of truth; its numbers are checked against the code by the test suite.
- [CHANGELOG.md](../CHANGELOG.md): what changed in each release and what a teacher must do.
- [FUTURE.md](FUTURE.md): what is not built yet.
