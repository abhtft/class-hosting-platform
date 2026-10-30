# State of the project: where it stands, what is strong, what is missing, when to ship

An honest assessment, written at release 2.0.3. It is a judgement, not a specification: the facts live in `TECHNICAL_SPECIFICATION.md`, the work list in `HANDOFF.md`. Update this file when the picture changes.

## 1. Conclusion

The platform is **feature-complete for its purpose and proven on real AWS by its author, but not yet proven by anyone else**. A teacher can install it with one command, run a class, switch features on per student, give students their own domains, update by plan and apply, back up and restore a class, and remove everything cleanly. A student's project keeps itself in line with the teacher's version. All of that was run against a real, throwaway AWS account, not only against fakes.

What is missing is mostly **confidence, not code**: one path only tested with simulated accounts (`transfer`), no outside users, and a handful of rough edges a stranger will meet first (below). That is the normal state of a strong **public beta**, and it is the right label today.

**What is proven on real AWS:** guided install with and without a domain, the certificate and DNS steps, `verify` with a sample site, update by plan and apply, every feature switched on, AI keys, custom domains end to end, `destroy --everything` (the account ends empty), **export, destroy, install and import** (students, sites and stored data came back, with the old teacher login), **the teacher editor tools in a real editor**, the student kit syncing itself, a rollback between two releases, a deploy with the backend off, and the signed release pipeline.

**What is not proven:** `transfer` between two real accounts (labelled experimental), regions other than `us-east-1`, classes beyond a few dozen students, and use by anyone but the author.

## 2. Key strengths

1. **One command, whole platform, in the teacher's own AWS account.** No server to run, no vendor account, no download beyond a GitHub release. The teacher owns the data and the bill.
2. **Safe by construction.** Updates are a plan, then an apply bound to that plan, and the tool refuses to replace or remove anything that holds data. Destroy shows an inventory and asks for the teacher login. `import` refuses to mix a backup into a platform that already has other students. Nothing on the teacher's side updates itself.
3. **Clean lifecycle, proven for real.** Install, verify with a sample site, update, switch custom domains on and off, rebuild from a backup, and `destroy --everything` that leaves the account empty. Every bug found that way became a permanent test.
4. **Every feature is installed, each one off per student until the teacher turns it on.** The server enforces it, so a client can never use a feature that is off.
5. **The student side follows the teacher's version.** The kit's own files are brought in line with the teacher's portal before a deploy (old copies saved, student code untouched), and there is no computer-wide token: each project has its own, so two windows can never share a student by accident.
6. **Built for AI editors.** The student kit has rules the editor reads, a single deploy step, `class_ai` and `class_storage` helpers and a student tool server. The assistant learns which features are on from the live portal, not from the rules file, and builds without a feature that is off.
7. **Class AI with a teacher-switchable provider** (OpenRouter or Gemini), a dollar cap per student, and a plain message when a key is not set up.
8. **Release and supply-chain discipline.** Tag equals version, one version for the platform, the kit and the editor package (a test checks it), a content hash recorded for every kit version, lint, the whole suite, a build with attestation, hashed and **Sigstore-signed** release files, CodeQL, a `checkov` scan of the template, Scorecard, pinned workflows and Dependabot. A guard keeps accounts, domains, keys and emails out of the repository.
9. **Documentation that matches the code.** A teacher guide, a student guide, an architecture document, a machine-checked specification and a handoff.

## 3. Gaps, in priority order

### Priority 1: before you announce it widely

| # | Gap | Why it matters |
|---|---|---|
| 1 | **No outside user has run it.** | The only proof code and tests cannot give. One teacher who installs, runs a class and updates once without help is worth more than any further feature. |
| 2 | **AWS can refuse CloudFront on an account until it is verified** (seen on the test account after a `destroy`). The installer now explains it, with the support-case steps and the request id, whether it appears in the pre-install check or while the stack is created. | A new teacher hits this first. The only fix is an AWS Support case, which takes hours to days: say so up front (the README does). **Done in the installer; the wait is AWS's.** |
| 3 | **`transfer` was never run on two real accounts.** | It moves a whole class. Keep it labelled experimental until it has been rehearsed. |
| 4 | **Repository trust settings are the owner's to switch on:** private vulnerability reporting, branch protection on `main`, Dependabot alerts, secret scanning with push protection. | The README and `SECURITY.md` already point at them. |
| 5 | **The clean-room test:** the README quickstart followed by a stranger in AWS CloudShell. | It finds the docs and message gaps nothing else will. |

### Priority 2: before calling it stable

