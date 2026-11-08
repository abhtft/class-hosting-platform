# Roadmap: from here to a public open-source release

> Written 2026-11-08 at release 2.3.1. It turns [CRITICAL_ISSUES.md](CRITICAL_ISSUES.md) into an order of work and says how to ship the project so that **a teacher and their students can use it with the fewest possible errors**. Estimates are rough and assume one maintainer working with an AI assistant. Where the owner must decide, it says so.

## 1. The strategy in four lines

1. **Stop adding features** (except the quality work in section 4). The platform has more features than proof.
2. **Close the P0 gaps** (alerts, capacity, backups), then **prove a clean install** on a second AWS account.
3. **Pilot with three to five teachers** who are not the author, supported personally.
4. **Announce publicly only when the pilot passes its gates** (section 5).

## 2. The phases

| Phase | Goal | Work | Done when | Rough time |
|---|---|---|---|---|
| **0. Safety net** | A teacher is told when something is wrong, and a rebuild does not lose books | Installer sets the alert email and an AWS Budget and `verify` checks them (issue 1); the export and import include `knowledge/`, the student-data table gets point-in-time recovery, traces are documented as expendable or exported (issue 4) | A test installs with an alert email, a deliberate error produces an email, and an export and import of a class with books restores the books | 3 to 5 days |
| **1. Capacity** | A class of 30 can ask the AI at once without "busy" | Read the account's Lambda quota at install and set the in-flight caps and API throttle from it; raise the defaults where allowed; make the helpers retry politely; put the capacity in the guide (issue 3) | The load measurement in CRITICAL_ISSUES item 3 shows no refusals at the documented class size, kept as a repeatable test | 3 to 5 days |
| **2. Clean-room install** | A stranger's install works from nothing | Get the second AWS account verified (support case, **owner**); follow only the README quickstart in CloudShell with no domain, then again with a domain; switch every feature on; `verify`; `destroy --everything`; fix every hesitation in the installer and the guide (issue 2) | Two clean installs in a row without a manual fix, in under 45 minutes of the teacher's time, apart from waiting for AWS | 1 to 2 weeks (mostly waiting on AWS) |
| **3. Real-AWS regression** | The next release cannot silently break what works | Extend the live smoke test to knowledge, traces, jobs and the worker status; add the load check; run it on a schedule against a test platform; add Windows and macOS to CI for the no-AWS tests; add an upgrade test (install the previous release, upgrade, verify) (issue 5) | A scheduled run is green and a deliberately broken change turns it red | 1 week |
| **4. Privacy, abuse and trust** | A school can say yes | `docs/PRIVACY.md` and a parent and student notice template; the "Erase this student" action (issue 6); an abuse contact and `/report` page, a per-site traffic view and alarm (issue 7); owner repository settings (section 6); mark the permanent-key path deprecated (issue 14) | A teacher can answer "where is my child's data and how do I delete it" from the documents and one button | 1 to 2 weeks |
| **5. Pilot** | Real classes, other people | Invite 3 to 5 teachers personally; a call or message at each stage (install, first token, first student publish, first feature switched on, first update); keep a log of every stumble; fix, release, repeat | The pilot gates in section 5 pass | 3 to 4 weeks |
| **6. Public beta** | Open the door | Announce (section 6); a monthly release rhythm; triage issues weekly | The announcement checklist in section 6 is complete | after the pilot |

Phases 0 to 2 can overlap (phase 2 waits on AWS, so start the support case today). Phases 3 and 4 can run while the pilot teachers are installing.

## 3. What to keep out of scope until the pilot has spoken

