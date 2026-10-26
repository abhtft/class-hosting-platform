# State of the project: where it stands, what is strong, what is missing, when to ship

An honest assessment, written at release 1.2.1 (`main` slightly ahead, documentation only). It is a judgement, not a specification: the facts live in `TECHNICAL_SPECIFICATION.md`, the work list in `HANDOFF.md`. Update this file when the picture changes.

## 1. Conclusion

The platform is **feature-complete for its purpose and proven on real AWS, but not yet proven by anyone other than its author**. A teacher can install it with one command, run a class, switch features on per student, give students their own domains, update by plan and apply, and remove everything cleanly. All of that was run against a real, throwaway AWS account, not only against fakes.

What is missing is mostly **confidence, not code**: a few paths that were only tested against a fake AWS CLI, one known gap in what the student's AI agent is told, and zero outside users. That is the normal state of a strong **public beta**, and it is the right label for it today.

## 2. Key strengths

1. **One command, whole platform, in the teacher's own AWS account.** No server to run, no vendor account, no download beyond a GitHub release. The teacher owns the data and the bill.
2. **Safe by construction.** Updates are a plan, then an apply bound to that plan, and the tool refuses to replace or remove anything that holds data. Destroy shows an inventory and asks for the teacher login. Nothing updates itself.
3. **Clean lifecycle, proven for real.** Install, verify with a sample site, update, switch custom domains on and off, and `destroy --everything` that leaves the account empty were all run against a real account. Every bug found that way became a permanent test.
4. **Every feature is installed, each one off per student until the teacher turns it on.** The teacher decides; nothing is hidden by an install-time choice.
5. **Student side is built for AI editors.** The kit has rules, a single deploy step, `class_ai` and `class_storage` helpers and a student MCP, so a student's agent produces sites that fit the platform.
6. **Class AI with a teacher-switchable provider** (OpenRouter or Gemini), a dollar cap per student, and a plain message when a key is not set up.
7. **Custom domains automated**, using the same approach that worked by hand, and removable again.
8. **Release discipline.** Tag equals `VERSION`, lint, a 37-step suite, a build with an attestation, hashed files on a GitHub Release. The suite includes a guard that keeps accounts, domains, keys and emails out of the repository.
9. **Documentation that matches the code.** A teacher guide, an architecture document, a machine-checked specification and a handoff.

## 3. Gaps, in priority order

### Priority 1: close before you announce it widely

| # | Gap | Why it matters |
|---|---|---|
| 1 | **The student's agent does not know which features are on for its student** (designed in `HANDOFF.md` 4a, not built). | The most visible correctness problem: a site can deploy fine and then fail at runtime with "not enabled". It is the one flaw a student meets on day one. |
| 2 | **`export`, `import` and `transfer` never ran on real accounts.** | This is the only path that moves students' data. A mistake here loses or duplicates data, and teachers trust a backup most when they need it least. Rehearse `export`, `destroy`, `install`, `import`, then a real transfer between two accounts. |
| 3 | **The teacher editor tools were never tried in a real editor.** `setup` and `update_editor_tools` were also tested only with fakes. | The "install from your AI editor" promise is untested where it will be used. |
| 4 | **Wrong-address mistake on custom domains.** The student page does not say what a wrong CNAME points at and what it must point at. | The most common mistake seen in practice; every student who uses the feature can hit it. |
| 5 | **Version visibility** (release 1.2.2): print the version on every command and warn when a copy is older than the newest release. | Teachers run an `npx` copy that may be stale; support questions start with "which version". |

### Priority 2: before calling it stable

| # | Gap | Why it matters |
|---|---|---|
| 6 | **Install needs AdministratorAccess.** Least-privilege IAM or AWS SSO is not done. | A security reviewer will ask first. Acceptable for a teacher's own account, awkward for a school's. |
| 7 | **No automatic upgrade test in CI** (install the previous tag, upgrade, verify). | The upgrade path is the riskiest thing a release changes; today it is checked by hand. |
| 8 | **Single region, single account tested** (us-east-1, one throwaway account). | A region or account-setting difference will surface as the first outside report. |
| 9 | **The CloudFront multi-tenant price was not checked.** | The cost line in the teacher guide should rest on a checked number. |
| 10 | **Contributor hygiene is in place** (`SECURITY.md`, `CONTRIBUTING.md`, issue and pull request templates). Remaining: a named way to report a vulnerability privately, and a short "supported versions" line. | Needed the moment a stranger files a bug or a vulnerability report. |
| 11 | **Publishing on npm** (`npx class-platform-teacher@latest`). | Shorter command, no cache trick; needs an npm account and a repository secret. |

### Priority 3: polish

12. The portal "Teacher tools" card and an in-page update notice (code exists in a stash).
13. Rename the teacher editor tools with a distinct prefix, show the AWS account in every confirmation, and tell the AI to call `status` first.
14. The owner's production move to a dedicated account (the owner's decision and timing, never done without an explicit instruction).

## 4. At which state to ship as open source

The repository and releases are already public, so the question is how to **label** the state and **when to announce** it.

| Stage | Label | Condition |
|---|---|---|
| **Now** | **Public beta (1.2.x)** | Already true. Say "beta" in the README, say what is proven and what is not (section 1), invite early teachers. Do not promote it widely and do not call `transfer` safe yet. |
| **Next** | **Release candidate (1.3.0)** | Priority 1 done: items 1 to 5, with `export`/`import`/`transfer` rehearsed on real accounts and the editor tools tried in a real editor. This is the point to announce it publicly (a post, a demo, teacher communities). |
| **Later** | **Stable (1.x, no "beta")** | Priority 2 done (at least 6 and 7), **and** at least one teacher who is not the author has installed, run a class and updated once without help from the author. |

**Recommendation:** keep "public beta" today, do Priority 1 as the next release (1.3.0), and announce at that release. Do not wait for Priority 2 to announce, but do not drop the beta label until an outside teacher has completed a full cycle. That outside cycle is the one proof that code and tests cannot give.

## 5. What this assessment does not cover

- Whether the owner's live class has been moved to the new tooling (it has not, by the owner's rule).
- Load: the platform is built for a classroom, and no large-scale load test was run.
- Third-party security review: none done. The repository guard only checks that no identifiers or keys are committed.
