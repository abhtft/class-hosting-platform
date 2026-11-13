// The kit's sign-in helper for Node (student/class_auth.mjs): the same behaviour as the Python helper (tests/test_class_auth.py): the modes, the
// Google flow with a pretend token service, every wrong callback, the signed session cookie, the place to return to. And the two helpers agree:
// a session cookie made by one is read by the other (same format, same key).
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const ca = await import(pathToFileURL(join(here, '..', 'student', 'class_auth.mjs')).href);
const say = (m) => console.log(`  ok  ${m}`);
let clock = 1_800_000_000;
ca.hooks.now = () => clock;
const ON = {
  CLASS_AUTH_HOST: 'https://sd-class-abc.auth.us-east-1.amazoncognito.com', CLASS_AUTH_CLIENT_ID: 'client-1',
  CLASS_AUTH_ISSUER: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pool', CLASS_AUTH_REDIRECT: 'https://ana.example.com/api/auth/callback',
  CLASS_AUTH_HOME: 'https://ana.example.com/', CLASS_AUTH_KEY: 'k'.repeat(43), CLASS_AUTH_DAYS: '7',
};
function setMode(kind, extra = {}) {
  for (const name of Object.keys(process.env)) if (name.startsWith('CLASS_AUTH_') || name === 'AWS_LAMBDA_FUNCTION_NAME') delete process.env[name];
  if (kind === 'on') Object.assign(process.env, ON, extra, { AWS_LAMBDA_FUNCTION_NAME: 'sd-ana' });
  if (kind === 'off') process.env.AWS_LAMBDA_FUNCTION_NAME = 'sd-ana';
}
const event = (path, { method = 'GET', query = '', cookies, headerCookie } = {}) => ({
  rawPath: path, rawQueryString: query, requestContext: { http: { method } }, headers: headerCookie ? { cookie: headerCookie } : {}, ...(cookies ? { cookies } : {}),
});
function jar(answer, old = []) {
  const held = Object.fromEntries(old.map((c) => [c.slice(0, c.indexOf('=')), c.slice(c.indexOf('=') + 1)]));
  for (const c of answer.cookies ?? []) {
    const [pair, ...attrs] = c.split('; ');
    const name = pair.slice(0, pair.indexOf('='));
    if (attrs.includes('Max-Age=0')) delete held[name]; else held[name] = pair.slice(pair.indexOf('=') + 1);
  }
  return Object.entries(held).map(([k, v]) => `${k}=${v}`);
}
const token = (claims) => `header.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;
const GOOD = { iss: ON.CLASS_AUTH_ISSUER, aud: 'client-1', token_use: 'id', exp: clock + 3600, sub: 'user-123', email: ['ana', 'example.org'].join('@'), name: 'Ana' };
const paramsOf = (answer) => Object.fromEntries(new URL(answer.headers.location).searchParams);

// ---------- modes and routes ----------
setMode('local'); assert.equal(ca.mode(), 'local');
setMode('off'); assert.equal(ca.mode(), 'off');
setMode('on'); assert.equal(ca.mode(), 'on');
assert.equal(await ca.handle(event('/notes')), null);
assert.equal((await ca.handle(event('/auth/unknown'))).statusCode, 404);
assert.equal((await ca.handle(event('/auth/logout'))).statusCode, 404, 'signing out is a POST');
say('three modes, and only /auth/... is ours');

// ---------- local ----------
setMode('local');
assert.deepEqual(JSON.parse((await ca.handle(event('/auth/me'))).body), { user: null, sign_in: 'local' });
let go = await ca.handle(event('/auth/login', { query: 'next=/notes' }));
assert.equal(go.statusCode, 302);
assert.equal(go.headers.location, '/notes');
assert.ok(go.cookies[0].includes('HttpOnly') && go.cookies[0].includes('SameSite=Lax') && !go.cookies[0].includes('Secure'));
let held = jar(go);
const local = ca.currentUser(event('/x', { cookies: held }));
assert.equal(local.id, 'local-visitor');
assert.deepEqual(ca.currentUser(event('/x', { headerCookie: held.join('; ') })), local);
const out = await ca.handle(event('/auth/logout', { method: 'POST', cookies: held }));
assert.deepEqual(jar(out, held), []);
assert.throws(() => ca.requireUser(event('/x')), (stop) => stop instanceof ca.SignInRequired && stop.response.statusCode === 401);
say('on your own computer a pretend visitor signs in at once; sign-out clears it; requireUser answers 401');

// ---------- off ----------
setMode('off');
assert.equal((await ca.handle(event('/auth/login'))).statusCode, 503);
assert.equal(ca.currentUser(event('/x', { cookies: held })), null);
say('on the platform with sign-in off: a plain 503, and nobody is signed in');

// ---------- on ----------
setMode('on');
go = await ca.handle(event('/auth/login', { query: 'next=/notes%3Fpage%3D2' }));
let q = paramsOf(go);
assert.ok(go.headers.location.startsWith(`${ON.CLASS_AUTH_HOST}/oauth2/authorize?`));
assert.equal(q.response_type, 'code'); assert.equal(q.client_id, 'client-1'); assert.equal(q.redirect_uri, ON.CLASS_AUTH_REDIRECT);
assert.equal(q.scope, 'openid email profile'); assert.equal(q.code_challenge_method, 'S256'); assert.equal(q.identity_provider, 'Google');
assert.ok(go.cookies[0].startsWith('class_auth_tx=') && go.cookies[0].includes('Secure') && go.cookies[0].includes('HttpOnly') && go.cookies[0].includes('Max-Age=600'));
const seen = {};
ca.hooks.post = async (url, fields) => { Object.assign(seen, { url, fields }); return { id_token: token(GOOD) }; };
const back = await ca.handle(event('/auth/callback', { query: `code=one-time-code&state=${q.state}`, cookies: jar(go) }));
assert.equal(back.statusCode, 302);
assert.equal(back.headers.location, '/notes?page=2');
assert.equal(seen.url, `${ON.CLASS_AUTH_HOST}/oauth2/token`);
assert.equal(createHash('sha256').update(seen.fields.code_verifier).digest('base64url'), q.code_challenge, 'the proof key matches the challenge');
const sessionCookie = back.cookies.find((c) => c.startsWith('class_session='));
assert.ok(sessionCookie.includes('HttpOnly') && sessionCookie.includes('Secure') && sessionCookie.includes('SameSite=Lax') && sessionCookie.includes(`Max-Age=${7 * 86400}`));
assert.ok(back.cookies.some((c) => c.startsWith('class_auth_tx=;') && c.includes('Max-Age=0')));
held = jar(back, jar(go));
assert.deepEqual(ca.currentUser(event('/x', { cookies: held })), { id: 'user-123', email: GOOD.email, name: 'Ana' });
say('the Google sign-in: proof key and random state, the code exchanged once, a signed HttpOnly Secure SameSite=Lax session cookie');

// ---------- wrong callbacks ----------
async function attempt({ claims = GOOD, state, cookies = 'pending', code = 'c', fail = false } = {}) {
  const start = await ca.handle(event('/auth/login'));
  const p = paramsOf(start);
  ca.hooks.post = async () => { if (fail) throw new Error('service down'); return { id_token: token(claims) }; };
  return ca.handle(event('/auth/callback', { query: `state=${state ?? p.state}${code ? `&code=${code}` : ''}`, cookies: cookies === 'pending' ? jar(start) : cookies }));
}
const wrong = {
  'a state that does not match': { state: 'someone-elses-state' },
  'no pending cookie': { cookies: [] },
  'a changed pending cookie': { cookies: ['class_auth_tx=abc.def'] },
  'no code': { code: '' },
  'the token service failing': { fail: true },
  'another issuer': { claims: { ...GOOD, iss: 'https://evil.example/pool' } },
  'a token for another app': { claims: { ...GOOD, aud: 'client-2' } },
  'an access token': { claims: { ...GOOD, token_use: 'access' } },
  'an expired token': { claims: { ...GOOD, exp: clock - 1 } },
  'no user id': { claims: { ...GOOD, sub: undefined } },
};
for (const [name, how] of Object.entries(wrong)) {
  const answer = await attempt(how);
  assert.equal(answer.headers.location, '/?signin=failed', name);
  assert.ok(!(answer.cookies ?? []).some((c) => c.startsWith('class_session=')), `${name}: nobody is signed in`);
}
go = await ca.handle(event('/auth/login'));
q = paramsOf(go);
ca.hooks.post = async () => ({ id_token: token(GOOD) });
clock += 601;
assert.equal((await ca.handle(event('/auth/callback', { query: `code=c&state=${q.state}`, cookies: jar(go) }))).headers.location, '/?signin=failed', 'too late');
clock -= 601;
say('a wrong callback signs nobody in (state, pending cookie, code, service, issuer, app, token kind, expiry, user id, too late)');

// ---------- the cookie cannot be forged, and it ends ----------
const value = held.find((c) => c.startsWith('class_session=')).slice('class_session='.length);
const [, sig] = value.split('.');
assert.equal(ca.currentUser(event('/x', { cookies: [`class_session=${value.slice(0, -2)}xx`] })), null);
const forged = Buffer.from(JSON.stringify({ id: 'admin', email: '', name: '', exp: clock + 999 })).toString('base64url');
assert.equal(ca.currentUser(event('/x', { cookies: [`class_session=${forged}.${sig}`] })), null);
setMode('on', { CLASS_AUTH_KEY: `another-apps-key-${'z'.repeat(30)}` });
assert.equal(ca.currentUser(event('/x', { cookies: held })), null, 'a cookie from another app');
setMode('on');
clock += 7 * 86400 + 1;
assert.equal(ca.currentUser(event('/x', { cookies: held })), null, 'the session ends');
clock -= 7 * 86400 + 1;
for (const garbage of ['', 'x', 'a.b.c', '....']) assert.equal(ca.currentUser(event('/x', { cookies: [`class_session=${garbage}`] })), null);
say('the session cookie: changed, forged, from another app and old ones are refused');

// ---------- the place to return to ----------
for (const bad of ['//evil.example/x', 'https://evil.example/', '/\\evil', 'javascript:alert(1)', '/a\nb', `/${'x'.repeat(600)}`]) assert.equal(ca.safeNext(bad), '/', bad);
assert.equal(ca.safeNext('/notes?a=1'), '/notes?a=1');
setMode('on', { CLASS_AUTH_HOME: 'https://d123.cloudfront.net/ana/' });
go = await ca.handle(event('/auth/login', { query: 'next=/notes' }));
q = paramsOf(go);
assert.equal((await ca.handle(event('/auth/callback', { query: `code=c&state=${q.state}`, cookies: jar(go) }))).headers.location, '/ana/notes');
setMode('on');
say('the place to return to is always a path inside this app');

// ---------- Python and Node agree on the cookie ----------
{
  const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
  const script = `
import os, sys, json
sys.path.insert(0, ${JSON.stringify(join(here, '..', 'student'))})
os.environ.update(${JSON.stringify({ ...ON, AWS_LAMBDA_FUNCTION_NAME: 'sd-ana' })})
import class_auth as ca
ca._now = lambda: ${clock}
made = ca._session_cookie({"id": "user-9", "email": "", "name": "Py"}).split(";")[0]
seen = ca.current_user({"cookies": [sys.argv[1]], "headers": {}})
print(json.dumps({"made": made, "seen": seen}))
`;
  const nodeMade = held.find((c) => c.startsWith('class_session='));
  const r = spawnSync(python, ['-c', script, nodeMade], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const result = JSON.parse(r.stdout);
  assert.deepEqual(result.seen, { id: 'user-123', email: GOOD.email, name: 'Ana' }, 'Python reads the cookie Node made');
  assert.deepEqual(ca.currentUser(event('/x', { cookies: [result.made] })), { id: 'user-9', email: '', name: 'Py' }, 'Node reads the cookie Python made');
  say('the Python and the Node helper read each other’s session cookie (one format)');
}
console.log('test_class_auth.mjs: the sign-in helper (Node) passes');
