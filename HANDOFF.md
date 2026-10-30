# Handoff: where the project stands, how to work on it, what is next

For a new session or a new maintainer. Read this first, then `docs/STATE.md` (honest assessment: strengths, gaps by priority, ship stages), `docs/README.md` (a map of all the documents), the top of `CHANGELOG.md`, and `docs/TECHNICAL_SPECIFICATION.md`. No account numbers, domains or keys are in this repository on purpose; the owner keeps those in a private, git-ignored file (see section 5).

## 1. State at the time of writing

- **Repository:** `github.com/abhtft/class-hosting-platform` (public), branch `main`, local clone `D:\Github_Codes\class-hosting-platform-github`. This is the **only** place to develop. The older private repository (`D:\Github_Codes\student-deploy`) is a backup and holds the owner's production configuration.
- **Latest release:** see `VERSION` and the Releases page (2.0.x). Each release has `class-platform-teacher-<version>.tgz`, an alias `class-platform-teacher.tgz`, `sha256sums.txt` and, from 2.0.3, Sigstore signatures (how to verify: `SECURITY.md`). One number covers the platform, the student kit and the editor package (`class-platform-mcp`).
- **What exists** (all in this repository):
  - the platform itself (`template.yaml`, `lambda/`, `web/`, `starter/` student kit, `student/` helpers);
  - the teacher tooling: `setup.mjs` and `installer/` (install, plan and apply, verify, destroy, export and import, transfer, credentials, custom domains), `teacher-mcp/` (the editor tools, all named `platform_...`), `teacher-package/` and `build-teacher-package.mjs` (the GitHub package), `.github/workflows/` (CI, release with signing, CodeQL, Scorecard, Dependabot);
  - documentation: `docs/README.md` is the map. The teacher guide is `docs/TEACHER_SETUP.md`, the student guide `student/README.md`, the source of truth `docs/TECHNICAL_SPECIFICATION.md` (machine-checked facts).
- **Proven on real AWS** (a throwaway account, not only fakes): guided install with and without a domain, the certificate and DNS steps, `verify` with a sample site, update by plan and apply, switching every feature on, saving AI keys, custom domains end to end, `destroy --everything` (the account ends empty), **export, destroy, install, import (students, sites and stored data came back, with the old teacher login)**, the **teacher editor tools in a real editor (Antigravity)**, the student kit syncing itself (edited files replaced after a backup, deleted ones left deleted), a **rollback between two releases**, a deploy with the backend switched off, and the release pipeline.
- **Not proven:** `transfer` (moving a class between two AWS accounts) has only been tested with simulated accounts: it is labelled experimental. Also unproven: regions other than `us-east-1`, classes beyond a few dozen students, and use by anyone but the author.
- **A real finding to fix:** AWS can refuse CloudFront resources on an account until it is verified (HTTP 403, "Your account must be verified before you can add new CloudFront resources"). It came back on the test account after a `destroy`, in the middle of a rebuild rehearsal. The installer only recognises it in its pre-install check, so when it appears while the stack is being created the teacher sees AWS's raw "Failed to create/update the stack" line. See item 3 in section 4.

## 2. How to work

```bash
node tests/run.mjs            # the whole suite, no AWS needed; Node 20+ and Python 3.12 with boto3
node tests/test_<name>.mjs    # one file; the teacher-side tests use a fake AWS CLI (see TEACHER_ARCHITECTURE.md section 11)
```

**A real-AWS check** (a throwaway account, never production, and always `--profile`): `node setup.mjs install --profile <test>`, set a login with `TEACHER_PASSWORD=... node setup.mjs teacher <name> --profile <test>`, `node setup.mjs verify --user <name> --profile <test>`, then `node setup.mjs destroy --everything --yes --profile <test>` and list what is left. Every bug this found became a permanent test; do it before each release.

