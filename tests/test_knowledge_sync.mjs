// The kit's folder sync (starter/template/tools/knowledge.mjs and `portal-deploy.mjs knowledge`): the folder scan and the plan are tested on their own, then the
// whole tool runs against the REAL portal and knowledge code (tests/knowledge_portal_fixture.py): first sync, nothing to do the second time, a changed file,
// a removed file (kept, then pruned), a book that cannot be read, the shelf limit, another student, and a student without the feature.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const tool = await import(pathToFileURL(join(repo, 'starter', 'template', 'tools', 'knowledge.mjs')).href);
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const work = mkdtempSync(join(tmpdir(), 'ksync-'));
const say = (m) => console.log(`  ok  ${m}`);
const put = (root, path, text) => { const full = join(root, path); mkdirSync(dirname(full), { recursive: true }); writeFileSync(full, text); };

// ---------- the scan ----------
{
  const root = join(work, 'scan');
  put(root, 'knowledge/physics/ohm.txt', 'Ohm');
  put(root, 'knowledge/physics/Laws of motion (1).md', '# Laws');
  put(root, 'knowledge/physics/notes.docx', 'not a book type');
  put(root, 'knowledge/physics/empty.txt', '');
  put(root, 'knowledge/physics/bad&name.txt', 'x');
  put(root, 'knowledge/physics/sub/deeper.txt', 'x');
  put(root, 'knowledge/physics/.hidden.txt', 'x');
  put(root, 'knowledge/Bad Shelf/a.txt', 'x');
  put(root, 'knowledge/stray.txt', 'x');
  put(root, 'knowledge/big/huge.txt', 'x'.repeat(2 * 1048576 + 1));
  const { shelves, warnings } = tool.scan(root, { uploadMb: 2 });
  assert.deepEqual(Object.keys(shelves).sort(), ['big', 'physics']);
  assert.deepEqual(shelves.physics.map((f) => f.name), ['Laws of motion (1).md', 'ohm.txt']);
  assert.match(shelves.physics[1].sha256, /^[0-9a-f]{64}$/);
  assert.deepEqual(shelves.big, [], 'a file over the limit is not offered');
  const text = warnings.join('\n');
  for (const expected of [/notes\.docx: only PDF/, /empty\.txt is empty/, /bad&name\.txt: the file name/, /sub\/ is a folder inside a shelf/, /Bad Shelf is not a valid shelf name/, /stray\.txt is not inside a shelf folder/, /huge\.txt is 2\.0 MB; your limit is 2 MB/]) assert.match(text, expected);
  assert.ok(!text.includes('.hidden'), 'hidden files are ignored silently');
  say('scan: shelves from folders, bad names, types, empty, oversized, subfolders and stray files are skipped with a reason; hidden files ignored');
}

// ---------- the plan ----------
{
  const f = (name, hash) => ({ name, sha256: hash.repeat(64).slice(0, 64), bytes: 10, path: name });
  const d = (id, filename, hash, state, message = '') => ({ id, filename, sha256: hash ? hash.repeat(64).slice(0, 64) : null, state, message });
  let plan = tool.reconcile([f('new.txt', 'a'), f('same.txt', 'b'), f('changed.txt', 'c'), f('reading.txt', 'd'), f('broken.txt', 'e')], [
    d('1', 'same.txt', 'b', 'ready'), d('2', 'changed.txt', '1', 'ready'), d('3', 'reading.txt', 'd', 'processing'), d('4', 'broken.txt', 'e', 'failed', 'No text'), d('5', 'gone.txt', '2', 'ready'),
    d('6', 'same.txt', '9', 'ready'), d('7', 'new.txt', 'a', 'uploading'),
  ]);
  assert.deepEqual(plan.upload.map((x) => x.name), ['new.txt', 'changed.txt']);
  assert.deepEqual(plan.keep.map((x) => x.name), ['same.txt']);
  assert.deepEqual(plan.reading.map((x) => x.name), ['reading.txt']);
  assert.deepEqual(plan.failed.map((x) => [x.file.name, x.message]), [['broken.txt', 'No text']], 'a book that failed is not tried again by itself');
  assert.deepEqual(plan.orphans.map((x) => x.id), ['5']);
  assert.deepEqual(plan.remove.map((x) => x.id).sort(), ['6', '7'], 'an old copy of an unchanged book and an unfinished upload go; nothing else');
  assert.deepEqual(plan.later.map((x) => x.id), ['2'], 'the old copy of a changed book goes only after the new one is ready');
  plan = tool.reconcile([f('broken.txt', 'e')], [d('4', 'broken.txt', 'e', 'failed', 'No text')], { retry: true });
  assert.deepEqual([plan.upload.length, plan.remove.map((x) => x.id)], [1, ['4']], 'with retry the failed one is replaced');
  assert.deepEqual(tool.reconcile([], [d('1', 'a.txt', 'a', 'ready')]).orphans.map((x) => x.id), ['1']);
  say('plan: new, unchanged, changed, being read, failed (not retried by itself), orphaned, old copies and leftovers, retry');
}

