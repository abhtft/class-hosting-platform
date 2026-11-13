# Chapter 8: MCP (Model Context Protocol) and AI editors

> Tags: **[B]** basics · **[I]** intermediate · **[D]** deep.
> There are **two MCP servers** here: the **student MCP** (`starter/mcp/server.mjs`, name `class-platform`, 13 tools) and the **teacher MCP** (`teacher-mcp/server.mjs`, name `class-platform-teacher`, 11 tools). Both speak the same protocol over stdio.

## A. What MCP is

**Q264 [B] What is MCP in plain words?**
An open standard that lets an AI application (the *host*, e.g. VS Code Copilot, Claude Code, Antigravity, Cursor) start a small program (the *server*) and call the **tools** it offers. The model decides when to call a tool; the host executes it and returns the text result. Think "USB-C for AI tools".
Study: [modelcontextprotocol.io](https://modelcontextprotocol.io/), [MCP specification](https://modelcontextprotocol.io/specification)

**Q265 [B] What are the three MCP roles: host, client, server?**
Host = the AI app the user runs. Client = the connector inside the host that talks to one server. Server = the program exposing tools/resources/prompts. In this project, the editor is the host; our `server.mjs` is the server.

**Q266 [I] What can an MCP server offer besides tools?**
*Resources* (read-only data the host can load), *prompts* (reusable templates) and, from the client side, *roots* and *sampling*. This project uses **tools** and the **roots** feature only.

**Q267 [B] What is JSON-RPC 2.0 and why is it used?**
A tiny request/response protocol in JSON: `{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}` → `{"jsonrpc":"2.0","id":1,"result":{...}}`. Notifications have no `id`. MCP chose it because it is simple and language-neutral.
Study: [JSON-RPC 2.0 spec](https://www.jsonrpc.org/specification)

**Q268 [I] What is stdio transport? What are its rules?**
The host starts the server as a child process and exchanges one JSON message per line on stdin/stdout. Rule: **stdout is only for protocol messages**; logs must go to stderr, or you corrupt the stream.
Study: [MCP transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

**Q269 [I] Which methods does `server.mjs` implement, and with which errors?**
`initialize` (returns name, version, `instructions`), `initialized` notification, `tools/list`, `tools/call`, `ping`; unknown methods → JSON-RPC error `-32601`, unknown tool → `-32602`, bad JSON → `-32700`. After `initialized` it sends `roots/list` to find the open folder.

**Q270 [I] What is the `instructions` field in `initialize`?**
Short text the host may add to the model's context: "ask first; never ask for the token". It's guidance, not enforcement (Q273).

**Q271 [I] What is a tool description and why is it effectively a prompt?**
The model reads each tool's name, description and JSON Schema to decide when/how to call it. Wording like "IMPORTANT: call with confirm=true only after the student says yes" steers behaviour. Poor descriptions are the #1 cause of wrong tool use.
Study: [Anthropic: Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

**Q272 [I] What does the tool result `{content:[{type:'text',text}], isError}` mean for the model?**
The text is what the model "sees". `isError:true` tells the model the call failed. Anything in that text is in the model's context, so it must be scrubbed of secrets and size-limited (cut to the last 8,000 chars).

## B. Safety design for tools

**Q273 [I] Advice vs enforcement: explain with `confirm`.**
The description *advises* the model to ask the human. The tool *refuses* to act unless `confirm: true` is passed, returning the sentence the model should say to the human. A model that ignores advice still can't act without the flag; a model that sets the flag without asking is a residual risk, bounded by the tool's limited power.
Repo: `starter/mcp/server.mjs` deploy tool; `teacher-mcp/server.mjs`.

**Q274 [I] Why does the teacher MCP omit destroy, transfer, import, passwords, API keys, IAM?**
"Whatever is destructive or secret stays in the teacher's own terminal." If the tool doesn't exist, prompt injection (malicious text in a web page/file the AI reads) cannot make the AI call it.
Study: [OWASP LLM01: Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [Simon Willison: prompt injection series](https://simonwillison.net/series/prompt-injection/)

**Q275 [I] What is the "lethal trifecta" and how does this design avoid it?**
Private data + untrusted content + a way to exfiltrate/act. Here: tools can't read the token or AWS keys, can't send data to arbitrary URLs, and changing actions need confirmation and are bound to a shown plan id.
Study: [Simon Willison: The lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)

**Q276 [I] Why does `platform_apply_update` accept only a hex plan id?**
Input validation: a free string could reach a shell. A hex id also ties the action to a previously shown plan.

**Q277 [I] How does the server cope with an install that takes 15 minutes?**
Background job: the tool starts the job and returns at once; `platform_job_status` returns the output tail and result. Only one job at a time. Hosts time out tool calls (often 30–60 s), so long work must be asynchronous.

**Q278 [I] Why scrub tokens/keys from tool output?**
Tool output enters the model's context, which may be logged by the provider or echoed to chat. The server replaces token-shaped strings with `***` (`mds_***`, `sk-or-***`, `AKIA***`...).

**Q279 [I] How does the student MCP decide which folder is "the project"?**
Order: the `path` argument, the editor's open folder (via `roots/list` and `roots/list_changed`), `--root`, the kit project containing the script, then the working directory. An explicit `path` that is not a project is refused rather than silently replaced.

**Q280 [I] Why are tokens never in the editor's MCP settings?**
Settings files are plain text and often synced or shared. The token lives only in the project's `.env.local`, written by `npm run login` after a git-ignore check; the checker errors if git tracks it.

**Q281 [D] What can still go wrong with confirm-gated tools?**
The model may call with `confirm:true` on its own, or a user may say "yes" without reading. Mitigations: plan ids, small blast radius, no destructive tools, logs. Always design assuming the model can be fooled.

## C. Student MCP tools (13)

**Q282 [B] List the student tools and group them.**
Setup/check: `init_project`, `check_project`, `status`. Deploy/ops: `deploy`, `logs`. Project sync: `project_status`, `pull_project`, `push_project`, `project_auto`. Add-ons: `list_addons`, `add_addon`, `remove_addon`. Kit: `update_kit`.

**Q283 [I] Why does `deploy` run the check first and refuse if it fails?**
The check enforces layout, size limits, imports and secret rules; deploying something that fails them wastes a publish or breaks a site. The same check is `npm run check` for humans.

**Q284 [I] Why backend first, then frontend in `deploy`?**
The frontend calls the backend; publishing the page first could expose a UI calling a missing/old API. Backend ready → then the page.

**Q285 [I] What do `push_project`/`pull_project` do about conflicts?**
Push never overwrites a file a teammate changed since your last sync (409 listing the paths). Pull saves conflicting files as `<file>.theirs`, never overwriting yours.

**Q286 [I] Why is `update_kit` only a dry run unless applied, and what files does it never touch?**
It updates the kit's own files (rules, tools, helpers) but never the student's code, `.env` files or files the student edited. Safe-by-default behaviour avoids surprising changes.

**Q287 [D] Why does a Windows editor need `cmd /c npx ...`?**
GUI-launched editors can't resolve `npx` (a `.cmd` shim) directly on Windows; invoking via `cmd /c` fixes it. Hence the entry written by `setup` on Windows.

**Q288 [I] How does `setup` modify the editor's JSON safely?**
Shows the exact entry, asks `[Y/n]`, makes a timestamped backup, merges one entry, never touches other servers, and **refuses to rewrite files with comments (JSONC)** — it prints the entry to paste instead.

**Q289 [I] Why is the student package served from the teacher's portal?**
Distribution without npm registry or GitHub: pinned tarball URL (`class-platform-mcp-<version>.tgz`), a moving alias only for first setup, `mcp-version.json` for update checks. Old tarballs are kept so pinned editors keep working.

**Q290 [D] Why is the tarball built "deterministically"?**
Fixed timestamps and ordering with a custom tar writer mean the same sources give byte-identical output, so hashes are stable and republishing causes no cache churn.

## D. Teacher MCP (11 tools)

**Q291 [B] List the teacher tools and mark which change AWS.**
Read-only: `platform_check_environment`, `platform_status`, `platform_verify` (public checks), `platform_plan_update`, `platform_job_status`, `platform_teacher_login` (explains). Changing and confirm-gated: `platform_install`, `platform_finish_domain`, `platform_apply_update`, `platform_publish_portal`, `platform_update_editor_tools`.

**Q292 [I] Why does the teacher MCP call `node setup.mjs <cmd> --json` instead of having AWS code?**
So what the AI can do equals what the teacher can do by hand; tests don't need an editor; there's one implementation of each behaviour.

**Q293 [I] Why is the editor entry pinned to one release, and what is the downside?**
Updates never change tool behaviour silently. Downside: the editor stays on the old version until `platform_update_editor_tools`/`editor-update` moves it.

**Q294 [D] What does "roots" do and why is it useful?**
The host tells the server which folders are open. So "open an empty folder and say *set up my class project*" works with no path argument.
Study: [MCP: Roots](https://modelcontextprotocol.io/specification/2025-06-18/client/roots)

## E. AI editors and agents (the human side)

**Q295 [B] What is an AI coding agent and what is its loop?**
A model that repeatedly (1) reads context, (2) chooses a tool call (read file, run command, edit), (3) sees the result, until done. MCP lets you add your own tools to that loop.
Study: [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

**Q296 [I] What is `AGENTS.md` and why does the kit ship one?**
A plain-text rules file many AI editors read automatically. The kit's rules tell any agent what the platform is, the golden rules (no secrets in frontend, use helpers), patterns, and how to deploy. It's how a platform steers agents it doesn't control.
Study: [agents.md](https://agents.md/)

**Q297 [I] How does the platform check that an AI actually follows the rules?**
Layers: rules (advice), `npm run check` (enforcement before deploy), `starter/eval/` scenarios (evaluate AI behaviour), and server-side limits (the platform enforces even if the agent misbehaves). Never rely on the first layer alone.

**Q298 [D] What is "context engineering" and where is it visible here?**
Deciding what goes into a model's context. Examples: capped tool output (8,000 chars), masked secrets, concise `instructions`, rules file sections, `INTERVIEW.md` (ask the student about the idea before building), `PATTERNS.md` (which AI pattern to use).
Study: [Anthropic: Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

## Study materials for this chapter

- **MCP docs and tutorials**: <https://modelcontextprotocol.io/docs/getting-started/intro>
- **MCP TypeScript SDK**: <https://github.com/modelcontextprotocol/typescript-sdk> (compare with our 300-line hand-written server)
- Anthropic: **Building effective agents** and **Writing tools for agents** (links above).
- Simon Willison's blog (prompt injection, MCP security): <https://simonwillison.net/tags/mcp/>
- YouTube (search): "Model Context Protocol explained", "MCP crash course", "Building an MCP server from scratch".
- Hands-on: run `node starter/mcp/server.mjs` and paste a JSON line: `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}` then `{"jsonrpc":"2.0","id":2,"method":"tools/list"}` to see the protocol raw. Read `tests/test_mcp.mjs` to see how it's tested.
