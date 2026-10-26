// The student kit (starter/): what it contains, that every IDE rule copy says the same thing, that the checker catches each class of
// mistake (and does not cry wolf), and that both local backends run with hot reload and an offline AI mock.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { randomBytes, createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const { buildKit, ruleFiles } = await import(pathToFileURL(join(repo, 'starter', 'build-kit.mjs')).href);
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const work = mkdtempSync(join(tmpdir(), 'kit-'));
const say = (m) => console.log(`  ok  ${m}`);
const read = (p) => readFileSync(p, 'utf8');
const lf = (p) => read(p).replace(/\r\n/g, '\n');

const kit = join(work, 'kit');
buildKit(kit);

// ---- 1. contents ----
const rules = read(join(repo, 'starter', 'rules', 'AGENTS.md')).replace(/\r\n/g, '\n');
for (const path of ['AGENTS.md', 'GEMINI.md', 'CLAUDE.md', '.claude/skills/class-platform/SKILL.md', '.cursor/rules/class-platform.mdc', '.github/copilot-instructions.md', '.windsurfrules',
  'README.md', 'PROMPTS.md', 'package.json', 'platform.json', '.gitignore', '.env.example', 'portal-deploy.mjs', 'kit-manifest.json', '.github/workflows/deploy.yml',
  'tools/check.mjs', 'tools/mcp.mjs', '.vscode/mcp.json', 'tools/dev.mjs', 'tools/dev_server.py', 'tools/dev_server.mjs', 'tools/doctor.mjs', 'frontend/package.json', 'frontend/package-lock.json',
  'frontend/src/App.tsx', 'frontend/src/api.ts', 'backend/app.py', 'backend/class_ai.py', 'backend/class_storage.py', 'variants/node-backend/index.mjs', 'variants/node-backend/class_ai.mjs', 'variants/node-backend/class_storage.mjs']) {
  assert.ok(existsSync(join(kit, path)), `the kit is missing ${path}`);
}
for (const [name, content] of Object.entries(ruleFiles(rules))) {
  if (name === 'CLAUDE.md') assert.match(read(join(kit, name)), /@AGENTS\.md/);
  else assert.ok(read(join(kit, name)).includes(rules), `${name} must carry the full rules, identical to AGENTS.md`);
  assert.equal(read(join(kit, name)), content);
}
say('every file an AI editor reads carries the same rules (CLAUDE.md imports them)');

// single sources: the kit copies are byte-identical to the platform's own files
for (const [from, to] of [['student/portal-deploy.mjs', 'portal-deploy.mjs'], ['student/class_ai.py', 'backend/class_ai.py'], ['student/class_ai.mjs', 'variants/node-backend/class_ai.mjs'],
  ['student/class_storage.py', 'backend/class_storage.py'], ['student/class_storage.mjs', 'variants/node-backend/class_storage.mjs']]) {
  assert.equal(read(join(kit, to)), lf(join(repo, from)), `${to} must be the platform's ${from} (line endings normalised to LF)`);
}
const manifest = JSON.parse(read(join(kit, 'kit-manifest.json')));
for (const [path, sha] of Object.entries(manifest.files)) assert.equal(createHash('sha256').update(readFileSync(join(kit, path))).digest('hex'), sha, `manifest hash of ${path}`);
assert.equal(manifest.kitVersion, JSON.parse(read(join(kit, 'platform.json'))).kitVersion);
say('single-source files are identical to the platform copies; the manifest hashes match');

// generic and safe to publish: no token, key, email address or personal path
const SECRET = /mds_[A-Za-z0-9_-]{16,}|sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{32,}|AKIA[0-9A-Z]{16}/;
const EMAIL = /[A-Za-z0-9._+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}/;
const listAll = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? listAll(join(dir, e.name)) : [join(dir, e.name)]));
for (const file of listAll(kit).filter((f) => !f.endsWith('package-lock.json'))) {
  const text = read(file);
  assert.ok(!SECRET.test(text), `a key-like string in the kit: ${file}`);
  assert.ok(!EMAIL.test(text), `an email address in the kit: ${file}`);
  assert.ok(!/Abhishek|D:\\\\Github|C:\\\\Users/.test(text), `a personal path or name in the kit: ${file}`);
}
say('the kit is generic: no token, key, email or personal path (safe for a public repository)');