| # | Gap | Why it matters |
|---|---|---|
| 6 | **No class-wide defaults or bulk actions.** Tokens are created one at a time and each feature is switched on per student (about 150 clicks for 30 students). | The first complaint from a teacher with a big class. Smallest useful step: "new students start with these features on". |
| 7 | **No notifications.** Regeneration requests and expiries show only on the teacher page. | A student can lose access to a site unnoticed. |
| 8 | **Install needs AdministratorAccess.** Least-privilege IAM or AWS SSO is not done. | A security reviewer will ask first. Acceptable for a teacher's own account, awkward for a school's. |
| 9 | **One teacher login per platform**; no co-teachers or audit log. | Shared classes need it. |
| 10 | **No automatic upgrade test in CI** (install the previous tag, upgrade, verify). | The upgrade path is the riskiest thing a release changes; today it is checked by hand. |
| 11 | **Single region and a single account tested**, and no load test. | A region or account-setting difference will surface as the first outside report. |
| 12 | **The CloudFront multi-tenant price was not checked.** | The cost line in the teacher guide should rest on a checked number. |
| 13 | **Cost visibility.** AI spend per student is shown; infrastructure cost is not, and the budget alert is a manual console step. | Cost anxiety is a reason not to adopt. |

### Priority 3: polish

14. A generic, data-driven feature list on the student side (before the next feature is added), and a checklist every new costly feature must pass (off by default, per-student switch, server-enforced cap, usage metering, a teacher-wide emergency off, cleanup in `destroy`).
15. The portal refusing a stale client, and a thin launcher for the editor package (both in `HANDOFF.md` 4e).
16. Publish on npm (`npx class-platform-teacher@latest`); the portal "Teacher tools" card; the Vite major upgrade (dev-server advisories only); an SBOM per release.
17. The owner's production move to a dedicated account (the owner's decision and timing, never done without an explicit instruction; the new account needs CloudFront verification first).

## 4. At which state to ship as open source

The repository and releases are already public, so the question is how to **label** the state and **when to announce** it.

| Stage | Label | Condition |
|---|---|---|
| **Now** | **Public beta (2.0.x)** | Already true. The README says "beta", what is proven and what is not, and its known limitations. Do not call `transfer` safe. |
| **Announce** | **Public beta, with testers wanted** | Priority 1 items 4 and 5 done (the repository settings, the clean-room test); item 2, the installer message, is done. Then invite three to five people personally and post publicly. |
| **Later** | **Stable (no "beta")** | Priority 2 done (at least 8 and 10), **and** at least one teacher who is not the author has installed, run a class and updated once without help. |

**Recommendation:** keep "public beta" now, finish Priority 1 items 4 and 5, and announce. Do not wait for Priority 2, but do not drop the beta label until an outside teacher has completed a full cycle. That cycle is the one proof that code and tests cannot give.

## 5. How to launch it without an audience

The goal is not sales or reach. It is **three to five outside people who really run the install**, find the bugs only an outsider finds, and perhaps send one fix.

1. **Be honest in the README.** Done: "public beta", what is proven and what is not, known limitations, and a pointer to this file.
2. **Find the right few people.** Teachers or trainers you know personally; people who already use AWS or run coding classes; small communities (r/aws, r/selfhosted, r/edtech, dev.to, a "Show HN" post, AWS user groups); one direct message to one teacher at a local college or bootcamp rather than a broadcast.
3. **Make it easy to say yes.** The one-command install at the top of the README; a short screen recording of an install and a student deploy (not yet made); the "I tried the install" issue template (done); five to eight `good first issue` / `help wanted` issues taken from the gaps above (not yet opened). Contributors need a small, clearly described task, not a vision.
4. **Tell a concrete story.** "I run a class where students deploy AI-built sites; I built a platform a teacher installs in their own AWS account with one command. It is open source and needs testers." Add the demo, ask for one specific thing ("if you teach coding and have an AWS account, 30 minutes"), reply to every comment.
5. **Protect your time.** State what you will and will not do (no guaranteed support; issues answered when possible). Keep destroy, passwords, keys and IAM terminal-only. Label feature requests "later" until the Priority 1 gaps are closed. Keep the owner's production class out of it.
6. **Define success.** Not sales: a few people who install without help, find real bugs, and maybe contribute a fix.

Order: Priority 1 items 4 and 5; then the demo recording and the issue labels; then personal invitations to three to five people; then the public post; then fix what the testers hit and work toward stable. Nothing is published without the owner's go-ahead.

## 6. What this assessment does not cover

- Whether the owner's live class has been moved to the new tooling (it has not, by the owner's rule).
- Load: the platform is built for a classroom, and no large-scale load test was run.
- Third-party security review: none done. The repository guard checks that no identifiers or keys are committed; CodeQL, `checkov`, Scorecard and Dependabot run automatically, but their first results still need reading.
