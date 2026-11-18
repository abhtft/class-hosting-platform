// `npm run login -- --google` (student/portal-deploy.mjs): the computer makes its own token, sends the platform only a hash of it, shows a code,
// waits for the student to approve it in the portal, then saves the token in .env.local. A fake portal stands in for the platform.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const cli = join(here, '..', 'student', 'portal-deploy.mjs');
const say = (m) => console.log(`  ok  ${m}`);

let started = null; // what the computer sent when it asked for a code
let approvals = 0;
let mode = 'approve'; // approve | deny | never
const seen = [];
const server = createServer((req, res) => {
  let body = '';
  req.on('data', (c) => (body += c));
  req.on('end', () => {
    const data = body ? JSON.parse(body) : {};
    seen.push(`${req.method} ${req.url} ${body}`);
    const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
    if (req.url === '/api/student/pair/start') { started = data; return send(200, { code: 'ABCD-EFGH', expires_at: 0, minutes: 1 }); }
    if (req.url === '/api/student/pair/poll') {
      if (data.code !== 'ABCD-EFGH' || data.hash !== started.hash) return send(200, { state: 'unknown' });
      approvals += 1;
      return send(200, { state: mode === 'never' ? 'pending' : approvals < 2 ? 'pending' : mode === 'deny' ? 'denied' : 'approved' });
    }
    if (req.url === '/api/student/login') {
      if (createHash('sha256').update(data.token || '').digest('hex') !== started?.hash) return send(401, { error: 'no' });
      return send(200, { session: 's', token: { slug: 'ann-lab', student_name: 'Ann', expires_at: Math.floor(Date.now() / 1000) + 5 * 86400 } });
    }
    return send(404, { error: 'unknown' });
  });
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${server.address().port}`;

function runCli(cwd, ...args) {
  return new Promise((resolve) => {
    const child = spawn('node', [cli, 'login', '--google', ...args], { cwd, env: { ...process.env, PORTAL_URL: url, PORTAL_PAIR_POLL_MS: '50', PORTAL_TOKEN: '' } });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (out += d));
    child.on('close', (status) => resolve({ status, out }));
  });
}

try {
  let dir = mkdtempSync(join(tmpdir(), 'pair-'));
  let r = await runCli(dir);
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /ABCD-EFGH/);
  assert.match(r.out, /Pair a computer/);
  const saved = readFileSync(join(dir, '.env.local'), 'utf8');
  const token = saved.match(/^PORTAL_TOKEN=(mdp_[A-Za-z0-9_-]{30,})$/m)?.[1];
  assert.ok(token, saved);
  assert.equal(createHash('sha256').update(token).digest('hex'), started.hash, 'the hash the platform got is the hash of the token saved');
  assert.ok(!seen.some((line) => line.includes(token) && !line.includes('/api/student/login')), 'the token was sent only to sign in, never when asking for the code');
  assert.ok(!r.out.includes(token), 'the token is never printed');
  assert.match(started.device, /\S/);
  assert.match(r.out, /Signed in as Ann \(ann-lab\)/);
  say('the computer makes its own token, sends only its hash, shows the code, waits, then saves the token in .env.local (never printed)');

  mode = 'deny'; approvals = 0;
  dir = mkdtempSync(join(tmpdir(), 'pair-'));
  r = await runCli(dir);
  assert.notEqual(r.status, 0);
  assert.match(r.out, /refused this computer/);
  assert.ok(!existsSync(join(dir, '.env.local')), 'nothing is saved when the student refuses');
  say('a refused pairing saves nothing');

  mode = 'never'; approvals = 0;
  dir = mkdtempSync(join(tmpdir(), 'pair-'));
  const quick = spawn('node', [cli, 'login', '--google'], { cwd: dir, env: { ...process.env, PORTAL_URL: url, PORTAL_PAIR_POLL_MS: '50', PORTAL_TOKEN: '' } });
  await new Promise((r2) => setTimeout(r2, 800));
  quick.kill();
  assert.ok(!existsSync(join(dir, '.env.local')), 'nothing is saved while the student has not approved');
  say('nothing is saved while the code is not approved');
} finally {
  server.close();
}
console.log('test_pair_cli.mjs: passed');
