#!/usr/bin/env node
// Publishes your project to the class hosting with your portal token (the one you sign in with).
//
//   node portal-deploy.mjs                 backend/ first, then frontend/
//   node portal-deploy.mjs --only frontend  (or: --only backend)
//   node portal-deploy.mjs --skip-build    deploy the frontend's existing build folder
//   node portal-deploy.mjs --frontend web --backend api    use other folder names
//   node portal-deploy.mjs status          is the saved token valid, and for how long (deploys nothing)
//   node portal-deploy.mjs login --global save your token once for this whole computer (~/.class-platform/token), used by every project
//   node portal-deploy.mjs login           save your token once (asks for it, checks it, writes .env.local); `npm run login`
//
// Expected layout:   frontend/  your app (built with `npm run build` into dist/, build/ or out/)
//                    backend/   index.mjs (Node) or app.py (Python) at its top level
// Either folder may be missing: only the one you have is published.
//
// Your token is read from PORTAL_TOKEN (environment, .env.local or .env). Never commit it.
// No npm install needed - only Node 20+. In GitHub Actions store the token as the secret PORTAL_TOKEN.
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, relative, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { deflateRawSync } from 'node:zlib';

// Teacher: the portal address of your class (override with PORTAL_URL).
const PORTAL_URL = (process.env.PORTAL_URL || 'https://deploy.example.edu').replace(/\/$/, '');
const BACKEND_ENTRIES = ['index.mjs', 'index.js', 'app.py'];
const JUNK = /(^|\/)(\.DS_Store|Thumbs\.db|__MACOSX|\.git|__pycache__)(\/|$)|\.py[co]$/;
const READY_TIMEOUT_MS = Number(process.env.PORTAL_READY_TIMEOUT_MS || 120000);

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);

/** fail() ends the run by throwing, so the process exits by itself (calling process.exit() while network connections are still closing
 *  can print a scary libuv assertion on Windows with newer Node versions). */
class Stop extends Error {}
function fail(message) {
  console.error(`
✖ ${message}`);
  throw new Stop(message);
}