// the checker's backend limits equal the platform's real limits (no drift)
const num = (expr) => expr.split('*').reduce((product, part) => product * Number(part.trim()), 1);
const backends = read(join(repo, 'lambda', 'backends.py'));
const limits = read(join(kit, 'tools', 'check.mjs')).match(/LIMITS = \{ files: (\d+), unzippedBytes: ([\d *]+), zippedBytes: ([\d *]+) \}/);
assert.equal(Number(limits[1]), Number(backends.match(/^MAX_FILES = (\d+)/m)[1]));
assert.equal(num(limits[2]), num(backends.match(/^MAX_UNZIPPED_BYTES = ([\d *]+)/m)[1]));
assert.equal(num(limits[3]), num(backends.match(/^MAX_ZIP_BYTES = ([\d *]+)/m)[1]));
say("the checker's backend limits equal the platform's");

// ---- 2. the checker ----
const check = (dir, ...extra) => {
  const r = spawnSync(process.execPath, [join(dir, 'tools', 'check.mjs'), '--root', dir, '--json', ...extra], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 });
  return { status: r.status, json: JSON.parse(r.stdout), stderr: r.stderr };
};
const pristine = check(kit);
assert.equal(pristine.status, 0, JSON.stringify(pristine.json.results.filter((r) => r.level !== 'ok'), null, 2));
assert.equal(pristine.json.warnings, 0, JSON.stringify(pristine.json.results.filter((r) => r.level === 'warn')));
say('a fresh kit passes with no warnings');

let n = 0;
const fixture = (id, level, mutate, { exit } = {}) => {
  const dir = join(work, `fx${++n}`);
  cpSync(kit, dir, { recursive: true });
  mutate(dir);
  const { status, json } = check(dir);
  const hit = json.results.find((r) => r.id === id && r.level === level);
  assert.ok(hit, `${id} (${level}) was not reported; got ${JSON.stringify(json.results.filter((r) => r.level !== 'ok').map((r) => r.id))}`);
  if (level === 'error') assert.ok(hit.fix, `${id} must say how to fix it`);
  assert.equal(status, exit ?? (level === 'error' ? 1 : 0), `${id}: exit code`);
  return json;
};
const toNode = (d) => { rmSync(join(d, 'backend', 'app.py')); rmSync(join(d, 'backend', 'class_ai.py')); rmSync(join(d, 'backend', 'class_storage.py')); cpSync(join(d, 'variants', 'node-backend'), join(d, 'backend'), { recursive: true }); rmSync(join(d, 'variants'), { recursive: true }); };
const append = (file, text) => (d) => writeFileSync(join(d, file), read(join(d, file)) + text);
const fakeToken = 'mds_' + 'a1B2'.repeat(6);
const fakeOrKey = 'sk-or-v1-' + 'a1B2c3D4'.repeat(4);
const fakeAws = 'AKIA' + 'ABCDEFGHIJKLMNOP';

fixture('layout-frontend', 'error', (d) => rmSync(join(d, 'frontend', 'package.json')));
fixture('frontend-build-script', 'error', (d) => { const p = join(d, 'frontend', 'package.json'); const j = JSON.parse(read(p)); delete j.scripts.build; writeFileSync(p, JSON.stringify(j)); });
fixture('frontend-index', 'error', (d) => rmSync(join(d, 'frontend', 'index.html')));
fixture('frontend-lockfile', 'warn', (d) => rmSync(join(d, 'frontend', 'package-lock.json')));
fixture('layout', 'error', (d) => { rmSync(join(d, 'frontend'), { recursive: true }); rmSync(join(d, 'backend'), { recursive: true }); });
fixture('no-frontend', 'warn', (d) => rmSync(join(d, 'frontend'), { recursive: true }));
say('layout: missing frontend, build script, index.html, both folders; lockfile and backend-only are warnings');

