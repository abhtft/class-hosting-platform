// Real-AWS test of background jobs, as a visitor's page would use them (public routes only, no token).  Private: it knows the live site.
//   node .temp/live-jobs.mjs <site url> [--limit]      --limit also runs the job that never ends (waits for the time limit: the teacher's minutes!)
const [, , siteArg, ...flags] = process.argv;
const site = siteArg.replace(/\/$/, '');
const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${String(detail).slice(0, 170)})` : ''}`); };
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));
const api = async (path, init) => { const r = await fetch(`${site}/api/_jobs${path}`, { ...init, signal: AbortSignal.timeout(40000) }); let body = null; try { body = await r.json(); } catch { /* not json */ } return { status: r.status, body }; };
const start = (name, payload) => api(`/${name}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload ?? {}) });
async function wait(id, maxSeconds) {
  const seen = new Set(); const began = Date.now();
  for (;;) {
    const r = await api(`/${id}`);
    if (r.body?.status) seen.add(r.body.status);
    if (['done', 'failed'].includes(r.body?.status)) return { ...r, seen: [...seen], seconds: Math.round((Date.now() - began) / 1000) };
    if ((Date.now() - began) / 1000 > maxSeconds) return { ...r, seen: [...seen], seconds: maxSeconds, timedOut: true };
    await sleep(3);
  }
}

console.log(`Background jobs on ${site}\n`);
let r = await api('');
check('GET /api/_jobs answers 200 with a list', r.status === 200 && Array.isArray(r.body?.jobs), `${r.status} ${JSON.stringify(r.body).slice(0, 80)}`);
check('the web function is unaffected: /api/health', (await fetch(`${site}/api/health`)).status === 200);

r = await start('sum', { n: 100 });
check('POST a quick job answers 202 queued with an id', r.status === 202 && r.body?.status === 'queued' && /^[0-9a-f]{32}$/.test(r.body?.id), `${r.status} ${JSON.stringify(r.body)}`);
const quick = r.body?.id;
let w = await wait(quick, 90);
check('the quick job finishes with the right result', w.body?.status === 'done' && w.body?.result?.sum === 5050, `${w.seconds}s, saw ${w.seen}, ${JSON.stringify(w.body?.result)}`);
check('it has start and finish times', Boolean(w.body?.started && w.body?.finished), JSON.stringify({ s: w.body?.started, f: w.body?.finished }));

// a job longer than the web function may run (25 s)
r = await start('slow', { seconds: 40 });
check('POST a 40-second job answers 202', r.status === 202, `${r.status}`);
const slow = r.body?.id;
await sleep(6);
const busy = await start('sum', { n: 1 });
check('while it runs a second job is refused (jobs_busy, one at a time)', busy.status === 429 && busy.body?.error === 'jobs_busy', `${busy.status} ${busy.body?.error}`);
const midway = await api(`/${slow}`);
check('while it runs the page can poll it (queued or running, no result yet)', ['queued', 'running'].includes(midway.body?.status) && !('result' in (midway.body ?? {})), midway.body?.status);
w = await wait(slow, 150);
check('the 40-second job finishes: longer than a web request may run', w.body?.status === 'done' && w.body?.result?.slept === 40 && w.seconds >= 25, `${w.seconds}s more after the first poll, saw ${w.seen}`);

// failures never show the student's own error text
r = await start('boom');
w = await wait(r.body?.id, 90);
const text = JSON.stringify(w.body);
check('a crashing job fails with a plain message', w.body?.status === 'failed' && /npm run logs/.test(w.body?.error ?? ''), text);
check('...and the student\'s secret in its error text is NOT shown', !/SECRETVALUE|J-BOOM|RuntimeError|Traceback/.test(text));

r = await start('big');
w = await wait(r.body?.id, 90);
check('a result over 100 KB fails with a plain message and stores no result', w.body?.status === 'failed' && /100 KB/.test(w.body?.error ?? '') && !('result' in (w.body ?? {})), JSON.stringify(w.body).slice(0, 140));

r = await start('env');
w = await wait(r.body?.id, 90);
check('the job function has the student\'s variables (TEST_SECRET)', w.body?.status === 'done' && w.body?.result?.test_secret_length === 14, JSON.stringify(w.body?.result));

// input checks and the id as a capability
check('a bad job name is refused', (await start('Not A Name')).status === 400);
check('an unknown job id is a 404', (await api(`/${'0'.repeat(32)}`)).status === 404);
check('a malformed id is a 400', (await api('/not-an-id')).status === 400);
const big = await fetch(`${site}/api/_jobs/sum`, { method: 'POST', body: JSON.stringify({ x: 'y'.repeat(70000) }), signal: AbortSignal.timeout(40000) });
check('an input over 64 KB is refused (413)', big.status === 413, big.status);
const listed = await api('');
check('the list shows the latest jobs newest first, without results', listed.body?.jobs?.length >= 5 && listed.body.jobs.every((j) => !('result' in j)) && listed.body.jobs[0].created >= listed.body.jobs.at(-1).created, `${listed.body?.jobs?.length} jobs`);

if (flags.includes('--limit')) {
  r = await start('forever', { seconds: 900 });
  const began = Date.now();
  w = await wait(r.body?.id, 960);
  check('a job that never ends is stopped at the teacher\'s time limit', w.body?.status === 'failed' && /longer than its limit of \d+ minute/.test(w.body?.error ?? ''), `${Math.round((Date.now() - began) / 1000)}s: ${w.body?.error}`);
}

const failed = results.filter((x) => !x.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed${failed.length ? `; failed: ${failed.map((f) => f.name).join('; ')}` : ''}`);
process.exit(failed.length ? 1 : 0);
