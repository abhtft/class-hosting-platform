// `verify` (installer/verify.mjs) against a fake portal in the same process: every check passes on a healthy install, each failure is named,
// the throwaway student is always revoked and cleaned up, a wrong password creates nothing, and a portal that is down does not crash it.
import assert from 'node:assert/strict';
import { localSigner } from '../starter/publish-files.mjs';
import { checkTeacherLogin, runVerify, SAMPLE_MARKER, sampleHtml, unknownSite } from '../installer/verify.mjs';

const PORTAL = 'https://deploy.example.test';

const KIT = Buffer.from(JSON.stringify({ kitVersion: '1.6.0', serial: 1 }));

/** signing: 'ok' (signed by the key it publishes), 'none' (no signature files) or 'forged' (signed by some other key). */
function fakePortal({ password = 'right-password', sites = true, features = true, kit = true, signing = 'ok' } = {}) {
  const platformKey = localSigner();
  const signature = signing === 'ok' ? platformKey.sign(KIT) : localSigner().sign(KIT);
  const log = [];
  const live = new Map();
  let lastSlug = '';
  const reply = (status, body, text) => ({ status, json: async () => body, text: async () => text ?? JSON.stringify(body ?? {}), arrayBuffer: async () => Buffer.from(text ?? JSON.stringify(body ?? {})) });
  const fetchImpl = async (url, init = {}) => {
    const path = url.replace(PORTAL, '');
    const method = init.method ?? 'GET';
    const auth = init.headers?.authorization;
    log.push(`${method} ${path}`);
    if (url.startsWith('https://zz-')) return live.has(url) ? reply(200, null, sampleHtml()) : reply(404, null, 'no');
    if (path === '/') return reply(200, null, '<html>portal</html>');
    if (path === '/mcp-version.json') return reply(200, { version: '1.0.0' });
    if (path === '/kit-version.json') return kit ? reply(200, JSON.parse(KIT), KIT.toString()) : reply(404, null);
    if (path === '/kit-version.json.sig') return signing === 'none' || !kit ? reply(404, null) : reply(200, null, signature);
    if (path === '/signing-key.pub') return signing === 'none' || !kit ? reply(404, null) : reply(200, null, platformKey.publicKeyPem);
    if (path === '/api/admin/tokens' && method === 'GET') return reply(auth ? 200 : 401, { error: 'login' });
    if (path === '/api/student/me') return auth === 'Bearer STUDENT' ? reply(200, { token: features ? { backend: { available: true }, ai: { available: true, providers: ['openrouter', 'gemini'] }, storage: { available: true } } : {} }) : reply(401, {});
    if (path === '/api/teacher/login') return JSON.parse(init.body).password === password ? reply(200, { session: 'TEACHER' }) : reply(401, { error: 'no' });
    if (path === '/api/admin/tokens' && method === 'POST') return auth === 'Bearer TEACHER' ? reply(201, { token: 'mds_throwaway', item: { id: 'tid1', slug: (lastSlug = JSON.parse(init.body).slug) } }) : reply(401, {});
    if (path === '/api/student/login') return JSON.parse(init.body).token === 'mds_throwaway' ? reply(200, { session: 'STUDENT' }) : reply(401, {});
    if (path === '/api/admin/tokens/tid1/revoke') return reply(200, {});
    return reply(404, {});
  };
  const publish = ({ token }) => { log.push(`publish with ${token}`); if (!sites) return ''; const url = `https://${lastSlug}.example.test/`; live.set(url, true); return url; };
  return { fetchImpl, publish, log };
}
const names = (r) => r.checks.map((c) => c.name);
const failedNames = (r) => r.checks.filter((c) => !c.ok).map((c) => c.name);

// ---------- a healthy install: every check passes, and the throwaway student is cleaned up ----------
{
  const p = fakePortal();
  const cleaned = [];
  const r = await runVerify({ portal: PORTAL, login: { username: 'teacher', password: 'right-password' }, features: ['backend', 'ai', 'gemini', 'storage'], repo: '.', fetchImpl: p.fetchImpl, publish: p.publish, cleanup: async (slug) => cleaned.push(slug), sleep: async () => {} });
  assert.equal(r.ok, true, JSON.stringify(failedNames(r)));
  for (const expected of ['The portal home page opens', 'The student MCP package is published', 'The student kit is published', 'The student kit is signed by the platform key', 'The teacher list needs a login (401 without one)',
    'A student page needs a token (401 without one)', 'The teacher can sign in', 'A student token can be created', 'The student can sign in with the token',
    'The feature "gemini" is offered to students', 'The student can publish a site', 'The published site is live and shows its page', 'A site that does not exist is refused']) {
    assert.ok(names(r).includes(expected), `missing check: ${expected}`);
  }
  assert.ok(p.log.includes('POST /api/admin/tokens/tid1/revoke'), 'the throwaway token is revoked');
  assert.equal(cleaned.length, 1);
  assert.match(cleaned[0], /^zz-verify-[a-z0-9]{5}$/, 'only a zz-verify name is ever cleaned up');
  assert.match(r.message, /Every check passed/);
  console.log(`  ok  a healthy install: ${r.checks.length} checks pass; the throwaway student is revoked and cleaned up`);
}