fixture('backend-entry', 'error', (d) => rmSync(join(d, 'backend', 'app.py')));
fixture('backend-two-entries', 'error', (d) => writeFileSync(join(d, 'backend', 'index.mjs'), 'export const handler = async () => ({});'));
fixture('backend-env-file', 'error', (d) => writeFileSync(join(d, 'backend', '.env'), 'X=1'));
fixture('backend-python-import', 'error', append('backend/app.py', '\nimport requests\n'));
fixture('backend-python-syntax', 'error', append('backend/app.py', '\ndef broken(:\n'));
fixture('backend-node-import', 'error', (d) => { toNode(d); append('backend/index.mjs', "\nimport _ from 'lodash';\n")(d); });
fixture('backend-node-syntax', 'error', (d) => { toNode(d); append('backend/index.mjs', '\nconst = ;\n')(d); });
fixture('backend-too-many-files', 'error', (d) => { for (let i = 0; i < 501; i++) writeFileSync(join(d, 'backend', `f${i}.txt`), 'x'); });
fixture('backend-zip-too-large', 'error', (d) => writeFileSync(join(d, 'backend', 'big.bin'), randomBytes(6 * 1024 * 1024)));
say('backend: entry file, two languages, .env, non-stdlib imports (Python and Node), syntax errors, file count, zip size');

fixture('frontend-direct-ai', 'error', append('frontend/src/App.tsx', "\nfetch('https://openrouter.ai/api/v1/chat/completions')\n"));
fixture('frontend-localhost', 'error', append('frontend/src/App.tsx', "\nfetch('http://localhost:8788/api/health')\n"));
fixture('vite-secret', 'error', (d) => writeFileSync(join(d, 'frontend', '.env.production'), 'VITE_API_KEY=abc\n'));
fixture('vite-secret', 'error', append('frontend/src/App.tsx', '\nconst k = import.meta.env.VITE_SECRET_TOKEN\n'));
say('frontend: AI called from the browser, localhost URLs, VITE_ secrets');

fixture('secret-token', 'error', append('README.md', `\nmy token ${fakeToken}\n`));
fixture('secret-key', 'error', append('backend/app.py', `\nKEY = "${fakeOrKey}"\n`));
fixture('secret-key', 'error', append('PROMPTS.md', `\n${fakeAws}\n`));
fixture('secret-key', 'error', append('PROMPTS.md', `\n${'-----BEGIN '}RSA PRIVATE KEY${'-----'}\n`));
say('secrets anywhere: portal token, AI key, AWS key, private key');

fixture('gitignore-env', 'error', (d) => writeFileSync(join(d, '.gitignore'), 'node_modules/\n'));
fixture('gitignore-missing', 'error', (d) => rmSync(join(d, '.gitignore')));
fixture('env-committed', 'error', (d) => {
  writeFileSync(join(d, '.env'), 'NOTHING=1\n');
  spawnSync('git', ['init', '-q'], { cwd: d });
  spawnSync('git', ['add', '-f', '.env'], { cwd: d });
});
fixture('workflow-missing', 'warn', (d) => rmSync(join(d, '.github', 'workflows'), { recursive: true }));
fixture('deploy-tool-missing', 'warn', (d) => rmSync(join(d, 'portal-deploy.mjs')));
say('git hygiene and wiring: .gitignore, tracked .env, missing workflow or deploy tool');

// no crying wolf: mentions in comments and obvious placeholders are fine
const calm = join(work, 'calm');
cpSync(kit, calm, { recursive: true });
append('frontend/src/App.tsx', '\n// never call openrouter.ai or http://localhost:8788 from here\n/* VITE_API_KEY is forbidden */\n')(calm);
append('README.md', '\nPORTAL_TOKEN=mds_xxxxxxxx and sk-or-... are placeholders\n')(calm);
append('backend/app.py', '\n# import requests would fail on the platform\n"""from lodash import x"""\n')(calm);
const quiet = check(calm);
assert.equal(quiet.status, 0, JSON.stringify(quiet.json.results.filter((r) => r.level === 'error')));
say('comments, docstrings and placeholders such as mds_xxxxxxxx do not trigger errors');

// a token saved by `npm run login` lives in .env.local: that is allowed, but never when git would track it
const saved = join(work, 'saved-token');
cpSync(kit, saved, { recursive: true });
writeFileSync(join(saved, '.env.local'), `PORTAL_TOKEN=${fakeToken}
`);
const savedRun = check(saved);
assert.equal(savedRun.status, 0, JSON.stringify(savedRun.json.results.filter((r) => r.level === 'error')));
fixture('env-committed', 'error', (d) => {
  writeFileSync(join(d, '.env.local'), `PORTAL_TOKEN=${fakeToken}
`);
  spawnSync('git', ['init', '-q'], { cwd: d });
  spawnSync('git', ['add', '-f', '.env.local'], { cwd: d });
});
say('a token saved by npm run login in .env.local passes the check; the same file tracked by git is an error');