// ---------- against the real portal and knowledge code ----------
const fixture = spawn(python, [join(here, 'knowledge_portal_fixture.py')], { stdio: ['ignore', 'pipe', 'inherit'] });
const info = await new Promise((resolve, reject) => {
  let buffer = '';
  fixture.stdout.on('data', (d) => { buffer += d; if (buffer.includes('\n')) resolve(JSON.parse(buffer.split('\n')[0])); });
  fixture.on('exit', () => reject(new Error('fixture exited')));
});
const login = async (token) => (await (await fetch(`${info.url}/api/student/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token }) })).json()).session;
const session = await login(info.token);
const quiet = { log: () => {}, warn: () => {} };
const opts = (root, extra = {}) => ({ portalUrl: info.url, session, root, ...quiet, ...extra });
const shelf = async (root, name) => (await tool.status(opts(root, { allShelves: true }))).shelves[name] || [];

try {
  const root = join(work, 'project');
  put(root, 'knowledge/physics/ohm.txt', "Ohm's law says the current through a conductor is proportional to the voltage across it. The constant is the resistance.");
  put(root, 'knowledge/physics/newton.md', "# Newton\n\nAn object stays at rest or keeps moving at constant velocity unless a net force acts on it. This is inertia.");
  put(root, 'knowledge/chemistry/acids.txt', 'Acids taste sour and turn blue litmus red. Bases feel soapy and turn red litmus blue.');

  assert.equal((await tool.sync(opts(root, { dryRun: true }))).uploaded.length, 0);
  assert.deepEqual(await shelf(root, 'physics'), [], 'a dry run changes nothing and creates no shelf');
  const first = await tool.sync(opts(root));
  assert.deepEqual(first.uploaded.sort(), ['chemistry/acids.txt', 'physics/newton.md', 'physics/ohm.txt']);
  assert.deepEqual(first.problems, []);
  const physics = await shelf(root, 'physics');
  assert.deepEqual(physics.map((d) => [d.filename, d.state]).sort(), [['newton.md', 'ready'], ['ohm.txt', 'ready']]);
  assert.ok(physics.every((d) => /^[0-9a-f]{64}$/.test(d.sha256) && d.passages >= 1));
  say('first sync: shelves are created, files uploaded to the form (file last) and read; a dry run changed nothing');

  const again = await tool.sync(opts(root));
  assert.deepEqual([again.uploaded.length, again.kept, again.problems.length], [0, 3, 0], 'nothing changed: nothing uploaded, nothing read again');
  say('second sync: all 3 files are unchanged, so nothing is uploaded or read again');

  put(root, 'knowledge/physics/ohm.txt', "Ohm's law, revised: current is proportional to voltage. Resistance is measured in ohms (symbol omega).");
  const changed = await tool.sync(opts(root, { wait: true }));
  assert.deepEqual(changed.uploaded, ['physics/ohm.txt']);
  assert.ok(changed.removed.some((x) => x.startsWith('physics/ohm.txt')), 'the old copy goes once the new one is ready');
  const after = (await shelf(root, 'physics')).filter((d) => d.filename === 'ohm.txt');
  assert.equal(after.length, 1, 'one book of that name, not two');
  say('a changed file replaces the book of the same name once the new copy is ready');

  rmSync(join(root, 'knowledge', 'physics', 'newton.md'));
  const kept = await tool.sync(opts(root));
  assert.deepEqual(kept.orphans, [{ shelf: 'physics', name: 'newton.md' }]);
  assert.equal((await shelf(root, 'physics')).length, 2, 'a book that left the folder stays on the shelf unless you ask');
  const pruned = await tool.sync(opts(root, { prune: true }));
  assert.ok(pruned.removed.includes('physics/newton.md'));
  assert.deepEqual((await shelf(root, 'physics')).map((d) => d.filename), ['ohm.txt']);
  say('a removed file is only reported; --prune removes it from the shelf');

  put(root, 'knowledge/chemistry/scan.pdf', 'this is not really a pdf');
  const broken = await tool.sync(opts(root));
  assert.deepEqual(broken.uploaded, ['chemistry/scan.pdf']);
  const bookAfter = (await shelf(root, 'chemistry')).find((d) => d.filename === 'scan.pdf');
  assert.equal(bookAfter.state, 'failed');
  const notRetried = await tool.sync(opts(root));
  assert.deepEqual([notRetried.uploaded.length, notRetried.failed.length], [0, 1], 'a failed book is reported, not tried again by itself');
  assert.match(notRetried.failed[0].message, /./);
  assert.equal((await tool.sync(opts(root, { retry: true }))).uploaded.length, 1, 'with --retry it is tried again');
  say('a book that cannot be read is reported with the reason, and only retried when asked');

  const lines = tool.statusLines(await tool.status({ portalUrl: info.url, session, root }));
  assert.ok(lines.some((l) => l.startsWith('physics: 1 book')) && lines.some((l) => l.includes('✖ scan.pdf')) && lines.some((l) => l.includes('✔ ohm.txt')) && lines.some((l) => l.startsWith('Using ')), lines.join('\n'));
  say('status lists every book with its state, and the use of the limits');

  // the shelf limit: 20 books in all by default. Add 20 small files; the rest wait and the tool says so
  const many = join(work, 'many');
  for (let i = 0; i < 20; i++) put(many, `knowledge/bulk/b${String(i).padStart(2, '0')}.txt`, `Book number ${i} is about the number ${i} and nothing else at all.`);
  const messages = [];
  const limited = await tool.sync({ ...opts(many), warn: (m) => messages.push(m) });
  assert.ok(limited.uploaded.length < 20 && limited.waiting.length > 0 && limited.uploaded.length + limited.waiting.length === 20, JSON.stringify({ u: limited.uploaded.length, w: limited.waiting.length }));
  assert.ok(messages.some((m) => /up to 20 documents/.test(m)), messages.join('\n'));
  assert.ok(limited.problems.length > 0);
  say('the shelf limit stops the uploads with the platform\'s message and lists what is waiting');

  // another student has their own shelves; a student without the feature is told so
  const otherSession = await login(info.token_other);
  const theirs = await tool.status({ portalUrl: info.url, session: otherSession, root, allShelves: true });
  assert.deepEqual(theirs.shelves, {}, 'another student sees none of these shelves');
  const offSession = await login(info.token_off);
  const off = await tool.sync({ portalUrl: info.url, session: offSession, root, ...quiet });
  assert.match(off.problems[0], /switched off/);
  assert.equal((await tool.sync({ ...opts(join(work, 'none')) })).skipped, true, 'no knowledge/ folder: nothing to do');
  say('another student sees none of these shelves; a student without the feature gets the teacher message; no folder means no work');

  // the command: npm run knowledge (node portal-deploy.mjs knowledge)
  const project = join(work, 'cli');
  mkdirSync(join(project, 'tools'), { recursive: true });
  cpSync(join(repo, 'starter', 'template', 'tools', 'knowledge.mjs'), join(project, 'tools', 'knowledge.mjs'));
  put(project, 'knowledge/notes/week1.txt', 'Week one covered photosynthesis, which turns light into chemical energy in leaves.');
  const run = (...args) => spawnSync(process.execPath, [join(repo, 'student', 'portal-deploy.mjs'), 'knowledge', ...args], {
    cwd: project, encoding: 'utf8', env: { ...process.env, PORTAL_DEPLOY_NO_MAIN: '', PORTAL_URL: info.url, PORTAL_TOKEN: info.token_other, CLASS_PLATFORM_HOME: join(work, 'no-home'), PLATFORM_KIT_SYNCED: '1' }, // a student whose shelves are still empty
  });
  const dry = run('--dry-run');
  assert.equal(dry.status, 0, dry.stdout + dry.stderr);
  assert.match(dry.stdout, /1 shelf in knowledge\/: 1 to upload/);
  assert.match(dry.stdout, /\+ notes\/week1\.txt/);
  assert.match(dry.stdout, /Dry run: nothing was changed/);
  const synced = run('--wait');
  assert.equal(synced.status, 0, synced.stdout + synced.stderr);
  assert.match(synced.stdout, /notes\/week1\.txt uploaded/);
  const statusRun = run('status');
  assert.match(statusRun.stdout, /notes: 1 book/);
  assert.match(statusRun.stdout, /✔ week1\.txt/);
  const offRun = spawnSync(process.execPath, [join(repo, 'student', 'portal-deploy.mjs'), 'knowledge'], {
    cwd: project, encoding: 'utf8', env: { ...process.env, PORTAL_DEPLOY_NO_MAIN: '', PORTAL_URL: info.url, PORTAL_TOKEN: info.token_off, CLASS_PLATFORM_HOME: join(work, 'no-home'), PLATFORM_KIT_SYNCED: '1' },
  });
  assert.equal(offRun.status, 1);
  assert.match(offRun.stderr, /Class knowledge is switched off for you. Ask your teacher/);
  say('npm run knowledge: --dry-run, a real sync with --wait, status, and the plain message for a student without the feature');
  console.log('test_knowledge_sync.mjs: all passed');
} finally {
  fixture.kill();
  rmSync(work, { recursive: true, force: true });
}
