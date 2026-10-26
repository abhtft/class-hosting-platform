// `verify`: does this installed platform really work? It acts like a student would, with a throwaway student it creates and removes:
// the pages answer, nothing private is open, a teacher can sign in, a student token can publish a small sample site, the site is live, an unknown
// site is refused, and every switched-on feature is offered. Everything goes through the portal's public address, exactly like a real class.
//
// The teacher's user and password are used once to sign in and are never stored. Without them only the public checks run.
// HTTP and the publishing step are parameters, so the logic is tested without a network (tests/test_verify.mjs).
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const SAMPLE_MARKER = 'Your class platform works';
export const sampleHtml = () => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sample site</title></head>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>${SAMPLE_MARKER}</h1>
<p>This small page was published by the platform's own check, as a student would publish theirs. It is removed again right after.</p>
</body></html>
`;

const json = async (response) => { try { return await response.json(); } catch { return null; } };

/** Sign in with the teacher login. Returns the session, or null. Retries for a while: a new login takes up to a minute to be picked up. */
export async function teacherSession(portal, username, password, { fetchImpl = fetch, wait = 5000, attempts = 18, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  for (let i = 0; i < attempts; i += 1) {
    const response = await fetchImpl(`${portal}/api/teacher/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username, password }) }).catch(() => null);
    if (response?.status === 200) return (await json(response))?.session ?? null;
    if (response?.status === 401 || response?.status === 429) { if (i === attempts - 1 || response.status === 429) return null; }
    await sleep(wait);
  }
  return null;
}

