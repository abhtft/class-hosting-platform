# Changelog

For teachers who run their own copy. Each release lists what is new, what (if anything) you must do, and whether it is safe to apply without thinking. Updating is always: the release's own `upgrade` command (each GitHub Release shows it) (or, with a git clone, `git pull` then `node setup.mjs upgrade`; or ask the teacher MCP: "plan an update").

## Unreleased

## 2.0.2

Launch-readiness release: safety guards, honest documentation, clearer tools.

- **`import` is for a freshly installed platform, and now refuses to mix classes.** It used to add a backup to any platform, which replaced the teacher login and could overwrite the sites of students who share a name. Now it refuses a platform that already has students who are not in the backup, refuses a clash of site names outright, and with the new `--merge` keeps the existing teacher login and key map. A fresh install that has only run `verify` still imports normally, and repeating the same backup is still safe. The teacher guide has the rule ("one platform, one class") and two new troubleshooting rows.
- **The teacher editor tools are renamed with a `platform_` prefix** (`platform_status`, `platform_install`, `platform_apply_update`, ...), so they can no longer be mixed up with the student tools (`status`, `deploy`, ...). Every confirmation now names the **AWS account** (and profile) the change would go to, and the tool instructions tell the assistant to call `platform_status` first in a session. If you wrote prompts or notes with the old tool names, update them.
- **A rollback hint only when it works.** `upgrade` and `apply` print the "put the previous kit back" command only when the previous release is 2.0.0 or later; older releases publish no kit hashes, so publishing one would not roll students back.
- **`.kit-backup/` keeps only the newest five folders.**
- **A new "Good to know" section in the student README** (features are the teacher's choice and how to see them, the small backend and its limits, the token belongs to the project, the kit keeps itself in step, and the no-install zip upload).
- **Documentation:** a new, shorter README (what it is, install, the four things to know before you start, students, known limitations, security and privacy, documentation map); the original manual SAM setup moved to `docs/LEGACY_MANUAL_SETUP.md`; the internal notes (`implement.md`, `learning.md`, `backend_wire.md`) moved into `docs/`; a "Supported versions" section in `SECURITY.md`; an "I tried the install" issue template.
- **Repository hygiene:** a scratch file that had been committed by mistake is removed and `.temp/` is ignored; the maintainer's test domains are replaced by `example.com` in the docs. A scan of the whole git history found no keys, tokens or account numbers. `npm audit` of the student kit shows no production vulnerabilities (the two it reports are in Vite's local development server and need a major upgrade; planned).

## 2.0.1

- **A deploy no longer fails completely when the backend is off.** The starter always contains `backend/`, and `deploy` published the backend first, so a student whose teacher had not switched the backend on got "Owner not enabled for you (403)" and the website was never published either. Now, when the portal says the backend is off for the student, `deploy` skips `backend/` with a clear warning and still publishes the website (`--only backend` while it is off is refused with the reason). The rules for AI editors say to build without a backend in that case. The kit syncs itself, so projects pick this up on their next command.

## 2.0.0

