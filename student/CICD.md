# Deploy automatically from GitHub

Push to `main` and your site and backend publish themselves. It uses the same token you sign in to the portal with, so there is nothing else to set up.

## Your repo layout

```
my-project/
  frontend/          your app (React, Vue, plain HTML ... anything with `npm run build`)
  backend/           index.mjs (Node 22) or app.py (Python 3.12) at the top level
  portal-deploy.mjs  copied from your teacher
  .github/workflows/deploy.yml   copied from portal-deploy.yml
```

Either folder may be missing: a frontend-only project just has `frontend/`. Other folder names work with `--frontend <dir>` and `--backend <dir>`.

**Is your app at the top of the repo** (a normal Vite or React project, with `backend/` beside `src/`)? That works too: use `node portal-deploy.mjs --frontend .`, and in the workflow set `FRONTEND: .` and add `--frontend .` to the deploy command. Do not rename `src/`.

### How it knows what to build and publish
Nothing is guessed from your code. It follows two rules:
1. **Build:** in the frontend folder (`frontend/` by default, or whatever `--frontend` names), if `package.json` has a `build` script, it runs `npm run build`.
2. **Publish:** it publishes the build output folder, the first of `dist/`, `build/` or `out/` (inside that folder) that contains an `index.html`. A plain site with no build step also works if its `index.html` sits directly in a folder such as `frontend/`.

Safety rules: the repo root itself is never published as-is, and `.env` files and `node_modules` are never included. `backend/` is a separate step with its own folder.

In your frontend, call the backend with a **relative** address, which works on your site and on a custom domain: `fetch('/api/items')`. (`/api/...` reaches your `backend/` function.)

## One-time setup

1. Copy `portal-deploy.mjs` to the repo root and `portal-deploy.yml` to `.github/workflows/deploy.yml`.
2. On GitHub: **Settings, Secrets and variables, Actions, New repository secret**. Name `PORTAL_TOKEN`, value: your token (starts with `mds_`).
3. Push to `main`. Watch it under the **Actions** tab.

Put API keys for your backend (for example an AI key) in the **Backend card on the portal**, once. They stay in place across deploys and never go in your code or in GitHub.

## Try it on your own computer first

```bash
echo "PORTAL_TOKEN=mds_yourtoken" > .env.local      # make sure .gitignore contains *.local
node portal-deploy.mjs                              # backend, then frontend
node portal-deploy.mjs --only frontend              # just one part
```

## What can go wrong

| Message | What to do |
|---|---|
| `PORTAL_TOKEN not found` | Add the secret in GitHub (or `.env.local` on your computer) |
| `... (HTTP 401)` at sign-in | The token is wrong or was revoked: copy it again, or ask your teacher |
| `cannot deploy right now (status: expired)` | Your token expired. Request regeneration on the portal, then update the `PORTAL_TOKEN` secret with the new token |
| `Owner not enabled for you` (backend) | Ask your teacher to switch your **Backend** on |
| `Remove ... .env` | Do not ship `.env` files. Set variables on the portal |
| `Nothing to deploy` | Run from the project root, which contains `frontend/` or `backend/` |
| Build failed | Run `npm run build` in `frontend/` locally and fix the errors |

## Good to know

- A rejected upload (bad zip, expired token) changes nothing that is live. Frontend and backend are published separately: the backend first, then the frontend.
- Your token only works while it is active, so pipelines stop when it expires. You get a warning in the log during the last 3 days.
- Treat the token like a password. Anyone with it can sign in as you and publish to your site.
