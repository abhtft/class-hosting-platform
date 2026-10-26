# Handoff: where the project stands, how to work on it, what is next

For a new session or a new maintainer. Read this first, then `docs/STATE.md` (honest assessment: strengths, gaps by priority, ship stages), `docs/TEACHER_ARCHITECTURE.md` (how the teacher side is built), the top of `CHANGELOG.md`, and `docs/TECHNICAL_SPECIFICATION.md` section 4.16. No account numbers, domains or keys are in this repository on purpose; the owner keeps those in a private, git-ignored file (see "Private facts" below).

## 1. State at the time of writing

- **Repository:** `github.com/abhtft/class-hosting-platform` (public), branch `main`, local clone `D:\Github_Codes\class-hosting-platform-github`. This is the **only** place to develop now. The older private repository (`D:\Github_Codes\student-deploy`) is a backup and holds the owner's production configuration; its content was carried into this repository.
- **Latest release:** `v1.2.1` (GitHub Release with `class-platform-teacher-1.2.1.tgz`, an alias `class-platform-teacher.tgz` and `sha256sums.txt`). `main` is ahead by documentation only (the teacher guide rewrite, the README quickstart). `v1.2.0` has no release files: its CI run failed on an identifier check, which `v1.2.1` fixed.
- **What exists** (all in this repository):
  - the platform itself (`template.yaml`, `lambda/`, `web/`, `starter/` student kit, `student/` helpers);
  - the teacher tooling: `setup.mjs` and `installer/` (install, plan and apply, verify, destroy, export and import, transfer, credentials, prompts, custom domains in `tenant.mjs`), `teacher-mcp/` (the editor tools), `teacher-package/` and `build-teacher-package.mjs` (the npm/GitHub package), `.github/workflows/release.yml` (tag, test, build, attest, release);
  - documentation: `docs/TEACHER_SETUP.md` (the guide for teachers), `docs/TEACHER_ARCHITECTURE.md` (a learning document), `docs/TECHNICAL_SPECIFICATION.md` (source of truth, machine-checked facts), the connector and MCP documents.
