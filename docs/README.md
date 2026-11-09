# Documentation map

Start here. The first half is a one-page overview; the second half says which document to read for what.

## 1. The project in one page

**What it is.** A teacher installs a class hosting platform in **their own AWS account** with one command. Each student gets a **token** (not AWS access) and publishes a website, and optionally a small backend with class AI, storage and books to answer from.

**The three parts**

| Part | What it is | Where it lives |
|---|---|---|
| **The platform** | What runs in the teacher's AWS account: student sites (S3 behind CloudFront), the web portal (teacher and student pages), the API functions (Lambda), data (DynamoDB), and the optional features | `template.yaml`, `lambda/`, `web/` |
| **The teacher tooling** | What the teacher runs from their computer or AWS CloudShell: install, plan and apply updates, verify, export and import, destroy, and editor tools. Shipped as one package on GitHub Releases | `setup.mjs`, `installer/`, `teacher-mcp/` |
| **The student kit** | A starter project with rules for AI editors, a one-command deploy, the helpers (class AI, storage, knowledge, agent, quality) and a small editor tool server. It comes from the teacher's portal and keeps itself in line with the teacher's version | `starter/`, `student/` |

**One version.** The platform, the student kit and the editor package share one number (`VERSION`) and one tag. The teacher's portal is the only authority on versions: students never choose one.

**The words you will meet**

| Word | Meaning |
|---|---|
| **Token** | A student's login (`mds_...`), shown once. Only its hash is stored. It expires; a student can request a new one |
| **Portal** | The web pages and API at `deploy.<your domain>`: the teacher page and the student page |
| **Slug** | A student's site name, giving `https://<slug>.<your domain>/` |
| **Stack** | The AWS resources the installer creates (one CloudFormation stack, `student-hosting` by default) |
| **Feature** | Backend, class AI, class storage, class knowledge, background jobs, custom domain, traces. Installed once; **off for every student** until the teacher switches it on for that student. The server enforces it |
| **Plan and apply** | An update is first a plan (what would change; nothing changes), then an apply bound to that plan. A change that would replace or remove data is refused |
| **Kit and sync** | The kit's own files are brought in line with the teacher's portal before a deploy; the old copies go to `.kit-backup/`. The student's own code is never touched |
| **Export and import** | A backup of what a class made and a restore into a **freshly installed** platform (see the known gaps in [STATUS.md](STATUS.md)) |

**How a deploy flows.** The student runs `npm run deploy` (or asks the editor). The project syncs its kit with the portal, signs in with its token, publishes the backend (if the teacher enabled it) and the website. The portal checks the files, uploads them to S3 and refreshes the CDN.

**Who can do what**

| | Teacher | Student |
|---|---|---|
| AWS access | full, in their own account | none |
| Can change | the platform, every student's features, caps and tokens | only their own site, backend and files |
| Cannot | (nothing is hidden from the teacher) | switch a feature on, see another student's data, hold an AWS key |
| AI editor can | check, plan and (with a yes) apply, publish | check, deploy (with a yes), read status |
| Terminal only | deleting the platform, passwords, AI keys, IAM | their token (`npm run login`) |

## 2. Which document for what

### Start here

| Document | Audience | What it is |
|---|---|---|
| [../README.md](../README.md) | everyone | What it is, the quickstart, known limitations |
| [TEACHER_SETUP.md](TEACHER_SETUP.md) | teachers | The complete guide: install, first class, features, alerts, updates, backups, destroy, troubleshooting |
| [../student/README.md](../student/README.md) | students | The student guide |
| [STATUS.md](STATUS.md) | owner, maintainers | **Where the project stands**: what is proven, what is not, the shape of the whole |
| [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md) | owner, maintainers | **What blocks a wider release**, ranked, with evidence and fixes |
| [ROADMAP.md](ROADMAP.md) | owner, maintainers | **The way forward** and the plan for shipping as open source |
| [../HANDOFF.md](../HANDOFF.md) | maintainers | How to work on the project, the release procedure, gotchas |

### Reference (kept current)

