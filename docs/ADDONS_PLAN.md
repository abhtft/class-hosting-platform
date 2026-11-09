# Add-ons: teacher-approved skills and open-source building blocks (plan)

Status: **phase 1 is built and released (kit 2.4.0)**, approved by the owner on 2026-11-09 with the defaults in section 10. What exists now is described in [ADDONS.md](ADDONS.md). Sections 7 to 11 below are the plan it was built from; phases 2 and 3 are still only plans.

## 1. The idea in one paragraph

Many good open-source building blocks exist (UI kits, chart libraries, icon sets, animation libraries) and many "skills" (written know-how that makes an AI editor much better at, for example, frontend design). A student's AI editor should be able to **use them safely**, without the student hunting the internet. The teacher decides what is allowed; the platform delivers it; the student's AI asks before adding anything.

## 2. Two kinds of add-on

| Kind | What it is | Where it ends up | Main risk |
|---|---|---|---|
| **Skill** | A short text file that teaches the AI one job well (frontend design, accessibility, responsive layout, charts, forms, a portfolio page) | `skills/<name>.md` in the student's project, pointed to from the kit rules | The text is an instruction to the AI, so a bad skill could try to make it leak or run something (prompt injection) |
| **Module** | An open-source package the site uses (a UI kit, a chart library, icons, fonts, animation) | An exact, pinned package added to the student's `frontend/package.json` (or a copied file set for plain CSS/fonts) | Supply chain (a package changes or runs code on install) and licences |

How the student frontend works today (checked in the repo): React 18 + Vite, built on the student's own computer, then the finished `dist` is deployed. So modules are normal npm packages, not CDN scripts.

## 3. Correction to my earlier note

I said the platform's security headers would block outside CDNs. **That was wrong.** The tests state that student sites have no CSP and may load what they need. The reasons to prefer packages built into the site over CDN links are different: the site keeps working if the CDN or the upstream project disappears, no third party sees the visitors, the exact code is pinned and checked, and the offline/dev machine works the same.

## 4. Rules (what makes it safe)

1. **Allowlist only.** An add-on that is not in the teacher-approved catalogue cannot be added, whatever the student or the AI asks.
2. **Exact versions, chosen by the teacher.** Same principle as the kit ("the teacher's portal is the version authority"): the student does not pick a version; updates are the teacher's decision.
3. **Integrity pinned.** A skill is pinned by SHA-256 (verified on download, as project sync does). A module is pinned by exact version plus the npm integrity hash, checked when it is added.
4. **No install scripts.** Modules are added with scripts turned off (`--ignore-scripts`); a package that needs a script to work is not accepted.
5. **Licence check.** Only MIT, Apache-2.0, BSD, ISC (and OFL for fonts). The licence name and text are stored with the catalogue entry and shown to the teacher.
6. **Size budget.** The whole project is limited to 50 MB (5 MB per file, project sync); each add-on states its size and must stay small. A module's downloaded size on the student's computer is not counted, only what is in the project.
7. **Skills are read by a human first.** The owner reads each skill before approval. Skills may contain advice and examples only: no commands to run, no links to fetch, no instructions about tokens or secrets. A test in the repo rejects those patterns in the catalogue.
8. **Always ask the student.** The AI asks "Add X to your project?" and acts only on a yes (same pattern as `push_project`).
9. **Nothing auto-imported** from public skill stores or "awesome" lists. We copy a proposal into the catalogue by hand after review, with the source URL, version and licence recorded.
10. **Existing sites never break.** Removing an add-on from the catalogue only stops new use.

## 5. How it works for a student (target experience)

1. The student asks: "make a dashboard with charts".
2. The AI calls `list_addons` and finds, say, `charts` (a skill + the chart module).
3. It asks: "Add the Charts add-on? It adds a chart library (Apache-2.0, 140 KB) and a guide for building dashboards." On a yes it calls `add_addon`.
4. The kit adds the pinned package to `frontend/package.json` (install with scripts off) and writes `skills/charts.md`; the project records what it added in `addons.json`, so it is repeatable on another computer.
5. The AI reads that skill and builds the dashboard. Deploy and project sync work as usual.
6. Terminal users: `npm run addons` (list), `npm run addons -- add charts`, `npm run addons -- remove charts`.

## 6. How it works for the teacher

**Phase 1:** the owner edits one catalogue file in the repository (`starter/addons/catalog.json` plus the skill texts and licence texts). The catalogue ships inside the kit, so it follows the normal kit release and the existing tests (kit hash, spec facts). Nothing new in AWS.

