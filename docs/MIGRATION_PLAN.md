# Migration assistant: can an existing tool or app move onto the platform, and how

> **Status: the final plan (2026-11-10). Nothing is built.** It came from students asking for "an agent that looks at a tool we already have and says whether it can be moved". The facts about the platform are in [STUDENT_KIT.md](STUDENT_KIT.md), [SETTINGS.md](SETTINGS.md) (every limit), [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md), [KNOWLEDGE_CONNECTOR.md](KNOWLEDGE_CONNECTOR.md) and [BACKGROUND_JOBS.md](BACKGROUND_JOBS.md).

## 1. The goal in one sentence

Give a student a **clear yes, yes-after-changes or no, with the reasons**, for an app they already have, and, when the answer is not "no", **plan the move and do it only after they approve each step**.

## 2. Decisions (the defaults I will build unless the owner changes them)

| Question | Decision | Why |
|---|---|---|
| Where does it run? | **In the student's own computer and AI editor.** No new function, table or permission on AWS | No AWS cost, no AI budget used, the old code never leaves the student's machine, and it reuses the check, project backup, add-ons and the confirm-first pattern |
| Is the verdict made by an AI? | **No.** The verdict and the reasons come from fixed rules (a scanner). The AI only helps with the rewrite, after the student approved a plan | A verdict must be repeatable, explainable and free. An AI can be wrong in a different way each time |
| What if the answer is "no"? | The report lists the reasons and, when possible, a **partial move** (for example "the website only") | A "no" with nothing else wastes the student's time |
| Does the teacher change anything? | **Usually nothing.** When a feature is off (backend, class AI, storage, knowledge, jobs) the report ends with "ask your teacher to switch on: ..." | The feature switches already exist |
| Who approves? | **The student**, at three gates: the plan, the start of the changes, and the publish | Matches how the editor tools already ask before they write or publish |
| Where do limits come from? | **One place.** The scanner reads the same limits as `npm run check` and the settings registry, never its own copies | A limit changed by the teacher must change the verdict |

## 3. The verdict

| Verdict | Meaning | What the report adds |
|---|---|---|
| **Green** | Fits with small changes | A short list of changes |
| **Yellow** | Fits after a redesign (for example a database becomes class storage) | What changes, what is lost, the effort (hours, not days), what the teacher must switch on |
| **Red** | Cannot move as it is | Every reason with the file that caused it, and a **partial move** if one exists |

## 4. The rules the scanner applies

The scanner looks at files and at the dependency lists; it never runs the old app. Each rule has an id, a plain sentence and a fix. First version:

| Found in the old app | Verdict | What the report says |
|---|---|---|
| Plain HTML, CSS and JavaScript | Green | Moves into the website folder |
| React, Vue or Svelte single-page app built to static files | Green | Rebuild with the project's own build |
| Next.js or similar **server-side rendering**, server components | Red (partial: static export) | No server rendering; a static export may work |
| A small web API (Flask, FastAPI, Express, Node scripts) with a few routes | Yellow | Becomes the one backend function: routes under `/api`, standard library only |
| Packages the backend does not have (anything beyond the standard library) | Yellow if pure and small (bundled into the folder, within the zip limit), **Red** if compiled (numpy, pandas, torch, Pillow, database drivers) | Names each package and which kind it is |
| SQLite, JSON or CSV files used as data | Yellow | Becomes class storage if it fits the value and total size limits; otherwise Red |
| A real database server (Postgres, MySQL, MongoDB, Redis) | Red (partial: small data to class storage) | No database server on the platform |
| User accounts, login, sessions, roles | Red | The platform has no login system for a student's visitors |
| Calls to an AI model SDK (OpenAI, Anthropic, Google) | Yellow | Switch to the class AI helper; streaming is not available |
| Question answering over documents (vector store, embeddings) | Yellow | Becomes a knowledge shelf (PDF and text books) |
| Agents and chains (LangChain and the like) | Yellow | Rewritten on the class agent helper, with its step and time limits |
| Work longer than a request allows | Yellow | Background jobs, up to the job limit |
| Websockets, server-sent events, live streaming | Red | Not supported |
| Scheduled jobs (cron), workers, queues, Docker, long-running servers | Red | Not supported |
| Uploads or files larger than the file limit | Red | Over the file limit |
| Payments, email sending, third-party sign-in | Red unless an outside API with a secret in the portal's settings is enough | Flagged for a human decision |
| Secrets in the old files (`.env`, keys, tokens) | **Warning for any verdict** | They are never copied; the report says where they are so the student can move them to the portal settings |
| Anything the scanner cannot classify | **Unknown, shown as such** | Never guessed into green |

