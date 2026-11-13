# Chapter 9: The student kit, add-ons, project sync, installable apps and the settings registry

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.

## A. The kit and how it is enforced

**Q299 [B] What is the student kit?**
A starter project (React + Vite + TypeScript frontend, Python or Node backend, AI-editor rules, helper libraries, checker, deploy CLI, dev servers) that the student downloads from the portal. The AI edits something that already builds and deploys instead of inventing a project.
Repo: `starter/template/`, `docs/STUDENT_KIT.md` section 2.

**Q300 [I] Explain the "four layers" of enforcement.**
(1) Scaffold: a working project in the right layout. (2) Rules for the AI (`AGENTS.md` and copies for other editors). (3) `npm run check` (local and first CI step). (4) The platform validates every upload server-side. Layers 1–3 help honest users; only layer 4 stops dishonest ones.

**Q301 [I] Why must the server re-validate what the client-side check already checked?**
Anything running on a student's machine can be skipped or edited. "Never trust the client": the platform validates paths, symlinks, `.env`, entry file and sizes again, and the checker's backend limits are pinned equal to the platform's by a test.

**Q302 [I] What does the checker forbid in the frontend and why?**
AI services called from the browser (OpenAI, OpenRouter, Anthropic, Google...), `localhost` URLs, `VITE_` variables holding key/secret/token/password. Anything in browser code is public; AI calls must go through the backend with the capped key.

**Q303 [I] Why "standard library only" for student backends?**
Nothing is installed on the platform: the function is the zip the student uploads, limited to 50 MB / 250 MB. Using only the standard library keeps functions small, startup fast and the supply chain empty (no student `pip install`).

**Q304 [I] Why is the backend a plain function and not Express/Flask?**
The gateway invokes a Lambda with an HTTP-API-style event. A tiny pattern (`handler(event, context)` with route dispatch) needs no framework; the local dev server simulates the gateway so code behaves identically.
Repo: `starter/template/backend/app.py`, `tools/dev_server.py`.

**Q305 [I] What does "single-source" mean for `portal-deploy.mjs` and the helpers?**
Files copied into the kit at build time from the platform repo (`student/*`), never edited in the template; a test asserts the kit copies are byte-identical to the originals. One source of truth avoids drift.

**Q306 [I] What is `kit-manifest.json` and `platform.json`?**
`kit-manifest.json` lists the sha256 of every kit-managed file (so the updater knows what the student changed). `platform.json` holds the kit version, the portal URL and the pinned `signingKey`.

**Q307 [I] How does kit auto-sync decide what to overwrite?**
Managed files that are unchanged on the student's side are updated; files the student edited are never touched. Hashes in the manifest make "unchanged" precise.

**Q308 [I] Why is `.env.local` the only place for the token, and how is that enforced?**
`npm run login` writes the token only after verifying the file is git-ignored; `check` errors if git tracks it; the MCP never receives or prints it. This keeps the credential out of commits, chat transcripts and screenshots.

**Q309 [D] What do `INTERVIEW.md` and `PATTERNS.md` accomplish?**
`INTERVIEW.md`: the AI first asks the student a few plain questions and writes a one-page `SPEC.md` they approve, so the build matches the idea. `PATTERNS.md`: a decision guide for AI features (plain call, answer from books, router, agent, plan-and-do job, human approval, quality loop).

**Q310 [I] How do per-editor copies of the rules work?**
`GEMINI.md`, `CLAUDE.md`, `.cursor/rules/`, `.github/copilot-instructions.md`, `.windsurfrules`, and a Claude skill all point at the same canonical `AGENTS.md`. Different editors look in different places; the content is shared.

**Q311 [I] Why does the kit ship an evaluation (`starter/eval`)?**
To test whether real AI editors obey the rules (e.g. refuse to hand-write a login). Prompts in `scenarios.md` and a `verify.mjs` check outcomes. Rules are only as good as their measured effect.

## B. Local development

**Q312 [I] How can a student run the app on their computer without AWS?**
`npm run dev` starts a local server that simulates the gateway and the platform's connectors with offline mocks (for example the class AI answers with an offline mock). No token or cost is needed to build and test the flow.

**Q313 [I] Why is the class AI an "offline mock" locally, and how does `mock=[...]` fit the agent helper?**
So students can develop without a key. An agent needs structured JSON replies, so `agent.run(..., mock=[reply, ...])` supplies scripted replies only while the offline mock is active (never on the platform).

**Q314 [I] What does `doctor.mjs` do?**
Diagnoses the environment (Node version, git, token presence, portal reachability) and prints hints. A "doctor" command is a common developer-experience pattern.

## C. Add-ons

**Q315 [I] What is an add-on and what two kinds exist?**
Teacher-approved extras: **skills** (a guide the AI reads) and **modules** (an exact open-source package plus a guide). Catalogue in `tools/addons/catalog.json`.

