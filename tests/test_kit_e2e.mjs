// The kit, end to end: build it, deploy it with its own portal-deploy.mjs to the whole dev portal (real portal, gateway and Lambda
// code on in-memory AWS), switch the class AI on as the teacher, and call the deployed backend through the gateway. Python and Node.
// Needs network for `npm ci` (the frontend's packages).
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { cpSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const { buildKit } = await import(pathToFileURL(join(repo, 'starter', 'build-kit.mjs')).href);
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const say = (m) => console.log(`  ok  ${m}`);

// a pretend OpenAI-compatible service: what the class AI key points at in this test
const modelCalls = [];
const model = createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => (raw += c));
  req.on('end', () => {
    modelCalls.push({ path: req.url, auth: req.headers.authorization, body: JSON.parse(raw) });
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'The fake model says hello' } }] }));
  });
});
await new Promise((resolve) => model.listen(0, '127.0.0.1', resolve));

const PORT = 8795;
const URL = `http://127.0.0.1:${PORT}`;
const server = spawn(python, [join(here, 'dev_server.py'), String(PORT)], {
  env: { ...process.env, AWS_DEFAULT_REGION: 'us-east-1', AI_BASE_URL: `http://127.0.0.1:${model.address().port}/v1` },
});
let out = '';
server.stdout.on('data', (d) => (out += d));
server.stderr.on('data', (d) => (out += d));
const work = mkdtempSync(join(tmpdir(), 'kit-e2e-'));

const post = async (path, body, session) => {
  const response = await fetch(URL + path, { method: 'POST', headers: { 'content-type': 'application/json', ...(session ? { authorization: `Bearer ${session}` } : {}) }, body: JSON.stringify(body ?? {}) });
  return { status: response.status, body: await response.json() };
};
const gateway = async (path, options) => {
  const response = await fetch(`${URL}/b/alice${path}`, options);
  return { status: response.status, body: JSON.parse(await response.text()) };
};

