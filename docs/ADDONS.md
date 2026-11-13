# Add-ons: approved skills and open-source packages

Kit 2.4.0. An **add-on** is something the teacher approved that a student's AI editor can add to a project: a **skill** (a guide that makes the AI better at one job) or a **module** (an open-source package for the site, with a guide). The reasoning and the later phases are in [ADDONS_PLAN.md](ADDONS_PLAN.md).

## For students (and their AI editor)

- `npm run addons` lists what is approved and what the project has. `npm run addons -- show <name>` explains one.
- `npm run addons -- add <name>` adds it; `-- remove <name>` takes it out.
- With an AI editor that has the `class-platform` tools, just ask: "make a dashboard with charts". The AI looks (`list_addons`), asks "Add the Charts add-on?", and on a yes adds it (`add_addon`) and reads its guide.
- Every add-on puts a guide in `skills/<name>.md`. A module also adds its package to `frontend/package.json`. What was added is listed in `addons.json`. These files are the student's own: they sync with the project (project sync) like any other, so a second computer or a teammate has the same add-ons.

## The first catalogue (kit 2.4.0)

| Add-on | Kind | What it gives | Licence |
|---|---|---|---|
| `frontend-design` | skill | Layout, spacing, type, colour, components, loading/empty/error states | written for this platform |
| `accessibility` | skill | A checklist: structure, keyboard, contrast, images, forms, motion | " |
| `responsive` | skill | Mobile-first layout, grids, touch sizes, checks | " |
| `charts-dashboard` | skill | Choosing the chart, readable charts, dashboard layout | " |
| `forms` | skill | Form design, validation in the page and the backend, sending states | " |
| `portfolio-page` | skill | A recipe for a personal page | " |
| `pico-css` | module | Plain HTML that looks good (`@picocss/pico` 2.1.1) | MIT |
| `lucide-icons` | module | Icons as React components (`lucide-react` 1.61.0) | ISC |
| `recharts` | module | Line, bar, area, pie, scatter charts (`recharts` 3.10.1, 39 packages) | MIT |
| `motion` | module | Gentle animation (`motion` 14.6.0) | MIT |
| `font-inter` | module | The Inter typeface, built into the site (`@fontsource-variable/inter` 5.3.0) | OFL-1.1 |
| `day-picker` | module | An accessible calendar (`react-day-picker` 10.0.2) | MIT |
| `sign-in` | app | Sign in with Google for an app made before kit 2.9.0: two page files and four short edits in the starter's own files (`app.py` or `index.mjs`, `App.tsx`), see [SIGN_IN.md](SIGN_IN.md) | written for this platform |
| `installable-app` | app | Install button and home-screen icon: a manifest, the install script and default icons, plus six lines in `index.html` (`frontend/public`, no store) | written for this platform |

Each module was added to a fresh kit with real npm, built with TypeScript and Vite using exactly the code in its guide, and removed again, before it went in.

## What makes it safe

1. **Allowlist.** Only entries in `tools/addons/catalog.json` can be added. The catalogue is part of the kit (`tools/` is kit-owned), so it is checksummed and signed with the kit and a student cannot widen it. Nothing is imported from a public skill store.
2. **Exact versions, chosen by the teacher.** Same rule as the kit: the student does not pick a version.
3. **Every package is pinned.** For a module the catalogue lists the whole set of packages it brings (name, exact version, sha512 integrity, licence). `add` installs with `--ignore-scripts`, then compares what changed in `package-lock.json` with that list. **If anything differs (an extra package, a changed checksum, an install script, a package that is not from the registry, a version that is not exact), the project is restored exactly as it was** and the student is told why.
4. **Licences.** Only MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, OFL-1.1, 0BSD, CC0-1.0 and BlueOak-1.0.0 (for an "A OR B" licence one accepted choice is enough; for "A AND B" all must be). Checked for the module and for every package it brings.
5. **Skills are text for an AI, so they are restricted.** The catalogue test rejects a skill that contains a web link, a shell command, an `.env` mention, a platform token, a request to ignore earlier instructions, the system prompt, or an API key or "send it to" instruction. A skill is also pinned by SHA-256 and limited to 12 KB. The kit rules tell the AI that a guide is advice about building and never overrides the rules file.
6. **The AI asks first.** `add_addon` and `remove_addon` need `confirm=true` after the student's yes. The rules also say: never `npm install` anything else for the site without the student's clear request.
7. **Existing sites never break.** Removing an entry from the catalogue only stops new use; `verify` just says the add-on is no longer approved.

## For teachers

The platform ships the catalogue above. To approve something else, or to drop something:

1. Put a skill's text in `starter/template/tools/addons/skills/<name>.md`, or choose a package and an exact version (at least two weeks old; its licence must be accepted; it must not need an install script).
2. Add an entry to `starter/template/tools/addons/catalog.json` (id, kind, title, description, licence, source; for a module: `package`, `version`, and a `howto` of 40 to 1800 characters that tells the AI how to use it; for a skill: `file`).
3. Run `node starter/addons/pin.mjs`. It writes the checksum of each skill and, for each module, the exact package list, integrity, licences and installed size, and stops if a package wants an install script or has a licence that is not accepted. `--skills-only` works offline; `--check` changes nothing and fails when the catalogue is out of date.
4. Read the text you added. A skill is an instruction to an AI: write advice about *how to build*, never orders about commands, links or secrets.
5. Run the tests (`node tests/run.mjs`; the add-ons test checks the catalogue), then publish the kit (`node setup.mjs publish`). Students get it with the next automatic kit sync.

Phase 2 (a teacher tab with per-class switches and usage counts) and phase 3 (teacher suggestions) wait for pilot feedback.

## Files

| File | What it is |
|---|---|
| `starter/template/tools/addons.mjs` | The command and the editor tools' engine: `list`, `show`, `add`, `remove`, `verify` |
| `starter/template/tools/addons/catalog.json` | The approved add-ons |
| `starter/template/tools/addons/skills/*.md` | The six skills |
| `starter/addons/pin.mjs` | Maintainer tool that pins the catalogue (not part of the student kit) |
| `starter/mcp/server.mjs` | The tools `list_addons`, `add_addon`, `remove_addon` |
| `tests/test_addons.mjs` | The catalogue rules, add and remove, the refusals and rollbacks, the command from a built kit |
| `skills/`, `addons.json` (in a student's project) | The guides and the record of what was added |