**Release procedure** (a tag is public, so only on the owner's explicit "release X"):
1. Change the version in **four files together**: `VERSION`, `teacher-mcp/package.json`, `starter/mcp/package.json` (the editor package) and `starter/template/platform.json` (`kitVersion`); a test fails if they differ. Add a `## X.Y.Z` section to `CHANGELOG.md` (the release notes are cut from it), and update the pinned example version in `docs/TEACHER_SETUP.md` and the version facts near the end of `docs/TECHNICAL_SPECIFICATION.md`.
2. If anything in the student kit changed, record the kit's content hash for the new version in `tests/kit-versions.json` (the failing test prints the line to add). The kit changes whenever `starter/`, `student/portal-deploy.mjs`, the rules or the helpers change.
3. `node tests/run.mjs` must pass. Check `git status` first (never `git add -A` blindly: a scratch file was once committed that way), commit, `git push origin main`, `git tag vX.Y.Z`, `git push origin vX.Y.Z`.
4. The workflow takes a few minutes. Confirm: `curl -sIL .../releases/download/vX.Y.Z/class-platform-teacher-X.Y.Z.tgz` returns 200 and `npx -y <that url> version` prints X.Y.Z. If it does not appear, read the failing step at `https://api.github.com/repos/abhtft/class-hosting-platform/actions/runs` (the identifier check and line endings were the usual causes).
5. A teacher then updates with `plan` and `apply`; students' projects follow on their next command. To go back, publish the previous release (it must be 2.0.0 or later to roll students back).

**Gotchas that cost time**
- `npx` caches a downloaded address: a plain `.../latest/download/...tgz` can keep running an old copy. Use a pinned address in tests and the `?v=<time>` form for "newest" (documented in the guide). In a new terminal the `$PKG` variable is gone: an empty `npx -y "$PKG"` says "could not determine executable".
- **Profiles:** the AWS CLI's own failure hints do not include `--profile`, so pasting one runs it against the default profile. Always pass `--profile <name>` (or `export AWS_PROFILE=<name>`) and check `aws sts get-caller-identity`. The owner's default profile is production.
- Windows: a shell heredoc with backslashes or `\n` inside code gets mangled (a `\n` inside a JavaScript string became a real newline more than once). Write code with the editor tools, and keep apostrophes out of single-quoted JavaScript strings. Files are CRLF in the working copy and LF in the repository; code that hashes file content must ignore line endings.
- Tests that mention a 12-digit account number in an ARN, or a real-looking domain, trip `tests/test_no_identifiers.py` on CI. Use `<ACCOUNT_ID>`-style placeholders and `example.com`.
- `git ls-files` drives the package contents; a deleted-but-not-committed file or a new untracked file affects local builds.
- CloudFront takes 15 to 25 minutes to delete and about 15 to create; a `destroy` followed by an `install` is an hour of waiting, plus DNS.
- A git stash named "teacher tools card (not released)" holds an unreleased portal card ("Teacher tools for your AI editor" with the update notice and the `teacher-tools.json` the installer would publish).

## 3. Rules the owner set (keep following them)

- Public actions need an explicit go-ahead: pushing a tag or creating a release ("release X"), publishing to npm, creating repositories, changing repository settings.
- Never touch the owner's **production** AWS account (it hosts a live class) without an explicit instruction naming it. A read-only source for a rehearsal also needs that instruction.
- No keys, passwords, account numbers, domains, e-mail addresses or tokens in the repository. The identifier guard enforces the shapes it can.
- A teacher's platform never updates by itself, and destructive or secret actions (delete, passwords, AI keys, IAM) are terminal-only, never editor tools. (A student's kit files are the deliberate exception: they follow the teacher's platform automatically, see 4e.)
- Commits end with the co-author line; documentation is updated with every behaviour change and the spec facts are machine-checked (`tests/test_spec.py`).

## 4. What is next, in the order I would do it

**Before announcing**
1. **Owner, repository settings:** turn on private vulnerability reporting (the README, `SECURITY.md` and the issue chooser all point at it), branch protection on `main`, Dependabot alerts, and secret scanning with push protection.
2. **The clean-room test:** follow only the README quickstart in AWS CloudShell with no domain, as a stranger would, then `destroy`. Note every point of hesitation and fix the docs.
3. **The installer should print why a stack failed.** On a failed create it should read the stack events and print the first failing reason, and recognise "Your account must be verified before you can add new CloudFront resources" (print the support-case steps and the request id, and say to run `destroy --everything` to clear the failed stack). Today only the pre-install check recognises it.
4. **Owner:** get the AWS Support case answered for the test account, then finish the interrupted rebuild rehearsal (install, DNS, `finish-domain`, `verify`, `import`, `secrets`).
5. **`transfer`:** rehearse it on two real accounts, or leave it labelled experimental.

**First tester round:** invite three to five people personally (teachers or trainers who have an AWS account), add a short demo recording to the README, open five to eight `good first issue` / `help wanted` issues from section 4f, then post publicly (LinkedIn, dev.to, Hacker News "Show HN", r/aws, r/edtech). Nothing is published without the owner's go-ahead. Details: `docs/STATE.md` section 5.

**Stable (drop "beta"):** an upgrade test in CI (install the previous tag, upgrade, verify), least-privilege IAM or SSO, and one teacher other than the owner has installed, run a class and updated once without help.

## 4a. Done: the student's agent knows which features are on

`node portal-deploy.mjs status` (and the editor's `status` tool) prints a "Features for you" block read live from the portal. The rules tell the assistant to call it first and to build without a feature that is off. `deploy` warns when the backend uses `class_ai` or `class_storage` while that feature is off, and skips a backend that is off while still publishing the website. The state is never written into the rules file (it would go stale, and the file is shared by every student).

## 4d. Done: the student token lives only in the project's `.env.local`

**Decision (owner):** no computer-wide token file. Reason: two editor windows on one computer silently shared one token (the last login won), and a shared or lab computer would hand the next person the previous student's token. The cost, one `npm run login` per project, is accepted. Built: token lookup is `PORTAL_TOKEN`, `.env.local`, `.env` of the project; `status` and `deploy` say which one was used; `login --global` and `class-platform-mcp login` are refused with the reason; `class-platform-mcp setup` only adds the editor entry; an old `~/.class-platform/token` is mentioned, never read. Keep the protections: `npm run login` refuses to write `.env.local` unless git ignores it, `check` flags a tracked token, and agents must never open `.env.local`.

## 4e. The teacher's portal is the only authority on versions

**Decision (owner):** the student's kit and tools always match their teacher's platform. A student has no version choice, is not asked, and there is no freeze. Cause: in the first real test of 1.2.2 a project kept an old kit and its agent ignored the new rules, because the kit version had not been bumped and nothing forced a sync.

**Built and proven:**
- The portal publishes a hash per kit-owned file (`managed` in `kit-version.json`).
- Before a `deploy` or `status`, a project compares its files and, if any differ, runs `node tools/platform.mjs sync` and then the same command again. The teacher's copy wins, every replaced file is saved in `.kit-backup/` (the newest five folders are kept), a file the project deleted stays deleted, and line endings are ignored.
- `upgrade` and `apply` print the rollback command (publish the previous release) when it can work.
- The editor's `status` tool syncs first, so an agent reads current rules at the start of a session.
- One version number and one tag for the platform, the kit and the editor package. 2.0.0 was chosen because kits 1.6.0 and 1.7.0 already existed in students' projects, and a lower number would have made them look newer than the portal's kit.

**Not built:** (a) the portal refusing requests from a stale client (needs the kit hash on the server side, so a Lambda change and a real-account test; it would also catch projects older than 2.0.0, which today need one manual `npm run platform -- update --apply`); (b) a thin, stable launcher for the editor package that downloads the current package from the teacher's portal, so editors never pin a stale one.

**Known costs:** a bad release reaches every student at once (hence rollback, and "try each release on a throwaway student first"); the sync needs the portal; changes can arrive mid-work. The trust model is unchanged (students already run their teacher's code); keep the hash check and HTTPS from the student's own portal.

## 4f. Launch audit: known pain points, and what is deferred

A walk through the teacher and student journeys found these. The launch-critical ones were fixed in 2.0.2; the rest are listed in the README as known limitations and are the first things to build after the launch.

**Deferred, in the order I would do them:**
1. **Class-wide defaults and bulk actions.** Tokens are created one at a time and each feature is switched on per student (about 150 clicks for 30 students). Smallest useful step: "new students start with these features on", then "apply to all".
2. **Teacher notifications.** Regeneration requests and expiries only show on the teacher page; the SNS topic for alarms exists and can be reused.
3. **Cost visibility and the budget alert** (the installer could ask for an alert email and create the budget).
4. **Co-teachers and an audit log** (one teacher login today).
5. **Least-privilege IAM or SSO** instead of AdministratorAccess; publish on npm (a shorter command).
6. **Make the student side data-driven:** the feature list in `student/portal-deploy.mjs` is hardcoded; the portal should return a generic feature list the client renders, before the next feature is added.
7. **A checklist for every new (costly) feature:** off by default, per-student switch, server-enforced cap, usage metering the teacher can see, a teacher-wide emergency off, cleanup in `destroy`, a features-block entry, tests.
8. **Trust signals to finish after the launch:** switch on (owner, repository settings) Dependabot alerts, secret scanning with push protection, branch protection on `main` (Scorecard scores it) and private vulnerability reporting; read the first Scorecard and CodeQL results and fix the serious ones before adding any badge; add an SBOM to each release; **check the first signed release** (the `sign` job in `release.yml` runs only on a tag: confirm the `.sigstore.json` assets appear and that the `cosign verify-blob` command in `SECURITY.md` works, then re-check the Scorecard Signed-Releases line); apply for the OpenSSF Best Practices badge (passing level); close the planned access-logging exceptions in `.checkov.yaml`; a paid third-party review only when an institution asks for one. (The actions are already pinned to commit SHAs, from 2.0.3.)
9. Upgrade test in CI; more than one region; a load test; the Vite major upgrade (dev-server advisories); the portal "Teacher tools" card (see the stash); the CloudFront multi-tenant price check; the owner's production move to a dedicated AWS account (the owner decides when; the new account needs CloudFront verification first).

## 5. Private facts (not in this repository)

The owner's AWS account numbers, profile names, domain names and DNS records for the test platform, and the state of the test accounts, are in a git-ignored file in the private repository: `D:\Github_Codes\student-deploy\handoff.local`. Passwords and keys are not recorded anywhere; they live in AWS Secrets Manager and in the owner's own password handling.