- **Tested for real** (on a throwaway AWS account, not only with fakes): guided install with and without a domain, the certificate and DNS steps, `verify` with a sample site, `destroy --everything` (the account ends completely empty), update by plan and apply, switching all features on, saving AI keys, the "key not set up" message, custom domains end to end with a real domain (HTTPS site served on a student's own domain), `custom-domains --disable`, the release pipeline, running from a GitHub release on Linux.
- **Tested only against a fake AWS CLI so far:** `export`, `import`, `transfer` (including its DNS CSV), `setup` (saving the AWS key), `update_editor_tools`, and the teacher editor tools inside a real editor. Rehearse before relying on them.

## 2. How to work

```bash
node tests/run.mjs            # the whole suite (37 steps, no AWS needed); needs Node 20+ and Python 3.12 with boto3
node tests/test_<name>.mjs    # one file; the teacher-side tests use a fake AWS CLI (see TEACHER_ARCHITECTURE.md section 11)
```

**A real-AWS check** (use a throwaway account, never production): `node setup.mjs install --profile <test>`, set a login with `TEACHER_PASSWORD=... node setup.mjs teacher <name> --profile <test>`, `node setup.mjs verify --user <name> --profile <test>`, then `node setup.mjs destroy --everything --yes --profile <test>` and list what is left. Every bug this found became a permanent test; do it before each release.

**Release procedure** (a tag is public, so only on the owner's explicit "release X"):
1. Change `VERSION`, `teacher-mcp/package.json` (same number) and `CHANGELOG.md` (a `## X.Y.Z` section; the release notes are cut from it).
2. `node tests/run.mjs` must pass. Commit, `git push origin main`, `git tag vX.Y.Z`, `git push origin vX.Y.Z`.
3. The workflow takes about 3 minutes. Confirm: `curl -sIL .../releases/download/vX.Y.Z/class-platform-teacher-X.Y.Z.tgz` returns 200 and `npx -y <that url> version` prints X.Y.Z. If it does not appear, read the run's failing step from `https://api.github.com/repos/abhtft/class-hosting-platform/actions/runs` (the identifier check and line endings were the two causes so far).
4. A teacher then updates with `plan`/`apply` (see the guide).

**Gotchas that cost time**
- `npx` caches a downloaded address: a plain `.../latest/download/...tgz` can keep running an old copy. Use a pinned address in tests and the `?v=<time>` form for "newest" (documented in the guide).
- Windows: a shell heredoc with backslashes or `\n` inside code gets mangled. Write files with the editor tools, or put a Python patch script in a file and run it. Files are CRLF in the working copy and LF in the repository; code that hashes file content must ignore line endings (the diagram hash does).
- Tests that mention a 12-digit account number in an ARN, or a real-looking domain, trip `tests/test_no_identifiers.py` on CI. Use `<ACCOUNT_ID>`-style placeholders or numbers the guard accepts, and run the guard before tagging.
- `git ls-files` drives the package contents; a deleted-but-not-committed file or a new untracked file affects local builds (the builder skips missing files and includes new, non-ignored ones).
- A git stash named "teacher tools card (not released)" holds an unreleased portal card ("Teacher tools for your AI editor" on the teacher page, with the update notice and the `teacher-tools.json` the installer would publish). It is a good starting point for item 5 below.

## 3. Rules the owner set (keep following them)

- Public actions need an explicit go-ahead: pushing a tag or creating a release ("release X"), publishing to npm, creating repositories.
- Never touch the owner's **production** AWS account (it hosts a live class) without an explicit instruction naming it. A read-only source for a rehearsal also needs that instruction.
- No keys, passwords, account numbers, domains, e-mail addresses or tokens in the repository. The identifier guard enforces the shapes it can.
- Updates are never automatic; destructive or secret actions (delete, passwords, AI keys, IAM) are terminal-only, never editor tools.
- Commits end with the co-author line; documentation is updated with every behaviour change and the spec facts are machine-checked (`tests/test_spec.py`).

## 4. Open items, in the order I would do them

1. **Release 1.2.2** (include section 4a, the student agent's feature awareness): print the version on the first line of every command; warn when a copy is older than the newest release (`install`, `upgrade`); update the pinned example version in the guide.
2. **Student page for custom domains:** when the CNAME points at the wrong address, say what it points at and what it must point at (the portal can look the CNAME up before asking CloudFront). The most common mistake seen was using the platform's *site* address instead of the custom-domain address.
3. **Rename the teacher editor tools** with a distinct prefix (the student tool set also has `status`), show the AWS account number in every confirmation, and make the server instructions say "call status at the start of a session".
4. **Rehearse export, import and transfer** on real accounts: `export`, `destroy --everything`, `install`, `import`, then check a student token and a published site; then a real `transfer` between two accounts.
5. **The portal "Teacher tools" card** (see the stash) and an in-page update notice.
6. **Publish on npm** (needs an npm account and an `NPM_TOKEN` repository secret; the release workflow already publishes when the secret exists) so the command becomes `npx class-platform-teacher@latest`.
7. **The owner's production move** to a dedicated AWS account, using the same installer and the transfer command (the owner decides when; read-only on the old account).
8. **Later:** least-privilege IAM or AWS SSO instead of AdministratorAccess; the CloudFront multi-tenant price check; an automatic upgrade test in CI (install the previous tag, upgrade, verify).

## 4a. Designed, not yet built: the student's agent should know which features are on

**Decision (owner asked, position agreed in discussion):** at build time the student's AI agent must know which features are on or off for *that* student, and it must learn this from the live portal, never from the rules file.

**Why:** today `starter/rules/AGENTS.md` only says the teacher "can" switch on class AI and class storage. The agent cannot tell, so it can build a feature that deploys fine and then shows a "not enabled" error at runtime. The portal already knows: `/api/student/me` returns `ai` (available, enabled, provider, model), `backend`, `storage` and `custom_domain` per student. Not yet checked: whether the student MCP `status` tool (`starter/mcp/server.mjs`) or `npm run check` already print these.

**Work to do (target 1.2.2):**
1. Add a features block to the student MCP `status` tool and to `check`, read from `/api/student/me`.
2. Add one rule to `starter/rules/AGENTS.md`: before planning anything that uses class AI, class storage, a backend or a custom domain, check the features block first. Do not put the on/off state itself in the rules (it goes stale and the file is shared by every student).
3. When a feature is off: tell the student plainly, say to ask the teacher to switch it on, and build a version that works without it (for example `localStorage` instead of class storage, or a marked placeholder for the AI part). Never build something that will fail silently.
4. Re-check on `deploy`: warn if the project uses a helper (`class_ai`, `class_storage`) whose feature is currently off, because the teacher can switch it off after the student started.
5. Tests for the features block and the deploy warning; update `docs/TECHNICAL_SPECIFICATION.md` (student kit section), the student guide and `CHANGELOG.md`.

## 4b. Shipping plan (details in `docs/STATE.md`)

- **Today: public beta (1.2.x).** Say so in the README, with what is proven and what is not. Do not call `transfer` safe yet.
- **Next release, 1.3.0 (release candidate):** Priority 1 of `docs/STATE.md` = student agent feature awareness (4a), real-account rehearsal of `export`/`import`/`transfer`, the teacher editor tools tried in a real editor, the wrong-CNAME message on the student custom-domain page, and the version print/old-copy warning (item 1 below). Announce publicly at this release.
- **Stable (drop "beta"):** Priority 2 items "upgrade test in CI" and "least-privilege IAM or SSO" done, and one teacher other than the owner has installed, run a class and updated once without help.
- Order of work: 4a and the 1.2.2 items first (small, visible), then the real-account rehearsals (they need the owner's test account and DNS), then the editor test.

## 4c. Launching without an audience (details in `docs/STATE.md` section 5)

The owner has a small following, so the plan is a few real testers, not reach: 3 to 5 outside people who run the install. To do, in order, after 1.3.0: add a "Testers wanted" section and a demo recording to the README; add an "I tried the install" issue template; open 5 to 8 `good first issue` / `help wanted` issues from the gaps in `docs/STATE.md`; invite 3 to 5 people personally; then post publicly (LinkedIn, dev.to, Hacker News "Show HN", r/aws, r/edtech). The assistant offered to draft the README section, the post and the issue texts; nothing is published without the owner's go-ahead.

## 5. Private facts (not in this repository)

The owner's AWS account numbers, profile names, domain names and DNS records for the test platform, and the state of the test accounts, are in a git-ignored file in the private repository: `D:\Github_Codes\student-deploy\handoff.local`. Passwords and keys are not recorded anywhere; they live in AWS Secrets Manager and in the owner's own password handling.
