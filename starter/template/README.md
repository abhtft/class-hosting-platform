# My class project

A website (`frontend/`) and one backend function (`backend/`), hosted on your class platform. Deploy it with one command, or push to GitHub and it deploys by itself.

## Start in 6 steps

1. **Get your token** from your teacher and sign in to the portal (it shows your site address).
2. **Open this folder in your AI editor** (Antigravity, Cursor, VS Code, Claude Code, ...). It already contains the platform's rules (`AGENTS.md`).
   If your assistant seems unaware of them, tell it: *"Read AGENTS.md and follow it."*
3. **Tell the AI what to build.** Ideas to copy are in `PROMPTS.md`.
4. **Try it on your computer:** `npm run dev` (open http://localhost:3000). The AI answers are an offline mock here, so nothing is spent.
5. **Put it online, no GitHub needed:**
   ```
   npm run login      (once: paste your token, it is hidden and saved only on this computer)
   npm run check
   npm run deploy
   ```
   Your site is live at the address the portal shows. Run `npm run deploy` again after each change.
   Prefer to just ask your AI editor? In VS Code open the Chat in Agent mode and start the `class-platform` tool server (this folder has it ready); in Antigravity run `npm run mcp-config` and paste what it prints. Then say "publish my site" (it still asks you first).
6. **Optional: automatic deploy from GitHub.** Create a GitHub repository for this folder, then repository > *Settings* > *Secrets and variables* > *Actions* > *New repository secret*, name `PORTAL_TOKEN`, value = your token. Then push again.
   Push to `main`; the **Actions** tab turns green when your site is live. Never write your token into a file yourself: `npm run login` stores it safely.

## What is where

| Path | What it is |
|---|---|
| `frontend/` | your website (React + Vite). Calls the backend with `fetch('/api/...')` |
| `backend/` | your function (Python). `app.py` has the routes; `class_ai.py` is the class AI helper (do not edit) |
| `variants/node-backend/` | the same backend in Node, if you prefer JavaScript (see `AGENTS.md`) |
| `tools/` | `npm run dev`, `npm run check`, `npm run doctor` (and `npm run login`, `npm run deploy` at the project root) |
| `.github/workflows/deploy.yml` | the automatic deploy |
| `AGENTS.md` | the rules your AI assistant follows |

## Need help?

- `npm run doctor` checks your computer (Node 22, Python 3.12, git).
- `npm run check` tells you exactly what to fix before pushing; paste its output to your AI assistant.
- The portal's **Backend** card has the logs and your AI allowance.
