# Future work: what is not built yet

> This is the **only** list of unbuilt work. What is built is in [STATUS.md](STATUS.md); the release record is [CHANGELOG.md](../CHANGELOG.md). Each item says **what** it is and **when it is done**. Items are grouped by theme, not scheduled: nothing here has a date. Where an item needs something outside the code (an AWS account, a document list, real apps from the author), it says so in the first line. Design notes for the larger unbuilt features are in [future/](future/).

## 1. Needed before a wider release

These block a public announcement. Items 1 to 5 are the gate to a pilot.

| # | Item | Done when |
|---|---|---|
| 1 | **Clean-room install.** The current version has never been installed on a new AWS account. *Needs a second AWS account verified by AWS Support.* | Two clean installs in a row, with no manual fix: once with no domain, once with a domain. Each runs `verify`, switches every feature on, then `destroy --everything`. |
| 2 | **Alerts and budget on a clean install.** Code shipped in 2.3.2; the e-mail path has not been run on a clean account. | A deliberate error sends an alert e-mail, and the AWS Budget exists. |
| 3 | **Backup test on a real account.** `export` and `import` with books and projects; point-in-time recovery on the student-data table (needs an `apply`). | A class with books is exported, the platform destroyed, rebuilt, and the books and projects are back. |
| 4 | **Capacity measurement.** Run `tests/live_load.py` with a student token on a platform whose quota is raised, and re-tune the caps if needed. | The result for a class of 30 shows no "busy" errors; the test is kept as a repeatable check. |
| 5 | **Live regression suite.** The live smoke test does not yet cover knowledge, traces, background jobs or the compute worker. CI runs on Linux only. | Those sections are added; a scheduled run against a test platform is green; a deliberately broken change turns it red; Windows and macOS run the no-AWS tests; an upgrade test (install the previous release, upgrade, verify) passes. The no-AWS suite is fully offline: one check in `tests/test_embeddings.py` ran for several minutes on the author's Windows machine, and one run was killed at four minutes, which points to a real network call. |
| 6 | **Privacy guidance and "Erase this student".** No privacy document for schools; no single action that removes everything about one student. | `docs/PRIVACY.md` exists (what is stored, where, for how long, who sees it, what leaves the account, a notice template for families). One action erases tokens, sites, backend, storage, books, traces and AI keys, with a report; a test covers it. |
| 7 | **Abuse path for public sites.** No way for the public to report a site; no per-site traffic limit or alarm. | A contact address appears on error pages and a `/report` page; a per-site traffic figure and alarm are on the Usage tab; the guide says what to do on a complaint. |
| 8 | **Repository settings** for the public release: private vulnerability reporting, branch protection on `main`, secret scanning with push protection, a code of conduct, five to eight "good first issue" tasks. | The settings are on, and checked. *Owner action.* |
| 9 | **Pilot with three to five outside teachers**, each with a real class for at least two weeks. Track minutes from "I have an AWS account" to "first site live", interventions per install, "busy" or error answers per class session, and support questions. | The pilot ends with every install done without the author editing the teacher's account, no data loss, no unexplained cost, and every problem fixed or listed as a known limitation. |
| 10 | **Free-plan account.** The author's live AWS account is on the Free plan, which ends with its credits. Free-plan accounts may also be refused some instance types. | The account is on a paid plan before the end date, a budget is set, and a recent export exists. *Owner action.* |

**Release gates.** Gate 1, *safe to pilot*: items 1 to 5 done and no open P0. Gate 2, *safe to announce*: the pilot passed, items 6 to 9 done. Gate 3, *drop "beta"* (later): a year of releases without a breaking upgrade, an outside contributor, least-privilege installer permissions or a sign-in path for the teacher, and a third-party security review.

## 2. Answer quality for class books (the largest track)

Full design: [future/RAG_ROADMAP.md](future/RAG_ROADMAP.md). The known problem: one real trace showed "The books do not say." although the search found the right passage with confidence, and nobody can yet measure how good book answers are.

| # | Item | Done when |
|---|---|---|
| Q0 | **Benchmark pack and runner:** 15 to 20 openly licensed documents in English and Hindi, a question set, an attack set, a report and a baseline. *Needs approval of the document list (source, size, licence) before any download.* | A baseline report exists for the current version. |
| Q1 | **Fix wrongful refusals**, measured against Q0. | The false-refusal rate falls within the target, and no document gets worse. |
| Q2 | **Shelf settings:** tier, thresholds, passage size, with safe ranges and the evaluation numbers beside them. | Changing a threshold visibly moves the benchmark. |
| Q3 | **Deterministic guards and a Review view** (a filtered view of the traces): injection flags, citation and number checks, personal-data rules including common Indian identifiers. | Attack success under 5 percent; guard decisions appear as trace steps. |
| Q4 | **Document profiler, Hindi and Hinglish support.** | Improves at least two very different documents and regresses none. |
| Q5 | **Groundedness judge and quote-only mode** for regulated shelves. | At least 95 percent of claims supported; wrong answers at most 2 percent. |
| Q6 | **Stability:** scheduled golden probes, drift alerts, a fallback chain (model, then passages only). | A deliberate quality drop is detected within a day. |
| Q7 | **Managed guard test** (Amazon Bedrock Guardrails) behind an interface, compared on the attack set in Hindi too. | Adopt only if it measurably wins. |
| Q8 | **Auto-tune** of presets and thresholds, with a report the owner approves. | Beats the rule-based choice on the benchmark. |
| Q9 | **Business readiness:** audit log, document-level permissions, document versions and effective dates. | Before any real business data is used. |

