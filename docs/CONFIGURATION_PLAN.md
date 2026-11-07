# Configuration plan: every setting in one place, for the student and for the teacher

> **Status: a plan. Nothing is built.** It answers one request from the owner (2026-11-07): *all the settings of all features should live in a configuration file the student can see and edit, which also helps the teacher.* It generalises the "per-shelf settings file" of [RAG_ROADMAP.md](RAG_ROADMAP.md) phase 1 into one idea for the whole kit.

## 1. The goal in one sentence

A student opens **one file in their project**, sees every setting their app can change (with its default, its allowed range and a plain explanation), edits what they want, and the platform **applies it within the limits the teacher set**; the teacher can see each student's effective settings in one place.

## 2. Where settings live today (the honest inventory)

Settings are scattered across three places, which is why they are hard to find:

| Where | Examples | Who controls it |
|---|---|---|
| **Teacher limits on the portal** (the cards on a student's panel) | Class AI on or off, provider and model, spending cap; storage caps and operations allowance; knowledge caps (shelves, books, passages, searches, embedding tokens); backend runs allowance; background jobs minutes and per-day; traces level, retention, daily limit, student view; custom domain | The teacher, **enforced by the platform** |
| **Arguments in the student's code** (helper calls) | `search(k=…)`, `answer(k=, max_tokens=, system=)`, `class_agent.run(max_steps=, max_seconds=, max_tokens=, step_tokens=, max_calls_per_tool=)`, `class_quality.cached(days=)`, `class_ai.ask(max_tokens=, temperature=, model=)` | The student, **inside the code, easy to miss** |
| **Built into the platform, not settable at all today** | The book-search confidence rules: `min_similarity` 0.55 and `min_coverage` 0.6 (stored per shelf but **no way to set them**), the passage size and overlap when a book is cut up, the keyword scoring (BM25 constants), the blend of keyword and meaning search, how many shelves a search may name (4) and the largest `k` (10) | The platform |

The third group is the real gap: the settings that most affect **answer quality** cannot be changed by anyone. The roadmap's per-shelf settings file was meant to fix exactly that.

## 3. The design in one picture (words)

Two layers, with a clear rule between them:

1. **Teacher policy (the ceiling).** What the teacher sets on the portal today, plus **allowed ranges** for the new student-editable settings (for example "students may set `min_similarity` between 0.40 and 0.75"). Stored on the platform. **Always wins.**
2. **Student configuration (the choice).** A file in the student's project. The student picks values; each is **clamped into the teacher's allowed range** by the platform, never beyond it.
3. **Built-in defaults.** Used when the file does not mention a setting.

**Effective value = the student's choice, limited to the teacher's range, else the built-in default.** An explicit value written in code still wins over the file, so nothing in existing projects changes.

**An honest limit:** a file inside the student's own backend is a convenience and a guide, **not a security boundary**; the student controls their own function. Real protections stay where they are today: the **server-side caps** (AI spending cap, search allowance, storage caps) that no student code can lift. The ranges the platform applies **on the server** (for example the search thresholds, applied inside the knowledge search) are enforced for real; ranges that only guide client-side helper behaviour (for example agent steps) are advice with a safety net from the server-side caps.

## 4. The files

| File | Where | What it holds | How it reaches the platform |
|---|---|---|---|
| **`class.config.json`** | Project root (the student's repo) | App-level settings: AI call defaults, answer defaults, agent budgets, cache and feedback, notes | Packaged with the backend at `npm run deploy`; read by the helpers; **its values are also reported to the portal at deploy** so the teacher can see them |
| **`knowledge/<shelf>/shelf.json`** | Next to each shelf's books | **Per-shelf search settings**: confidence thresholds, passages returned, risk tier, notes | Synced by `npm run knowledge` (like the books), stored on the platform because the search runs there |
| **`class.config.schema.json`** | Project root, managed by the kit | A description of every setting: type, default, allowed range, plain-language help, and what it changes | Lets VS Code, Antigravity and other editors show hints and catch mistakes while typing |

JSON is used because the student's functions read only the standard library in both Python and Node. The schema file gives the explanations a JSON file cannot hold itself. (Comments inside JSON are not allowed, so the kit also ships a short `CONFIG.md` explaining each section.)

### 4.1 What `class.config.json` would contain (sections, all optional)

| Section | Settings (name: default) | Notes |
|---|---|---|
| **ai** | `max_tokens`: 300; `temperature`: model default; `timeout_seconds`: 20 | The model and spending cap stay with the teacher |
| **answer** (books) | `k` (passages used): 4; `max_tokens`: 400; `not_sure_text`; `system` (an instruction prefix) | The wording of "The books do not say." |
| **agent** | `max_steps`: 6; `max_seconds`: 20; `max_tokens`: 8000; `step_tokens`: 400; `max_calls_per_tool`: 3 | Allowed ranges set by the teacher |
| **quality** | `cache_days`: 7; `feedback`: on | |
| **app** | `name`, `notes` | Free text for the teacher's view |

### 4.2 What `shelf.json` would contain (per shelf)

| Setting | Default | Effect | When it applies |
|---|---|---|---|
| `min_similarity` | 0.55 | How close in meaning the best passage must be for the books to "answer" | At once |
| `min_coverage` | 0.60 | How many of the question's words the best passage must contain | At once |
| `k` (default passages) | 5 | How many passages a search returns | At once |
| `passage_words` (target) and `overlap_words` | 300 and 40 | How a book is cut up | Needs the book to be read again |
| `tier` | A (study) | Turns on stricter guards later (roadmap) | At once |
| `language` | auto | For Hindi-aware search later (roadmap phase 3) | After that feature exists |
| `notes` | empty | Free text for the teacher | n/a |

The built-in values stay as the defaults, so **a shelf without a file behaves exactly as today.**

## 5. What the teacher sees and controls

- **A "Settings" view per student** (on the student's panel): the teacher's limits side by side with the **last deployed** `class.config.json` and each shelf's settings, with out-of-range or unusual values marked. The teacher can read it without asking the student.
- **Class defaults and allowed ranges** on one teacher screen: for each student-editable setting, the default and the minimum and maximum. Changing a range applies to all students at once; a per-student override is possible.
- **A warning, not a block, at deploy:** if the file asks for a value outside the range, `npm run check` and the deploy say so in plain words, and the platform clamps the value at run time.
- The **teacher MCP** (the editor tool you use) can read the same summary.

## 6. What the student sees

- **`npm run config`**: prints every setting with its **effective value**, where it came from (default, your file, or clamped by the teacher), its allowed range, and a one-line explanation.
- **`npm run config -- set answer.k 6`**: edits the file safely (checks the range first).
- Editor hints from the schema, and `CONFIG.md` with a plain explanation of each setting and a "when to change it" line.
- `npm run check` validates the file (unknown names, wrong types, out-of-range values).

## 7. Compatibility and safety rules

- **No change for existing projects.** Without the file everything behaves as today; explicit arguments in code still win.
- **Unknown or invalid values never break an app:** they are ignored with a warning and the default is used.
- **The file holds no secrets.** The kit's checker refuses keys or tokens in it. (It is reported to the portal, so it must be safe to read.)
- **Versioned format:** the file has a `version` number; a newer platform reads older files and the kit's update tool can migrate them.
- **Teacher ranges are applied on the server where the server owns the setting**, and by the helpers (with the server caps as the safety net) elsewhere. Documentation says which is which, in the table of section 2.

## 8. The build order (each step works alone, each applied only after a shown plan)

| Step | Deliverable | Done when |
|---|---|---|
| **C0. Inventory and the reference** | `docs/CONFIGURATION.md`: every setting of every feature (name, default, range, who controls it, where it applies), checked against the code by a test so it cannot drift. **No behaviour change** | The owner reads it and every setting is accounted for |
| **C1. Per-shelf settings (the roadmap's phase 1)** | `shelf.json` synced by `npm run knowledge`; the platform stores and uses the thresholds and `k`; teacher ranges for them; `npm run knowledge status` shows them | Changing a threshold visibly changes a search result in a test; a shelf without a file is unchanged |
| **C2. The project file and the schema** | `class.config.json`, the schema, `CONFIG.md`, the helpers reading it (Python and Node) with the precedence of section 3, `npm run config`, checker rules | The helpers use the file; code arguments still win; bad values are ignored with a warning |
| **C3. The teacher's side** | Class defaults and allowed ranges screen; the per-student Settings view fed by what deploy reports | The teacher sees a student's effective settings and can change a range for the class |
| **C4. Later, as features arrive** | New settings are added to the schema and the reference as part of each feature (traces, Hindi, guards) | A test fails if a feature adds a setting that is not in the reference |

C0 and C1 give the most value for the least risk: C0 makes everything findable, and C1 unlocks tuning of answer quality, which was impossible so far.

## 9. What this plan will not do

- No second place to configure the same thing: a setting has **one** home, and the other views show it.
- No hidden magic: the student can always print the effective value and see why.
- No setting that lets a student raise a cost cap or reach another student's data. Caps stay server-side and teacher-only.
- No automatic tuning in this plan (the roadmap's auto-tune is separate and comes after the benchmark).

## 10. Questions for the owner

1. **One project file plus one small file per shelf** (sections 4 and 4.2): is this the shape you want?
2. **Precedence** (teacher range over student file over built-in default, explicit code over the file): agreed?
3. **The teacher sees each student's deployed settings** (they are not secret): agreed?
4. **Start with C0 and C1** (the reference and per-shelf settings, which unblock the RAG quality work) and the project file (C2) after: agreed?
5. **File name:** `class.config.json`, or another name you prefer?
