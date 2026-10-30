# Student guide

You publish your own website, and optionally a small backend, to your teacher's class platform. Your AI editor does most of the work.

**You need from your teacher:** your **token** (it starts with `mds_`; it is shown only once, so keep it safe and never share it) and the **portal address** (for example `https://deploy.yourschool.edu`).

**You need on your computer:** Node 22, git, and an AI editor (VS Code or Antigravity work out of the box). **If you cannot install anything**, build your site anywhere and drag the build folder (or a zip of it) onto the portal's student page.

## Get started

1. Open the portal address, go to **Student**, and sign in with your token. The **Start here** card shows your site address and the exact command for your editor.
2. Run the command from the card **once on your computer**. It connects your editor to the platform. Restart the editor.
3. Open an **empty folder** in the editor and tell the AI: *"Set up my class project."* It writes a starter project (a website in `frontend/`, a small backend in `backend/`, and the platform's rules in `AGENTS.md`).
4. In that folder's terminal run, **once for each project**:
   ```bash
   npm run login
   ```
   Paste your token when asked (it is hidden). It is saved only in this project's `.env.local`.
5. Tell the AI what to build. Try it on your computer with `npm run dev` (open http://localhost:3000).
6. Publish: ask the editor *"Publish my site"*, or run:
   ```bash
   npm run check
   npm run deploy
   ```
   Your site is live at the address on your portal page. Run `npm run deploy` again after each change.

## Good to know

- **Your teacher decides which features you have.** The backend, class AI, class storage and your own domain are each switched on per student. See what is on for you with `node portal-deploy.mjs status` (or ask the editor *"Which features do I have?"*). If the backend is off, your deploy still publishes the website and skips `backend/`; ask your teacher if you need it.
- **The backend is small on purpose:** one function, Python's standard library or Node's built-ins only (no package installs), 10 seconds per request, no SQL database. If your AI suggests Express, Flask or an npm/pip package for the backend, remind it of this.
- **Never put a key or your token in your code or in the browser.** `npm run check` blocks it. AI calls go through your backend with the class AI helper, never from the page.
- **Your kit updates itself.** Before a deploy it may replace the kit's own files (`AGENTS.md`, `tools/`, `portal-deploy.mjs`) with your teacher's current ones. A copy of what it replaced is in `.kit-backup/`. Your own code is never touched. Do not edit the kit's files.
- **Your token has an end date.** After it expires your site is read-only for 3 days, then it goes offline (it is archived, not deleted). Use **Request regeneration** on your portal page before that; your teacher approves it. A lost token cannot be shown again: ask your teacher for a new one.
- **Your own domain:** if your teacher switches it on, a **Custom domain** card appears on your page. Add the CNAME it shows at your DNS host first, then save the domain.

## Something wrong?

| Message | What to do |
|---|---|
| `No token found in this project` | Run `npm run login` inside this project's folder. |
| `The portal does not accept this token` | It was mistyped, expired or replaced. Run `npm run login` with the current token, or ask your teacher. |
| `Owner not enabled for you` / `...is switched off for you` | Your teacher has not switched that feature on. Ask them, or build without it. |
| `Your kit is not the same as your teacher's platform. Bringing it in line first...` | Normal. Your kit is updating itself; the command then carries on. |
| The deploy fails at `check` | Read the `fix:` line under each ✖, or paste the whole output to your AI editor. |
| Your old version is still showing | Wait about 30 seconds and hard-refresh (Ctrl+Shift+R). |

More: [the student kit in detail](../docs/STUDENT_KIT.md), [deploying from GitHub](CICD.md), [writing a backend](BACKEND.md).
