# Documentation map

Start here. Section 1 is a one-page overview of the project. Section 2 says which document to read for what, and which documents describe **built** features and which describe **future** work.

## 1. The project in one page

**What it is.** A teacher installs a class hosting platform in **their own AWS account** with one command. Each student gets a **token** (not AWS access), publishes a website, and optionally a small backend, class AI, storage, books to answer from, background jobs, and sign-in with Google for their app.

**The parts**

| Part | What it is | Where it lives |
|---|---|---|
| **The platform** | What runs in the teacher's AWS account: student sites (S3 behind CloudFront), the web portal (teacher and student pages), the API functions (Lambda), data (DynamoDB), and the optional features | `template.yaml`, `lambda/`, `web/`, `config/settings.json` |
| **The teacher tooling** | What the teacher runs from a computer or AWS CloudShell: install, plan and apply, verify, export and import, destroy, the editor tools. Shipped as one package on GitHub Releases | `setup.mjs`, `installer/`, `teacher-mcp/` |
| **The student kit** | A starter project with rules for AI editors, a one-command deploy, helpers (class AI, storage, knowledge, agent, quality, sign-in, traces), add-ons and a small editor tool server. It comes from the teacher's portal and keeps itself in step with the teacher's version | `starter/`, `student/` |

**One version.** The platform, the student kit and the editor package share one number (`VERSION`) and one tag. The teacher's portal is the only authority on versions: students never choose one.

**Words you will meet**

| Word | Meaning |
|---|---|
| **Token** | A student's login (`mds_...`), shown once. Only its hash is stored. It expires; the student can ask for a new one |
| **Portal** | The web pages and API at the deploy address: the teacher page and the student page |
| **Slug** | A student's site name, giving `https://<slug>.<your domain>/` |
| **Stack** | The AWS resources the installer creates (one CloudFormation stack, `student-hosting` by default) |
| **Feature** | Backend, class AI, class storage, class knowledge, background jobs, custom domain, traces, sign-in. Installed once; **off for every student** until the teacher switches it on for that student. The server enforces it |
| **Plan and apply** | An update is first a plan (what would change; nothing changes), then an apply bound to that plan. A change that would replace or remove data is refused |
| **Kit and sync** | The kit's own files are brought in line with the teacher's portal before a deploy; the old copies go to `.kit-backup/`. The student's own code is never touched |
| **Export and import** | A backup of what a class made, and a restore into a freshly installed platform (books and projects included; traces are not) |

**How a deploy flows.** The student runs `npm run deploy`, or asks the editor. The project syncs its kit with the portal, signs in with its token, publishes the backend (if the teacher enabled it) and the website. The portal checks the files, uploads them to S3 and refreshes the CDN.

**Who can do what**

| | Teacher | Student |
|---|---|---|
| AWS access | full, in their own account | none |
| Can change | the platform, every student's features, caps and tokens | only their own site, backend and files |
| Cannot | (nothing is hidden from the teacher) | switch a feature on, see another student's data, hold an AWS key |
| AI editor can | check, plan and (with a yes) apply, publish | check, deploy (with a yes), read status |
| Terminal only | deleting the platform, passwords, AI keys, IAM | their token (`npm run login`) |

## 2. Which document for what

### 2.1 Start here

| Document | Audience | What it is |
|---|---|---|
| [../README.md](../README.md) | everyone | What it is, the quickstart, known limitations |
| [TEACHER_SETUP.md](TEACHER_SETUP.md) | teachers | The complete guide: install, first class, features, alerts, updates, backups, destroy, troubleshooting |
| [../student/README.md](../student/README.md) | students | The student guide |
| [STATUS.md](STATUS.md) | everyone | **What is built**, by area and release, how well each part is proven, the current limits |
| [FUTURE.md](FUTURE.md) | maintainers, the author | **What is not built yet**, grouped by theme, with the test for "done" |
| [../CHANGELOG.md](../CHANGELOG.md) | teachers | Every release: what is new and what a teacher must do |
| [../HANDOFF.md](../HANDOFF.md) | maintainers | How to work on the project, the release procedure, gotchas |

### 2.2 Reference (built, kept current)

| Document | What it is |
|---|---|
| [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) | The source of truth: architecture, data model, every feature, limits, security. Its numbers are checked against the code by the test suite |
| [API_REFERENCE.md](API_REFERENCE.md) | Every HTTP route, request, response and error code |
| [TEACHER_ARCHITECTURE.md](TEACHER_ARCHITECTURE.md) | How the teacher side is built: installer, plan and apply, verify, destroy, export and import, the editor tools |
| [STUDENT_KIT.md](STUDENT_KIT.md) | The kit in depth: the journey, enforcement layers, the checker, publishing, sync |
| [MCP.md](MCP.md) | The student tool server and the `class-platform-mcp` package |
| [SETTINGS.md](SETTINGS.md) | Generated from `config/settings.json`: every platform limit, with its default, range, scope and risk |
| [AWS_LEARNINGS.md](AWS_LEARNINGS.md) | A learning guide to the AWS ideas in this project (not instructions) |