| Document | What it is |
|---|---|
| [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) | The source of truth: scope, architecture, data model, state machines, every feature, limits, security. Its facts are checked by tests; if another document disagrees, this one wins |
| [API_REFERENCE.md](API_REFERENCE.md) | Every HTTP route, request, response and error code |
| [TEACHER_ARCHITECTURE.md](TEACHER_ARCHITECTURE.md) | How the teacher side is built: installer, plan and apply, verify, destroy, export and import, the editor tools |
| [STUDENT_KIT.md](STUDENT_KIT.md) | The kit in depth: journey, enforcement layers, the checker, publishing, sync |
| [MCP.md](MCP.md) | The student tool server and the `class-platform-mcp` package |

### The features

| Document | What it is |
|---|---|
| [AI_CONNECTOR.md](AI_CONNECTOR.md) | Class AI: setup, per-student keys, caps, threat model |
| [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md) | Class storage: isolation, caps, helpers |
| [KNOWLEDGE_CONNECTOR.md](KNOWLEDGE_CONNECTOR.md) | Class knowledge: books and a class library to answer from; limits, cost, permissions |
| [RAG_GUIDE.md](RAG_GUIDE.md) | How class knowledge works end to end in plain words, every setting, a tuning playbook |
| [AGENT_GUIDE.md](AGENT_GUIDE.md) | How the student agent works: one run step by step, guardrails, approval, settings, cost |
| [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md) | Work longer than a request: parts, states, limits, security, cost |
| [TRACE_SCHEMA.md](TRACE_SCHEMA.md) | Traces: the format, privacy levels, retention, who sees what, the archive |
| [PROJECT_SYNC.md](PROJECT_SYNC.md) | Project sync: source files shared through the platform (second computer, teams) without GitHub accounts; how it works, limits, routes |
| [COMPUTE_WORKER.md](COMPUTE_WORKER.md) | The optional teacher-only machine for heavy checks: install, controls, cost, protection |

### Plans (not built)

| Document | What it is |
|---|---|
| [ROADMAP.md](ROADMAP.md) | The overall plan |
| [RAG_ROADMAP.md](RAG_ROADMAP.md) | The plan for trustworthy, measured answers: benchmark, guards, Hindi, drift |
| [ADDONS.md](ADDONS.md) | Add-ons: teacher-approved skills and open-source packages the AI can add to a project (kit 2.4.0): what they are, the safety rules, how a teacher adds one |
| [ADDONS_PLAN.md](ADDONS_PLAN.md) | The plan behind add-ons: phase 1 built, phases 2 and 3 (teacher tab, suggestions) still plans |
| [SETTINGS.md](SETTINGS.md) | **Reference, generated:** every platform setting (limit, default, time) with its range, scope, risk and what it does, from `config/settings.json` |
| [SETTINGS_PLAN.md](SETTINGS_PLAN.md) | The plan for one settings registry, one portal page and one command: steps S0 and S1 (registry, loader) built; S2 to S5 still plans |
| [CONFIGURATION_PLAN.md](CONFIGURATION_PLAN.md) | One place for every setting: per-shelf files, a project file, teacher ranges |

### Background

| Document | What it is |
|---|---|
| [AWS_LEARNINGS.md](AWS_LEARNINGS.md) | A learning guide to the AWS ideas in this project (not instructions) |
| [../SECURITY.md](../SECURITY.md), [../CONTRIBUTING.md](../CONTRIBUTING.md), [../CHANGELOG.md](../CHANGELOG.md) | Reporting a problem and verifying a release; how to contribute; what changed |
| [../student/BACKEND.md](../student/BACKEND.md), [../student/CICD.md](../student/CICD.md) | Writing a student backend; deploying from GitHub |

## 3. Suggested reading order

- **I am a teacher:** the README, then `TEACHER_SETUP.md`, then `../student/README.md`.
- **I want to understand how it works:** this page, then `STATUS.md`, `TECHNICAL_SPECIFICATION.md` sections 1 to 4, then `TEACHER_ARCHITECTURE.md` and `STUDENT_KIT.md`.
- **I want to contribute or maintain:** `../HANDOFF.md`, `STATUS.md`, `CRITICAL_ISSUES.md`, `ROADMAP.md`, `../CONTRIBUTING.md`.
- **I want to learn the AWS side:** `AWS_LEARNINGS.md`.
