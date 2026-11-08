# Project status: the whole picture

> Written 2026-11-08 at release **2.3.1**, after about 30 days of work, from a read of the documentation and the code. It is a judgement, not a specification: the facts live in [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md), the problems in [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md), the plan in [ROADMAP.md](ROADMAP.md). Update it when the picture changes.

## 1. The conclusion in five lines

1. The platform **works and is feature-rich**: a teacher installs it with one command; students publish sites and backends from their AI editor; the teacher controls every feature, limit and cost per student.
2. It has been **proven only by its author**, on the author's own AWS account, mostly by upgrading in place. **Nobody else has installed it, and a clean install of the current version has never been run.**
3. The **core is solid** (security design, tests, release process). The **edges are thin**: alerts, backups of the newer features, capacity for a whole class using AI at once, and automated checks on real AWS.
4. It is **ready for a small, supervised pilot** (the author plus a few friendly teachers) once a handful of blockers are fixed, and **not yet ready for a public "install it yourself" announcement**.
5. The fastest way to the pilot is **stop adding features and close the gaps in CRITICAL_ISSUES.md**, then run the clean-room install.

## 2. What exists

| Area | What it is | Size |
|---|---|---|
| **Platform** (`template.yaml`, `lambda/`) | One CloudFormation stack: S3 and CloudFront for sites, an API for the portal, 9 Lambda functions (plus small CloudFront functions), 3 DynamoDB tables, a KMS key that signs the student kit | about 6,700 lines of Python plus a vendored PDF reader |
| **Portal** (`web/`) | Teacher page (students, requests, storage, library, traces, worker, usage) and student page | about 2,600 lines of JavaScript, no framework |
| **Teacher tooling** (`setup.mjs`, `installer/`, `teacher-mcp/`) | install, plan and apply, verify, destroy, export and import, transfer, editor tools | about 2,400 lines |
| **Student kit** (`starter/`, `student/`) | A starter project for AI editors, a one-command deploy, helpers for class AI, storage, knowledge, agents and answer quality, a checker, an editor tool server | about 9,600 lines |
| **Tests** (`tests/`) | 46 test groups run by one command (about 13,800 lines), no AWS needed; a separate live smoke test for real AWS | |
| **Release process** | Signed releases (Sigstore), CI, CodeQL, Scorecard, Dependabot, a changelog, issue templates | |
| **Documentation** | One map ([docs/README.md](README.md)), a teacher guide, a student guide, a specification whose facts are machine-checked, one guide per feature | |

## 3. The features, and how well each is proven

"Live" means it has been exercised on the author's real AWS account (not only by tests with fake AWS).

| Feature | Built | Exercised live | Weakest point |
|---|---|---|---|
| Install, update by plan and apply, verify, destroy | yes | yes (on the author's account; installs on a throwaway account earlier in the project) | **A clean install of the current version has not been run**; the test account is blocked by AWS CloudFront verification |
| Student sites and the portal | yes | yes | Capacity for many simultaneous visitors is untested |
| Custom domains for students | yes | yes (a real domain) | Lives partly outside CloudFormation |
| Backend (one function per student) | yes | yes | **Only 3 backend requests run at once on the whole platform** (see CRITICAL_ISSUES, item 3) |
| Class AI (OpenRouter and Gemini) | yes | yes | Model names and prices are hard-coded and will go stale |
| Class storage | yes | yes | Not point-in-time recoverable (it is in the export) |
| Background jobs | yes | yes (a long job, a crash, caps) | Few concurrent jobs by design |
| Class knowledge (books, class library, folder sync) | yes | yes (real PDFs, the class library, Hindi not measured) | **No benchmark, so answer quality is not measured**; one real wrongful refusal seen; thresholds cannot be set |
| Agent and answer-quality helpers | yes | yes (search, approval, resume) | Same: not measured |
| Traces (a record of every answer) | yes | yes | The teacher-login routes were checked by tests and by the author only partly |
| Compute worker (optional machine) | yes | partly (boot, install, watchdog) | **No tasks yet**; Start and Stop from the page not tried by the owner |
| Teacher editor tools and student editor tools | yes | yes (Antigravity) | Depends on the editor's own behaviour |
| Backup, restore and `transfer` | yes | export, destroy, install and import: yes; `transfer`: simulated only | **A backup does not include books or traces** |

## 4. How a class would actually use it, and where it breaks first

**Teacher.** Installs (15 minutes; days if AWS must verify the account), signs in, creates tokens one at a time, switches features on per student, watches the Usage tab. *Rough edges:* no alerts or budget are created, no class-wide defaults, the AWS Support case for a new account is an unavoidable wait, the instructions assume AdministratorAccess.

**Student.** Gets a token, runs one command that connects the editor, asks the editor to build and publish. *Rough edges:* Node 22 and git are required; the first minutes depend on the AI editor following the rules; if the teacher has not switched a feature on, the student must understand why.

**Everyone at once.** A class of 30 using the AI features in the same minute exceeds what the backend gateway allows (3 requests in flight, about 10 a second), so some students see "busy". It is by design on a new AWS account (10 concurrent executions), but it will be the first thing a real class notices.

## 5. Where the project is, honestly

| Stage | Meaning | Status |
|---|---|---|
| Works for its author | Built, tested, used on a real account | **Yes** |
| Works for a friendly outsider | Someone else installs it and runs a class with help | **Not yet** |
| Works for strangers without help | The docs and the installer are enough | Not yet |
| Safe to announce publicly | Known blockers closed, support path and abuse path exist | Not yet |

The project is a **strong public beta candidate**: about 80 percent of the work for a first pilot is done. The remaining 20 percent is the unglamorous part (setup safety, backups, capacity, real-AWS regression checks, privacy guidance) and is listed in [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md).

## 6. What was decided on purpose (so nobody re-opens it)

| Decision | Why |
|---|---|
| One install per organisation, in the teacher's own AWS account | Ownership, cost and privacy stay with the teacher |
| Students never hold AWS credentials; the teacher's portal is the only version authority | Safety and a single, consistent kit |
| Every feature is installed once and off per student until switched on | Cost and risk stay under the teacher's control |
| No student code outside the student's own function (no worker access, no job queue) | Untrusted code stays in the sandbox that already exists |
| An optional compute worker, off by default and teacher-only, with no tasks yet | A place for heavy checks later, without changing the platform |
| Own trace format, numbers only by default | Cheap, private, and exportable later |
| Plain HTML, JavaScript and standard-library Python | Few dependencies, easy to audit, easy to contribute to |

## 7. What to read next

- To fix what blocks a release: [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md).
- To see the order of work and the shipping plan: [ROADMAP.md](ROADMAP.md).
- To understand how it is built: [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md).