function readToken() {
  if (process.env.PORTAL_TOKEN) return process.env.PORTAL_TOKEN.trim();
  for (const file of ['.env.local', '.env']) {
    if (!existsSync(file)) continue;
    const match = readFileSync(file, 'utf8').match(/^\s*PORTAL_TOKEN\s*=\s*["']?([^"'\s]+)/m);
    if (match) return match[1];
  }
  // last: the token saved once for the whole computer (`login --global`, or `class-platform-mcp setup`). Never inside a project, so it cannot be committed.
  try {
    const saved = readFileSync(join(homeFolder(), 'token'), 'utf8').trim();
    if (saved) return saved;
  } catch { /* none saved */ }
  return null;
}

/** Where the computer-wide token lives. CLASS_PLATFORM_HOME overrides it (tests, or a shared computer). */
function homeFolder() {
  return process.env.CLASS_PLATFORM_HOME || join(homedir(), '.class-platform');
}

/** Applies one chunk of typed or pasted keys to the line being typed. Enter ends it, Backspace deletes, Ctrl+C cancels, other control keys are ignored. */
export function editLine(buffer, chunk) {
  if (chunk.startsWith('\u001b')) return { buffer, done: false, abort: false }; // arrow keys and other escape sequences
  for (const ch of chunk) {
    if (ch === '\r' || ch === '\n') return { buffer, done: true, abort: false };
    if (ch === '\u0003') return { buffer, done: false, abort: true };
    if (ch === '\u007f' || ch === '\b') buffer = buffer.slice(0, -1);
    else if (ch >= ' ') buffer += ch;
  }
  return { buffer, done: false, abort: false };
}

/** What is shown for a secret while it is typed: the first 4 characters (so "mds_" proves the paste arrived), then one * per character. */
export const maskSecret = (text) => text.slice(0, 4) + '*'.repeat(Math.max(0, text.length - 4));

/** One line from the keyboard. With `hidden` the characters are masked as they arrive (see maskSecret). Piped input works too. */
async function ask(question, { hidden = false } = {}) {
  process.stdout.write(question);
  if (!process.stdin.isTTY || typeof process.stdin.setRawMode !== 'function') {
    let data = '';
    for await (const chunk of process.stdin) { data += chunk; if (data.includes('\n')) break; }
    return data.split(/\r?\n/)[0].trim();
  }
  return new Promise((resolve, reject) => {
    let line = '';
    const show = () => process.stdout.write(`\r${question}${hidden ? maskSecret(line) : line}\u001b[K`);
    const finish = (error) => {
      process.stdin.off('data', onData);
      process.stdin.setRawMode(false);
      process.stdin.pause();
      process.stdout.write('\n');
      if (error) reject(error); else resolve(line.trim());
    };
    function onData(chunk) {
      const next = editLine(line, String(chunk));
      line = next.buffer;
      if (next.abort) { process.stderr.write('\n✖ Cancelled.\n'); return finish(new Stop('Cancelled.')); }
      show();
      if (next.done) finish();
    }
    process.stdin.setEncoding('utf8');
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.on('data', onData);
  });
}

/** `status`: signs in with the saved token and says how long it is valid. Deploys nothing. */
async function status() {
  const saved = readToken();
  if (!saved) fail('No token found. Run  npm run login  once (it asks for your token and saves it).');
  const info = await signIn(saved);
  const { token } = info;
  console.log(`Signed in as ${info.name} (${info.slug}). Token status: ${token.status}; valid for ${info.daysLeft} more day${info.daysLeft === 1 ? '' : 's'}; ${token.can_deploy ? 'it can deploy.' : 'it CANNOT deploy right now: ask your teacher.'}`);
}

/** `login`: ask for the token, check it with the portal, save it to .env.local. The token is never printed. */
async function login(given) {
  if (!given) console.log('Paste your portal token (the one your teacher gave you) and press Enter. You will see mds_ and one * per character: the rest is hidden on purpose.');
  const token = (given ?? await ask('Token: ', { hidden: true })).replace(/^["']|["']$/g, '');
  if (!/^[\w.~-]{8,}$/.test(token)) fail('That does not look like a portal token (it starts with mds_ and has no spaces). Copy it again from your teacher or the portal.');
  const everywhere = flag('--global');
  if (everywhere) {
    const info = await signIn(token);
    mkdirSync(homeFolder(), { recursive: true });
    writeFileSync(join(homeFolder(), 'token'), `${token}\n`, { mode: 0o600 });
    console.log(`✔ Saved. Signed in as ${info.name} (${info.slug}); the token is valid for ${info.daysLeft} more day${info.daysLeft === 1 ? '' : 's'}. It is stored in ${join(homeFolder(), 'token')}, outside any project, so every project on this computer can use it.`);
    return;
  }
  // never write a secret to a file that git would pick up: .env.local must be ignored
  if (spawnSync('git', ['check-ignore', '-q', '.env.local']).status === 1) fail('.env.local is not ignored by git, so saving the token there could publish it. Add the lines  .env  and  .env.*  to .gitignore (npm run check explains), then run this again.');
  const info = await signIn(token);
  const file = '.env.local';
  const line = `PORTAL_TOKEN=${token}`;
  const text = existsSync(file) ? readFileSync(file, 'utf8') : '';
  const replaced = /^\s*#?\s*PORTAL_TOKEN\s*=.*$/m.test(text) ? text.replace(/^\s*#?\s*PORTAL_TOKEN\s*=.*$/m, line) : `${text}${text && !text.endsWith('\n') ? '\n' : ''}${line}\n`;
  writeFileSync(file, replaced);
  console.log(`✔ Saved. Signed in as ${info.name} (${info.slug}); the token is valid for ${info.daysLeft} more day${info.daysLeft === 1 ? '' : 's'}. It is stored in .env.local, which stays on this computer.`);
  console.log('Now deploy with:  npm run deploy');
}

// ---------- API ----------
async function api(method, path, { session, body } = {}) {
  const headers = { 'content-type': 'application/json' };
  if (session) headers.authorization = `Bearer ${session}`;
  const res = await fetch(PORTAL_URL + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }).catch(
    (e) => fail(`Cannot reach the portal (${e.cause?.code || e.message}). Check your internet connection.`),
  );
  const data = await res.json().catch(() => ({}));
  if (!res.ok) fail(`${data.error || data.message || `${method} ${path} failed`} (HTTP ${res.status})`);
  return data;
}

async function putZip(uploadUrl, zip) {
  const put = await fetch(uploadUrl, { method: 'PUT', body: zip }).catch((e) => fail(`Upload failed (${e.cause?.code || e.message}).`));
  if (!put.ok) fail(`Upload failed (HTTP ${put.status}).`);
}

async function signIn(token) {
  const res = await fetch(`${PORTAL_URL}/api/student/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token }) })
    .catch((e) => fail(`Cannot reach the portal (${e.cause?.code || e.message}). Check your internet connection.`));
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 || res.status === 403 || res.status === 404) fail('The portal does not accept this token. It may be mistyped, expired or replaced by a newer one. Run  npm run login  and paste the current token (ask your teacher if you are unsure).');
  if (!res.ok) fail(`${data.error || data.message || 'Sign-in failed'} (HTTP ${res.status})`);
  const daysLeft = Math.ceil((data.token.expires_at - Date.now() / 1000) / 86400);
  return { ...data, name: data.token.student_name || data.token.slug, slug: data.token.slug, daysLeft };
}

// ---------- ZIP (deflate, UTF-8 names) so students need no extra tools ----------
function listFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? listFiles(path) : entry.isFile() ? [path] : [];
  });
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function zipFiles(root, files) {
  const dosDate = ((2024 - 1980) << 9) | (1 << 5) | 1;
  const parts = [];
  const central = [];
  let offset = 0;
  for (const file of files) {
    const name = Buffer.from(relative(root, file).split(sep).join('/'));
    const data = readFileSync(file);
    const packed = deflateRawSync(data);
    const crc = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    parts.push(local, name, packed);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt16LE(8, 10);
    entry.writeUInt16LE(dosDate, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(packed.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);
    offset += local.length + name.length + packed.length;
  }
  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, directory, end]);
}

// ---------- frontend ----------
function buildFrontend(project) {
  const pkgPath = join(project, 'package.json');
  const pkg = existsSync(pkgPath) ? JSON.parse(readFileSync(pkgPath, 'utf8')) : {};
  if (!pkg.scripts?.build) return;
  console.log(`▶ npm run build (in ${project}/)`);
  const result = spawnSync('npm run build', { cwd: project, stdio: 'inherit', shell: true });
  if (result.status !== 0) fail('Build failed - fix the errors above and try again.');
}

async function deployFrontend(session, project) {
  if (!flag('--skip-build')) buildFrontend(project);
  // Which folder is published: the build output (dist/, build/ or out/ inside the frontend folder). A plain site with no build step
  // (index.html directly in a frontend/ folder) works too, but the repo root itself is never published as-is: it holds backend/, .git, .env ...
  const isRepoRoot = relative(process.cwd(), project) === '';
  const dir = ['dist', 'build', 'out'].map((d) => join(project, d)).find((d) => existsSync(join(d, 'index.html')))
    || (!isRepoRoot && existsSync(join(project, 'index.html')) ? project : null);
  if (!dir) fail(`No build folder with index.html found in ${project}/ (looked in dist/, build/, out/). Make sure "npm run build" creates one.`);
  const hidden = (f) => /(^|\/)(\.env(\..*)?|node_modules)(\/|$)/.test(relative(dir, f).split(sep).join('/'));
  const files = listFiles(dir).filter((f) => !JUNK.test(relative(dir, f).split(sep).join('/')) && !hidden(f));
  const zip = zipFiles(dir, files);
  console.log(`▶ Frontend: packed ${files.length} files from ${dir}/ (${(zip.length / 1024).toFixed(0)} KB)`);
  const { uploadUrl, maxBytes } = await api('POST', '/api/student/upload-url', { session });
  if (zip.length > maxBytes) fail(`The frontend is too large (${(zip.length / 1048576).toFixed(1)} MB, limit ${maxBytes / 1048576} MB).`);
  await putZip(uploadUrl, zip);
  console.log('▶ Frontend: publishing...');
  const result = await api('POST', '/api/student/publish', { session });
  if (result.warning) console.warn(`\n⚠ ${result.warning}`);
  console.log(`✔ Frontend live at ${result.url}  (${result.files} files, may take ~30s to refresh everywhere)`);
}

// ---------- backend ----------
async function deployBackend(session, dir) {
  const entry = BACKEND_ENTRIES.filter((e) => existsSync(join(dir, e)));
  if (entry.length === 0) fail(`No entry file in ${dir}/. Add index.mjs (Node 22) or app.py (Python 3.12) at its top level.`);
  if (entry.some((e) => e.endsWith('.py')) && entry.some((e) => !e.endsWith('.py'))) fail(`Include only one entry file in ${dir}/: index.mjs or app.py, not both.`);
  const files = listFiles(dir).filter((f) => !JUNK.test(relative(dir, f).split(sep).join('/')));
  const secrets = files.filter((f) => /(^|[\\/])\.env(\..*)?$/.test(f));
  if (secrets.length) fail(`Remove ${secrets.slice(0, 3).join(', ')} from ${dir}/. Secrets are set as environment variables on the portal (Backend card), not shipped in the code.`);
  const zip = zipFiles(dir, files);
  console.log(`▶ Backend: packed ${files.length} files from ${dir}/ (${(zip.length / 1024).toFixed(0)} KB)`);
  const { uploadUrl, maxBytes } = await api('POST', '/api/student/backend/upload-url', { session });
  if (zip.length > maxBytes) fail(`The backend is too large (${(zip.length / 1048576).toFixed(1)} MB, limit ${maxBytes / 1048576} MB). Bundle your dependencies (for example with esbuild).`);
  await putZip(uploadUrl, zip);
  console.log('▶ Backend: publishing...');
  let { token } = await api('POST', '/api/student/backend/publish', { session });
  const started = Date.now();
  while (token.backend.state === 'creating') {
    if (Date.now() - started > READY_TIMEOUT_MS) fail('The backend is still being created after waiting. Check the Backend card on the portal.');
    await new Promise((resolve) => setTimeout(resolve, 3000));
    ({ token } = await api('GET', '/api/student/backend', { session }));
  }
  if (token.backend.state !== 'ready') fail(`The backend did not start: ${token.backend.error || token.backend.state}`);
  console.log(`✔ Backend ready (${token.backend.runtime}). Your site calls it at /api/<route>.`);
}

// ---------- main ----------
async function main() {
  if (args[0] === 'login') return login(option('--token'));
  if (args[0] === 'status') return status();
  const only = option('--only');
  if (only && !['frontend', 'backend'].includes(only)) fail('--only must be "frontend" or "backend".');
  const frontendDir = option('--frontend', 'frontend');
  const backendDir = option('--backend', 'backend');
  const doBackend = only !== 'frontend' && (existsSync(backendDir) || only === 'backend');
  const doFrontend = only !== 'backend' && (existsSync(frontendDir) || only === 'frontend');
  if (only === 'backend' && !existsSync(backendDir)) fail(`The folder ${backendDir}/ does not exist here.`);
  if (only === 'frontend' && !existsSync(frontendDir)) fail(`The folder ${frontendDir}/ does not exist here.`);
  if (!doBackend && !doFrontend) fail(`Nothing to deploy: no ${frontendDir}/ or ${backendDir}/ folder here. Run this from the root of your project.`);

  let saved = readToken();
  if (!saved) {
    if (!process.stdin.isTTY) fail('No token found. Run  npm run login  once (it asks for your token and saves it), or set PORTAL_TOKEN. In GitHub Actions add it as a repository secret named PORTAL_TOKEN.');
    console.log('No token saved yet, so let us do that now.');
    await login();
    saved = readToken();
  }
  const { session, token, daysLeft } = await signIn(saved);
  if (!token.can_deploy) fail(`Your token cannot deploy right now (status: ${token.status}). Ask your teacher, or request regeneration on the portal.`);
  console.log(`▶ Signed in as ${token.student_name || token.slug} (${token.slug}). Token valid for ${daysLeft} more day${daysLeft === 1 ? '' : 's'}.`);
  if (daysLeft <= 3) console.warn('⚠ Your token expires soon. Request regeneration on the portal so deploys keep working.');

  if (doBackend) await deployBackend(session, backendDir);
  if (doFrontend) await deployFrontend(session, frontendDir);
  // inside the student kit: one quiet line when a newer kit exists (a terminal only; this file also runs alone, so it is optional)
  const notice = join(process.cwd(), 'tools', 'update-notice.mjs');
  if (existsSync(notice)) await import(pathToFileURL(notice).href).then((m) => m.updateNotice(process.cwd())).catch(() => {});
}

// (tests import editLine and maskSecret without starting a deploy)
if (!process.env.PORTAL_DEPLOY_NO_MAIN) {
  main().catch((error) => {
    if (!(error instanceof Stop)) console.error(error);
    process.exitCode = 1;
  });
}
