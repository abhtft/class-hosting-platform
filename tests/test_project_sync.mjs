// The kit's project sync (starter/template/tools/project.mjs and `portal-deploy.mjs pull|push|project`): the folder scan and the two plans are tested on their own,
// then the whole tool runs against the REAL portal and project code (tests/projects_fixture.py) as two "computers" of one student and as a team of two:
// first push, a second computer pulling, edits both ways, a real conflict and its two ways out, deletes, a team project, a stranger, secrets that never leave, and
// the commands.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const tool = await import(pathToFileURL(join(repo, 'starter', 'template', 'tools', 'project.mjs')).href);
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const work = mkdtempSync(join(tmpdir(), 'psync-'));
const say = (m) => console.log(`  ok  ${m}`);
const put = (root, path, text) => { const full = join(root, path); mkdirSync(dirname(full), { recursive: true }); writeFileSync(full, text); };
const read = (root, path) => readFileSync(join(root, path), 'utf8');
const sha = (text) => createHash('sha256').update(text).digest('hex');
const tree = (root, dir = '') => readdirSync(join(root, dir), { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? tree(root, join(dir, e.name)) : [join(dir, e.name).replaceAll('\\', '/')])).sort();

// ---------- the scan ----------
{
  const root = join(work, 'scan');
  put(root, 'platform.json', JSON.stringify({ managed: ['AGENTS.md', 'tools/', 'portal-deploy.mjs'] }));
  for (const [p, t] of Object.entries({
    'frontend/src/App.tsx': 'app', 'backend/app.py': 'py', 'README.md': 'r', 'package.json': '{}', '.env.example': 'K=', 'docs/notes.md': 'n',
    'AGENTS.md': 'kit', 'tools/check.mjs': 'kit', 'portal-deploy.mjs': 'kit',
    '.env': 'SECRET', '.env.local': 'SECRET', 'frontend/.env.production': 'S', 'keys/id.pem': 'S', 'node_modules/x/i.js': 'dep', 'frontend/node_modules/y/i.js': 'dep',
    'frontend/dist/a.js': 'b', 'build/o.js': 'b', '.git/config': 'g', '.venv/bin/p': 'v', '__pycache__/a.pyc': 'c', 'knowledge/books/a.txt': 'book', '.class-project.json': '{}',
    'a.txt.theirs': 't', 'a.txt.mine': 'm', 'Thumbs.db': 'x', '.DS_Store': 'x', 'big/huge.bin': 'x'.repeat(5 * 1048576 + 1), 'skipme/a.txt': 'x', 'data.csv': 'x', 'draft/ok.md': 'x',
    '.syncignore': 'skipme/\n*.csv\n# comment\ndraft/ok.md\n',
  })) put(root, p, t);
  const { files, warnings } = tool.scan(root);
  assert.deepEqual([...files.keys()].sort(), ['.env.example', '.syncignore', 'README.md', 'backend/app.py', 'docs/notes.md', 'frontend/src/App.tsx', 'package.json'], 'project files only');
  assert.equal(files.get('README.md').sha, sha('r'));
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /big\/huge\.bin is 5\.0 MB; a project file can be up to 5\.0 MB, so it is skipped/);
  say('scan: project files only (no kit files, dependencies, build output, secrets, books or leftovers), .syncignore honoured, an oversized file is skipped with a reason');
}

// ---------- the plans ----------
{
  const f = (hash) => ({ sha256: hash, by: 'bob' });
  let plan = tool.planPull({ a: 'A1', b: 'B1', c: 'C2', d: 'D1' }, { a: 'A1', b: 'B1', c: 'C1', d: 'D1' }, { a: f('A2'), b: f('B1'), c: f('C3'), e: f('E1') });
  assert.deepEqual([plan.get, plan.conflict, plan.removeLocal], [['a', 'e'], ['c'], ['d']].map((x) => x), 'a changed remotely is fetched; c changed on both sides conflicts; e is new; d was deleted remotely and is unchanged here');
  plan = tool.planPull({ a: 'A9' }, { a: 'A1' }, { a: f('A2') }, { theirs: true });
  assert.deepEqual([plan.get, plan.conflict], [['a'], []], '--theirs takes the platform copy');
  plan = tool.planPull({ a: 'A9' }, {}, { a: f('A2') }, { first: true });
  assert.deepEqual([plan.get, plan.conflict], [['a'], []]);
  assert.match(plan.note.a, /kept as a\.mine/, 'the first pull never loses a local file');
  plan = tool.planPull({ a: 'A9' }, { a: 'A1' }, {});
  assert.deepEqual([plan.removeLocal, plan.forget], [[], ['a']], 'deleted remotely but changed here: it stays');
  plan = tool.planPull({}, { a: 'A1' }, { a: f('A2') });
  assert.deepEqual(plan.get, ['a'], 'deleted here but changed remotely: restored');
  const l = (hash) => ({ sha: hash, size: 3 });
  assert.deepEqual(tool.planPush({ a: l('A2'), b: l('B1'), n: l('N1') }, { a: 'A1', b: 'B1', gone: 'G1' }).map((c) => [c.op, c.path, c.base ?? null]), [['put', 'a', 'A1'], ['put', 'n', null], ['delete', 'gone', 'G1']]);
  assert.deepEqual(tool.planPush({ a: l('A1') }, { a: 'A1' }), []);
  say('plans: what a pull fetches, conflicts, removes and keeps; what a push sends');
}

