# Documentation map and one-page overview

Start here if you want to understand the whole project. The first half is a one-page overview; the second half tells you which document to read for what, and which ones are current and which are history.

## 1. The project in one page

**What it is.** A teacher installs a class hosting platform in **their own AWS account** with one command. Each student gets a **token** (not AWS access) and publishes their own website, and optionally a small backend with class AI and class storage, mostly from an AI editor. The teacher decides, per student, which features are on and how much they may spend.

**The three parts**

| Part | What it is | Where it lives |
|---|---|---|
| **The platform** | Everything that runs in the teacher's AWS account: the student sites (S3 behind CloudFront), the web portal (teacher page and student page), the API and its functions (Lambda), the data (DynamoDB), the optional connectors (student backends, class AI, class storage) and custom domains (CloudFront multi-tenant). | `template.yaml`, `lambda/`, `web/` |
| **The teacher tooling** | What a teacher runs from their own computer or AWS CloudShell: install, plan and apply updates, verify, export and import, destroy, and the editor tools that let an AI editor do the safe parts. Distributed as one package from a GitHub release. | `setup.mjs`, `installer/`, `teacher-mcp/`, `teacher-package/` |
| **The student kit** | A starter project with rules for AI editors, a one-command deploy, the class AI and storage helpers, and a small tool server for the editor (`class-platform-mcp`). It comes from the teacher's portal and keeps itself in line with the teacher's version. | `starter/`, `student/` |

**One version.** The platform, the student kit and the editor package share one version number (the `VERSION` file) and one tag. The teacher's portal is the only authority on versions: students never choose one.

**The words you will meet**

| Word | Meaning |
|---|---|
| **Token** | A student's login (`mds_...`), shown once when the teacher creates it. Only its hash is stored. It expires; a student can request regeneration. In a student's project it lives in `.env.local`. |
| **Portal** | The web pages and API at `deploy.<your domain>` (or a `cloudfront.net` address): the teacher page and the student page. |
| **Slug** | A student's site name, which gives `https://<slug>.<your domain>/`. |
| **Stack** | The set of AWS resources the installer creates (a CloudFormation stack, `student-hosting` by default). |
| **Feature** | Backend, class AI, class storage or custom domain: each is installed, and **off for every student** until the teacher switches it on for that student. The server enforces it. |
| **Plan and apply** | An update is first a plan (what would change; nothing is changed), then an apply bound to that plan. Anything that would replace or remove data is refused. |
| **Kit and sync** | The kit's own files (rules, `tools/`, the deploy script, the helpers) are brought in line with the teacher's portal before a deploy; the old copies go to `.kit-backup/`. The student's own code is never touched. |
| **Export and import** | A backup of everything a class made, and restoring it into a **freshly installed** platform (one platform, one class). |
| **Teacher tools and student tools** | Two separate tool servers for AI editors. The teacher's are all named `platform_...`; the student's are `init_project`, `check_project`, `deploy`, `status`, `update_kit`. |

**How a deploy flows.** The student runs `npm run deploy` (or asks the editor). The project first syncs its kit files with the portal, then signs in with the project's token, then publishes the backend (if the teacher enabled it) and the website: the files go to S3 through a short-lived upload address, the portal validates and publishes them, and CloudFront serves the site. A backend request travels CloudFront, then the gateway (which checks the student's allowance), then that student's own function.

**Who can do what**

| | Teacher | Student |
|---|---|---|
| AWS access | full, in their own account | none |
| Can change | the platform, every student's features, caps and tokens | only their own site, backend and files |
| Cannot | (nothing is hidden from the teacher) | switch a feature on, see another student's data, hold an AWS key |
| AI editor can | check, plan and (with a yes) apply, publish | check, deploy (with a yes), read status |
| Terminal only | deleting the platform, passwords, AI keys, IAM | their token (`npm run login`) |

## 2. Which document for what

### Read these first

| Document | Audience | What it is |
|---|---|---|
| [../README.md](../README.md) | everyone | what it is, the quickstart, known limitations, security and privacy |
| [TEACHER_SETUP.md](TEACHER_SETUP.md) | teachers | the complete guide: install, first class, features, custom domains, updates, backups, destroy, troubleshooting |
| [../student/README.md](../student/README.md) | students | the student guide |
| [STATE.md](STATE.md) | owner, maintainers | an honest assessment: strengths, gaps by priority, when to ship |
| [../HANDOFF.md](../HANDOFF.md) | maintainers | state, how to work, release procedure, rules, what is next |

### Reference (kept current)

| Document | What it is |
|---|---|
| [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md) | **The source of truth:** scope, architecture, data model, state machines, every feature, limits, security. Its facts are checked by tests. If another document disagrees, this one wins. |
| [TEACHER_ARCHITECTURE.md](TEACHER_ARCHITECTURE.md) | How the teacher side is built (installer engine, plan and apply, verify, destroy, export and import, transfer, the editor tools) |
| [STUDENT_KIT.md](STUDENT_KIT.md) | The student kit in depth: journey, enforcement layers, the checker, publishing, how it stays in sync |
| [MCP.md](MCP.md) | The student tool server and the `class-platform-mcp` package: tools, setup, tokens, updates, security |
| [AI_CONNECTOR.md](AI_CONNECTOR.md) | Class AI: setup, per-student keys, caps, threat model |
| [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md) | Class storage: isolation, caps, helpers |
| [API_REFERENCE.md](API_REFERENCE.md) | Every HTTP route, request, response and error code |
| [END_TO_END.md](END_TO_END.md) | The whole workflow and day-to-day operation; section 4 describes the manual (SAM) route |
| [../SECURITY.md](../SECURITY.md) | Reporting a problem, the security model, how to verify a release |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How to contribute |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed in each release |
| [../student/BACKEND.md](../student/BACKEND.md), [../student/CICD.md](../student/CICD.md) | Writing a student backend; deploying from GitHub |

### Background and history (read for understanding, not instructions)

| Document | What it is |
|---|---|
| [AWS_LEARNINGS.md](AWS_LEARNINGS.md) | A learning guide to the AWS ideas in this project |
| [NEW_ACCOUNT_AND_TRANSFER.md](NEW_ACCOUNT_AND_TRANSFER.md) | A worked example of moving to a new AWS account (what a new account needs, including CloudFront verification) |
| [learning.md](learning.md) | Lessons learned: mistakes and how they were fixed |
| [backend_wire.md](backend_wire.md) | The design reasoning for student backends (a historical plan) |
| [implement.md](implement.md) | The original step-by-step manual setup, Parts 1 to 4 |
| [LEGACY_MANUAL_SETUP.md](LEGACY_MANUAL_SETUP.md) | The original SAM and deploy-key route, superseded by the installer |
| [../student/LEGACY_DEPLOY_KEY.md](../student/LEGACY_DEPLOY_KEY.md) | The original per-student deploy-key instructions |

## 3. Suggested reading order

- **I am a teacher:** the README, then `TEACHER_SETUP.md`, then `../student/README.md` so you know what your students will see.
- **I want to understand how it works:** this page, then `TECHNICAL_SPECIFICATION.md` sections 1 to 4, then `TEACHER_ARCHITECTURE.md` and `STUDENT_KIT.md`.
- **I want to contribute or maintain:** `../HANDOFF.md`, `STATE.md`, `../CONTRIBUTING.md`, then the specification section for the area you will change.
- **I want to learn the AWS side:** `AWS_LEARNINGS.md`, then `learning.md`.