### 2.3 Built features

| Document | What it is |
|---|---|
| [AI_CONNECTOR.md](AI_CONNECTOR.md) | Class AI: setup, per-student keys, caps, threat model |
| [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md) | Class storage: isolation, caps, helpers |
| [KNOWLEDGE_CONNECTOR.md](KNOWLEDGE_CONNECTOR.md) | Class knowledge: books and a class library to answer from; limits, cost, permissions |
| [CHATWOOT_ADAPTER.md](CHATWOOT_ADAPTER.md) | Chatwoot Agent Bot webhook (step T1: receive, verify, de-duplicate, record; no reply yet); how to turn it on, what is stored, permissions |
| [RAG_GUIDE.md](RAG_GUIDE.md) | How class knowledge works end to end in plain words, every setting, a tuning playbook |
| [AGENT_GUIDE.md](AGENT_GUIDE.md) | The student agent: one run step by step, guardrails, approval, settings, cost |
| [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md) | Work longer than a request: parts, states, limits, security, cost |
| [TRACE_SCHEMA.md](TRACE_SCHEMA.md) | Traces: the format, privacy levels, retention, who sees what, the archive |
| [PROJECT_SYNC.md](PROJECT_SYNC.md) | Project sync: source files on the platform (second computer, team projects) without GitHub accounts |
| [ADDONS.md](ADDONS.md) | Add-ons: teacher-approved skills and open-source packages the AI can add; the safety rules; how a teacher adds one |
| [SIGN_IN.md](SIGN_IN.md) | Sign in with Google for the apps students build: the teacher's one-time setup, the student's switch, what a visitor sees |
| [STUDENT_IDENTITY.md](STUDENT_IDENTITY.md) | Students signing in with Google: requests, pairing computers, the teacher's modes (2.11.0) |
| [PROJECT_HOME.md](PROJECT_HOME.md) | The project cards on the teacher and student homes, and people on a project by e-mail (2.11.0) |
| [AUTH_PLAN.md](AUTH_PLAN.md) | Design and status of sign in for student apps (built; remaining items in FUTURE) |
| [INSTALLABLE_PLAN.md](INSTALLABLE_PLAN.md) | Design and status of installable sites (built; remaining items in FUTURE) |
| [SETTINGS_PLAN.md](SETTINGS_PLAN.md) | Status of the settings registry (steps S0 and S1 built; S2 to S5 future) |
| [COMPUTE_WORKER.md](COMPUTE_WORKER.md) | The optional teacher-only machine for heavy checks: install, controls, cost, protection (no tasks yet) |

### 2.4 Future work (not built)

| Document | What it is |
|---|---|
| [FUTURE.md](FUTURE.md) | **The one list of unbuilt work**, with what each item needs and when it is done |
| [future/RAG_ROADMAP.md](future/RAG_ROADMAP.md) | Design: measured, trustworthy book answers (benchmark, guards, Hindi, drift) |
| [future/CONFIGURATION_PLAN.md](future/CONFIGURATION_PLAN.md) | Design: student project and shelf settings files inside the teacher's ranges |
| [future/MIGRATION_PLAN.md](future/MIGRATION_PLAN.md) | Design: a migration assistant that says whether an existing app can move onto the platform |

### 2.5 Background and project files

| Document | What it is |
|---|---|
| [../SECURITY.md](../SECURITY.md) | Reporting a problem and verifying a release |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How to contribute |
| [../student/BACKEND.md](../student/BACKEND.md), [../student/CICD.md](../student/CICD.md) | Writing a student backend; deploying from GitHub |
| [../student/LEGACY_DEPLOY_KEY.md](../student/LEGACY_DEPLOY_KEY.md) | The old permanent-key deploy path (still shipped; to be deprecated and removed, see FUTURE.md O5) |

## 3. Suggested reading order

- **I am a teacher:** the README, then [TEACHER_SETUP.md](TEACHER_SETUP.md), then [../student/README.md](../student/README.md).
- **I want to understand how it works:** this page, then [STATUS.md](STATUS.md), then [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) sections 1 to 4, then [TEACHER_ARCHITECTURE.md](TEACHER_ARCHITECTURE.md) and [STUDENT_KIT.md](STUDENT_KIT.md).
- **I want to contribute or maintain:** [../HANDOFF.md](../HANDOFF.md), [STATUS.md](STATUS.md), [FUTURE.md](FUTURE.md), [../CONTRIBUTING.md](../CONTRIBUTING.md).
- **I want to know what comes next:** [FUTURE.md](FUTURE.md) only.

## 4. Rules for these documents

- A document describes **built** features in the present tense. Unbuilt work goes only in [FUTURE.md](FUTURE.md) (or in a design under `future/`, linked from it).
- Each release updates [STATUS.md](STATUS.md), the changelog and, where a number changed, the specification. The test suite fails if a specification number disagrees with the code.
- Plans that are built are rewritten as status records (the file name stays when code refers to it), not kept as plans.
