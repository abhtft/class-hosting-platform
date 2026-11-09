# Platform settings in one place (plan)

> **Status: steps S0 and S1 are built (2026-11-09, not yet deployed or released); S2 to S5 are still plans.** What exists: the registry `config/settings.json` (about 170 settings, groups, ranges, scopes, risks), the generated reference [SETTINGS.md](SETTINGS.md), the loader `lambda/platform_settings.py` (named so because several modules already have a function called `settings`), and `tests/test_settings.py`. The plan below is the design; where the built version differs: the data file is `lambda/platform_settings_data.py`, the generator is `tools/gen_settings.py`, and the answers to the section 10 questions were "yes, as recommended" (S0 and S1 first). Nothing is clamped yet; enforcing the ranges and runtime overrides come with S2. Request from the owner (2026-11-09): *every limit of the teacher portal and the platform must be in one place, because the application will be shipped as open source and a new user must be able to find a setting and change it easily.* This is the **teacher / operator** side. The **student-editable** side (the project file `class.config.json`, per-shelf settings) is in [CONFIGURATION_PLAN.md](CONFIGURATION_PLAN.md); this plan gives both plans one shared registry (section 6).

## 1. The goal in one sentence

One **registry** lists every setting the platform has (name, plain explanation, default, allowed range, unit, who may change it, when a change takes effect); a teacher finds and changes settings in **one portal page or one command**; and tests make sure no number lives anywhere else without being in the registry.

## 2. Where settings live today (checked in the code, 2026-11-09)

| Place | How many | Example | Problem for a new user |
|---|---|---|---|
| Constants at the top of the Lambda files (`lambda/*.py`) | about 200 in 22 files | `MAX_ITEMS = 5000`, `MAX_FAILED_LOGINS = 5`, `MAX_FILE_BYTES` | Must read Python to find them; changing one means editing code and redeploying |
| Environment variables read by the Lambdas | 48 | `OFFLINE_GRACE_DAYS`, `INFLIGHT_STUDENT` | Set in `template.yaml` Globals; easy to miss one |
| CloudFormation parameters in `template.yaml` | 30 | `BackendInflightGlobal`, `KnowledgeMonthlyCapUsd`, `WorkerInstanceType` | Needs `plan` and `apply`; the list is long and not grouped for a human |
| Per-student limits set in the teacher portal | about 25 (AI cap, storage MB and operations, knowledge caps, jobs minutes, traces level) | The cards on a student's panel | Good, but their **defaults and maximums** are constants in code |
| Installer defaults | a few (capacity sizing from the AWS quota, budget, alert address) | `capacityFor(quota)` | Separate rules, separate place |
| The numbers in the documents | many | `docs/TECHNICAL_SPECIFICATION.md` facts block, student guides | Already checked by `tests/test_spec.py` against the code, which is a good seed for the registry |

Result: a user who wants "students may upload 10 MB files instead of 5" or "lock out after 3 failed logins" has no single place to look, and no way to know whether it is safe.

## 3. The design in one picture (words)

**One registry file, three kinds of setting, three ways to change them, one rule that nothing else holds a number.**

### 3.1 The registry (`config/settings.json`)
One JSON file in the repository. Each setting has:

| Field | Meaning |
|---|---|
| `key` | A stable dotted name, for example `storage.maxFileMB` |
| `group`, `title`, `help` | Where it appears and a plain sentence of what it does and what happens if it is raised or lowered |
| `type`, `unit` | whole number, decimal, text, yes/no, a list; MB, seconds, US dollars |
| `default`, `min`, `max` | The shipped default and the hard range. **The range is enforced on the server**, whoever sets the value |
| `scope` | `runtime`, `deploy` or `fixed` (below) |
| `risk` | `low`, `careful` or `security`: shown as a badge; `security` settings need a typed confirmation |
| `appliesTo` | Which parts read it (gateway, storage, portal, knowledge, ...) |
| `cost` | Optional note when raising it can raise the AWS bill |

