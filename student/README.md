# Deploying your frontend

You need Node 18+, the `deploy.mjs` file from your teacher, and your `DEPLOY_KEY`.

## Setup (once)

1. Put `deploy.mjs` in the root of your project (next to `package.json`).
2. Create a file named `.env.local` in the same folder containing your key:
   ```
   DEPLOY_KEY=yourname.xxxxxxxxxxxxxxxx
   ```
   Check that `.gitignore` contains `*.local` or `.env.local`. **Never commit your key.**
3. Make your app call the backend through an environment variable instead of a hard-coded URL:
   ```js
   const API_URL = import.meta.env.VITE_API_URL;
   fetch(`${API_URL}/api/items`);
   ```
   Then put the backend URL in `.env.production`:
   ```
   VITE_API_URL=https://your-backend.example.com
   ```

## Deploy

```bash
node deploy.mjs
```

This builds the project, uploads it, and prints your live URL. Run it again whenever you want to update the site.

Options: `--skip-build` deploys the existing build, and `--dir build` deploys a different folder. `dist/`, `build/` and `out/` are detected automatically.

## Optional: auto-deploy from GitHub

Copy `deploy.yml` to `.github/workflows/deploy.yml`. Then go to **Settings → Secrets and variables → Actions** in your GitHub repo and add:
- the secret `DEPLOY_KEY`
- the variable `VITE_API_URL`

Every push to `main` then deploys automatically.

## Troubleshooting

| Message | Fix |
|---|---|
| `DEPLOY_KEY not found` | Add it to `.env.local` as shown above |
| `Invalid or missing x-deploy-key` | Check the key is copied exactly, or ask your teacher |
| `index.html not found` | Your build failed or used another folder. Use `--dir <folder>` |
| `Set base: '/yourname/'` warning | Your class uses path URLs. Add `base: '/yourname/'` to `vite.config` and redeploy |
| Page loads but API calls fail | The backend must be HTTPS and allow your site in CORS. Check `VITE_API_URL` |
| Old version still showing | Wait about 30 s and hard-refresh (Ctrl+Shift+R) |