/** Is this teacher login accepted? 'ok', 'denied' (wrong user or password, or locked out), or 'unreachable' (no answer from the portal). One attempt, no retries. */
export async function checkTeacherLogin(portal, username, password, { fetchImpl = fetch } = {}) {
  const response = await fetchImpl(`${String(portal).replace(/\/$/, '')}/api/teacher/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username, password }) }).catch(() => null);
  if (!response) return 'unreachable';
  if (response.status === 200) return 'ok';
  return [401, 403, 429].includes(response.status) ? 'denied' : 'unreachable';
}

/** Publish the sample site with the student's own command-line tool, as a student does. Returns the live address or ''. */
export function publishSample({ repo, portal, token, spawn = spawnSync }) {
  const work = mkdtempSync(join(tmpdir(), 'verify-site-'));
  try {
    mkdirSync(join(work, 'frontend', 'dist'), { recursive: true });
    writeFileSync(join(work, 'frontend', 'dist', 'index.html'), sampleHtml());
    const run = spawn(process.execPath, [join(repo, 'student', 'portal-deploy.mjs'), '--only', 'frontend', '--skip-build'], {
      cwd: work, encoding: 'utf8', timeout: 180000,
      env: { ...process.env, PORTAL_URL: portal, PORTAL_TOKEN: token, CLASS_PLATFORM_HOME: join(work, 'no-home'), PORTAL_READY_TIMEOUT_MS: '60000' },
    });
    const live = String(run.stdout ?? '').match(/Frontend live at (https?:\/\/\S+)/);
    return run.status === 0 && live ? live[1] : '';
  } finally {
    try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may hold the folder a moment */ }
  }
}

/** The address of a site that does not exist, made from a real one by changing the site name. */
export function unknownSite(liveUrl, slug) {
  const url = new URL(liveUrl);
  if (url.pathname.includes(`/${slug}/`)) return liveUrl.replace(`/${slug}/`, '/zz-no-such-site/');
  return liveUrl.replace(`${slug}.`, 'zz-no-such-site.');
}

/**
 * Run all the checks. `login` is {username, password} or null. `features` are the switched-on feature names. `cleanup(slug)` removes the throwaway
 * student's remaining data (the sample site's files) and is called even when a check failed.
 */
export async function runVerify({ portal, login = null, features = [], repo, fetchImpl = fetch, publish = publishSample, cleanup = async () => {}, sleep, slug = `zz-verify-${Math.random().toString(36).slice(2, 7)}` }) {
  const base = String(portal).replace(/\/$/, '');
  const checks = [];
  const add = (name, ok, detail = '') => { checks.push({ name, ok: Boolean(ok), detail: ok ? '' : String(detail) }); return Boolean(ok); };
  const get = (url, init) => fetchImpl(url, init).then((r) => r, (e) => ({ status: 0, error: e.message }));
  const call = async (path, { method = 'GET', body, session } = {}) => {
    const response = await get(`${base}${path}`, { method, headers: { 'content-type': 'application/json', ...(session ? { authorization: `Bearer ${session}` } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, data: response.status ? await json(response) : null };
  };

  // 1. public: pages answer, private things are closed
  const home = await get(`${base}/`);
  add('The portal home page opens', home.status === 200, `HTTP ${home.status}${home.error ? ` (${home.error})` : ''}`);
  const mcp = await get(`${base}/mcp-version.json`);
  add('The student MCP package is published', mcp.status === 200, `HTTP ${mcp.status}`);
  const kit = await get(`${base}/kit-version.json`);
  add('The student kit is published', kit.status === 200, `HTTP ${kit.status}`);
  add('The teacher list needs a login (401 without one)', (await call('/api/admin/tokens')).status === 401, 'it answered without a login');
  add('A student page needs a token (401 without one)', (await call('/api/student/me')).status === 401, 'it answered without a token');

  if (!login) {
    add('Signed-in checks', false, 'skipped: no teacher login was given. Run verify in your terminal to include them');
    return summary(checks, true);
  }

  // 2. a throwaway student publishes the sample site
  let tokenId = '';
  let session = null;
  try {
    session = await teacherSession(base, login.username, login.password, { fetchImpl, sleep });
    if (!add('The teacher can sign in', session, 'the user or password was not accepted (a new login can take a minute: try again)')) return summary(checks);
    const made = await call('/api/admin/tokens', { method: 'POST', session, body: { days: 1, student_name: 'verify', slug } });
    tokenId = made.data?.item?.id ?? ''; // remembered at once, so even a failed check below still removes this student
    if (!add('A student token can be created', [200, 201].includes(made.status) && made.data?.token, `HTTP ${made.status}`)) return summary(checks);
    const student = await call('/api/student/login', { method: 'POST', body: { token: made.data.token } });
    if (!add('The student can sign in with the token', student.status === 200 && student.data?.session, `HTTP ${student.status}`)) return summary(checks);
    const me = await call('/api/student/me', { session: student.data.session });
    const view = me.data?.token ?? {};
    for (const feature of features) {
      const offered = { backend: view.backend?.available, ai: view.ai?.available, gemini: view.ai?.providers?.includes('gemini'), storage: view.storage?.available, customdomains: view.custom_domain?.available }[feature];
      add(`The feature "${feature}" is offered to students`, offered, 'it is switched on but the portal does not offer it');
    }
    const live = publish({ repo, portal: base, token: made.data.token });
    if (!add('The student can publish a site', live, 'the publish step failed (run it by hand to see why)')) return summary(checks);
    const page = await get(live);
    const text = page.status === 200 && page.text ? await page.text() : '';
    add('The published site is live and shows its page', page.status === 200 && text.includes(SAMPLE_MARKER), `HTTP ${page.status}${page.status === 200 ? ' but the page is not the sample' : ''} (a new site can take a minute to appear)`);
    const missing = await get(unknownSite(live, slug));
    add('A site that does not exist is refused', [403, 404].includes(missing.status), `HTTP ${missing.status}`);
  } finally {
    // always remove the throwaway student, even after a failed check
    if (tokenId) {
      await call(`/api/admin/tokens/${tokenId}/revoke`, { method: 'POST', session, body: {} });
      try { await cleanup(slug); } catch (e) { add('The throwaway student was removed', false, e.message); }
    }
  }
  return summary(checks);
}

function summary(checks, publicOnly = false) {
  const failed = checks.filter((c) => !c.ok && !(publicOnly && c.name === 'Signed-in checks'));
  return { ok: failed.length === 0, publicOnly, checks, failed: failed.length,
    message: failed.length ? `${failed.length} check${failed.length === 1 ? '' : 's'} failed: ${failed.map((c) => c.name).join('; ')}` : (publicOnly ? 'The public checks passed. Run verify in your terminal, with your teacher login, for the full check.' : 'Every check passed: a student can publish a site and it is live.') };
}
