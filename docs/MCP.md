# MCP in this project: how the AI editor sets up, checks and publishes a class project

> **Companion document.** The source of truth for this release is [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md); if the two disagree, the specification wins. Student-facing steps are in [STUDENT_KIT.md](STUDENT_KIT.md) (section 4a). In this document `<portal>` stands for the teacher's portal address (for example `https://deploy.example.edu`).

## 1. What MCP is, and why this project uses it

**MCP (Model Context Protocol)** is an open standard that lets an AI editor (VS Code with Copilot, Antigravity, Cursor, Claude Code) call **tools** that a small program offers. The editor starts the program, asks "what can you do?", and the AI then calls those tools when the student asks in plain words.

Why it matters here: students are beginners. Instead of learning `npm run check` and `npm run deploy`, they say **"set up my class project"** or **"publish my site"**, and the AI calls our tools. The tools only run the same checks and deploy script that a student could run by hand, so MCP adds convenience, not new powers.

| Without MCP | With MCP |
|---|---|
| Student downloads the kit, unzips, opens a terminal, types commands | Student runs one `npx` command once, then talks to the AI |
| The AI guesses how to deploy | The AI has five named tools with descriptions telling it to ask first |
| Easy to deploy something broken | `deploy` runs the check first and refuses if it fails |

## 2. At a glance