A rule that is not certain says "unknown" or "check this by hand". A wrong green is worse than an honest unknown.

## 5. The report

One file, `MIGRATION_REPORT.md`, written into the **old project's folder**, plus the same text on screen:

1. The verdict and the one-line reason.
2. A table: rule id, file, what was found, verdict contribution, fix.
3. What the teacher must switch on (and a message the student can copy to the teacher).
4. The effort estimate and the biggest risk.
5. The partial move, if any.
6. A list of the things the scanner could **not** look at.

## 6. From a report to a finished move

| Step | Who | What happens |
|---|---|---|
| 1. Scan | Student runs it, or asks the editor | Report, nothing changed |
| 2. Plan | The AI, from the report | A numbered plan: files that will change, what is dropped, how data moves, the tests that will prove it works. **Gate 1: the student approves** |
| 3. Safety | The tool | The student's current project is saved first (project backup); the old app is copied read-only into a `legacy/` folder in the new project, never edited |
| 4. Move | The AI, in small steps | After each step the platform check and the tests run; the student can stop at any step. **Gate 2: approve starting** |
| 5. Prove | The tools | Smoke tests written from the old app's own behaviour (the same inputs give the same answers) must pass; the report is updated to "done" |
| 6. Publish | The student | The existing publish, which asks again. **Gate 3** |

Failure rules: if a step fails twice, stop and say so; never loosen a check to make it pass; never delete the legacy copy.

## 7. What is built (all in the student kit and the editor package; no AWS change)

| Piece | What it is |
|---|---|
| `tools/migrate.mjs` | The scanner and report writer. Offline, free, deterministic |
| `npm run migrate` | `scan <folder>` and `report`; later `plan` and `status` |
| Editor tools | `migrate_scan` (always allowed), `migrate_plan` and `migrate_apply` (each asks first, like the existing publish) |
| Rules for the AI | A `MIGRATION.md` section in the kit: how to read a report, how to plan, what never to do (copy secrets, skip tests, edit `legacy/`) |
| Terminal | The same through `npx <package> migrate <folder>`, so no AI editor is needed for the scan |
| Tests | A sample set of real old apps with the expected verdicts (section 9) |

## 8. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **M0. Scanner and report** | The rules of section 4, the report, `npm run migrate scan`, the terminal command, and the rules file | On the sample set the verdicts match the expected ones; no green is wrong; reasons name the right files; limits come from the shared place |
| **M1. Guided move, top four patterns** | Plan and move for: static or React site, small web API, file or SQLite data to class storage, AI SDK to class AI | Each of the four moves a sample app end to end and passes the smoke tests; the three gates ask |
| **M2. Data and parity** | A data-copy helper and smoke tests generated from the old app's behaviour | A moved app with data shows the same data and answers |
| **M3. Teacher requests and more patterns** | A "ask my teacher to switch on X" request in the portal; knowledge and jobs patterns; a teacher view of who is migrating what | A student sends a request and the teacher sees it |

Start with **M0**. It already answers the students' question ("can it move, and if not why"), is small, and the later phases depend on it.

## 9. The sample set (the proof)

The scanner is only as good as its examples. Before M0 is called done there must be **5 to 8 real old apps**, chosen from what the students named, with the verdict a human agreed for each (including at least one clear red and one that looks green but is not). They live in the tests and every rule change runs against them. **The owner provides the names or the code of the apps the students mentioned; I will not invent examples.**

## 10. Safety and limits

- The scanner never runs the old code and never sends it anywhere. The editor's AI sees only what the student lets it open.
- Secrets are reported by location, never printed, and never copied into the new project.
- Third-party code the student does not own is not moved without a note in the report that licence and ownership are theirs to check.
- The legacy copy stays read-only; nothing in it is deleted.
- Every file written is inside the new project folder (the same path checks as the project setup).

## 11. Not in scope

Running the old app on the platform as it is, a hosted agent that takes uploaded code, moving a database server's data, translating between languages automatically beyond what the AI does inside an approved plan, and anything that needs the teacher's AWS account.

## 12. What I need from the owner before M0 starts

1. The 5 to 8 apps or tools the students mentioned (names, a repository, or a folder), so the sample set is real.
2. A yes or no to the decisions in section 2 (especially "runs in the editor, not on the platform" and "the verdict is rule-based, not AI").
3. Whether a partial move (website only) should be offered for a red verdict (the plan assumes yes).
