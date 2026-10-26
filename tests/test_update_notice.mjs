// The one-line "a newer kit is available" notice (tools/update-notice.mjs), as `npm run check` shows it: quiet by design.
// A tiny fake portal in this process answers kit-version.json; every check runs in a child process like a student's terminal would.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const { buildKit } = await import(pathToFileURL(join(here, '..', 'starter', 'build-kit.mjs')).href);
const { compareVersions } = await import(pathToFileURL(join(here, '..', 'starter', 'template', 'tools', 'update-notice.mjs')).href);
const say = (m) => console.log(`  ok  ${m}`);
const work = mkdtempSync(join(tmpdir(), 'kit-notice-'));

let answer = { kitVersion: '9.9.9', minKit: '1.0.0', scripts: {} };
let asked = 0;
const portal = createServer((req, res) => {
  asked++;
  if (req.url !== '/kit-version.json') { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify(answer));
});
await new Promise((resolve) => portal.listen(0, '127.0.0.1', resolve));
const url = `http://127.0.0.1:${portal.address().port}`;

const kit = join(work, 'kit');
buildKit(kit);
const setMine = (version) => writeFileSync(join(kit, 'platform.json'), JSON.stringify({ ...JSON.parse(readFileSync(join(kit, 'platform.json'), 'utf8')), kitVersion: version }, null, 2));
const noCache = () => rmSync(join(kit, '.platform-notice.json'), { force: true });
const check = (args = [], env = {}) => new Promise((resolve) => {
  const child = spawn(process.execPath, [join(kit, 'tools', 'check.mjs'), ...args], { cwd: kit, env: { ...process.env, PLATFORM_PORTAL: url, ...env } });
  let stdout = '', stderr = '';
  child.stdout.on('data', (d) => (stdout += d));
  child.stderr.on('data', (d) => (stderr += d));
  child.on('close', (status) => resolve({ status, stdout, stderr }));
});
const FORCE = { PLATFORM_NOTICE: 'force' };

try {
  assert.equal(compareVersions('1.10.0', '1.9.0'), 1);
  assert.equal(compareVersions('1.4.0', '1.4'), 0);
  assert.equal(compareVersions('0.9.9', '1.0.0'), -1);
  assert.equal(compareVersions('', 'x'), 0);
  say('versions compare as numbers (1.10.0 is newer than 1.9.0)');

  setMine('1.4.0');
  let r = await check([], FORCE);
  assert.equal(r.status, 0, r.stdout);
  assert.match(r.stderr, /A newer class kit is available \(you have 1\.4\.0, the portal has 9\.9\.9\)\. Update:  npm run platform -- update --apply/);
  assert.doesNotMatch(r.stdout, /newer class kit/, 'the notice goes to stderr, never into the report');
  assert.ok(existsSync(join(kit, '.platform-notice.json')));
  assert.equal(asked, 1);
  say('a newer kit gives one line on stderr and the report is unchanged');

  r = await check([], FORCE);
  assert.match(r.stderr, /newer class kit is available/);
  assert.equal(asked, 1, 'the second check uses the answer kept from today: no new request');
  say('the portal is asked at most once a day');

  const cache = JSON.parse(readFileSync(join(kit, '.platform-notice.json'), 'utf8'));
  writeFileSync(join(kit, '.platform-notice.json'), JSON.stringify({ ...cache, checkedAt: Date.now() - 25 * 3600 * 1000 }));
  await check([], FORCE);
  assert.equal(asked, 2, 'a day later it asks again');
  say('a day later it asks again');

  r = await check(['--json'], FORCE);
  assert.doesNotThrow(() => JSON.parse(r.stdout), 'machine-readable output stays clean');
  assert.equal(r.stderr, '', '--json prints no notice');
  say('--json output has no notice');

  r = await check([], {});
  assert.doesNotMatch(r.stderr, /newer class kit/, 'piped output (no terminal) stays clean: logs, CI and scripts never see it');
  r = await check([], { PLATFORM_NOTICE: 'off' });
  assert.doesNotMatch(r.stderr, /newer class kit/);
  r = await check([], { ...FORCE, CI: '' });
  say('silent when output is piped or PLATFORM_NOTICE=off');

  noCache();
  answer = { kitVersion: '1.4.0', minKit: '1.0.0', scripts: {} };
  r = await check([], FORCE);
  assert.equal(r.stderr, '', 'up to date: nothing to say');
  say('up to date: no line');

  noCache();
  answer = { kitVersion: '9.9.9', minKit: '1.5.0', scripts: {} };
  r = await check([], FORCE);
  assert.match(r.stderr, /Your class kit \(1\.4\.0\) is too old for this platform\. Update it now/);
  say('a kit older than the portal\'s minimum gets a stronger warning');

  noCache();
  answer = { kitVersion: '1.4.0', minKit: '1.0.0', scripts: { login: 'node portal-deploy.mjs login', 'mcp-config': 'node tools/mcp.mjs --print-config' } };
  const pkgPath = join(kit, 'package.json');
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
  delete pkg.scripts.login;
  writeFileSync(pkgPath, JSON.stringify(pkg, null, 2));
  r = await check([], FORCE);
  assert.match(r.stderr, /package\.json is missing kit scripts \(login\)/);
  assert.doesNotMatch(r.stderr, /mcp-config/, 'only what is really missing is named');
  say('same version but a missing script is mentioned');

  // an unreachable or broken portal never slows or breaks the check
  noCache();
  const started = Date.now();
  r = await check([], { ...FORCE, PLATFORM_PORTAL: 'http://127.0.0.1:1' });
  assert.equal(r.status, 0);
  assert.equal(r.stderr, '');
  assert.ok(Date.now() - started < 8000, 'an unreachable portal costs at most a couple of seconds');
  noCache();
  portal.removeAllListeners('request');
  portal.on('request', (req, res) => { res.writeHead(500); res.end('boom'); });
  r = await check([], FORCE);
  assert.equal(r.stderr, '');
  noCache();
  portal.removeAllListeners('request');
  portal.on('request', (req, res) => { res.writeHead(200); res.end('not json'); });
  r = await check([], FORCE);
  assert.equal(r.status, 0);
  assert.equal(r.stderr, '');
  say('offline, an error answer or garbage from the portal: no output, no delay, the check still passes');

  console.log('test_update_notice.mjs: all passed');
} finally {
  portal.close();
  rmSync(work, { recursive: true, force: true });
}
process.exit(0);