try {
  for (let i = 0; i < 300 && !out.includes('Backend demo:'); i++) await new Promise((r) => setTimeout(r, 100));
  assert.ok(out.includes('Backend demo:'), `the dev portal did not start within 30 s:
${out}`);
  const token = out.match(/Alice\s+(mds_\S+)/)[1];
  const teacher = (await post('/api/teacher/login', { username: 'teacher', password: 'teacher-pass-123' })).body.session;
  const list = await (await fetch(`${URL}/api/admin/tokens`, { headers: { authorization: `Bearer ${teacher}` } })).json();
  const aliceId = list.tokens.find((t) => t.slug === 'alice').id;
  const studentSession = (await post('/api/student/login', { token })).body.session;

  const kit = join(work, 'kit');
  buildKit(kit);
  const install = spawnSync('npm ci --no-audit --no-fund', { cwd: join(kit, 'frontend'), encoding: 'utf8', shell: true });
  assert.equal(install.status, 0, install.stdout + install.stderr);
  const deploy = (dir, ...extra) => spawnSync(process.execPath, [join(dir, 'portal-deploy.mjs'), ...extra], {
    cwd: dir, encoding: 'utf8', env: { ...process.env, PORTAL_URL: URL, PORTAL_TOKEN: token, PORTAL_READY_TIMEOUT_MS: '60000' },
  });
  const aiOn = (enabled) => post(`/api/admin/tokens/${aliceId}/ai`, { enabled, limit_usd: 0.05 }, teacher);

  // ---- Python kit: AI switched on BEFORE the first deploy ----
  assert.equal((await aiOn(true)).status, 200);
  const first = deploy(kit);
  assert.equal(first.status, 0, first.stdout + first.stderr);
  assert.match(first.stdout, /Backend: packed 3 files from backend\//);
  assert.match(first.stdout, /Backend ready \(python3\.12\)/);
  assert.match(first.stdout, /Frontend: packed \d+ files from frontend[\\/]dist\//);
  assert.match(first.stdout, /Frontend live at https:\/\/alice\.example\.edu\//);
  say('the kit deploys as it is: check passes, frontend builds, backend and site published through portal-deploy.mjs');

  assert.deepEqual((await gateway('/health')).body, { ok: true });
  assert.deepEqual((await gateway('/hello?name=Kit')).body, { message: 'Hello, Kit!' });
  const asked = await gateway('/ask', { method: 'POST', body: JSON.stringify({ prompt: 'What is a CDN?' }) });
  assert.equal(asked.status, 200, JSON.stringify(asked.body));
  assert.equal(asked.body.answer, 'The fake model says hello');
  const call = modelCalls.at(-1);
  assert.equal(call.path, '/v1/chat/completions');
  assert.match(call.auth, /^Bearer sk-or-v1-fake-\d+$/, "the student's own capped key, not a platform key");
  assert.equal(call.body.model, 'anthropic/claude-haiku-5.5');
  assert.equal(call.body.messages.at(-1).content, 'What is a CDN?');
  say('Python: with the class AI on, ask("...") reaches the model with the student key, base URL and default model');

  // the teacher switches the AI off: the same student code now says so in friendly words
  assert.equal((await aiOn(false)).status, 200);
  const off = await gateway('/ask', { method: 'POST', body: JSON.stringify({ prompt: 'again' }) });
  assert.equal(off.status, 503);
  assert.equal(off.body.error, 'not_enabled');
  assert.match(off.body.message, /not switched on/);
  assert.equal(modelCalls.length, 1, 'no call reaches the model while the AI is off');
  say('Python: AI switched off -> a friendly not_enabled answer and no model call');

  // ---- class storage: the teacher switches it on AFTER the function exists; the kit's /notes route saves and reads through the real storage API ----
  const storageOn = (enabled) => post(`/api/admin/tokens/${aliceId}/storage`, { enabled }, teacher);
  const notes = (method, body) => gateway('/notes', { method, ...(body ? { body: JSON.stringify(body) } : {}) });
  const off0 = await notes('GET');
  assert.equal(off0.status, 503);
  assert.equal(off0.body.error, 'not_enabled', JSON.stringify(off0.body));
  assert.equal((await storageOn(true)).status, 200);
  assert.equal((await notes('POST', { text: 'first note' })).status, 201);
  await new Promise((r) => setTimeout(r, 5));
  assert.equal((await notes('POST', { text: 'second note' })).status, 201);
  assert.equal((await notes('POST', { text: '' })).status, 400);
  assert.deepEqual((await notes('GET')).body.notes.map((n) => n.text), ['first note', 'second note']);
  assert.equal((await storageOn(false)).status, 200);
  assert.equal((await notes('GET')).body.error, 'not_enabled', 'switching storage off stops it at once');
  assert.equal((await storageOn(true)).status, 200);
  assert.equal((await notes('GET')).body.notes.length, 2, 'the data is kept while storage is off');
  say('Python: the kit\'s /notes route saves and lists through class storage; off stops it, on brings the data back');

  // ---- Node variant: switch language as the rules describe, then deploy ----
  const nodeKit = join(work, 'node-kit');
  cpSync(kit, nodeKit, { recursive: true });
  const { rmSync: rm, cpSync: cp } = await import('node:fs');
  rm(join(nodeKit, 'backend', 'app.py'));
  rm(join(nodeKit, 'backend', 'class_ai.py'));
  rm(join(nodeKit, 'backend', 'class_storage.py'));
  cp(join(nodeKit, 'variants', 'node-backend'), join(nodeKit, 'backend'), { recursive: true });
  rm(join(nodeKit, 'variants'), { recursive: true });
  const check = spawnSync(process.execPath, [join(nodeKit, 'tools', 'check.mjs')], { encoding: 'utf8' });
  assert.equal(check.status, 0, check.stdout);
  const removal = await fetch(`${URL}/api/student/backend`, { method: 'DELETE', headers: { authorization: `Bearer ${studentSession}` } });
  assert.equal(removal.status, 200, 'a runtime switch needs the old backend removed first (the platform says so)');
  assert.equal((await aiOn(true)).status, 200);
  const second = deploy(nodeKit, '--only', 'backend');
  assert.equal(second.status, 0, second.stdout + second.stderr);
  assert.match(second.stdout, /Backend ready \(nodejs22\.x\)/);
  const nodeAsk = await gateway('/ask', { method: 'POST', body: JSON.stringify({ prompt: 'Node please' }) });
  assert.equal(nodeAsk.status, 200, JSON.stringify(nodeAsk.body));
  assert.equal(nodeAsk.body.answer, 'The fake model says hello');
  assert.equal(modelCalls.at(-1).body.messages.at(-1).content, 'Node please');
  assert.match(modelCalls.at(-1).auth, /^Bearer sk-or-v1-fake-/);
  say('Node variant: switched as the rules say, deploys, and ask("...") reaches the model too');

  assert.equal((await storageOn(true)).status, 200);
  assert.equal((await notes('POST', { text: 'from node' })).status, 201);
  assert.deepEqual((await notes('GET')).body.notes.map((n) => n.text).slice(-1), ['from node']);
  say('Node variant: the same /notes route works through class_storage.mjs');

  console.log('test_kit_e2e.mjs: all passed');
} finally {
  server.kill();
  model.close();
  rmSync(work, { recursive: true, force: true });
}
process.exit(0);