**Phase 2:** an **Add-ons** tab in the teacher portal: the list with version, licence, size, "used by N projects", and an on/off switch per class. The catalogue then lives on the platform and updates without a kit release.

**Phase 3 (only if pilot teachers ask):** teachers propose add-ons; the owner reviews and approves.

## 7. Phase 1 scope (smallest useful version)

Delivered as a new kit version (2.4.0) when the owner approves:

- **Catalogue + `addons.json` record:** list, add, remove, verify (the same pinned-checksum idea as the kit).
- **Editor tools:** `list_addons`, `add_addon` (asks first), `remove_addon` (asks first); terminal `npm run addons`.
- **Kit rules:** one short section in `AGENTS.md`: look at the catalogue before writing a UI or a chart from scratch; add only after the student agrees; read the skill before using it.
- **6 skills and about 6 modules** (see section 8), each tried on the test student before approval.
- **Tests:** catalogue rules (licence allowlist, pinned hashes, size cap, banned patterns in skills), add/remove round trip, `ignore-scripts` is always used, existing kit tests stay green.
- **Docs:** this file turns into the guide; `STUDENT_KIT.md`, `MCP.md`, `TEACHER_SETUP.md` and the spec get one section each.

Not in phase 1: the teacher tab, per-class switches, usage counts, teacher submissions, any AWS change.

## 8. Proposed first catalogue (needs the owner's yes before anything is downloaded)

Skills (text I draft, the owner reads):

| Skill | Teaches |
|---|---|
| `frontend-design` | Layout, spacing, type scale, colour and contrast, empty/loading/error states |
| `accessibility` | Labels, keyboard use, contrast, alt text, a short checklist |
| `responsive` | Mobile first, breakpoints, touch targets |
| `charts-dashboard` | Choosing the chart, readable axes, a clean dashboard layout |
| `forms` | Validation, error messages, disabled/submitting states |
| `portfolio-page` | A recipe for a clean personal landing page |

Modules (candidates, each to be verified for licence, size and `ignore-scripts` before approval):

| Module | Why | Typical licence |
|---|---|---|
| A small CSS kit (Pico CSS or similar) | Good looking pages without hand-written CSS | MIT |
| `lucide-react` | Icons | ISC |
| `recharts` or Chart.js | Charts | MIT |
| `framer-motion` (or `motion`) | Gentle animation | MIT |
| Self-hosted fonts (for example Inter, via Fontsource) | Good type without Google Fonts | OFL |
| A date/calendar widget | Forms with dates | MIT |

I will check the real versions, licences and sizes at build time and give the owner a table to approve. Candidates can be swapped for others the owner prefers.

## 9. Risks and how they are handled

| Risk | Handling |
|---|---|
| A malicious or careless skill steers the AI | Human review, SHA-256 pin, banned-pattern test, no auto-import |
| A package changes or runs code on install | Exact version, integrity hash, `--ignore-scripts`, teacher chooses updates |
| Licence trouble | Permissive-only allowlist, licence text stored with the entry |
| Too many add-ons make the AI slow or confused | Short catalogue (about 12 at first), one-line descriptions, the AI loads a skill only when the task needs it |
| Weaker students depend on heavy libraries | Skills say "use the plain approach first"; modules are opt-in |
| Scope grows during the pilot | Phase 1 only; phases 2 and 3 wait for pilot feedback (matches the roadmap rule: freeze features, pilot with teachers) |

## 10. Decisions

Decided by the owner: add-ons are **curated by the teacher**; the idea is in scope.

Still to confirm (defaults in brackets):

1. Order in the queue (default: **Add-ons phase 1 next**, then RAG quality, because the owner asked to focus on this idea now).
2. The first catalogue (default: the table in section 8; the owner may swap items).
3. Skill file format (default: plain Markdown files under `skills/`, referenced from `AGENTS.md`; the same text also works for Claude, Cursor and Copilot).
4. Module delivery (default: pinned npm packages in `frontend/package.json`, added with scripts off; fonts and plain CSS copied as files).

## 11. Order of work for phase 1

1. Owner approves the catalogue (section 8) and the defaults (section 10).
2. Draft the 6 skills; the owner reads and edits them.
3. Verify each module (version, licence, size, no install script) and try it on the test student (`test_claude`) with a real build and deploy.
4. Build the catalogue, tools and tests; update docs and spec.
5. Real-AWS check on `test_claude`: ask the AI for a chart dashboard and a styled landing page, with and without add-ons, and compare.
6. Release as kit 2.4.0 only on the owner's explicit "release".
