// The teacher MCP package (teacher-mcp/): the protocol, which tools exist (and which deliberately do not), that everything that changes
// something asks first, background jobs, how it runs the repository's setup.mjs, key masking, and the editor setup (merge, backup, JSONC refused).
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const pkg = join(here, '..', 'teacher-mcp');
const KEY = 'sk-or-' + 'v1-abcdefghijklmnop'; // built in two pieces so no key-shaped text sits in the repository
const work = mkdtempSync(join(tmpdir(), 'teacher-mcp-'));

// a stand-in platform repository: template.yaml, and a setup.mjs that records its arguments and answers like the real one
const repo = join(work, 'repo');
const log = join(work, 'calls.log');
await import('node:fs').then(({ mkdirSync }) => mkdirSync(repo));
writeFileSync(join(repo, 'template.yaml'), 'Resources: {}\n');
writeFileSync(join(repo, 'setup.mjs'), `
import { appendFileSync } from 'node:fs';
const argv = process.argv.slice(2);
appendFileSync(${JSON.stringify(log)}, JSON.stringify(argv) + '\\n');
const [command] = argv;
const answers = {
  check: { ok: true, account: '111122223333', message: 'Ready.' },
  status: { ok: true, installed: true, features: ['backend'], key: ${JSON.stringify(KEY)} },
  install: { ok: true, portalUrl: 'https://x.cloudfront.net/' },
  plan: { ok: true, planId: 'abc12345def67890', adds: 1, modifies: 2, removes: 0, blocked: false },
  apply: { ok: true, version: '1.1.0' },
  'finish-domain': { ok: true },
  publish: { ok: true, message: 'published' },
};
if (command === 'install') await new Promise((r) => setTimeout(r, 700));
console.log(JSON.stringify(answers[command] ?? { ok: false, error: 'unknown' }));
`);