| | |
|---|---|
| Package | `class-platform-mcp` (published by the teacher's portal, not on the public npm registry) |
| Transport | **stdio**: the editor starts the process and exchanges one JSON-RPC 2.0 message per line on stdin/stdout |
| Tools | `init_project`, `check_project`, `deploy`, `status`, `update_kit` |
| Who writes the files | The tools run existing scripts (`check.mjs`, `portal-deploy.mjs`, `platform.mjs`) and unpack the starter kit; nothing else |
| Token | Never in the editor's settings. Read from the environment, the project's `.env.local`, or the computer-wide `~/.class-platform/token` |
| Code | `starter/mcp/server.mjs` (the one server), `bin.mjs` (command entry), `setup.mjs` (editor setup), `jsonc.mjs`, `package.json`; built by `starter/build-mcp-package.mjs` |
| Tests | `tests/test_mcp.mjs`, `tests/test_mcp_package.mjs`, plus the update tests (`test_update_notice.mjs`, `test_kit_update.mjs`) |

## 3. The five tools

| Tool | What it does | Asks the student first? |
|---|---|---|
| `init_project` | Writes the starter (React site in `frontend/`, a backend, `AGENTS.md` rules for AI editors, helpers for the class AI and class storage, the check) into an **empty** folder, then runs `npm ci` in `frontend/` | **Yes**: `confirm=true` only after a yes |
| `check_project` | Same as `npm run check`: layout, limits, imports, secrets | No (read-only) |
| `deploy` | Runs the check, **refuses to publish if it fails**, then publishes backend first, then frontend, and returns the live address. Optional `only: frontend | backend`. One deploy at a time | **Yes** |
| `status` | Is the saved token valid and for how many days; is a newer kit or a newer MCP package available. Deploys nothing | No |
| `update_kit` | Brings the kit's own files (rules, `tools/`, deploy script, helpers) up to date. Never touches the student's code, `.env` files or files the student edited | **Yes** |

The "ask first" rule is written into each tool's **description**, which is what the AI reads. The tools also enforce it: without `confirm: true` they do nothing and answer with the sentence the AI should say to the student.

Every tool takes an optional `path` (the project folder). Output is cleaned before the AI sees it: colour codes removed, anything shaped like a portal token (`mds_...`) masked as `mds_***`, and cut to the last 8,000 characters.

## 4. How the pieces talk

```
Student ──"publish my site"──▶ AI editor ──starts──▶ npx -y <portal>/class-platform-mcp-1.0.0.tgz
                                   │                         (server.mjs, speaks MCP on stdio)
                                   │  initialize / tools/list / tools/call
                                   ▼
                              tool `deploy` ──▶ check.mjs ──▶ portal-deploy.mjs ──HTTPS + token──▶ Portal API
```

The protocol part of `server.mjs` is small (about 60 lines):

1. **`initialize`**: the server answers with its name (`class-platform`), its version and a short `instructions` text for the AI ("ask first; never ask for the token").
2. **`tools/list`**: returns the five tools with their JSON Schemas.
3. **`tools/call`**: runs the tool and answers `{ content: [{ type: 'text', text }], isError }`.
4. **`ping`** is answered; unknown methods get JSON-RPC error `-32601`, unknown tools `-32602`, bad JSON `-32700`.

### Which folder is "the project"?

In this order: the `path` the AI passes, the folder the editor says is open (the MCP **roots** feature: the server sends `roots/list` to the editor after `initialized` and when the roots change), `--root <dir>`, the kit project the file lives in, then the working directory. This is why "open an empty folder, then say *set up my class project*" works without any path.

## 5. One file, two homes

`starter/mcp/server.mjs` is the single source and runs in two places:

| Where | How it starts | Notes |
|---|---|---|
| **The package** (new students) | The editor runs `npx -y <portal>/class-platform-mcp-<version>.tgz` | Bundles `check.mjs`, `portal-deploy.mjs`, `kit.zip` (for `init_project`) |
| **Inside a kit project** (older students) | `node tools/mcp.mjs --root <project>` from `.vscode/mcp.json` | `build-kit.mjs` copies the same file to `tools/mcp.mjs` |

Within a kit project the server prefers the project's own copies of the scripts, so a project keeps working the way it was set up; outside a kit it uses the bundled ones.

## 6. Distribution: no npm registry, no GitHub download

The teacher's portal serves the package as a static file. `node admin.mjs portal-publish` builds it (`starter/build-mcp-package.mjs`) and uploads:

| File | Purpose |
|---|---|
| `class-platform-mcp-<version>.tgz` | The pinned package. **Editors are configured with this versioned URL**, so a project never changes behind the student's back |
| `class-platform-mcp.tgz` | An alias to the newest version, used **only** for the one-time setup command |
| `mcp-version.json` | `{ version, alias }` so `setup` and `status` can tell whether something newer exists |

Old versioned tarballs are kept, so an editor pinned to an older version keeps working.

The tarball is built **deterministically** (fixed timestamps and ordering by our own small tar writer), so publishing the same sources twice gives byte-identical output and no needless cache churn.

## 7. The student's one command

```bash
npx -y <portal>/class-platform-mcp.tgz setup
```

`setup` (in `setup.mjs`) does two things, once per computer:

1. **Token.** If `~/.class-platform/token` is missing or invalid, it asks for the token the teacher gave (masked input), checks it with the portal and saves it there. The folder can be moved with `CLASS_PLATFORM_HOME`. The token is never written into any project or editor setting.
2. **Editors.** It finds VS Code and Antigravity, **shows exactly what it will add**, asks `[Y/n]`, makes a timestamped backup (`mcp.json.bak-YYYYMMDDHHMMSS`) and merges **one** entry named `class-platform`. Other servers are never touched.

| Editor | Settings file | Shape of the entry |
|---|---|---|
| VS Code | user `mcp.json` (Windows `%APPDATA%\Code\User`, macOS `~/Library/Application Support/Code/User`, Linux `~/.config/Code/User`) | `servers` → `{ "type": "stdio", "command", "args" }` |
| Antigravity | `~/.gemini/config/mcp_config.json` (or `~/.gemini/antigravity/mcp_config.json` if that exists) | `mcpServers` → `{ "command", "args" }` |

The entry points at the **pinned** tarball. On Windows editors cannot find `npx` directly, so the entry is `cmd /c npx -y <url>`.

### Safety rules of `setup`

| Situation | Behaviour |
|---|---|
| Settings file has comments (JSONC) or is not valid JSON | **Left alone.** Saving would remove the comments, so it prints the exact entry to paste instead |
| Entry already identical | "already set up", nothing written |
| Entry exists but differs (older version) | Shows it, asks, replaces only that entry |
| No terminal to ask | Skips and says to run again with `--yes` |
| `--dry-run` | Prints what it would do, writes nothing (also skips the token step) |

Other flags: `--yes`, `--no-token`, `--editors vscode,antigravity|none`, `--portal <url>`. The same package also offers `login` (save the token for the whole computer), `version` and `help`.

### Checking an existing installation

Run the same setup command again. It reports "Token: already saved and valid", and for each editor either "already set up" or what it would change. Use `--dry-run` to look without changing anything. In the editor, the `status` tool (ask the AI "check my class platform status") reports the token's remaining days and whether a newer kit or package exists.

## 8. Security model

| Concern | Control |
|---|---|
| Token leaking into the AI chat or settings | The token is only read from the environment, `.env.local` or `~/.class-platform/token`. Tool descriptions tell the AI to never ask for it; `status` says the student must save it in their own terminal. Output masking removes `mds_...` values |
| The AI publishing without consent | Destructive tools need `confirm: true`, and their descriptions say to ask the student first. `deploy` also runs the check and refuses on errors |
| Overwriting a student's work | `init_project` refuses a folder that already has files (editor folders such as `.git`, `.vscode`, `.idea` are ignored). `update_kit` only touches kit-managed files |
| A malicious zip writing outside the folder | `zip.mjs` `readZip` refuses any path that would leave the target folder |
| Hanging a tool | Scripts run with stdin closed and a timeout (check 120 s, deploy 300 s, update 120 s); a second `deploy` while one runs is refused |
| A version change breaking a project | Editors are pinned to a versioned URL; the alias is only for first-time setup |
| Corrupting editor settings | Backup first, merge only our entry, refuse JSONC |
| Supply chain | The package comes from the teacher's own portal over HTTPS, is `private`, has no dependencies and is built from this repository |

Residual risk to be honest about: the MCP server runs with the student's own permissions on their computer, like any program they start. It does only what its five tools do.

## 9. Keeping students up to date

Two separate versions exist and both are checked:

| What | Where it lives | How a student learns about a new one |
|---|---|---|
| **Kit** (rules, `tools/`, helpers; `platform.json` `kitVersion`) | In each project | `npm run check` prints a notice at its end; `status` says so; `update_kit` applies it after a yes |
| **MCP package** | The pinned URL in the editor setting | `status` compares with `mcp-version.json` and says to run the setup command again, then restart the tool server |

`platform.json` also records `minKit` and `managedScripts`: the oldest kit the current tools work with, and the scripts the platform maintains. `update-notice.mjs` caches the lookup in `.platform-notice.json` and honours `PLATFORM_NOTICE` (turn off) and `PLATFORM_PORTAL` (other portal).

## 10. Portability: no personal address in the code

Source files hold the placeholder `https://deploy.example.edu` (`DEFAULT_PORTAL` in `server.mjs` and `setup.mjs`). When the teacher publishes, the build substitutes the stack's real portal address (`starter/publish-files.mjs`, `buildPublishFiles(dir, portalUrl)`), so each teacher's package and kit point at their own portal. At run time `PLATFORM_PORTAL` or `--portal` overrides it.

## 11. Testing

| Test | What it proves |
|---|---|
| `tests/test_mcp.mjs` | The protocol (initialize, tools/list, tools/call, errors), every tool, the confirm rule, roots handling, token masking, deploy refusing on a failed check |
| `tests/test_mcp_package.mjs` | The built package: deterministic tarball, `npx`-style start, `init_project` into an empty and a non-empty folder, `setup` against fake editor settings (create, add, update, same, JSONC refused, backups), version file |
| `tests/test_update_notice.mjs`, `tests/test_kit_update.mjs` | The update notice, `minKit`, managed scripts, `update_kit` |
| `tests/dev_server.py` | A local portal that serves the published files, so the above run against real HTTP with no AWS |

Run all with `node tests/run.mjs`.

## 12. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| The editor lists no `class-platform` server | Restart the editor or refresh its MCP list; run the setup command again with `--dry-run` to see what is configured |
| "portal does not accept this token" | Token expired or mistyped: run `npx -y <portal>/class-platform-mcp.tgz login` |
| `setup` says a file "has comments" | Your settings file is JSONC; add the printed entry by hand |
| Antigravity rejects the pasted config | Paste **only** the JSON between the two marker lines (`npm run mcp-config -- --json` prints just the file) |
| `init_project` says the folder is not empty | Open a new empty folder |
| Windows: `npx` not found by the editor | The entry must be `cmd /c npx ...`; run setup again |
| After an update the old behaviour remains | Restart the MCP server or reload the editor |

## 13. Extending it (for the teacher)

Add a tool by appending to the `TOOLS` array in `starter/mcp/server.mjs`: a `name`, a `description` that tells the AI **when** to use it and whether to ask first, a JSON Schema, and a `run` function returning `{ ok, text }`. Keep tools thin: call an existing script rather than re-implement it. Then bump `starter/mcp/package.json` `version`, add a test in `tests/test_mcp.mjs`, run `node tests/run.mjs`, and `node admin.mjs portal-publish`. Students see the new version through `status`.
