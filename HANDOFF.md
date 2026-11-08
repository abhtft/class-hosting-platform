# Handoff: how to work on this project

For a new session or a new maintainer. Read this first, then [docs/STATUS.md](docs/STATUS.md) (where the project stands), [docs/CRITICAL_ISSUES.md](docs/CRITICAL_ISSUES.md) (what blocks a wider release), [docs/ROADMAP.md](docs/ROADMAP.md) (what comes next), [docs/README.md](docs/README.md) (map of all documents), the top of `CHANGELOG.md` and `docs/TECHNICAL_SPECIFICATION.md`. No account numbers, domains or keys are in this repository on purpose; the owner keeps them in a private, git-ignored file (section 6).

## 1. Where things are

- **Repository:** `github.com/abhtft/class-hosting-platform` (public), branch `main`, local clone `D:\Github_Codes\class-hosting-platform-github`. The only place to develop.
- **Release:** see `VERSION` and the Releases page. Each release has `class-platform-teacher-<version>.tgz`, the alias `class-platform-teacher.tgz`, `sha256sums.txt` and Sigstore signatures (how to verify: `SECURITY.md`). One number covers the platform, the student kit and the editor package.
- **Layout:** `template.yaml` and `lambda/` (the platform), `web/` (portal pages), `starter/` and `student/` (the student kit and helpers), `setup.mjs` and `installer/` (teacher tooling), `teacher-mcp/` (teacher editor tools), `worker/` (compute worker runner), `custom-domain/`, `tests/`, `docs/`.
- **Decisions that shape the code:** the student token lives only in the project's `.env.local` (no computer-wide file); the teacher's portal is the only authority on versions (the kit syncs itself to the teacher's version, replaced files go to `.kit-backup/`); every feature is installed once and **off per student** until the teacher switches it on, enforced on the server.

## 2. How to work

```bash
node tests/run.mjs            # the whole suite, no AWS needed; Node 20+ and Python 3.12 with boto3
node tests/test_<name>.mjs    # one file (Python tests: python tests/test_<name>.py)
```

CI (`.github/workflows/ci.yml`) runs the suite, `cfn-lint -i W1030 W2506 -t template.yaml` and checkov on Ubuntu.

**Every behaviour change updates the documentation.** The specification's facts are machine-checked (`tests/test_spec.py`): changing a count (test steps, tools, alarms) needs the spec updated.

**A real-AWS check** (a throwaway account, never production, always `--profile`): `node setup.mjs install --profile <test>`, set a login with `TEACHER_PASSWORD=... node setup.mjs teacher <name> --profile <test>`, `node setup.mjs verify --user <name> --profile <test>`, then `node setup.mjs destroy --everything --yes --profile <test>` and list what is left. Do it before each release.

**Testing a change on the live platform.** The owner's own platform (the default AWS profile) holds only the owner's test students, and `test_claude` (a project folder outside this repository) is the real-AWS test bed. In-memory fakes have missed a bug in nearly every feature (DynamoDB `Decimal` numbers, instance types a Free plan refuses, wrongful refusals visible only in real traces), so after the suite passes: `node setup.mjs plan --profile default`, show the plan to the owner, `node setup.mjs apply <plan id> --profile default` only after the owner approves **that plan**, then exercise the feature as `test_claude` and read the function logs for `AccessDenied` and errors. Never `destroy`, and never edit the live portal's kit files, without the owner's explicit yes.

## 3. Release procedure (only on the owner's explicit "release X")

1. Change the version in **four files together**: `VERSION`, `teacher-mcp/package.json`, `starter/mcp/package.json` and `starter/template/platform.json` (`kitVersion`); a test fails if they differ. Rename the `## X.Y.Z (unreleased)` section of `CHANGELOG.md`; update the pinned example version in `docs/TEACHER_SETUP.md` and the version facts in `docs/TECHNICAL_SPECIFICATION.md`.
2. If the student kit changed (`starter/`, `student/portal-deploy.mjs`, the rules or helpers), record the kit's content hash for the new version in `tests/kit-versions.json` (the failing test prints the line). A kit change after a version was applied live needs a new version number.
3. `node tests/run.mjs` passes. Check `git status`, commit, `git push origin main`, `git tag vX.Y.Z`, `git push origin vX.Y.Z`.
4. The workflow takes about ten minutes. Confirm CI, Release, CodeQL and Scorecard succeed and the release page has the `.tgz`, `sha256sums.txt` and the `.sigstore.json` files.
5. A teacher updates with `plan` and `apply`; students' projects follow on their next command. To go back, publish the previous release.

## 4. Rules the owner set (keep following them)

- Public actions need an explicit go-ahead: pushing a tag or creating a release, publishing to npm, creating repositories, changing repository settings.
- Never touch the owner's **production** AWS account without an explicit instruction. Every AWS `plan` is shown and approved before its `apply`; every `publish` needs approval; destructive or secret actions are terminal-only.
- No keys, passwords, account numbers, domains, e-mail addresses or tokens in the repository (a test enforces the shapes it can; build addresses in tests from parts).
- A teacher's platform never updates by itself.
- Commits end with the co-author line.

## 5. Gotchas that cost time

- **`apply` takes the plan id as an argument** (`apply <id> --profile <p>`, not `--plan`), and it must be the plan most recently shown by a `plan` run that was not cut short. Never pipe `plan` through `head`; redirect it to a file. `apply` also republishes the portal pages, the kit and the editor package.
- **`npx` caches a downloaded address.** Use a pinned address in tests and the `?v=<time>` form for "newest".
- **Profiles:** the AWS CLI's own hints do not include `--profile`; always pass it and check `aws sts get-caller-identity`.
- **Windows:** a shell heredoc with backslashes mangles code (`\n` becomes a newline, `\b` a control character). Write files with the editor tools, and keep apostrophes out of single-quoted JavaScript strings. Files are CRLF in the working copy and LF in the repository.
- **Git Bash:** `export MSYS_NO_PATHCONV=1` before commands with `/aws/...` names.
- **Tests that mention a 12-digit account number or a real-looking domain or an e-mail address** trip `tests/test_no_identifiers.py` and `test_template_ops.py`.
- **`git ls-files` drives the package contents**; a deleted-but-not-committed file breaks `test_no_identifiers.py` until it is staged.
- **npm drops files named `.gitignore`**: the starter keeps its ignore file as `gitignore.txt` and the build renames it.
- **Real runtimes differ from the fakes:** DynamoDB returns `Decimal` numbers; a Lambda timeout is logged only as `REPORT ... Status: timeout`; AWS refuses CloudFront resources on a new account until it is verified (support case); a **Free plan** account may only use free-tier EC2 types.
- **The local install state** (`~/.class-platform-teacher/install.json`, or `.class-platform/` in a clone) is keyed by stack name; `plan` trusts the live stack when it disagrees. If a plan lists the removal of a live feature, STOP: it is a stale local state.
- CloudFront takes 15 to 25 minutes to delete and about 15 to create.

## 6. Private facts (not in this repository)

The owner's AWS account numbers, profile names, domains and DNS records, and the state of the test accounts, are in a git-ignored file in the owner's private repository (`D:\Github_Codes\student-deploy\handoff.local`). Passwords and keys are recorded nowhere: they live in AWS Secrets Manager and in the owner's head.