function start(extra = [], cwd = work) {
  const child = spawn(process.execPath, [join(pkg, 'bin.mjs'), ...extra], { cwd, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env } });
  const waiting = new Map();
  let buffer = '';
  const serverRequests = [];
  child.stdout.on('data', (d) => {
    buffer += d;
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i);
      buffer = buffer.slice(i + 1);
      if (!line.trim()) continue;
      const message = JSON.parse(line);
      if (message.method) { serverRequests.push(message); continue; }
      waiting.get(message.id)?.(message);
    }
  });
  let n = 0;
  const rpc = (method, params = {}) => new Promise((resolve) => { const id = ++n; waiting.set(id, resolve); child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`); });
  const notify = (method, params = {}) => child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
  const reply = (id, result) => child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, result })}\n`);
  const call = async (name, args = {}) => { const r = await rpc('tools/call', { name, arguments: args }); return { text: r.result.content[0].text, isError: r.result.isError }; };
  return { child, rpc, notify, reply, call, serverRequests, stop: () => child.kill() };
}
const calls = () => (existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l)) : []);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- protocol and the list of tools ----------
const s = start(['--repo', repo]);
process.on('exit', () => s.stop());
const init = await s.rpc('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 't' } });
assert.equal(init.result.serverInfo.name, 'class-platform-teacher');
assert.match(init.result.instructions, /never ask for them/);
const listed = (await s.rpc('tools/list')).result.tools;
assert.deepEqual(listed.map((t) => t.name).sort(), ['apply_update', 'check_environment', 'finish_domain', 'install', 'job_status', 'plan_update', 'publish_portal', 'status', 'teacher_login']);
for (const forbidden of ['destroy', 'delete', 'secrets', 'password', 'iam', 'teacher_setup']) assert.ok(!listed.some((t) => t.name.includes(forbidden)), `no ${forbidden} tool`);
for (const name of ['install', 'finish_domain', 'apply_update', 'publish_portal']) {
  const t = listed.find((x) => x.name === name);
  assert.deepEqual(t.inputSchema.required.includes('confirm'), true, `${name} needs confirm`);
  assert.match(t.description, /ask|IMPORTANT/i);
}
assert.equal((await s.rpc('ping')).result && 1, 1);
assert.equal((await s.rpc('nothing/here')).error.code, -32601);
assert.equal((await s.rpc('tools/call', { name: 'nope', arguments: {} })).error.code, -32602);
console.log('  ok  protocol; nine tools; no deleting, no passwords or keys, no IAM; every changing tool needs confirm');

// ---------- read-only tools run the repository's own setup.mjs in JSON mode ----------
const checked = await s.call('check_environment', { profile: 'new' });
assert.equal(checked.isError, false);
assert.deepEqual(calls().pop(), ['check', '--profile', 'new', '--json']);
const status = await s.call('status');
assert.ok(!status.text.includes(KEY) && status.text.includes('***'), 'anything shaped like a key is masked');
const plan = await s.call('plan_update', { enable: 'gemini' });
assert.match(plan.text, /abc12345def67890/);
assert.deepEqual(calls().pop(), ['plan', '--enable', 'gemini', '--json']);
console.log('  ok  check, status and plan run setup.mjs --json with the right flags; key-shaped text is masked');

// ---------- everything that changes asks first ----------
const before = calls().length;
for (const [name, args] of [['install', {}], ['finish_domain', {}], ['apply_update', { plan_id: 'abc12345def67890' }], ['publish_portal', {}]]) {
  const r = await s.call(name, args);
  assert.equal(r.isError, true, `${name} without confirm`);
  assert.match(r.text, /Ask|ask|Tell/, name);
}
assert.equal((await s.call('install', { confirm: false })).isError, true);
assert.equal(calls().length, before, 'nothing ran without confirm');
const badPlan = await s.call('apply_update', { confirm: true, plan_id: '../etc; rm -rf' });
assert.equal(badPlan.isError, true);
assert.equal(calls().length, before, 'a malformed plan id never reaches the shell');
console.log('  ok  without confirm=true nothing runs; a malformed plan id is refused');

// ---------- install is a background job ----------
const started = await s.call('install', { confirm: true, profile: 'new', domain: 'apps.example.com', features: ['ai', 'storage'] });
assert.equal(started.isError, false);
assert.match(started.text, /Started "install"/);
const during = await s.call('job_status');
assert.match(during.text, /still running|finished/);
const second = await s.call('install', { confirm: true });
assert.match(second.text, /already running|Started/, 'a second job is refused while one runs, or starts after it ended');
await wait(1200);
const finished = await s.call('job_status');
assert.match(finished.text, /finished/);
assert.match(finished.text, /x\.cloudfront\.net/);
assert.deepEqual(calls().find((c) => c[0] === 'install'), ['install', '--profile', 'new', '--domain', 'apps.example.com', '--features', 'ai,storage', '--json']);
const applied = await s.call('apply_update', { confirm: true, plan_id: 'abc12345def67890' });
assert.match(applied.text, /Started "apply_update"/);
await wait(500);
assert.deepEqual(calls().pop(), ['apply', 'abc12345def67890', '--json']);
assert.match((await s.call('job_status')).text, /1\.1\.0/);
const dry = await s.call('install', { confirm: true, dry_run: true });
assert.deepEqual(calls().pop(), ['install', '--dry-run', '--json'], 'a dry run is answered at once and not as a job');
const login = await s.call('teacher_login', { username: 'ms-rao' });
assert.match(login.text, /node setup\.mjs teacher ms-rao/);
assert.match((await s.call('teacher_login', { username: 'x; rm -rf /' })).text, /<username>/, 'an odd name is not echoed into a command');
console.log('  ok  install and apply run in the background with the right flags; dry run is immediate; the password is only explained, never taken');

// ---------- which folder: the editor\'s open folder (roots), or a clear message ----------
s.stop();
const r = start([], join(work));
const initR = await r.rpc('initialize', { protocolVersion: '2025-06-18', capabilities: { roots: {} }, clientInfo: { name: 't' } });
assert.ok(initR.result);
const lost = await r.call('status');
assert.equal(lost.isError, true);
assert.match(lost.text, /Open the platform folder/);
r.notify('notifications/initialized');
await wait(300);
const asked = r.serverRequests.find((m) => m.method === 'roots/list');
assert.ok(asked, 'the server asks the editor which folder is open');
r.reply(asked.id, { roots: [{ uri: `file:///${repo.replace(/\\/g, '/')}` }] });
await wait(300);
const found = await r.call('status');
assert.equal(found.isError, false, found.text);
const wrong = await r.call('status', { path: join(work, 'not-a-repo') });
assert.equal(wrong.isError, true, 'an explicit path that is not the platform folder is refused, never silently replaced');
assert.match(wrong.text, /not the platform folder/);
r.stop();
console.log('  ok  the open folder is learned from the editor; without one the message says what to do');

// ---------- editor setup ----------
const { plan: planEditor, apply: applyEditor, entryFor } = await import('../teacher-mcp/setup.mjs');
assert.deepEqual(entryFor('1.0.0', 'win32'), { command: 'cmd', args: ['/c', 'npx', '-y', 'class-platform-teacher' + '@1.0.0', 'mcp'] });
assert.deepEqual(entryFor('1.0.0', 'linux'), { command: 'npx', args: ['-y', 'class-platform-teacher' + '@1.0.0', 'mcp'] });
const file = join(work, 'mcp.json');
const editor = { id: 'vscode', name: 'VS Code', file, key: 'servers', typed: true };
const created = planEditor(editor, entryFor('1.0.0', 'linux'));
assert.equal(created.action, 'create');
writeFileSync(file, JSON.stringify({ servers: { other: { command: 'x' } }, inputs: [] }));
const added = planEditor(editor, entryFor('1.0.0', 'linux'));
assert.equal(added.action, 'add');
assert.deepEqual(Object.keys(added.doc.servers).sort(), ['class-platform-teacher', 'other'], 'other servers are kept');
assert.equal(added.doc.servers['class-platform-teacher'].type, 'stdio');
const backup = applyEditor(added);
assert.ok(backup && existsSync(backup), 'a backup is made');
assert.equal(planEditor(editor, entryFor('1.0.0', 'linux')).action, 'same');
assert.equal(planEditor(editor, entryFor('1.1.0', 'linux')).action, 'update');
writeFileSync(file, '{ // my notes\n "servers": {} }');
assert.equal(planEditor(editor, entryFor('1.0.0', 'linux')).action, 'manual', 'a file with comments is never rewritten');
writeFileSync(file, '{ broken');
assert.equal(planEditor(editor, entryFor('1.0.0', 'linux')).action, 'manual');
assert.equal(readFileSync(join(pkg, 'jsonc.mjs'), 'utf8'), readFileSync(join(here, '..', 'starter', 'mcp', 'jsonc.mjs'), 'utf8'), 'the JSONC reader is the student package\'s, unchanged');
const version = spawn(process.execPath, [join(pkg, 'bin.mjs'), 'version'], { stdio: ['ignore', 'pipe', 'pipe'] });
let out = '';
version.stdout.on('data', (d) => (out += d));
await new Promise((resolve) => version.on('close', resolve));
assert.equal(out.trim(), JSON.parse(readFileSync(join(pkg, 'package.json'), 'utf8')).version);
console.log('  ok  editor setup merges one entry, backs up, updates a pinned version, refuses files with comments');

rmSync(work, { recursive: true, force: true });
console.log('test_teacher_mcp.mjs: all passed');