// the human-readable report carries a fix line and a hint to ask the AI
const broken = join(work, 'readable');
cpSync(kit, broken, { recursive: true });
rmSync(join(broken, 'backend', 'app.py'));
const text = spawnSync(process.execPath, [join(broken, 'tools', 'check.mjs'), '--root', broken], { encoding: 'utf8' });
assert.equal(text.status, 1);
assert.match(text.stdout, /✖ \[backend-entry\]/);
assert.match(text.stdout, /fix: /);
assert.match(text.stdout, /Paste this whole output to your AI assistant/);
say('the printed report has ✖ lines with fixes and tells the student what to do next');

// ---- 3. local backends with hot reload and the offline mock ----
async function withServer(dir, script, port, fn) {
  const command = script.endsWith('.py') ? python : process.execPath;
  const child = spawn(command, [join(dir, 'tools', script), String(port)], { cwd: dir, env: { ...process.env, AI_MOCK: '', OPENAI_API_KEY: '' }, stdio: 'ignore' });
  try {
    for (let i = 0; i < 100; i++) {
      if (await fetch(`http://127.0.0.1:${port}/api/health`).then((r) => r.ok, () => false)) break;
      await new Promise((r) => setTimeout(r, 100));
    }
    await fn((path, options) => fetch(`http://127.0.0.1:${port}${path}`, options).then(async (r) => ({ status: r.status, text: await r.text() })));
  } finally {
    child.kill();
  }
}
const exercise = async (call, hotReloadFile, marker) => {
  assert.deepEqual(JSON.parse((await call('/api/health')).text), { ok: true });
  assert.deepEqual(JSON.parse((await call('/api/hello?name=Sam')).text), { message: 'Hello, Sam!' });
  const asked = await call('/api/ask', { method: 'POST', body: JSON.stringify({ prompt: 'What is a CDN?' }) });
  assert.equal(asked.status, 200);
  assert.equal(JSON.parse(asked.text).answer, '[mock AI] You asked: What is a CDN?', 'locally the class AI is an offline mock');
  assert.equal((await call('/api/ask', { method: 'POST', body: JSON.stringify({ prompt: '' }) })).status, 400);
  assert.equal((await call('/api/nothing')).status, 404);
  assert.equal((await call('/elsewhere')).status, 404, 'only /api is served');
  writeFileSync(hotReloadFile, read(hotReloadFile).replace(marker[0], marker[1]));
  await new Promise((r) => setTimeout(r, 1200));
  assert.match((await call('/api/hello?name=Sam')).text, /HOT RELOAD/, 'a changed backend file is picked up without a restart');
};
const pyKit = join(work, 'dev-py');
cpSync(kit, pyKit, { recursive: true });
await withServer(pyKit, 'dev_server.py', 18941, (call) => exercise(call, join(pyKit, 'backend', 'app.py'), ['f"Hello, {name}!"', 'f"HOT RELOAD {name}!"']));
say('Python dev server: routes, offline AI mock, 400/404, hot reload');
const nodeKit = join(work, 'dev-node');
cpSync(kit, nodeKit, { recursive: true });
toNode(nodeKit);
await withServer(nodeKit, 'dev_server.mjs', 18942, (call) => exercise(call, join(nodeKit, 'backend', 'index.mjs'), ['`Hello, ${name}!`', '`HOT RELOAD ${name}!`']));
say('Node dev server: routes, offline AI mock, 400/404, hot reload');

// ---- 4. doctor runs ----
const doctor = spawnSync(process.execPath, [join(kit, 'tools', 'doctor.mjs')], { encoding: 'utf8' });
assert.match(doctor.stdout, /Node \d+/);
assert.match(doctor.stdout, /git /);
say('doctor reports Node, npm, git and Python');

// the kit is deterministic and uses LF everywhere, so manifests match whichever computer built it
const again = join(work, 'kit-again');
buildKit(again);
assert.equal(read(join(again, 'kit-manifest.json')), read(join(kit, 'kit-manifest.json')), 'two builds give the same manifest');
assert.deepEqual(listAll(kit).filter((f) => readFileSync(f).includes(Buffer.from([13, 10]))), [], 'no CRLF line endings in the kit');
say('building twice gives the same manifest; every text file uses LF');

// build-kit refuses to overwrite by accident
assert.throws(() => buildKit(kit), /not empty/);
rmSync(work, { recursive: true, force: true });
console.log('test_kit.mjs: all passed');
process.exit(0);