### 3.2 The three kinds (`scope`)
1. **`runtime`**: changeable in the portal, takes effect within about a minute, no `apply`. Most limits: file sizes, item counts, quotas defaults, session hours, login lockout, retention, rate limits that the code enforces itself.
2. **`deploy`**: part of the AWS stack (Lambda memory and timeout, API throttles, reserved concurrency, worker machine, the embedding model). Changed with one command and then the usual `plan` and `apply`; the portal shows them **read-only** with the exact command.
3. **`fixed`**: a value that must not change (a format, a security constant that the design depends on). Listed with the reason, so a reader knows it is deliberate.

### 3.3 Three ways to change a setting (all write the same place)
- **Teacher portal, "Platform settings" page**: groups, search, "changed from default" badge, the range and the help text next to each value, "reset to default", and a history of who changed what and when.
- **Command line**: `node setup.mjs config` (list, show, `set <key> <value>`, `reset <key>`, `export`, `import`). The same checks as the portal.
- **Teacher MCP tools** (the AI editor): `settings_get`, `settings_set`, with the same checks and the usual "ask first".

### 3.4 Where the chosen values are stored
- **Runtime values:** one item in the platform's own database (`config#platform`), versioned, read by every function through one small shared module with a short cache (about 60 seconds). Nothing is lost on an upgrade.
- **Deploy values:** kept as the stack's own parameters (as the capacity caps are today); `config set` writes them for the next `plan`.
- **Export and import:** `config export` writes a readable file (only values that differ from the defaults) that can be committed or moved to another account; `config import` reads it back after showing a diff. This is the open-source friendly "infrastructure as a file" path, without making a file the only way.

### 3.5 Where the defaults are used in code
Lambdas stop owning numbers. A single module (`lambda/settings.py`) loads the shipped defaults (generated from the registry at build) and the runtime overrides, and every function asks it: `settings.get("storage.maxFileMB")`. The constants disappear from the Lambda files.

## 4. Rules that keep it safe

- **Server-side ranges.** The portal, the command and the tools all validate against `min` and `max`; the functions also clamp what they read, so a bad value in the database can never exceed the hard range.
- **Nothing can lift a cost or isolation guarantee past its ceiling.** The AWS-bound limits (Lambda concurrency from the account quota, the monthly spending caps) keep their hard maximums in the registry, and some are **derived** from others (capacity caps from the AWS quota); derived settings show as "calculated" with a link to the setting they come from.
- **Security settings** (session length, login lockout, token maximum age) have narrow ranges, a `security` badge and a typed confirmation.
- **A change history** (key, old value, new value, who, when) kept for 180 days, shown on the page.
- **Defaults never change silently:** a release that changes a default says so in the changelog, and an operator's own value is never overwritten by an upgrade.
- **Existing platforms behave exactly the same** until a value is changed.

## 5. How a new open-source user experiences it

1. Reads `docs/SETTINGS.md`, which is **generated from the registry** (always current; a test fails if it is not): every setting in a table by group, with default, range, scope and the explanation.
2. Opens the portal, **Platform settings**, searches "upload", finds `storage.maxFileMB`, sees "5 MB, range 1 to 25, careful, raising it increases storage use", changes it, and sees it live within a minute.
3. Or runs `node setup.mjs config set storage.maxFileMB 10`.
4. For a deploy-scope setting the page says: "this changes the AWS stack: run `config set backend.memoryMB 512`, then `plan` and `apply`".
5. Moves to a new account with `config export` and `config import`.

## 6. How this fits the student configuration plan

[CONFIGURATION_PLAN.md](CONFIGURATION_PLAN.md) lets a student edit settings (`class.config.json`, `shelf.json`) inside a teacher-set range. It should **use this registry**:
- Its step C0 (the reference of every setting) becomes this registry; its "teacher ranges" (C3) become registry entries with `scope: runtime` and a `studentEditable: true` flag.
- Per-student teacher limits (AI cap, storage MB, jobs minutes) stay on the student's panel, but their **class default and maximum** become registry entries, so a teacher can change "the default AI cap for new students" in one place.