// ---------- against the real portal and project code ----------
const fixture = spawn(python, [join(here, 'projects_fixture.py')], { stdio: ['ignore', 'pipe', 'inherit'] });
try {
  const info = await new Promise((resolve, reject) => {
    let buffer = '';
    fixture.stdout.on('data', (d) => { buffer += d; if (buffer.includes('\n')) resolve(JSON.parse(buffer.split('\n')[0])); });
    fixture.on('exit', () => reject(new Error('fixture exited')));
  });
  const post = async (path, body, session) => (await fetch(`${info.url}${path}`, { method: 'POST', headers: { 'content-type': 'application/json', ...(session ? { authorization: `Bearer ${session}` } : {}) }, body: JSON.stringify(body) })).json();
  const login = async (token) => (await post('/api/student/login', { token })).session;
  const sessions = { alice: await login(info.alice), bob: await login(info.bob), carol: await login(info.carol) };
  const teacher = (await post('/api/teacher/login', { username: 'teacher', password: 'pw' })).session;
  const slugOf = async (who) => (await (await fetch(`${info.url}/api/student/me`, { headers: { authorization: `Bearer ${sessions[who]}` } })).json()).token.slug;
  const out = [];
  const quiet = { log: (m) => out.push(m), warn: (m) => out.push(`WARN ${m}`) };
  const opts = (who, root, extra = {}) => ({ portalUrl: info.url, session: sessions[who], root, ...quiet, ...extra });
  const said = () => out.splice(0).join('\n');

  // computer A: the first push
  const A = join(work, 'A');
  put(A, 'platform.json', JSON.stringify({ managed: ['tools/'] }));
  put(A, 'tools/check.mjs', 'kit file');
  put(A, 'frontend/src/App.tsx', 'export default () => "v1"');
  put(A, 'backend/app.py', 'print("v1")');
  put(A, 'README.md', '# my project');
  put(A, '.env.local', 'PORTAL_TOKEN=secret-token');
  put(A, 'node_modules/dep/index.js', 'dep');
  let result = await tool.push(opts('alice', A, { dryRun: true }));
  assert.equal(result.dryRun, true);
  assert.match(said(), /3 new, 0 changed, 0 deleted[\s\S]*would send {2}README\.md/);
  assert.equal(existsSync(join(A, tool.STATE_FILE)), false, 'a dry run changes nothing');
  result = await tool.push(opts('alice', A));
  assert.equal(result.pushed, 3);
  assert.match(said(), /Pushed 3 changes\. The project is now at revision 1/);
  assert.deepEqual(Object.keys(JSON.parse(read(A, tool.STATE_FILE)).base).sort(), ['README.md', 'backend/app.py', 'frontend/src/App.tsx']);
  assert.equal((await tool.push(opts('alice', A))).pushed, 0, 'nothing changed: nothing to send');
  assert.match(said(), /Nothing to push/);
  say('computer A: a dry run changes nothing, the first push sends the 3 project files (never the kit, secrets or dependencies), a second push has nothing to do');

  // computer B: the same student, an empty kit folder
  const B = join(work, 'B');
  put(B, 'platform.json', JSON.stringify({ managed: ['tools/'] }));
  put(B, 'tools/check.mjs', 'kit file');
  put(B, 'README.md', '# starter readme from the kit');
  result = await tool.pull(opts('alice', B));
  assert.deepEqual(tree(B), ['.class-project.json', 'README.md', 'backend/app.py', 'frontend/src/App.tsx', 'platform.json', 'tools/check.mjs', 'README.md.mine'].sort());
  assert.equal(read(B, 'README.md'), '# my project');
  assert.equal(read(B, 'README.md.mine'), '# starter readme from the kit', 'the first pull keeps the local file it replaced');
  assert.match(said(), /get {3}README\.md {3}\(your version is kept as README\.md\.mine\)/);
  assert.equal(JSON.parse(read(B, tool.STATE_FILE)).rev, 1);
  assert.equal((await tool.pull(opts('alice', B))).got, 0);
  assert.match(said(), /Nothing to pull: you are up to date/);
  say('computer B: pull brings the project (the kit files stay as they are), keeps a differing local file as .mine, and a second pull has nothing to do');

  // edits travel both ways
  put(B, 'backend/app.py', 'print("v2 from B")');
  put(B, 'docs/new.md', 'new on B');
  rmSync(join(B, 'README.md.mine'));
  await tool.push(opts('alice', B));
  assert.match(said(), /1 new, 1 changed, 0 deleted/);
  await tool.pull(opts('alice', A));
  assert.equal(read(A, 'backend/app.py'), 'print("v2 from B")');
  assert.equal(read(A, 'docs/new.md'), 'new on B');
  say('an edit and a new file made on B arrive on A');

  // a real conflict: both change app.py
  put(A, 'backend/app.py', 'print("A wins?")');
  put(B, 'backend/app.py', 'print("B wins?")');
  await tool.push(opts('alice', A));
  said();
  result = await tool.push(opts('alice', B));
  assert.equal(result.pushed, 0);
  assert.equal(result.conflicts[0].path, 'backend/app.py');
  assert.match(said(), /CONFLICT backend\/app\.py {3}\(changed by [a-z0-9-]+ since you last synced\)[\s\S]*Run {2}npm run pull/);
  assert.equal(read(B, 'backend/app.py'), 'print("B wins?")', 'nothing of B was overwritten');
  result = await tool.pull(opts('alice', B));
  assert.deepEqual(result.conflicts, ['backend/app.py']);
  assert.equal(read(B, 'backend/app.py'), 'print("B wins?")', 'a conflicting file is never overwritten by a pull');
  assert.equal(read(B, 'backend/app.py.theirs'), 'print("A wins?")', 'their copy is saved next to it');
  assert.match(said(), /CONFLICT backend\/app\.py[\s\S]*keep what you want in your file, delete the \.theirs copy, then run {2}npm run push -- --mine/);
  // way out 1: keep mine
  rmSync(join(B, 'backend/app.py.theirs'));
  result = await tool.push(opts('alice', B, { mine: true }));
  assert.equal(result.pushed, 1, said());
  await tool.pull(opts('alice', A));
  assert.equal(read(A, 'backend/app.py'), 'print("B wins?")');
  // way out 2: take theirs
  put(A, 'backend/app.py', 'print("A again")');
  put(B, 'backend/app.py', 'print("B again")');
  await tool.push(opts('alice', A));
  said();
  result = await tool.pull(opts('alice', B, { theirs: true }));
  assert.deepEqual(result.conflicts, []);
  assert.equal(read(B, 'backend/app.py'), 'print("A again")');
  assert.equal(read(B, 'backend/app.py.mine'), 'print("B again")', 'the replaced file is kept as .mine');
  assert.equal((await tool.push(opts('alice', B))).pushed, 0, 'after taking theirs there is nothing to send');
  say('a real conflict: the push is refused with who changed it, nothing is overwritten, pull saves their copy as .theirs, and both ways out work (--mine, --theirs keeping .mine)');

  // deletes
  rmSync(join(B, 'docs/new.md'));
  await tool.push(opts('alice', B));
  assert.match(said(), /0 new, 0 changed, 1 deleted/);
  await tool.pull(opts('alice', A));
  assert.equal(existsSync(join(A, 'docs/new.md')), false, 'a file deleted on B is removed on A');
  put(A, 'README.md', '# changed on A');
  await tool.push(opts('alice', A));
  rmSync(join(B, 'README.md'));
  said();
  await tool.pull(opts('alice', B));
  assert.equal(read(B, 'README.md'), '# changed on A', 'deleted here but changed on the platform: restored, not lost');
  say('deletes travel too; a file deleted here that changed elsewhere comes back');

  // a team project
  const aliceSlug = await slugOf('alice');
  const bobSlug = await slugOf('bob');
  const carolSlug = await slugOf('carol');
  const made = await post('/api/admin/projects', { id: 'robot-club', title: 'Robot club', members: [aliceSlug, bobSlug] }, teacher);
  assert.equal(made.project.members.length, 2);
  const T1 = join(work, 'T1');
  const T2 = join(work, 'T2');
  const T3 = join(work, 'T3');
  put(T1, 'src/robot.py', 'drive()');
  await tool.push(opts('alice', T1, { project: 'robot-club' }));
  assert.equal(JSON.parse(read(T1, tool.STATE_FILE)).project, 'robot-club');
  result = await tool.pull(opts('bob', T2, { project: 'robot-club' }));
  assert.equal(read(T2, 'src/robot.py'), 'drive()');
  put(T2, 'src/sensor.py', 'read()');
  put(T1, 'src/robot.py', 'drive(fast)');
  await tool.push(opts('bob', T2));
  await tool.push(opts('alice', T1)); // different files: no conflict
  await tool.pull(opts('bob', T2));
  await tool.pull(opts('alice', T1));
  assert.deepEqual(tree(T1).filter((p) => p.startsWith('src/')), ['src/robot.py', 'src/sensor.py']);
  assert.equal(read(T2, 'src/robot.py'), 'drive(fast)');
  await assert.rejects(tool.pull(opts('carol', T3, { project: 'robot-club' })), /not a member/);
  const listing = await tool.status(opts('alice', T1));
  assert.deepEqual(listing.projects.map((p) => p.id), ['personal', 'robot-club']);
  assert.match(said(), /\* robot-club {2}\(team: Robot club, with .*\)/);
  await assert.rejects(tool.push(opts('alice', T1, { project: 'personal' })), /synced with "robot-club"/);
  say('a team project: two people push different files and both land, a stranger is turned away, status lists the projects and marks the one in use, a folder cannot be pushed to another project by mistake');

  // a damaged download is refused and nothing is written
  const mine = await (await fetch(`${info.url}/api/student/projects/robot-club`, { headers: { authorization: `Bearer ${sessions.alice}` } })).json();
  const entry = mine.files['src/sensor.py'];
  await fetch(`${info.url}/__corrupt/projects/t/robot-club/blobs/${entry.sha256}`);
  const T4 = join(work, 'T4');
  await assert.rejects(tool.pull(opts('bob', T4, { project: 'robot-club' })), /did not match its checksum/);
  assert.equal(existsSync(join(T4, 'src/sensor.py')), false, 'a damaged file is not written');
  say('a downloaded file whose bytes do not match its SHA-256 is refused and not written');

  // the commands
  const cli = join(work, 'cli');
  mkdirSync(join(cli, 'tools'), { recursive: true });
  cpSync(join(repo, 'starter', 'template', 'tools', 'project.mjs'), join(cli, 'tools', 'project.mjs'));
  put(cli, 'platform.json', JSON.stringify({ managed: ['tools/'] }));
  put(cli, 'app.txt', 'hello');
  const run = (name, ...args) => spawnSync(process.execPath, [join(repo, 'student', 'portal-deploy.mjs'), name, ...args], {
    cwd: cli, encoding: 'utf8', env: { ...process.env, PORTAL_DEPLOY_NO_MAIN: '', PORTAL_URL: info.url, PORTAL_TOKEN: info.carol, CLASS_PLATFORM_HOME: join(work, 'no-home'), PLATFORM_KIT_SYNCED: '1' },
  });
  let r = run('push', '--dry-run');
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /1 new, 0 changed, 0 deleted/);
  r = run('push');
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /Pushed 1 change/);
  r = run('project');
  assert.match(r.stdout, /\* personal {2}\(only yours\) {3}revision 1, 1 file/);
  assert.match(r.stdout, /Not yet sent: 0 new, 0 changed, 0 deleted/);
  r = run('pull', '--project', 'robot-club');
  assert.equal(r.status, 1);
  assert.match(r.stderr, /not a member/);
  say('npm run push (--dry-run), npm run project and npm run pull -- --project: the commands work end to end and refuse a stranger in plain words');
  console.log('test_project_sync.mjs: all passed');
} finally {
  fixture.kill();
  rmSync(work, { recursive: true, force: true });
}

