# Changelog

For teachers who run their own copy. Each release lists what is new, what (if anything) you must do, and whether it is safe to apply without thinking. Updating is always: the release's own `upgrade` command (each GitHub Release shows it) (or, with a git clone, `git pull` then `node setup.mjs upgrade`; or ask the teacher MCP: "plan an update").

## 1.0.0

- First release: student sites, tokens, teacher page, student backends, class AI (OpenRouter, Google Gemini through a platform proxy), class storage, student kit and MCP package.
- New: one command installs the whole platform into your own AWS account, with no clone and no SAM CLI: `npx -y https://github.com/abhtft/class-hosting-platform/releases/latest/download/class-platform-teacher.tgz install`. `plan` / `apply` / `upgrade` update it, and the same package serves the teacher MCP (`... editor` adds it to your AI editor).
- Teacher action needed: none (first release).
- Breaking changes: none.