| Idea | Why not now |
|---|---|
| New student-facing features (visitor sign-in for students' apps, SQL databases, more AI patterns) | Visitor sign-in is probably the biggest missing capability, but it needs a design discussion and real teacher demand first |
| Student access to the compute worker, a job queue, running student code on a machine | Considered and removed: the existing Background Jobs feature already covers long student work safely |
| Compute worker tasks | Wait for the benchmark; the machine is ready when a task exists |
| Co-teachers, audit log, bulk actions | Real, but second order; wait for pilot feedback on what a teacher really clicks |
| Other AWS regions, a hosted multi-class service, a mobile app | Out of the project's shape (one install per organisation, `us-east-1`) |

## 4. The one parallel track: answer quality

The books and agent features are the project's differentiator, and today their quality is unmeasured. This track does not need the pilot and does not add features; it makes the existing one trustworthy. It follows [RAG_ROADMAP.md](RAG_ROADMAP.md) and [CONFIGURATION_PLAN.md](CONFIGURATION_PLAN.md):

1. **Benchmark pack and runner** (the document list needs the owner's approval before any download) and a baseline report.
2. **Fix wrongful refusals**, measured against the benchmark.
3. **Per-shelf settings** (the thresholds) and the settings reference (C0, C1).
4. **Hindi support**, then deterministic guards. Defer the rest of the roadmap.

Run it in the gaps between the phases above, not ahead of them.

## 5. The gates (when to move to the next stage)

| Gate | To pass |
|---|---|
| **Gate 1: safe to pilot** | Phases 0 to 3 done; no open P0; the clean-room install passed twice; the live suite is green |
| **Gate 2: safe to announce** | The pilot ran with at least **3 teachers outside the author, each with a real class for at least 2 weeks**; every install finished without the author editing anything on their account; no data loss; no unexplained cost; every pilot problem is fixed or listed as a known limitation; phase 4 done |
| **Gate 3: drop "beta"** (later) | A year of releases without a breaking upgrade; an outside contributor merged; least-privilege installer permissions or an SSO path; a third-party security review |

Measures to track from the pilot on: minutes from "I have an AWS account" to "my first student's site is live"; number of times the author had to intervene per install; number of "busy" or error answers per class session; support questions per teacher per week.

## 6. The announcement checklist (phase 6)

**Repository (owner's settings, one afternoon):** private vulnerability reporting on; branch protection on `main` (reviews, required checks); Dependabot alerts and secret scanning with push protection on; Scorecard and CodeQL results read and the serious ones fixed before adding any badge; a `CODE_OF_CONDUCT.md`; five to eight `good first issue` and `help wanted` issues.

**Documents:** the README quickstart verified by a stranger; a two-minute demo recording; `PRIVACY.md`; the teacher guide's cost and alert pages; a one-page "run a class in a week" plan; a support statement ("issues on GitHub, best effort") so expectations are set.

**Distribution:** keep the GitHub Release `.tgz` as the install source. Publishing to npm would shorten the command (`npx class-platform-teacher install`) but needs a name check, provenance and the owner's explicit go-ahead; decide after the pilot.

**Where to post:** a personal note to the pilot teachers first; then a LinkedIn post, dev.to, "Show HN", r/aws and r/edtech, in that order, only when someone can answer questions the same day.

**A release rhythm:** one tagged release a month with a changelog; a patch release within days for any P0 found; a deprecation notice one release ahead of any removal (the permanent-key path goes in the next major version).

## 7. Decisions the owner must make

| # | Decision | Recommendation |
|---|---|---|
| 1 | Start the AWS Support case for the second account **today** | Yes: it is the longest wait and blocks the clean-room install |
| 2 | Upgrade the live account's plan and set the date (issue 8) | Put the end date in the calendar now; upgrade a month before |
| 3 | Pilot teachers: who to invite, and how many | 3 to 5 people you know who teach and have, or can open, an AWS account |
| 4 | Feature freeze until Gate 1 | Yes, except the answer-quality track |
| 5 | Repository settings (section 6) | Do them before the pilot, not the announcement |
| 6 | Approve the benchmark document list when sent | Needed before any download; see [RAG_ROADMAP.md](RAG_ROADMAP.md) |
| 7 | Publish to npm, now or later | Later, after the pilot |

## 8. The next five actions

1. Open the AWS Support case for the second account (owner).
2. Fix the installer: alert email and budget (issue 1), with a test.
3. Make the capacity caps follow the account's quota and add the load check (issue 3).
4. Add `knowledge/` to the export and point-in-time recovery to the student-data table (issue 4).
5. Send the benchmark document list for approval (so the quality track can start without waiting).
