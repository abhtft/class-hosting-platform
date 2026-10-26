# Changelog

For teachers who run their own copy. Each release lists what is new, what (if anything) you must do, and whether it is safe to apply without thinking. Updating is always: the release's own `upgrade` command (each GitHub Release shows it) (or, with a git clone, `git pull` then `node setup.mjs upgrade`; or ask the teacher MCP: "plan an update").

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
