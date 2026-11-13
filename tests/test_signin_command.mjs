// `npm run signin` (student/portal-deploy.mjs, signinCommand): the kit's real tool against a small fake portal. It shows the state, turns sign-in on and off,
// prints the addresses (the app's return address, and the one the teacher registers on Google), shows the platform's plain message when Google is not
// set up yet, and refuses a word it does not know.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const say = (m) => console.log(`  ok  ${m}`);
const { buildKit } = await import(pathToFileURL(join(repo, 'starter', 'build-kit.mjs')).href);
const work = mkdtempSync(join(tmpdir(), 'signin-cmd-'));
const kit = join(work, 'kit');
buildKit(kit);

const seen = [];
const portal = { enabled: false, googleSetUp: true };
const server = createServer((req, res) => {
  let body = '';
  req.on('data', (chunk) => { body += chunk; });
  req.on('end', () => {
    const send = (status, data) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(data)); };
    seen.push(`${req.method} ${req.url} ${req.headers.authorization ?? ''}`.trim());
    if (req.url === '/api/student/login') return send(200, { session: 'session-1', token: { slug: 'ana', student_name: 'Ana', expires_at: Date.now() / 1000 + 20 * 86400 } });
    if (req.url === '/api/student/sign-in' && req.headers.authorization === 'Bearer session-1') {
      const state = () => ({ enabled: portal.enabled, callback: portal.enabled ? 'https://ana.example.com/api/auth/callback' : null, logout: portal.enabled ? 'https://ana.example.com/' : null,
        ...(portal.googleSetUp || portal.enabled ? { google_return_address: 'https://sd-class-abc.auth.us-east-1.amazoncognito.com/oauth2/idpresponse' } : {}) });
      if (req.method === 'GET') return send(200, state());
      if (req.method === 'POST') {
        const wanted = JSON.parse(body).enabled;
        if (wanted && !portal.googleSetUp) return send(503, { error: 'Google sign-in is not set up yet. The teacher stores the Google client once (see docs/AUTH_PLAN.md).' });
        portal.enabled = wanted;
        return send(200, state());
      }
    }
    return send(404, { error: 'Unknown route.' });
  });
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${server.address().port}`;

function run(...args) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, ['portal-deploy.mjs', ...args], { cwd: kit, env: { ...process.env, PORTAL_URL: url, PORTAL_TOKEN: `mds_${'a1B2'.repeat(6)}`, PLATFORM_KIT_SYNC: 'off', CLASS_PLATFORM_HOME: join(work, 'home') } });
    let out = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { out += d; });
    child.on('close', (status) => resolve({ status, out }));
  });
}

try {
  let r = await run('signin');
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /Sign-in with Google is OFF for your app\. Turn it on with:  npm run signin -- on/);
  assert.ok(seen.includes('GET /api/student/sign-in Bearer session-1'), 'it asks the portal with the student session');
  say('npm run signin shows that sign-in is off, and how to turn it on');

  r = await run('signin', 'on');
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /✔ Sign-in with Google is ON for your app\./);
  assert.match(r.out, /Visitors come back to: https:\/\/ana\.example\.com\/api\/auth\/callback/);
  assert.match(r.out, /\/api\/auth\/login, \/api\/auth\/me, \/api\/auth\/logout/);
  assert.match(r.out, /Google client must list this return address:\s+https:\/\/sd-class-abc\.auth\.us-east-1\.amazoncognito\.com\/oauth2\/idpresponse/);
  assert.ok(seen.some((s) => s.startsWith('POST /api/student/sign-in')), 'on is a POST');
  say('npm run signin -- on turns it on and prints the app\'s return address and the address the teacher registers on Google');

  r = await run('signin');
  assert.match(r.out, /ON for your app/);
  r = await run('signin', 'off');
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /OFF for your app\. Visitors can no longer sign in\./);
  assert.equal(portal.enabled, false);
  say('the state is shown, and -- off turns it off again');

  portal.googleSetUp = false;
  r = await run('signin', 'on');
  assert.notEqual(r.status, 0, 'a refusal is an error exit');
  assert.match(r.out, /Google sign-in is not set up yet\. The teacher stores the Google client once/);
  assert.equal(portal.enabled, false);
  say('when the teacher has not set up Google, the platform\'s plain message is shown and nothing is turned on');

  r = await run('signin', 'maybe');
  assert.notEqual(r.status, 0);
  assert.match(r.out, /Use:  npm run signin/);
  say('an unknown word is refused with the three ways to use the command');
} finally {
  server.close();
  rmSync(work, { recursive: true, force: true });
}
console.log('npm run signin: all checks passed');
