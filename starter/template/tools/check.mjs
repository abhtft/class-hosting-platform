#!/usr/bin/env node
// Platform check: does this project fit the class platform? Run it before every push (`npm run check`); GitHub runs it first on every deploy.
//
//   npm run check               print the result (exit code 1 when something must be fixed)
//   node tools/check.mjs --json machine-readable result
//
// ✖ means "this would fail or be unsafe": fix it. ⚠ means "probably a mistake": read it. Paste the output to your AI assistant
// if you are stuck: every ✖ comes with a fix line. The platform's rules are in AGENTS.md.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { builtinModules } from 'node:module';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

const args = process.argv.slice(2);
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const root = resolve(option('--root', resolve(dirname(fileURLToPath(import.meta.url)), '..')));
const asJson = args.includes('--json');

const config = (() => {
  try { return JSON.parse(readFileSync(join(root, 'platform.json'), 'utf8')); } catch { return {}; }
})();
const FRONTEND = config.frontend || 'frontend';
const BACKEND = config.backend || 'backend';
const LIMITS = { files: 500, unzippedBytes: 50 * 1024 * 1024, zippedBytes: 5 * 1024 * 1024 }; // the platform's backend limits
const SKIP = new Set(['node_modules', '.git', 'dist', 'build', 'out', '__pycache__', '.venv', '.idea', '.vscode']);
const ENTRIES = { 'index.mjs': 'node', 'index.js': 'node', 'app.py': 'python' };

const results = [];
const add = (level, id, text, fix = '') => results.push({ level, id, text, fix });
const errorsIn = (ids) => results.some((r) => r.level === 'error' && ids.includes(r.id));

// ---------- helpers ----------
function walk(dir, skip = SKIP) {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (skip.has(entry.name)) continue;
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(path, skip));
    else if (entry.isFile()) out.push({ abs: path, rel: relative(root, path).split(sep).join('/'), size: statSync(path).size });
  }
  return out;
}

function readText(file) {
  if (file.size > 1024 * 1024) return null;
  const buffer = readFileSync(file.abs);
  return buffer.includes(0) ? null : buffer.toString('utf8');
}

/** Drop comments so a mention in a comment is not mistaken for code (keeps "://" in URLs). */
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

function run(commands, commandArgs) {
  for (const command of commands) {
    const r = spawnSync(command, commandArgs, { encoding: 'utf8' });
    if (r.status === 0) return { command, stdout: r.stdout, stderr: r.stderr };
  }
  return null;
}

// ---------- layout ----------
const frontendDir = join(root, FRONTEND);
const backendDir = join(root, BACKEND);
const hasFrontend = existsSync(frontendDir);
const hasBackend = existsSync(backendDir);

if (!hasFrontend && !hasBackend) {
  add('error', 'layout', `Neither ${FRONTEND}/ nor ${BACKEND}/ exists.`, `Create ${FRONTEND}/ (your site) and ${BACKEND}/ (your function) at the top of the project. See AGENTS.md.`);
}
if (hasFrontend) {
  const pkgPath = join(frontendDir, 'package.json');
  if (!existsSync(pkgPath)) {
    add('error', 'layout-frontend', `${FRONTEND}/package.json is missing.`, `Your site must be a normal npm project inside ${FRONTEND}/ with a "build" script.`);
  } else {
    let pkg = {};
    try { pkg = JSON.parse(readFileSync(pkgPath, 'utf8')); } catch { add('error', 'frontend-package-json', `${FRONTEND}/package.json is not valid JSON.`, 'Fix the JSON syntax.'); }
    if (!pkg.scripts?.build) add('error', 'frontend-build-script', `${FRONTEND}/package.json has no "build" script.`, 'Add  "build": "vite build"  (or your framework\'s build) under "scripts". The platform runs npm run build.');
    if (!existsSync(join(frontendDir, 'package-lock.json'))) add('warn', 'frontend-lockfile', `${FRONTEND}/package-lock.json is missing.`, `Run  cd ${FRONTEND} && npm install  once and commit package-lock.json so builds are repeatable.`);
  }
  if (!existsSync(join(frontendDir, 'index.html')) && !existsSync(join(frontendDir, 'public', 'index.html'))) {
    add('error', 'frontend-index', `${FRONTEND}/index.html is missing.`, 'A site needs an index.html (Vite keeps it at the top of the frontend folder).');
  }
} else if (hasBackend) {
  add('warn', 'no-frontend', `There is no ${FRONTEND}/ folder, so only the backend will be published.`, `Add ${FRONTEND}/ if you also want a website.`);
}
if (!errorsIn(['layout', 'layout-frontend', 'frontend-build-script', 'frontend-index', 'frontend-package-json']) && hasFrontend) add('ok', 'layout-ok', `${FRONTEND}/ looks right (package.json, build script, index.html).`);

