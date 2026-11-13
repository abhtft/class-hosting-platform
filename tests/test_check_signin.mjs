// `npm run check` and the sign-in standard: a project made from the kit has no login of its own (and says so); each way of writing a login by hand
// (a password field, a token in the browser's storage, a /login route, password hashing or token making in the backend) is flagged with the same advice:
// use the platform's sign-in. The platform's own helper and the page parts are never flagged.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { appendFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const say = (m) => console.log(`  ok  ${m}`);
const { buildKit } = await import(pathToFileURL(join(repo, 'starter', 'build-kit.mjs')).href);
const work = mkdtempSync(join(tmpdir(), 'check-signin-'));

function check(kit) {
  const r = spawnSync(process.execPath, [join('tools', 'check.mjs'), '--json'], { cwd: kit, encoding: 'utf8' });
  const start = r.stdout.indexOf('{');
  return JSON.parse(r.stdout.slice(start)).results ?? JSON.parse(r.stdout.slice(start));
}
const ids = (results) => results.filter((x) => x.level === 'warn').map((x) => x.id);

try {
  // a fresh kit: no login of its own
  const clean = join(work, 'clean');
  buildKit(clean);
  let results;
  try { results = check(clean); } catch { results = null; }
  if (!results) { // the checker prints text, not JSON: read its lines
    const r = spawnSync(process.execPath, [join('tools', 'check.mjs')], { cwd: clean, encoding: 'utf8' });
    assert.match(r.stdout, /No login of your own: sign-in, if you use it, comes from the platform\./, r.stdout.slice(0, 600));
    assert.doesNotMatch(r.stdout, /login of your own\.|password field|has a login or sign-up route|hashes passwords/);
  } else {
    assert.ok(results.some((x) => x.id === 'signin-ok'));
    assert.ok(!ids(results).some((id) => /login|token-in-browser|password/.test(id)));
  }
  say('a fresh kit has no login of its own: the platform\'s helper and the page parts are not flagged');

  // each way of writing a login by hand
  const own = join(work, 'own');
  buildKit(own);
  mkdirSync(join(own, 'frontend', 'src'), { recursive: true });
  writeFileSync(join(own, 'frontend', 'src', 'Login.tsx'),
    'export default function Login() {\n  localStorage.setItem("auth_token", "x")\n  return <form><input type="password" /></form>\n}\n');
  appendFileSync(join(own, 'backend', 'app.py'), '\n\ndef sign_up(path):\n    if path == "/signup":\n        import hashlib\n        return hashlib.pbkdf2_hmac("sha256", b"p", b"s", 1)\n');
  const r = spawnSync(process.execPath, [join('tools', 'check.mjs')], { cwd: own, encoding: 'utf8' });
  for (const needle of [
    /frontend\/src\/Login\.tsx has a password field: a login of your own\./,
    /frontend\/src\/Login\.tsx saves a token or login in the browser's storage\./,
    /backend\/app\.py has a login or sign-up route of your own\./,
    /backend\/app\.py hashes passwords or makes tokens itself\./,
    /Use the platform's sign-in instead: class_auth in the backend and useUser\(\) in the page/,
  ]) assert.match(r.stdout, needle);
  assert.doesNotMatch(r.stdout, /No login of your own/);
  assert.doesNotMatch(r.stdout, /class_auth\.py (has|hashes|saves)/, 'the platform\'s own helper is never flagged');
  assert.equal(r.status, 0, 'these are warnings first: they do not stop a deploy');
  say('a password field, a token in browser storage, a /signup route and password hashing are each flagged with the same advice; they warn but do not stop a deploy');
} finally {
  rmSync(work, { recursive: true, force: true });
}
console.log('check and sign-in: all checks passed');
