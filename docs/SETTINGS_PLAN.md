# Platform settings in one place: status and design

> **Status: steps S0 and S1 are built and released (2.5.0). Steps S2 to S5 are not built**; their design is kept in section 4 and their status is in [FUTURE.md](FUTURE.md) section 3. The file name is kept because code refers to it. The generated reference of every setting is [SETTINGS.md](SETTINGS.md).

## 1. The goal

One registry lists every setting the platform has: name, plain explanation, default, allowed range, unit, scope and risk. Everything reads its values from it, and tests make sure no number lives anywhere else without being in it. A teacher or a new open-source user should find any limit in one place.

## 2. What is built

| Step | What | Released |
|---|---|---|
| **S0. Inventory** | `config/settings.json`: 172 settings in 19 groups, each with its explanation, default, range, scope (`runtime` or `deploy`) and risk (`low`, `careful` or `security`). `docs/SETTINGS.md` is generated from it by `tools/gen_settings.py`. | 2.5.0 |
| **S1. One loader** | `lambda/platform_settings.py` reads the registry; the platform's functions take their limits from it. 137 constants were compared before and after, and every value is unchanged. Tests fail if a number is added to a Lambda file outside the registry, or if the registry, the code, the CloudFormation template and the generated page disagree. | 2.5.0 |

Nothing changes behaviour when a value is not changed. The template's environment variables still win where a deployment sets them.

## 3. What the registry does today, and does not

- **Does:** states every limit and default in one place; the platform reads them; the generated page and the tests keep them consistent.
- **Does not yet:** let a teacher change a setting from the portal or a command (S2, S3); apply a changed deploy-scope setting through a command (S4); export and import settings (S5). Today a change means editing `config/settings.json`, regenerating the page, and publishing, or editing the template parameter and running `plan` and `apply`.

## 4. Design for the remaining steps

These are the decisions the design still needs (see FUTURE.md section 3 for the list).

### 4.1 Three kinds of setting

1. **`runtime`:** changeable in the portal, effective within about a minute, no stack change. Most limits: file sizes, item counts, session length, login lockout, retention.
2. **`deploy`:** part of the AWS stack (memory, timeouts, throttles, the in-flight caps). Changed with one command, then the usual `plan` and `apply`; the portal shows them read-only with the command.
3. **`fixed`:** must not change (a format, a security constant the design relies on). Listed with the reason.

### 4.2 Three ways to change a setting (S2 and S3)

- The teacher portal's Platform settings page: groups, search, a "changed from default" badge, the range and help beside each value, reset, and a history.
- The command line: `node setup.mjs config` (list, show, `set`, `reset`, `export`, `import`), with the same checks as the portal.
- The teacher editor tools: `settings_get` and `settings_set`, with the same checks and the usual confirm-first rule.

### 4.3 Where chosen values live

- Runtime values: one versioned item in the platform's own database, read by every function through one small module with a cache of about 60 seconds.
- Deploy values: kept as the stack's parameters, written by `config set` for the next `plan`.
- Export and import: a readable file of the values that differ from the defaults, which can be committed or moved to another account (S5).

### 4.4 Rules

- Ranges are enforced on the server whoever sets the value, and clamped again when read.
- Nothing may lift a cost or isolation guarantee past its ceiling. Some values are derived (for example the in-flight caps from the account's Lambda limit) and show as calculated.
- Security settings (session length, login lockout, token maximum age) have narrow ranges and a typed confirmation.
- A change history is kept (key, old value, new value, who, when).
- Defaults never change silently; a release that changes a default says so in the changelog, and an operator's own value is never overwritten by an upgrade.

### 4.5 What this will not do

No second place for the same number; no setting that lets a teacher or student go past an AWS or cost ceiling; no automatic tuning; no change of any default.