## 7. The build order (each step works alone; each deploy only after a shown plan)

| Step | Deliverable | Done when |
|---|---|---|
| **S0. Inventory** | `config/settings.json` with every setting classified (runtime, deploy, fixed), help text written, defaults and ranges filled from the code. **No behaviour change.** Generated `docs/SETTINGS.md`. A test compares each default with the code constant, the template parameter and the spec facts | The owner reads the generated page and agrees the classification (especially the `fixed` and `security` ones) |
| **S1. One loader** | `lambda/settings.py` with the defaults and clamping; functions read through it; constants removed from the Lambda files; a test that fails if a new numeric constant is added outside the registry | All existing tests pass unchanged; defaults identical; deployed with the usual plan and apply |
| **S2. Runtime overrides** | The `config#platform` item, the portal API (`/api/admin/settings`), history, validation, cache; `node setup.mjs config`; teacher MCP tools | Changing a runtime setting by command changes the real behaviour within a minute on the test platform; a value outside its range is refused; history shows it |
| **S3. The portal page** | "Platform settings" tab in the teacher portal: groups, search, badges, reset, history, read-only deploy settings with their command | The owner changes a setting from the page on the live platform and sees it take effect |
| **S4. Deploy-scope settings** | `config set` for stack parameters feeds `plan`/`apply`; upgrades keep the operator's values; installer sizing (capacity) shown as derived | A deploy setting changed by command appears in the next `plan` and, after `apply`, in the stack |
| **S5. Open-source polish** | `config export` and `import`, the settings page linked from the README and the install guide, a short "settings cookbook" (common changes, with cost notes) | A person who has never seen the code changes three settings using only the docs |

S0 and S1 carry no risk and make everything findable; S2 and S3 are what the owner described; S4 and S5 finish it.

## 8. What this plan will not do

- No second place for the same number: the registry is the single source; the page, the command and the document are views of it.
- No setting that lets a teacher or a student bypass an AWS or cost ceiling.
- No automatic tuning.
- No change of any default in this plan (it only makes them visible and changeable).
- It does not replace per-student limits on a student's panel; it gives their defaults a home.

## 9. Risks and how they are handled

| Risk | Handling |
|---|---|
| A hot path (the gateway) slows down reading settings | Cached read of one small item; settings that the gateway needs are also baked in as environment values at deploy, with the runtime value taking over within a minute |
| A wrong value breaks the platform | Hard ranges in the registry, clamped again when read, one-click "reset to default", history |
| Registry and code drift apart | Tests: every default equals the code and the template; no numeric constant outside the registry; the generated document is current |
| The cache shows an old value for up to a minute | Stated on the page; acceptable for limits |
| Too many settings overwhelm a new user | Groups, search, a default "Common" view (about 20 settings) and an "All settings" view |
| Security settings loosened by mistake | Narrow ranges, badge, typed confirmation, history |

## 10. Decisions needed from the owner

1. **Where chosen values live:** in the platform (database item) with export and import (recommended), or in a file in the repository only? A file only is simpler to read but cannot change without a redeploy.
2. **Security settings** (session hours, login lockout, token maximum age): adjustable within narrow ranges with a confirmation (recommended), or fixed?
3. **Deploy-scope settings** (memory, timeouts, throttles): changed by command plus `plan` and `apply` and shown read-only in the portal (recommended), or also from the portal with a built-in apply?
4. **One registry for both plans** (platform settings here, student settings in the configuration plan): agreed? If yes, S0 replaces the configuration plan's C0.
5. **Order against the rest of the queue:** my suggestion is S0 and S1 now (no risk, makes the project understandable for open source), then RAG quality, then S2 and S3 when the first outside teacher asks for them. The alternative is to do S0 to S3 first.
6. **Names:** `config/settings.json` and `node setup.mjs config`, or other names?