## 3. Platform settings

Design: [SETTINGS_PLAN.md](SETTINGS_PLAN.md). Steps S0 and S1 are built (2.5.0): every limit is in `config/settings.json`, read through one loader.

| # | Item | Done when |
|---|---|---|
| S2 | **Runtime overrides:** a stored settings item with history and validation, the portal API, a cache of about a minute, `node setup.mjs config` (list, show, set, reset), and the teacher editor tools. | Changing a runtime setting by command changes behaviour within a minute on the test platform; a value outside its range is refused. |
| S3 | **Platform settings page** in the teacher portal: groups, search, badges, reset, history. | The author changes a setting on the live portal and sees it take effect. |
| S4 | **Deploy-scope settings** (memory, timeouts, throttles) changed by command and applied by `plan` and `apply`; shown read-only in the portal with the command. | A changed value appears in the next plan and, after apply, in the stack. |
| S5 | **`config export` and `config import`**, a settings cookbook, and a link from the README. | A new person changes three settings using only the documents. |

## 4. Student configuration files

Design: [future/CONFIGURATION_PLAN.md](future/CONFIGURATION_PLAN.md). A project file (`class.config.json`) and per-shelf files that a student can read and edit, inside ranges the teacher sets, built on the settings registry (S0 and S1 are done).

- **Done when:** a student changes a value in their project file and the platform applies it inside the teacher's range; a value outside the range is refused with the reason.

## 5. Migration assistant

Design: [future/MIGRATION_PLAN.md](future/MIGRATION_PLAN.md). A rule-based scanner that gives an existing app a green, yellow or red verdict with reasons, then a guided move with the student's approval at each step. Nothing is built.

| # | Item | Done when |
|---|---|---|
| M0 | Scanner and report, with the terminal command and the rules file. *Needs 5 to 8 real sample apps from the author.* | On the sample set the verdicts match the expected ones; no green verdict is wrong. |
| M1 | Guided move for the four most common patterns (static or React site, small web API, file data, AI SDK). | Each moves a sample app end to end and passes its smoke tests. |
| M2 | Data copy and parity tests. | A moved app with data gives the same answers. |
| M3 | A "ask my teacher to switch on X" request in the portal, and more patterns. | A student sends a request and the teacher sees it. |

## 6. Add-ons, later phases

Phase 1 is built (2.4.0; see [ADDONS.md](ADDONS.md)).

- **Phase 2, teacher Add-ons tab:** per-class on and off switches, usage counts, and a catalogue that lives on the platform so it changes without a kit release. Done when a teacher turns an add-on off for one class and students see it disappear on their next sync.
- **Phase 3, teacher suggestions:** teachers propose an add-on; the author reviews and approves it. Done when a proposal can be made from the portal and approved into the catalogue.

## 7. Sign in for student apps, remaining parts

Built: the platform side (2.8.0), the helper, switch and page parts (2.9.0), the add-on for older apps (2.10.0). Design and status: [AUTH_PLAN.md](AUTH_PLAN.md); guide: [SIGN_IN.md](SIGN_IN.md).

| # | Item | Done when |
|---|---|---|
| A4 | **Teacher view:** monthly users per app, a sign-in switch per app, a per-app user cap with a clear message, in the portal. | The cap stops new sign-ins for an app and the page shows the count. |
| A5 | **Google consent-screen check:** confirm the publishing rules against Google's current policy before a pilot. | Written down in SIGN_IN.md, with the date checked. |
| A6 | **Visitor privacy line:** the platform uses one shared sign-in pool, so one Google account is recognised across student apps. The notice shown to visitors must say so. | The notice is in the kit's sign-in guide and the template. |
| A7 | **More sign-in options** (email sign-in or other providers), only when a teacher asks, within the budget. | Built only on request. |

## 8. Installable apps

Built: the install files and button (2.6.0) and the add-on for older projects (2.7.0). Design: [INSTALLABLE_PLAN.md](INSTALLABLE_PLAN.md).

- **Real-device check:** install the site on one Android phone and one iPhone, and record a short checklist. Done when the icon opens the site full screen on both.
- **Offline support** (an optional service worker that keeps the app shell, never private data). Only if asked.

## 9. Traces

Built: the trace format, the Traces tab, privacy levels, retention, the student view (`npm run traces`), daily summaries and alerts, the archive download.

- **Replay** of stored questions with new settings, so a change can be tested against real questions.
- **A converted file for Phoenix** (an open tracing tool), for teachers who want a standard viewer.
- **A student web-page panel** that shows traces in the browser (the command-line view exists).

## 10. AI agents

