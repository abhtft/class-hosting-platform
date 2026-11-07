// `npm run traces` in the student kit (student/portal-deploy.mjs): lists the student's newest answers and shows one, in both views; a clear message when the
// teacher has not allowed it. A fake local portal plays the platform; the token never appears in the output.
import assert from 'node:assert/strict';
import { spawnSync, spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const script = join(dirname(fileURLToPath(import.meta.url)), '..', 'student', 'portal-deploy.mjs');
const TOKEN = 'mds_fake-token-never-printed';
const ID = 'tr_0123456789abcdef';
let view = 'metadata';
const server = createServer((req, res) => {
  res.setHeader('content-type', 'application/json');
  const send = (status, body) => { res.statusCode = status; res.end(JSON.stringify(body)); };
  if (req.url === '/api/student/login') return send(200, { session: 's1', token: { expires_at: Date.now() / 1000 + 86400 * 20, student_name: 'Alice', slug: 'alice', status: 'active', can_deploy: true } });
  if (view === 'off') return send(403, { error: 'Your teacher has not switched on trace viewing for you.' });
  if (req.url.startsWith('/api/student/traces?')) return send(200, { view, traces: [{ trace_id: ID, started: 1793986800000, spans: 3, tokens: 1055, errors: 0 }] });
  if (req.url === `/api/student/traces/${ID}`) return send(200, { view, trace: { trace_id: ID, started: 1793986800000, spans: 1, tokens: 120 },
    spans: [{ name: 'llm', status: 'ok', source: 'platform', dur_ms: 1500, attrs: { model: 'm', ...(view === 'stored' ? { answer: 'stored answer text' } : { answer_chars: 18 }) } }] });
  send(404, { error: 'not found' });
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const env = { ...process.env, PORTAL_URL: `http://127.0.0.1:${server.address().port}`, PORTAL_TOKEN: TOKEN };
const run = (...args) => new Promise((resolve) => {
  const child = spawn('node', [script, 'traces', ...args], { env, cwd: dirname(script) });
  let out = '';
  child.stdout.on('data', (d) => { out += d; });
  child.stderr.on('data', (d) => { out += d; });
  child.on('close', (code) => resolve({ code, out }));
});

let r = await run();
assert.equal(r.code, 0, r.out);
assert.match(r.out, /tr_0123456789abcdef .*3 steps.*1055 tokens/);
assert.match(r.out, /numbers only/);
r = await run(ID);
assert.equal(r.code, 0, r.out);
assert.match(r.out, /answer_chars: 18/);
assert.doesNotMatch(r.out, /stored answer text/);
view = 'stored';
r = await run(ID);
assert.match(r.out, /stored answer text/);
assert.match(r.out, /as stored/);
r = await run('nonsense');
assert.notEqual(r.code, 0);
assert.match(r.out, /looks like tr_/);
view = 'off';
r = await run();
assert.notEqual(r.code, 0);
assert.match(r.out, /not switched on trace viewing/);
assert.ok(!r.out.includes(TOKEN), 'the token is never printed');
server.close();
console.log('  ok  npm run traces: list, one trace, numbers-only and as-stored views, bad reference, teacher has not allowed it');
process.exitCode = 0;
