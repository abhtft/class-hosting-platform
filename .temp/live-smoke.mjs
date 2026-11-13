// Real-AWS smoke test as the live test student. Private (.temp is git-ignored): it knows the live site.
//   node .temp/live-smoke.mjs <project folder> <site url> [--quick] [--no-slow]
// It signs in with the token saved in the project's .env.local (the owner ran `npm run login`; this script never reads or prints the token).
// Needs the test routes in the project's backend/app.py: /t-normal /t-boom /t-long /t-slow.
// --quick skips the three page deploys; --no-slow skips the 26 second timeout request.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const [, , project, siteArg, ...flags] = process.argv;
if (!project || !siteArg) { console.error('Usage: node .temp/live-smoke.mjs <project folder> <site url> [--quick] [--no-slow]'); process.exit(2); }
const site = siteArg.replace(/\/$/, '');
const quick = flags.includes('--quick');
const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${String(detail).slice(0, 150)})` : ''}`); };
const run = (args) => spawnSync(args[0], args.slice(1), { cwd: project, encoding: 'utf8', shell: process.platform === 'win32', timeout: 600000 });
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));
const get = async (path, init) => { const r = await fetch(`${site}${path}`, { ...init, signal: AbortSignal.timeout(60000) }); return { status: r.status, text: await r.text(), headers: r.headers }; };
const logs = (...extra) => run(['node', 'portal-deploy.mjs', 'logs', ...extra]).stdout ?? '';
const deploy = () => { const r = run(['npm', 'run', 'deploy']); return { ok: r.status === 0 && /Frontend live/.test(r.stdout), out: `${r.stdout}${r.stderr}` }; };

console.log(`Smoke test of ${site} from ${project}\n`);
check('the project has a saved login', existsSync(join(project, '.env.local')));

// 1. the kit
const status = run(['node', 'tools/platform.mjs', 'status']).stdout ?? '';
check('the kit is signed and the key is pinned', /signed by your teacher's key/.test(status), status.split('\n')[1]);
check('the kit is up to date', /Up to date/.test(status), status.split('\n').find((l) => /kit/i.test(l)));
const checked = run(['node', 'tools/check.mjs']);
check('npm run check passes', checked.status === 0, (checked.stdout ?? '').split('\n').find((l) => /✖/.test(l)) ?? '');

// 2. publish, cache header
let d = deploy();
check('npm run deploy publishes backend and frontend', d.ok, d.ok ? '' : d.out.split('\n').slice(-4).join(' | '));
let page = await get('/');
check('the site answers 200', page.status === 200, page.status);
const cache = page.headers.get('cache-control') ?? '';
check('Cache-Control has s-maxage=86400 (one day, not a year)', /s-maxage=86400/.test(cache) && !/31536000/.test(cache), cache);

// 3. a changed page shows at once, a deleted page is gone
if (!quick) {
  const dir = join(project, 'frontend', 'public');
  mkdirSync(dir, { recursive: true });
  const file = join(dir, 'smoke.html');
  const marker = (v) => `<!doctype html><meta charset="utf-8"><p>SMOKE-${v}</p>\n`;
  const tag = Date.now().toString(36);
  writeFileSync(file, marker(`${tag}-A`));
  deploy();
  check('a new page is live', /SMOKE-.*-A/.test((await get('/smoke.html')).text));
  writeFileSync(file, marker(`${tag}-B`));
  deploy();
  check('a changed page shows the new text at once', (await get('/smoke.html')).text.includes(`${tag}-B`));
  rmSync(file);
  deploy();
  const gone = await get('/smoke.html');
  check('a deleted page is gone', gone.status === 403 || gone.status === 404, gone.status);
}

// 4. the backend and its logs
check('/api/health answers 200', (await get('/api/health')).status === 200);
check('/api/t-normal answers 200', (await get('/api/t-normal')).status === 200);
check('/api/t-boom answers 502 with a friendly message', (await get('/api/t-boom')).status === 502);
check('/api/t-long answers 502', (await get('/api/t-long')).status === 502);
let slowOk = true;
if (!flags.includes('--no-slow')) {
  const started = Date.now();
  const slow = await get('/api/t-slow');
  slowOk = slow.status === 504 && /function_timeout/.test(slow.text);
  check('a request past the time limit answers 504 function_timeout', slowOk, `${slow.status} after ${Math.round((Date.now() - started) / 1000)}s`);
}
await sleep(25);
const all = logs('--minutes', '10');
const errors = logs('--errors', '--minutes', '10');
check('logs: the newest lines are there', /T-NORMAL/.test(all));
check('logs: no START/END/INIT_START noise', !/(^|\n)\S+ +[ap]m +(START|END) RequestId/.test(all) && !/INIT_START/.test(all), /INIT_START/.test(all) ? 'INIT_START still shown (platform not on 2.1.1)' : '');
check('logs: a printed variable value is masked', !/SECRETVALUE123/.test(all) && /secret-is=(\*\*\*|\(not set\))/.test(all), (all.match(/secret-is=\S+/) ?? [''])[0]);
check('logs --errors: the traceback and its real error are shown', /T-BOOM-REAL-ERROR/.test(errors));
check('logs --errors: normal lines are left out', !/T-NORMAL: everything is fine/.test(errors));
if (!flags.includes('--no-slow')) check('logs --errors: the timeout is shown (platform 2.1.1 or newer)', /Status: timeout|Task timed out/.test(errors));
check('logs: a long message keeps its end', /T-LONG-ERROR-END/.test(all));
check('logs --limit 3 returns at most 3 events', (logs('--minutes', '10', '--limit', '3').match(/^\d{1,2}:\d{2}:\d{2} [ap]m/gm) ?? []).length <= 3);

// 5. class storage (the owner clears it from the teacher page afterwards)
const note = await get('/api/notes', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: 'smoke-note' }) });
check('class storage: a note is saved and read back', note.status === 201 && /smoke-note/.test((await get('/api/notes')).text), note.status);

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed${failed.length ? `; failed: ${failed.map((f) => f.name).join('; ')}` : ''}`);
console.log('By hand (only the owner can click): take offline then Bring online, Clear class storage, read the Usage tab.');
process.exit(failed.length ? 1 : 0);