Built: `class_agent` and `class_quality` with their limits (2.2.0). Guide: [AGENT_GUIDE.md](AGENT_GUIDE.md), section 13.

- A teacher screen for the agent's limits and a per-student agent cap, separate from the AI cap.
- An optional native tool-calling mode for providers that support it well.
- Parallel tool calls, streaming replies, memory across sessions, several cooperating agents.
- Approval inside a background job (today a job can return the request instead).
- A trace dashboard for agent runs, and step-level evaluation with a faithfulness check of the final answer.
- An `agent.json` profile per app (steps, time, allowed tools, approval rules).

## 11. Compute worker

Built and closed for its purpose (2.3.1). It has no tasks.

- **First task:** the benchmark or the daily probes from section 2, once they exist. The machine is ready for it.
- **Check by a person:** Start now, Stop now, the daily schedule and the cap, on the live page. Done when a person has done each once and written the result in COMPUTE_WORKER.md.

## 12. Operations and teacher features

| # | Item | Done when |
|---|---|---|
| O1 | **Cold starts** take about 10 seconds on the first book search after a quiet period. Measure where the time goes; keep the knowledge function warm only if the cost is small. | The first call is measured and documented; the student guide says a first call can be slow. |
| O2 | **Teacher login lockout** is per address, so a student on the same school network can lock the teacher out. Change to a lockout per username with an increasing delay; keep the command-line reset. | A wrong-password test from one address no longer locks the teacher's account out. |
| O3 | **Model names and prices** are written in the code. Move them to a data file with a test, and show the teacher "AI unavailable: model retired" when a provider rejects a model. | A retired model produces the message, not a broken AI for every student. |
| O4 | **The vendored PDF reader** (pypdf) is not tracked for updates. A scheduled check should compare its version with the latest release. | An issue is opened when a newer version exists. |
| O5 | **The old permanent-key deploy path** (`admin.mjs add`, the `/deploy` route, `student/deploy.mjs`) is still shipped. Mark it deprecated in one release, remove it in the next major version, with its documents. | Deprecation notice in the changelog; removal in the next major version. |
| O6 | **Deploy activity history** ("did my push go live?"); the checklist now ticks "published" from the last deploy of any kind. | The checklist shows the time and result of the last deploy of that student's site. |
| O7 | **AI spend:** an alert when the OpenRouter balance runs low, and a teacher view of AI spend across students. | Both appear on the Usage tab and in the alert e-mail. |
| O8 | **"Rotate this student's key"** button (a manual runbook exists). | One button replaces a student's AI key and revokes the old one. |
| O9 | **Custom-domain infrastructure** is partly outside CloudFormation. Move it in. | A stack update recreates the custom-domain resources with no manual step. |
| O10 | **Class management:** several teacher accounts, an audit log of who changed what, class-wide defaults for new students, notifications for requests. | Taken up after the pilot, in that order. |
| O11 | **Content-Security-Policy** on the teacher portal (student sites deliberately have none, so they can be embedded in school pages). | A strict policy is tested against the real uploads and pages. |
| O12 | **"Request more runs"** from the student, to ask the teacher for more backend runs. | A student's request appears in the Requests tab. |
| O13 | **Public template repository** for the "start a project from GitHub" option (the URL is set in the code). | The repository exists and the URL points to it. *Owner action.* |
| O14 | **Re-check the AI editor rules:** confirm that each editor (VS Code, Antigravity, Cursor, Claude Code) loads the rule files, and re-run the AI evaluation against the current rules. | A written result for each editor and a passing evaluation. |

## 14. Project homes, next steps

Built part: [PROJECT_HOME.md](PROJECT_HOME.md).

| # | Item | Done when |
|---|---|---|
| P1 | **Catalogue:** a new, not yet admitted person sees the names of projects the teacher marked "visible to all". | A requester's home lists them without granting access. |
| P2 | **Request access directly** to a project, without owning a site first. | A request for a named project reaches the teacher and, once accepted, adds the person. |
| P3 | **Tokens stored encrypted** (own key, encryption context project and member) with **Reveal / Copy / Rotate** for the student and the teacher, and an audit line per reveal. Old tokens become revealable only after Rotate. Needs a plan and apply (a new key and permission). | A person can see their own project token at any time; the teacher can see all of them; a reveal is logged. |
| P4 | **Pull first, merge, ask:** a publish pulls the latest, merges line by line, and asks the person to resolve real conflicts (CLI and the editor tool); the server refuses a publish from a stale revision. | Two people change different lines of one file and both publish with no lost work; a real conflict is shown and resolved. |
| P5 | **New project** on the teacher home creates the real project model (no old-style token first). | The card flow no longer produces a token the teacher must hand out. |

## 13. Out of scope for now

These were considered and set aside. Reopen only with a reason written down.

- Other AWS regions, a hosted multi-class service, a mobile app.
- Step Functions or other workflow services for students (a student-defined state machine would run under its own role).
- Student access to the compute worker, or a student job queue on the worker (background jobs already cover long student work).
- Publishing to npm (a shorter install command). Decide after the pilot; needs a name check, provenance and the author's go-ahead.
- Visitor sign-in beyond Google (item A7 covers it only on request).