**Q316 [I] Why an allowlist with exact versions and integrity hashes?**
npm packages are a major supply-chain risk. The catalogue pins each package's name, version, sha512 and licence; `add` installs with `--ignore-scripts` and compares the lockfile change to the approved set; any difference is undone.
Study: [npm: ignore-scripts](https://docs.npmjs.com/cli/v10/using-npm/config#ignore-scripts), [Subresource integrity](https://developer.mozilla.org/en-US/docs/Web/Security/Subresource_Integrity)

**Q317 [D] Why can a skill file be dangerous?**
A skill is text an AI will follow. A malicious one could say "ignore previous instructions" or "send the token to...". The catalogue test rejects links, shell commands, `.env` mentions, tokens, prompt-override phrases and "send it to".

**Q318 [D] Why do add-on checksums ignore line endings (change in 2.8.0)?**
On Windows, git may convert LF to CRLF and change the hash even though the content is semantically the same, making checks fail. Normalising line endings before hashing makes verification platform-independent.

## D. Project sync

**Q319 [I] What is project sync and how is it different from Git?**
Students push/pull source files to the platform's bucket; it moves whole files and stops on conflict. No GitHub account needed. It's simpler than Git (no merge) and suited to beginners and small teams.
Repo: `lambda/projects.py`, `docs/PROJECT_SYNC.md`.

**Q320 [I] What is content-addressable storage?**
Files are stored under their hash (`projects/<p|t>/<name>/blobs/<sha256>`). The same content is stored once, nothing is ever overwritten, and integrity is self-checking. Git's object store works the same way.
Study: [Git Internals: objects](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)

**Q321 [I] How does a commit avoid lost updates?**
The manifest item (path → sha, size, author, time) carries a revision; the commit is a conditional write that succeeds only if no one committed in between. Otherwise 409 with conflicting paths, so the client pulls, resolves and pushes again.

**Q322 [I] Why can the teacher read pushed files, and how is that communicated?**
The platform stores them in the teacher's bucket. Docs tell the teacher to inform students (privacy). Transparency about who can read what is part of honest design.

**Q323 [I] What is never synced and why?**
Kit files, `node_modules`, `.git`, build output, `.env`, keys, the `knowledge/` books and `.syncignore` entries. Secrets, generated files and large binaries don't belong in source sync.

## E. Installable apps (PWA)

**Q324 [B] What is a Progressive Web App (PWA)?**
A website that can be installed to the home screen and behave like an app, via a web app manifest (name, icons, `start_url`, `display`) served over HTTPS, optionally with a service worker for offline.
Study: [web.dev: PWA](https://web.dev/learn/pwa), [MDN: Web app manifest](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest)

**Q325 [I] What does the `installable-app` add-on add?**
A manifest, an install script, default icons and six lines in `index.html` (`frontend/public`, no app store). Installing needs HTTPS, which CloudFront provides.

**Q326 [D] Why does path-based hosting complicate PWAs?**
`start_url` and `scope` are relative to the origin; on `<dist>.cloudfront.net/<slug>/` the app lives under a path. The kit sets Vite `base: '/<name>/'`. Subdomain hosting is cleaner.

## F. The settings registry

**Q327 [B] What is `config/settings.json`?**
The single registry of every limit, default and time the platform uses (171 settings in groups: frontend, backend, gateway, sessions, tokens, ai, storage, knowledge, jobs, traces, projects, worker, alerts, apiThrottles, lambdas...). `tools/gen_settings.py` generates `lambda/platform_settings_data.py`; code reads `platform_settings.value("gateway.perIpPerMinute")`.

**Q328 [I] Why a registry instead of constants spread over files?**
One place to document, validate (min/max), test, expose in docs (`docs/SETTINGS.md`) and later let teachers change safely. It also prevents two files disagreeing about the same limit (e.g. the checker and the platform).

**Q329 [I] What are the helper converters `kb`, `mb`, `hours`, `minutes`?**
The registry stores human units (KB, hours); each consumer converts to the unit it needs (bytes, seconds). Prevents unit bugs.

**Q330 [D] What is "kept in code on purpose"?**
A section of SETTINGS listing values that should NOT be teacher-tunable (security-critical constants, formats). Not everything should be configurable.

## Study materials for this chapter

- **web.dev PWA course**: <https://web.dev/learn/pwa>
- **Vite guide** (what a bundler does, `base`, env vars): <https://vite.dev/guide/>
- **npm lockfile and integrity**: <https://docs.npmjs.com/cli/v10/configuring-npm/package-lock-json>
- **Pro Git book** (chapters 1 and 10): <https://git-scm.com/book/en/v2>
- **Developer experience** talks on YouTube (search): "Developer experience matters (Stripe)", "How we built a great CLI".
- Hands-on: `starter/template/tools/check.mjs` (the rules as code) and `tests/test_kit.mjs` (what the kit must contain).