- **One version number for everything.** The platform, the student kit and the editor package (`class-platform-mcp`) now carry the same version and one tag (`VERSION`), checked by a test, so "which version?" has one answer. It starts at 2.0.0 on purpose: kits 1.6.0 and 1.7.0 are already in students' projects, and a lower number such as 1.3.0 would have made them look newer than the platform's kit, so no update notice would ever appear. It is also a breaking release for students (the computer-wide token is gone). Releases: change `VERSION`, `teacher-mcp/package.json`, `starter/mcp/package.json` and `starter/template/platform.json` together (the test fails if one differs), record the kit hash in `tests/kit-versions.json`.
- **The student kit follows the teacher's platform automatically.** Your portal now publishes a hash for every file the kit owns (`managed` in `kit-version.json`). Before a `deploy` or a `status`, a student's project compares its own copies and, when any differ, replaces them (the teacher's version wins, even over a file the student edited) and runs the command again. Nobody is asked. Every replaced file is first copied to `.kit-backup/<time>/`; a kit file the project once had and then deleted stays deleted; line endings do not matter; the student's own code and `.env` files are never touched; a kit that does not match its published hash is never applied. The editor's `status` tool does this first, so an agent reads current rules at the start of a session. New command: `node tools/platform.mjs sync`. A project on a kit older than 1.8.0 updates once by hand (`npm run platform -- update --apply`) and follows you from then on.
- **Teacher rollback.** `upgrade` and `apply` now print the command that puts the previous release's student kit, portal pages and editor package back (`npx -y <previous release> publish`); every student syncs to it on their next command. See the teacher guide, section 8.1.
- **The student token lives only in the project (`.env.local`).** The computer-wide token file is gone: `npm run login` saves the token in the project, `login --global` and `class-platform-mcp login` are refused with the reason, `class-platform-mcp setup` only adds the editor entry and tells the student to log in inside each project, an old `~/.class-platform/token` is never read (only mentioned), and `status` and `deploy` say which file the token came from. Reason: two editor windows on one computer silently shared one student.
- Not built yet (planned in `HANDOFF.md` 4e): the portal refusing a stale client, one version number for platform, kit and package, and the thin launcher for the editor package.

## 1.2.3

- **Student kit 1.7.0 and `class-platform-mcp` 1.0.1.** Release 1.2.2 changed the kit (the rules in `AGENTS.md` and `portal-deploy.mjs`) but left `kitVersion` at 1.6.0, so students' `status` and the one-line update notice said "up to date" and nobody was told to update; the unchanged package version also let `npx` keep serving a cached copy to `init_project`. Both versions are bumped. Students on kit 1.6.0 get the notice and run `npm run platform -- update --apply` (or the `update_kit` tool); `update` already replaced files by content, so it worked before, it just was not announced. To get a fresh package, set the editor tools to the new pinned address with `npx -y <portal>/class-platform-mcp-1.0.1.tgz setup` (the old tarball stays available).
- **A test now fails when the kit changes without a version bump** (`tests/kit-versions.json`, checked in `tests/test_kit.mjs`).

## 1.2.2

- **The student's AI agent now knows which features are on.** `status` (the student MCP tool and `node portal-deploy.mjs status`) prints a "Features for you" block (backend, class AI, class storage, custom domain) read live from the portal. The rules file `AGENTS.md` now tells the agent to check it before planning anything that uses a feature, and to build a version that works without a feature that is off (and tell the student to ask the teacher), instead of deploying something that fails with "not enabled". `deploy` warns when the backend uses `class_ai` or `class_storage` while that feature is off for the student. Students get the new rules with `update_kit`.
- **The custom-domain page says what a wrong CNAME points at.** While waiting for DNS, the portal looks up the student's CNAME and shows one of: not visible yet, correct, or "points at X, but it must point at Y" (the usual mistake is using the platform's site address). The lookup uses DNS over HTTPS and says nothing when it cannot answer.
- **Every command prints the version first** (`Class platform teacher tools X.Y.Z`; not in `--json` mode). `install` and `upgrade` also warn when GitHub has a newer release than the running copy, with the exact command, unless you declined update notices.
- **Teacher guide rewritten** ([docs/TEACHER_SETUP.md](docs/TEACHER_SETUP.md)): a complete, ordered guide (what you get and costs, how to run the newest or a pinned version, install with and without a domain including the three DNS records, first class, features, students' custom domains with the common mistakes seen in practice, updates, `verify`, the editor tools, backup and transfer, destroy, troubleshooting, a table of every command, and what the tool never does).
- The README quickstart now uses the newest-version command (`?v=`), which avoids an `npx` copy that was downloaded earlier.

## 1.2.1

- The first release with custom domains in the install (the tag `v1.2.0` was created, but its release build stopped at a test that found a fake account number inside an ARN in a new test file, so no 1.2.0 release was ever published). Everything described for 1.2.0 below is in 1.2.1.
- The check that keeps account numbers and domains out of the repository now also looks at new, not yet committed files, so such a slip is caught before a commit instead of afterwards in CI.

## 1.2.0 (tagged, never published)

- **Custom domains are part of the install.** A student can use their own domain (`www.theirname.com`): the installer creates what CloudFront needs (two functions, a connection group and a multi-tenant distribution, as in the hand-made original), the teacher page offers the **Custom domain** switch per student, and the student adds one CNAME. `custom-domains` sets it up on an existing platform or retries it; `destroy` removes it; `transfer` recreates it in the new account and lists each student's domain in the DNS file. Updates carry the custom-domain ids from the live stack, so an update can never switch the feature off.
- The portal passes a connection group id when it creates a student's tenant (a platform with none keeps using CloudFront's default group, so installs made by hand are unchanged).
- Teacher action needed: none. **To add custom domains to your existing platform:** `upgrade` (or `plan` then `apply <id>`) to 1.2.0, then `custom-domains`.

