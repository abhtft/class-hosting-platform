// End to end for student/portal-deploy.mjs: the real CLI -> the whole dev server (real portal, gateway and Lambda code on in-memory AWS).
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const PORT = 8781;
const URL = `http://127.0.0.1:${PORT}`;

const server = spawn(python, [join(here, 'dev_server.py'), String(PORT)], { env: { ...process.env, AWS_DEFAULT_REGION: 'us-east-1' } });
let out = '';
server.stdout.on('data', (d) => (out += d));
server.stderr.on('data', (d) => (out += d));
const work = mkdtempSync(join(tmpdir(), 'portal-cli-'));

try {
  for (let i = 0; i < 300 && !out.includes('Backend demo:'); i++) await new Promise((r) => setTimeout(r, 100));
  const tokens = Object.fromEntries([...out.matchAll(/^\s+(\w+)\s+(mds_\S+)\s+\(expires/gm)].map((m) => [m[1], m[2]]));
  assert.ok(tokens.Alice && tokens.Bob && tokens.Carol && tokens.Erin, `demo tokens not found in:\n${out}`);

  const project = (name, { frontend = true, backend = true } = {}) => {
    const dir = join(work, name);
    if (frontend) {
      mkdirSync(join(dir, 'frontend', 'dist', 'assets'), { recursive: true });
      writeFileSync(join(dir, 'frontend', 'dist', 'index.html'), '<script src="/assets/app.js"></script>');
      writeFileSync(join(dir, 'frontend', 'dist', 'assets', 'app.js'), 'console.log(1)');
    }
    if (backend) {
      mkdirSync(join(dir, 'backend'), { recursive: true });
      copyFileSync(join(root, 'student', 'backend-template', 'python', 'app.py'), join(dir, 'backend', 'app.py'));
    }
    return dir;
  };
  const run = (cwd, token, ...args) =>
    spawnSync('node', [join(root, 'student', 'portal-deploy.mjs'), '--skip-build', ...args], {
      cwd, encoding: 'utf8', env: { ...process.env, PORTAL_URL: URL, PORTAL_TOKEN: token ?? '', CLASS_PLATFORM_HOME: join(work, 'no-home'), PORTAL_READY_TIMEOUT_MS: '30000' },
    });
  const check = (name, fn) => { try { fn(); console.log(`  ok  ${name}`); } catch (e) { console.log(`  FAIL ${name}`); throw e; } };

  // 1. a student with the backend switched on deploys both parts
  const full = project('alice');
  const first = run(full, tokens.Alice);
  check('Alice: backend and frontend both published', () => {
    assert.equal(first.status, 0, first.stdout + first.stderr);
    assert.match(first.stdout, /Backend ready \(python3\.12\)/);
    assert.match(first.stdout, /Frontend live at https:\/\/alice\.example\.edu\/ {2}\(2 files/);
    assert.ok(first.stdout.indexOf('Backend ready') < first.stdout.indexOf('Frontend live'), 'backend goes first');
  });
  const hello = await (await fetch(`${URL}/b/alice/hello?name=CLI`)).json();
  check('the deployed backend answers through the gateway', () => assert.deepEqual(hello, { message: 'Hello, CLI!' }));

  // 2. redeploy is fine; a token can be read from .env.local instead of the environment
  writeFileSync(join(full, '.env.local'), `PORTAL_TOKEN="${tokens.Alice}"\n`);
  const again = run(full, undefined, '--only', 'frontend');
  check('redeploy with the token from .env.local, frontend only', () => {
    assert.equal(again.status, 0, again.stdout + again.stderr);
    assert.doesNotMatch(again.stdout, /Backend/);
  });

  // an app at the repo root (a normal Vite project) next to backend/ is a supported layout: only its build output is published
  const rootLayout = project('rootapp', { frontend: false });
  mkdirSync(join(rootLayout, 'dist'), { recursive: true });
  writeFileSync(join(rootLayout, 'dist', 'index.html'), '<h1>root app</h1>');
  writeFileSync(join(rootLayout, 'dist', '.env'), 'SECRET=1');
  const rootRun = run(rootLayout, tokens.Alice, '--frontend', '.');
  check('app at the repo root: --frontend . publishes dist/ (and backend/ too), never a .env', () => {
    assert.equal(rootRun.status, 0, rootRun.stdout + rootRun.stderr);
    assert.match(rootRun.stdout, /Frontend: packed 1 files from dist\//);
    assert.match(rootRun.stdout, /Backend ready/);
  });
  const noBuild = join(work, 'nobuild');
  mkdirSync(noBuild, { recursive: true });
  writeFileSync(join(noBuild, 'index.html'), '<h1>source</h1>');
  writeFileSync(join(noBuild, '.env'), 'SECRET=1');
  const noBuildRun = run(noBuild, tokens.Alice, '--frontend', '.');
  check('repo root without a build folder is refused, not published as-is', () => {
    assert.notEqual(noBuildRun.status, 0);
    assert.match(noBuildRun.stderr, /No build folder/);
    assert.doesNotMatch(noBuildRun.stdout, /publishing/);
  });

  // the files the student page offers are served by the portal, byte for byte as in the repo
  const downloads = JSON.parse(readFileSync(join(root, 'portal-downloads.json'), 'utf8'));
  for (const [name, source] of Object.entries(downloads)) {
    const served = await (await fetch(`${URL}/${name}`)).text();
    const norm = (text) => text.replace(/\r\n/g, '\n');
    check(`download ${name} is served and matches ${source}`, () => assert.equal(norm(served), norm(readFileSync(join(root, source), 'utf8'))));
  }

  // 3. refusals each give a clear message and a failing exit code
  const frontOnly = project('bob', { backend: false });
  const bob = run(frontOnly, tokens.Bob);
  check('Bob (expires in 2 days): frontend deploys and a warning is shown', () => {
    assert.equal(bob.status, 0, bob.stdout + bob.stderr);
    assert.match(bob.stderr, /expires soon/);
  });
  check('a folder that is missing is simply skipped', () => assert.doesNotMatch(bob.stdout, /Backend/));
  const bobBackend = run(project('bob2'), tokens.Bob);
  check('backend not enabled by the teacher: it is skipped with a clear warning and the website is still published', () => {
    assert.equal(bobBackend.status, 0, bobBackend.stdout + bobBackend.stderr);
    assert.match(bobBackend.stderr, /The backend is switched off for you, so backend\/ was NOT published; only your website is/);
    assert.match(bobBackend.stdout, /Frontend live at/);
    assert.doesNotMatch(bobBackend.stdout, /Backend: packed|Backend ready/, 'nothing is uploaded for a backend that is off');
  });
  const bobOnlyBackend = run(project('bob3'), tokens.Bob, '--only', 'backend');
  check('asking for the backend only while it is off: a clear refusal, exit 1', () => {
    assert.notEqual(bobOnlyBackend.status, 0);
    assert.match(bobOnlyBackend.stderr, /Your backend cannot be published: it is switched off for you.*Ask your teacher/s);
  });
  const carol = run(project('carol'), tokens.Carol);
  check('expired token cannot deploy', () => {
    assert.notEqual(carol.status, 0);
    assert.match(carol.stderr, /cannot deploy right now \(status: expired\)/);
  });
  const erin = run(project('erin'), tokens.Erin);
  check('revoked token is refused at sign-in', () => {
    assert.notEqual(erin.status, 0);
    assert.match(erin.stderr, /does not accept this token.*npm run login/s);
  });
  const none = run(project('nobody'), 'mds_notarealtoken');
  check('unknown token is refused', () => assert.notEqual(none.status, 0));
  const missing = run(project('notoken'), '');
  check('no token: explains how to set PORTAL_TOKEN', () => {
    assert.notEqual(missing.status, 0);
    assert.match(missing.stderr, /No token found.*npm run login/s);
  });

  // 4. local mistakes are caught before anything is uploaded
  const withEnv = project('envfile');
  writeFileSync(join(withEnv, 'backend', '.env'), 'SECRET=1');
  const envRun = run(withEnv, tokens.Alice, '--only', 'backend');
  check('a .env file in backend/ is refused', () => {
    assert.notEqual(envRun.status, 0);
    assert.match(envRun.stderr, /Remove .*\.env/);
    assert.doesNotMatch(envRun.stdout, /packed|publishing/, 'refused locally, before anything is uploaded');
  });
  const both = project('both');
  writeFileSync(join(both, 'backend', 'index.mjs'), 'export const handler = async () => ({});');
  const bothRun = run(both, tokens.Alice, '--only', 'backend');
  check('two entry files are refused', () => {
    assert.notEqual(bothRun.status, 0);
    assert.match(bothRun.stderr, /only one entry file/);
  });
  const empty = run(mkdtempSync(join(work, 'empty-')), tokens.Alice);
  check('an empty folder explains the expected layout', () => {
    assert.notEqual(empty.status, 0);
    assert.match(empty.stderr, /Nothing to deploy/);
  });

  // 5. `login`: asks for the token, checks it, saves it to .env.local; the token is never printed
  const login = (cwd, input, ...args) => spawnSync('node', [join(root, 'student', 'portal-deploy.mjs'), 'login', ...args], {
    cwd, input, encoding: 'utf8', env: { ...process.env, PORTAL_URL: URL, PORTAL_TOKEN: '' },
  });
  const fresh = project('login-ok', { backend: false });
  const loggedIn = login(fresh, `${tokens.Alice}\n`);
  check('login: a valid token is saved to .env.local and never printed', () => {
    assert.equal(loggedIn.status, 0, loggedIn.stdout + loggedIn.stderr);
    assert.match(readFileSync(join(fresh, '.env.local'), 'utf8'), new RegExp(`^PORTAL_TOKEN=${tokens.Alice}\n$`));
    assert.match(loggedIn.stdout, /Signed in as .*alice/i);
    assert.doesNotMatch(loggedIn.stdout + loggedIn.stderr, new RegExp(tokens.Alice));
  });
  const afterLogin = run(fresh, '', '--only', 'frontend');
  check('after login, npm run deploy needs nothing else', () => assert.equal(afterLogin.status, 0, afterLogin.stdout + afterLogin.stderr));
  const badLogin = project('login-bad', { backend: false });
  const refused = login(badLogin, 'mds_notarealtoken123\n');
  check('login: a wrong token is refused and nothing is saved', () => {
    assert.notEqual(refused.status, 0);
    assert.match(refused.stderr, /does not accept this token/);
    assert.equal(existsSync(join(badLogin, '.env.local')), false);
  });
  const typo = login(badLogin, 'abc\n');
  check('login: something that is not a token is explained', () => {
    assert.notEqual(typo.status, 0);
    assert.match(typo.stderr, /does not look like a portal token/);
  });
  const keepOthers = project('login-merge', { backend: false });
  writeFileSync(join(keepOthers, '.env.local'), `OPENAI_API_KEY=mine\n# PORTAL_TOKEN=old\nOTHER=1\n`);
  login(keepOthers, `${tokens.Alice}\n`);
  check('login: replaces only the token line and keeps the other settings', () => {
    const text = readFileSync(join(keepOthers, '.env.local'), 'utf8');
    assert.equal(text, `OPENAI_API_KEY=mine\nPORTAL_TOKEN=${tokens.Alice}\nOTHER=1\n`);
  });
  const unsafe = project('login-unsafe', { backend: false });
  spawnSync('git', ['init', '-q'], { cwd: unsafe });
  const unsafeRun = login(unsafe, `${tokens.Alice}\n`);
  check('login: refuses to write the token where git would publish it', () => {
    assert.notEqual(unsafeRun.status, 0);
    assert.match(unsafeRun.stderr, /not ignored by git/);
    assert.equal(existsSync(join(unsafe, '.env.local')), false);
  });
  writeFileSync(join(unsafe, '.gitignore'), '.env.local\n');
  check('login: works once .env.local is git-ignored', () => assert.equal(login(unsafe, `${tokens.Alice}\n`).status, 0));
  // 6. what the student sees while pasting: mds_ and one * per character; keys behave like a normal prompt
  process.env.PORTAL_DEPLOY_NO_MAIN = '1';
  const { editLine, maskSecret } = await import(`${pathToFileURL(join(root, 'student', 'portal-deploy.mjs')).href}?keys`);
  check('typing shows mds_ then stars, never the rest of the token', () => {
    assert.equal(maskSecret(''), '');
    assert.equal(maskSecret('mds'), 'mds');
    assert.equal(maskSecret('mds_abcdef'), 'mds_******');
    assert.ok(!maskSecret(tokens.Alice).includes(tokens.Alice.slice(4, 10)));
    assert.equal(maskSecret(tokens.Alice).length, tokens.Alice.length);
  });
  check('a pasted token arrives in one chunk, Enter finishes, Backspace deletes, Ctrl+C cancels, arrows are ignored', () => {
    assert.deepEqual(editLine('', tokens.Alice), { buffer: tokens.Alice, done: false, abort: false });
    assert.deepEqual(editLine('mds_abc', '\r'), { buffer: 'mds_abc', done: true, abort: false });
    assert.equal(editLine('mds_abc', '\u007f').buffer, 'mds_ab');
    assert.equal(editLine('', '\u007f').buffer, '');
    assert.equal(editLine('mds_ab', 'c\n').done, true);
    assert.equal(editLine('mds_ab', '\u0003').abort, true);
    assert.equal(editLine('mds_ab', '\u001b[A').buffer, 'mds_ab');
    assert.equal(editLine('', 'mds_ab\tc').buffer, 'mds_abc', 'other control keys are ignored');
  });
  // 7. the student's agent learns which features are on from the live portal (status), and a deploy warns about a helper whose feature is off
  const { featureList, featureLines, featureWarnings, helpersUsed } = await import(`${pathToFileURL(join(root, 'student', 'portal-deploy.mjs')).href}?features`);
  const sample = {
    backend: { available: true, enabled: true },
    ai: { available: true, enabled: false },
    storage: { available: false },
    custom_domain: { available: true, enabled: true, domain: 'kid.example.test', state: 'live' },
  };
  check('featureList: on needs available and enabled; the reason says why a feature is off', () => {
    const byId = Object.fromEntries(featureList(sample).map((f) => [f.id, f]));
    assert.equal(byId.backend.on, true);
    assert.equal(byId.ai.on, false);
    assert.match(byId.ai.detail, /switched off/);
    assert.equal(byId.storage.on, false);
    assert.match(byId.storage.detail, /not installed/);
    assert.match(byId.custom_domain.detail, /kid\.example\.test/);
    assert.equal(featureList({}).every((f) => !f.on), true, 'a token view with no feature data counts as everything off');
  });
  check('featureLines: lists every feature and tells the agent the teacher switches them on', () => {
    const text = featureLines(sample).join('\n');
    assert.match(text, /✔ Backend/);
    assert.match(text, /✖ Class AI/);
    assert.match(text, /teacher/);
  });
  const helperDir = join(work, 'helpers');
  mkdirSync(helperDir, { recursive: true });
  writeFileSync(join(helperDir, 'app.py'), 'from class_ai import ask\n');
  writeFileSync(join(helperDir, 'class_storage.py'), '# the kit copy itself must not count\n');
  check('helpersUsed: finds class_ai in the student code and ignores the kit helper files', () => {
    assert.deepEqual([...helpersUsed(helperDir)], ['ai']);
    assert.deepEqual([...helpersUsed(join(work, 'nope'))], []);
  });
  check('featureWarnings: only a used helper whose feature is off', () => {
    assert.equal(featureWarnings(sample, new Set(['ai'])).length, 1);
    assert.match(featureWarnings(sample, new Set(['ai']))[0], /class_ai/);
    assert.equal(featureWarnings(sample, new Set(['storage'])).length, 1);
    assert.equal(featureWarnings(sample, new Set()).length, 0);
    assert.equal(featureWarnings({ ...sample, ai: { available: true, enabled: true } }, new Set(['ai'])).length, 0);
  });
  const statusRun = spawnSync('node', [join(root, 'student', 'portal-deploy.mjs'), 'status'], {
    cwd: work, encoding: 'utf8', env: { ...process.env, PORTAL_DEPLOY_NO_MAIN: '', PORTAL_URL: URL, PORTAL_TOKEN: tokens.Alice, CLASS_PLATFORM_HOME: join(work, 'no-home') },
  });
  check('status prints the features block read from the portal (Alice has the backend on)', () => {
    assert.equal(statusRun.status, 0, statusRun.stdout + statusRun.stderr);
    assert.match(statusRun.stdout, /Features for you/);
    assert.match(statusRun.stdout, /✔ Backend/);
  });
  // 8. the token belongs to the project: its source is named, an old computer-wide file is never read, `login --global` is gone
  check('status names where the token came from', () => assert.match(statusRun.stdout, /Token from the PORTAL_TOKEN environment variable\./));
  const oldHome = join(work, 'old-home');
  mkdirSync(oldHome, { recursive: true });
  writeFileSync(join(oldHome, 'token'), `${tokens.Alice}\n`);
  const noTokenDir = join(work, 'no-token');
  mkdirSync(noTokenDir, { recursive: true });
  const bare = (args, extraEnv = {}, input = '') => spawnSync('node', [join(root, 'student', 'portal-deploy.mjs'), ...args], {
    cwd: noTokenDir, encoding: 'utf8', input, env: { ...process.env, PORTAL_DEPLOY_NO_MAIN: '', PORTAL_URL: URL, PORTAL_TOKEN: '', CLASS_PLATFORM_HOME: oldHome, ...extraEnv },
  });
  const oldFile = bare(['status']);
  check('an old computer-wide token file signs nobody in; it is only mentioned', () => {
    assert.notEqual(oldFile.status, 0);
    assert.match(oldFile.stderr, /No token found in this project/);
    assert.match(oldFile.stderr, /old computer-wide token file exists.*no longer used/);
    assert.doesNotMatch(oldFile.stdout + oldFile.stderr, /Signed in as/);
  });
  const globalLogin = bare(['login', '--global'], {}, `${tokens.Alice}\n`);
  check('login --global was removed, with the reason, and writes nothing', () => {
    assert.notEqual(globalLogin.status, 0);
    assert.match(globalLogin.stderr, /whole computer was removed.*npm run login/s);
    assert.equal(existsSync(join(noTokenDir, '.env.local')), false);
    assert.equal(readFileSync(join(oldHome, 'token'), 'utf8').trim(), tokens.Alice, 'the old file is left as it was');
  });
  writeFileSync(join(noTokenDir, '.env.local'), `PORTAL_TOKEN=${tokens.Alice}\n`);
  const local = bare(['status']);
  check('with a project .env.local the status says so', () => {
    assert.equal(local.status, 0, local.stdout + local.stderr);
    assert.match(local.stdout, /Token from \.env\.local\./);
  });
  const aiProject = project('alice-ai');
  writeFileSync(join(aiProject, 'backend', 'app.py'), `${readFileSync(join(aiProject, 'backend', 'app.py'), 'utf8')}\nfrom class_ai import ask\n`);
  const aiRun = spawnSync('node', [join(root, 'student', 'portal-deploy.mjs'), '--skip-build'], {
    cwd: aiProject, encoding: 'utf8', env: { ...process.env, PORTAL_DEPLOY_NO_MAIN: '', PORTAL_URL: URL, PORTAL_TOKEN: tokens.Alice, CLASS_PLATFORM_HOME: join(work, 'no-home'), PORTAL_READY_TIMEOUT_MS: '30000' },
  });
  check('deploy warns (and still deploys) when the backend uses class_ai while it is off for the student', () => {
    assert.equal(aiRun.status, 0, aiRun.stdout + aiRun.stderr);
    assert.match(aiRun.stderr, /Your backend uses class_ai/);
  });
  console.log('portal-deploy CLI: all passed');
} finally {
  server.kill();
  rmSync(work, { recursive: true, force: true });
}