// ---------- backend ----------
if (hasBackend) {
  const files = walk(backendDir, new Set(['__pycache__', '.git']));
  const top = readdirSync(backendDir);
  const found = Object.keys(ENTRIES).filter((e) => top.includes(e));
  const runtimes = new Set(found.map((e) => ENTRIES[e]));
  if (found.length === 0) add('error', 'backend-entry', `${BACKEND}/ has no entry file.`, `Add ${BACKEND}/app.py (Python 3.12) or ${BACKEND}/index.mjs (Node 22) exporting a handler.`);
  if (runtimes.size > 1) add('error', 'backend-two-entries', `${BACKEND}/ has both app.py and an index file.`, 'Keep exactly one backend language: delete app.py or index.mjs/index.js.');
  for (const file of files.filter((f) => /(^|\/)\.env(\..*)?$/.test(f.rel))) {
    add('error', 'backend-env-file', `${file.rel} must not be in the backend folder (it would be uploaded).`, 'Delete it. Secrets are set in the portal (Backend card > Environment variables), or in .env.local at the project root for local runs.');
  }
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (files.length > LIMITS.files) add('error', 'backend-too-many-files', `${BACKEND}/ has ${files.length} files; the limit is ${LIMITS.files}.`, 'Remove unused files, or bundle your dependencies (for example with esbuild) into one file.');
  if (total > LIMITS.unzippedBytes) add('error', 'backend-too-large', `${BACKEND}/ is ${(total / 1048576).toFixed(0)} MB unzipped; the limit is ${LIMITS.unzippedBytes / 1048576} MB.`, 'Remove large files or unused dependencies.');
  else {
    let zipped = 0;
    for (const file of files) zipped += file.size > 8 * 1024 * 1024 ? file.size : deflateSync(readFileSync(file.abs), { level: 6 }).length;
    if (zipped > LIMITS.zippedBytes) add('error', 'backend-zip-too-large', `${BACKEND}/ would zip to about ${(zipped / 1048576).toFixed(1)} MB; the limit is ${LIMITS.zippedBytes / 1048576} MB.`, 'Remove large files, or bundle your dependencies (for example with esbuild).');
  }

  if (runtimes.has('python')) {
    const stdlib = run(['python', 'python3'], ['-c', 'import sys; print(" ".join(sys.stdlib_module_names))']);
    const script = [
      'import ast, json, sys',
      'out = {}',
      'for path in sys.argv[1:]:',
      '    try:',
      '        tree = ast.parse(open(path, encoding="utf-8").read())',
      '    except SyntaxError as error:',
      '        out[path] = {"syntax": f"line {error.lineno}: {error.msg}"}',
      '        continue',
      '    names = set()',
      '    for node in ast.walk(tree):',
      '        if isinstance(node, ast.Import):',
      '            names.update(a.name.split(".")[0] for a in node.names)',
      '        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:',
      '            names.add(node.module.split(".")[0])',
      '    out[path] = sorted(names)',
      'print(json.dumps(out))',
    ].join('\n');
    const pyFiles = files.filter((f) => f.rel.endsWith('.py')).map((f) => f.abs);
    const parsed = stdlib ? run([stdlib.command], ['-c', script, ...pyFiles]) : null;
    if (!stdlib || !parsed) {
      add('warn', 'backend-python-unchecked', 'Python was not found, so the backend\'s imports were not checked.', 'Install Python 3.12 (npm run doctor) to get this check.');
    } else {
      const std = new Set(stdlib.stdout.trim().split(/\s+/));
      const local = new Set(top.map((n) => n.replace(/\.py$/, '')));
      for (const [path, info] of Object.entries(JSON.parse(parsed.stdout))) {
        const rel = relative(root, path).split(sep).join('/');
        if (info.syntax) { add('error', 'backend-python-syntax', `${rel}: ${info.syntax}`, 'Fix the Python syntax error; the function would crash on load.'); continue; }
        for (const name of info) {
          if (!std.has(name) && !local.has(name)) {
            add('error', 'backend-python-import', `${rel} imports "${name}", which is not part of Python and not in ${BACKEND}/.`, `The platform installs nothing: use only the standard library (urllib.request instead of requests, json instead of anything fancy), or copy the package's files into ${BACKEND}/.`);
          }
        }
      }
    }
  }
  if (runtimes.has('node')) {
    for (const file of files.filter((f) => /\.(mjs|js)$/.test(f.rel) && !f.rel.includes('/node_modules/'))) {
      const syntax = spawnSync(process.execPath, ['--check', file.abs], { encoding: 'utf8' });
      if (syntax.status !== 0) add('error', 'backend-node-syntax', `${file.rel}: ${(syntax.stderr.split('\n').find((l) => /Error/.test(l)) || 'syntax error').trim()}`, 'Fix the JavaScript syntax error; the function would crash on load.');
      const text = readText(file) ?? '';
      const specs = [...text.matchAll(/(?:from|import)\s*\(?\s*['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1] || m[2]);
      for (const spec of specs) {
        if (spec.startsWith('.') || spec.startsWith('/') || spec.startsWith('node:')) continue;
        const name = spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0];
        if (builtinModules.includes(spec) || builtinModules.includes(name) || existsSync(join(backendDir, 'node_modules', name))) continue;
        add('error', 'backend-node-import', `${file.rel} imports "${spec}", which is not a Node built-in and is not in ${BACKEND}/node_modules.`, `The platform runs npm install nowhere: use only Node 22 built-ins (fetch, node:fs, ...), or bundle the package into one file (npx esbuild ${BACKEND}/index.mjs --bundle --platform=node --format=esm --outfile=${BACKEND}/index.mjs) before pushing.`);
      }
    }
  }
  if (!errorsIn(['backend-entry', 'backend-two-entries', 'backend-env-file', 'backend-too-many-files', 'backend-too-large', 'backend-zip-too-large', 'backend-python-import', 'backend-python-syntax', 'backend-node-import', 'backend-node-syntax'])) {
    add('ok', 'backend-ok', `${BACKEND}/ looks right (${[...runtimes].map((r) => (r === 'python' ? 'Python 3.12' : 'Node 22')).join('')}, ${files.length} files, within limits).`);
  }
}

// ---------- frontend: nothing secret, nothing that only works on your laptop ----------
if (hasFrontend) {
  const sources = walk(join(frontendDir, 'src')).filter((f) => /\.(tsx?|jsx?|vue|svelte)$/.test(f.rel));
  const html = walk(frontendDir, new Set([...SKIP, 'src', 'public'])).filter((f) => f.rel.endsWith('.html'));
  for (const file of [...sources, ...html]) {
    const text = readText(file);
    if (text === null) continue;
    const code = stripComments(text);
    const ai = code.match(/(openrouter\.ai|api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.mistral\.ai|api\.groq\.com)/);
    if (ai) add('error', 'frontend-direct-ai', `${file.rel} calls an AI service (${ai[1]}) from the browser.`, 'Everything in the browser is public, so a key there is stolen within hours. Call your own backend route (fetch("/api/ask")) and let backend/ call the AI with the class AI helper.');
    const local = code.match(/https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0)[:/\d]*/);
    if (local) add('error', 'frontend-localhost', `${file.rel} calls ${local[0]}, which only exists on your computer.`, 'Use a relative address: fetch("/api/...").');
    const secret = code.match(/VITE_[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*/);
    if (secret) add('error', 'vite-secret', `${file.rel} reads ${secret[0]}: VITE_ variables are copied into the public website.`, 'Keep secrets in backend/ (portal Environment variables). Never use a VITE_ variable for a key, token or password.');
  }
  for (const file of readdirSync(frontendDir).filter((n) => /^\.env/.test(n)).map((n) => join(frontendDir, n))) {
    const text = readFileSync(file, 'utf8');
    const secret = text.match(/^\s*(VITE_[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)\s*=/m);
    if (secret) add('error', 'vite-secret', `${relative(root, file).split(sep).join('/')} sets ${secret[1]}: VITE_ variables are copied into the public website.`, 'Delete it. Never use a VITE_ variable for a key, token or password.');
  }
  if (!errorsIn(['frontend-direct-ai', 'frontend-localhost', 'vite-secret'])) add('ok', 'frontend-ok', 'The site calls only relative /api addresses and holds no secrets.');
}

// ---------- secrets anywhere in the project ----------
const PATTERNS = [
  ['secret-token', /mds_[A-Za-z0-9_-]{16,}/, 'a portal token'],
  ['secret-key', /sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{32,}/, 'an AI API key'],
  ['secret-key', /AKIA[0-9A-Z]{16}/, 'an AWS access key'],
  ['secret-key', /gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}/, 'a GitHub token'],
  ['secret-key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/, 'a private key'],
];
let secretsFound = false;
for (const file of walk(root)) {
  if (/\.(png|jpe?g|gif|webp|ico|svg|woff2?|ttf|zip|pdf|lock)$/i.test(file.rel) || file.rel.endsWith('package-lock.json')) continue;
  if (file.rel === '.env.local') continue; // where `npm run login` keeps the token; the checks below refuse it if git would track it
  const text = readText(file);
  if (text === null) continue;
  for (const [id, pattern, label] of PATTERNS) {
    if (pattern.test(text)) {
      secretsFound = true;
      add('error', id, `${file.rel} contains ${label}.`, id === 'secret-token'
        ? 'Remove it from the file. The portal token only goes into the GitHub secret PORTAL_TOKEN (and `npm run login` keeps it in .env.local, which stays on your computer). If it was ever pushed, ask your teacher to regenerate it.'
        : 'Remove it from the file and revoke the key (it counts as leaked once pushed). Backend secrets go in the portal\'s Environment variables.');
    }
  }
}
if (!secretsFound) add('ok', 'secrets-ok', 'No keys or tokens found in your files.');

// ---------- git hygiene ----------
const gitignore = join(root, '.gitignore');
if (!existsSync(gitignore)) {
  add('error', 'gitignore-missing', '.gitignore is missing.', 'Restore it from the kit: it keeps .env files and node_modules out of GitHub.');
} else {
  const lines = readFileSync(gitignore, 'utf8').split(/\r?\n/).map((l) => l.trim());
  if (!lines.some((l) => ['.env', '.env*', '.env.*', '*.local'].includes(l))) add('error', 'gitignore-env', '.gitignore does not exclude .env files.', 'Add the lines  .env  and  .env.*  (and  *.local ) to .gitignore.');
}
const tracked = spawnSync('git', ['ls-files'], { cwd: root, encoding: 'utf8' });
if (tracked.status === 0) {
  const list = tracked.stdout.split(/\r?\n/).filter(Boolean);
  for (const name of list.filter((n) => /(^|\/)\.env(\.[^/]*)?$/.test(n) && !n.endsWith('.env.example') || /\.local$/.test(n))) {
    add('error', 'env-committed', `${name} is tracked by git, so it goes to GitHub.`, `Run  git rm --cached ${name}  and commit. If it held a key or token, treat it as leaked: revoke it.`);
  }
  if (list.some((n) => /(^|\/)node_modules\//.test(n))) add('warn', 'node-modules-committed', 'node_modules is tracked by git.', 'Run  git rm -r --cached frontend/node_modules  and commit; the build installs packages itself.');
}

// ---------- deploy wiring and tools ----------
const workflows = existsSync(join(root, '.github', 'workflows')) ? readdirSync(join(root, '.github', 'workflows')).filter((n) => /\.ya?ml$/.test(n)) : [];
const wired = workflows.some((n) => /portal-deploy\.mjs/.test(readFileSync(join(root, '.github', 'workflows', n), 'utf8')));
if (!wired) add('warn', 'workflow-missing', 'No GitHub workflow runs portal-deploy.mjs, so pushing will not deploy.', 'Restore .github/workflows/deploy.yml from the kit.');
else if (!existsSync(join(root, 'portal-deploy.mjs'))) add('warn', 'deploy-tool-missing', 'portal-deploy.mjs is missing, so the workflow will fail.', 'Restore portal-deploy.mjs from the kit (it sits at the top of the project).');
else add('ok', 'workflow-ok', 'The deploy workflow is in place (add the PORTAL_TOKEN secret on GitHub once).');
if (Number(process.versions.node.split('.')[0]) < 22) add('warn', 'node-version', `You are running Node ${process.versions.node}; the platform uses Node 22.`, 'Install Node 22 (npm run doctor).');

// ---------- report ----------
const errors = results.filter((r) => r.level === 'error');
const warnings = results.filter((r) => r.level === 'warn');
if (asJson) {
  console.log(JSON.stringify({ ok: errors.length === 0, errors: errors.length, warnings: warnings.length, results }, null, 2));
} else {
  console.log('Platform check');
  for (const r of results) {
    const icon = { ok: '✔', warn: '⚠', error: '✖' }[r.level];
    console.log(`${icon} ${r.level === 'ok' ? '' : `[${r.id}] `}${r.text}${r.fix ? `\n    fix: ${r.fix}` : ''}`);
  }
  console.log(errors.length ? `\n${errors.length} problem(s) to fix${warnings.length ? `, ${warnings.length} warning(s)` : ''}. Fix the ✖ items and run: npm run check\nStuck? Paste this whole output to your AI assistant.` : `\nAll good${warnings.length ? ` (${warnings.length} warning(s) above)` : ''}. Push to GitHub to deploy.`);
}
process.exitCode = errors.length ? 1 : 0;
if (!asJson) await import('./update-notice.mjs').then((m) => m.updateNotice(root)).catch(() => {}); // one quiet line when a newer kit exists (terminal only)