// ---------- each kind of failure is named, and cleanup still runs ----------
{
  const noSite = fakePortal({ sites: false });
  const cleaned = [];
  const r = await runVerify({ portal: PORTAL, login: { username: 'teacher', password: 'right-password' }, repo: '.', fetchImpl: noSite.fetchImpl, publish: noSite.publish, cleanup: async (s) => cleaned.push(s), sleep: async () => {} });
  assert.equal(r.ok, false);
  assert.deepEqual(failedNames(r), ['The student can publish a site']);
  assert.ok(noSite.log.includes('POST /api/admin/tokens/tid1/revoke') && cleaned.length === 1, 'cleanup runs after a failed publish');

  const noFeatures = fakePortal({ features: false });
  const f = await runVerify({ portal: PORTAL, login: { username: 'teacher', password: 'right-password' }, features: ['storage'], repo: '.', fetchImpl: noFeatures.fetchImpl, publish: noFeatures.publish, sleep: async () => {} });
  assert.deepEqual(failedNames(f), ['The feature "storage" is offered to students']);

  const noKit = fakePortal({ kit: false });
  const k = await runVerify({ portal: PORTAL, login: null, repo: '.', fetchImpl: noKit.fetchImpl });
  assert.ok(failedNames(k).includes('The student kit is published'));

  // an unsigned kit, or one signed by some other key, is named; students' computers would refuse it
  for (const signing of ['none', 'forged']) {
    const bad = fakePortal({ signing });
    const r = await runVerify({ portal: PORTAL, login: null, repo: '.', fetchImpl: bad.fetchImpl });
    assert.ok(failedNames(r).includes('The student kit is signed by the platform key'), signing);
    assert.ok(!failedNames(r).includes('The student kit is published'));
  }
  console.log('  ok  a missing site, feature or kit is named exactly; cleanup runs after a failure');
}

// ---------- the real portal answers 201 for a new token; a failure after creation still removes the student ----------
{
  const p = fakePortal();
  const cleaned = [];
  const brokenLogin = async (url, init) => (url.endsWith('/api/student/login') ? { status: 500, json: async () => ({}), text: async () => '' } : p.fetchImpl(url, init));
  const r = await runVerify({ portal: PORTAL, login: { username: 'teacher', password: 'right-password' }, repo: '.', fetchImpl: brokenLogin, publish: p.publish, cleanup: async (s) => cleaned.push(s), sleep: async () => {} });
  assert.deepEqual(failedNames(r), ['The student can sign in with the token']);
  assert.ok(p.log.includes('POST /api/admin/tokens/tid1/revoke') && cleaned.length === 1, 'the token made just before the failure is revoked and cleaned up');
  console.log('  ok  a token answered with 201 is accepted; a failure right after creating it still revokes and cleans the throwaway student');
}

// ---------- a wrong password creates nothing ----------
{
  const p = fakePortal();
  const cleaned = [];
  const r = await runVerify({ portal: PORTAL, login: { username: 'teacher', password: 'wrong' }, repo: '.', fetchImpl: p.fetchImpl, publish: p.publish, cleanup: async (s) => cleaned.push(s), sleep: async () => {} });
  assert.equal(r.ok, false);
  assert.ok(failedNames(r).includes('The teacher can sign in'));
  assert.ok(!p.log.includes('POST /api/admin/tokens'), 'no student is created without a valid login');
  assert.equal(cleaned.length, 0, 'and nothing needs cleaning');
  assert.ok(!p.log.some((l) => l.startsWith('publish')), 'nothing is published');
  console.log('  ok  a wrong teacher password creates and publishes nothing');
}

// ---------- public checks only (the IDE tool, or no login given) ----------
{
  const p = fakePortal();
  const r = await runVerify({ portal: PORTAL, login: null, repo: '.', fetchImpl: p.fetchImpl, publish: p.publish });
  assert.equal(r.ok, true, 'public checks pass; the signed-in part is reported as skipped, not failed');
  assert.equal(r.publicOnly, true);
  assert.match(r.message, /public checks passed/);
  assert.ok(!p.log.includes('POST /api/teacher/login'), 'no login attempt');
  console.log('  ok  without a login only the public checks run, and they say the rest was skipped');
}

// ---------- the portal is down ----------
{
  const r = await runVerify({ portal: PORTAL, login: null, repo: '.', fetchImpl: async () => { throw new Error('getaddrinfo ENOTFOUND'); } });
  assert.equal(r.ok, false);
  assert.ok(r.checks.find((c) => c.name === 'The portal home page opens').detail.includes('ENOTFOUND'));
  console.log('  ok  a portal that is down is reported, not thrown');
}

// ---------- helpers ----------
assert.equal(unknownSite('https://alice.example.test/', 'alice'), 'https://zz-no-such-site.example.test/');
assert.equal(unknownSite('https://x.cloudfront.net/alice/', 'alice'), 'https://x.cloudfront.net/zz-no-such-site/');
assert.ok(sampleHtml().includes(SAMPLE_MARKER));
{
  const p = fakePortal();
  assert.equal(await checkTeacherLogin(PORTAL, 'teacher', 'right-password', { fetchImpl: p.fetchImpl }), 'ok');
  assert.equal(await checkTeacherLogin(PORTAL, 'teacher', 'nope', { fetchImpl: p.fetchImpl }), 'denied');
  assert.equal(await checkTeacherLogin(PORTAL, 'teacher', 'x', { fetchImpl: async () => { throw new Error('down'); } }), 'unreachable');
  assert.equal(await checkTeacherLogin(PORTAL, 'teacher', 'x', { fetchImpl: async () => ({ status: 503 }) }), 'unreachable');
}
console.log('  ok  unknown-site address, sample page, and the teacher-login check (ok / denied / unreachable)');
console.log('test_verify.mjs: all passed');