## 1.1.1

- **Every feature is installed by default** (student backends, class AI, Google Gemini, class storage), so the teacher page offers each of them. A feature stays off for every student until you switch it on for that student; `--features backend,storage` (or `--features none`) installs fewer, and `plan --enable a,b` / `plan --disable a` change it later. The teacher page says how to switch features on when none are installed yet.
- A missing AI key is now a plain instruction ("save it once, in a terminal, with the secrets command") instead of a server error, for OpenRouter and for Gemini.
- The domain install now prints the right command for the package (not `node setup.mjs`) and lists all three DNS records up front.
- Teacher action needed: none. To get the features on an existing install: `plan --enable backend,ai,gemini,storage`, then `apply <id>`.

## 1.1.0

- **Simpler install.** It now asks only what must be decided: the AWS account (only when there is a real choice, never in AWS CloudShell), whether you want your own domain, and your teacher login. Features (AI, Gemini, storage, backends) and AI keys are switched on later with `plan --enable` and `secrets`.
- **Works in AWS CloudShell**, the terminal inside the AWS console: no AWS CLI to install, no access key. Progress lines tell you what is happening, and a window closed in the middle of the 10 to 15 minute build is picked up by running the same command again.
- **`destroy` really cleans up** (it used to fail on the bucket's old versions): all versions, the stack, the installer's staging bucket and the saved state; `destroy --everything` also removes the student data table, parameters and the AI keys secret, for test accounts.
- Fixed the guided install ending with "unsettled top-level await" at the "Continue?" question after a hidden answer (found on a real Ubuntu terminal). A teacher password is now fully masked and typed twice; API keys show only their first four letters.
- **`verify`**: a full check from the outside: a throwaway student publishes a sample site that must come up live, private pages must refuse visitors, every switched-on feature must be offered; run automatically at the end of the guided install and any time later. `destroy` shows what it will remove and asks for your teacher login. The install asks once whether you want release notices (default yes; nothing ever updates by itself).
- **`setup`**: one command once per computer, like the students' setup: saves your AWS access as its own profile (after a backup, nothing else in your AWS files is touched) and adds the teacher tools to VS Code and Antigravity. The editor tool `update_editor_tools` later moves the editor entry to a new release by itself, after a yes.
- **`export` / `import`**: back up everything a class made to a private folder, and add a backup to a platform (deletes nothing, safe to repeat). **`transfer --from --to`**: move the class to another AWS account in steps (install, copy, check, DNS file) with a `class-transfer-dns.csv` of every DNS change; the old account is only read. **Transfer, export and import were tested with simulated accounts only: rehearse them (export, destroy, install, import) before relying on them for a real class.**
- Teacher action needed: none. Breaking changes: none.

## 1.0.2

- After an upgrade, and in `status` when a newer release exists, the teacher is told the exact `editor` command that moves the editor's teacher tools to the new version (the teacher MCP passes this on).
- Teacher action needed: none. Breaking changes: none.

## 1.0.1

- Guided install: lists the AWS profiles it finds, shows the AWS account and user it will use and asks you to confirm before creating anything, and says where a bracketed profile default comes from (your `AWS_PROFILE` setting or an earlier install on this computer).
- Repository: the README is restored (it had been blanked by the first merge).
- Teacher action needed: none. Breaking changes: none.

## 1.0.0

- First release: student sites, tokens, teacher page, student backends, class AI (OpenRouter, Google Gemini through a platform proxy), class storage, student kit and MCP package.
- New: one command installs the whole platform into your own AWS account, with no clone and no SAM CLI: `npx -y https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz install`. `plan` / `apply` / `upgrade` update it, and the same package serves the teacher MCP (`... editor` adds it to your AI editor).
- Teacher action needed: none (first release).
- Breaking changes: none.
