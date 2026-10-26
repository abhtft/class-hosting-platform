// The teacher npm package (build-teacher-package.mjs): it carries everything the installer and `portal-publish` need and nothing personal, its
// student kit and MCP package come out byte-identical to the repository's, its commands and `mcp` entry work from the unpacked folder, and the
// version, state folder and editor entry are consistent.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buildTeacherPackage, packageFiles, PACKAGE_NAME } from '../build-teacher-package.mjs';
import { buildPublishFiles } from '../starter/publish-files.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const work = mkdtempSync(join(tmpdir(), 'teacher-package-'));
const out = join(work, 'package');
const built = buildTeacherPackage(out, { pack: false, repo: 'someone/class-platform' });
const walk = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(join(dir, e.name)) : [relative(out, join(dir, e.name)).replace(/\\/g, '/')]));
const all = walk(out);

// ---------- contents ----------
for (const needed of ['template.yaml', 'admin.mjs', 'setup.mjs', 'bin.mjs', 'package.json', 'VERSION', 'CHANGELOG.md', 'README.md', '.packaged', 'portal-downloads.json',
  'installer/engine.mjs', 'mcp/server.mjs', 'mcp/setup.mjs', 'mcp/jsonc.mjs', 'lambda/portal.py', 'lambda/ai_proxy.py', 'web/teacher.html', 'student/portal-deploy.mjs',
  'starter/build-kit.mjs', 'starter/publish-files.mjs', 'starter/mcp/server.mjs', 'docs/TEACHER_SETUP.md']) {
  assert.ok(all.includes(needed), `the package must contain ${needed}`);
}
for (const forbidden of [/\.local$/, /^tests\//, /(^|\/)__pycache__\//, /^starter\/eval\//, /^samconfig\.toml$/, /^identifiers/, /node_modules/, /^\.class-platform\//, /^opensource_plan/, /^implement\.md/]) {
  assert.ok(!all.some((f) => forbidden.test(f)), `the package must not contain ${forbidden}`);
}
assert.ok(!all.some((f) => /\.(png|zip)$/.test(f) && !f.startsWith('starter/')) , 'no stray images or archives outside the starter');
const total = all.reduce((sum, f) => sum + statSync(join(out, f)).size, 0);
assert.ok(total < 5 * 1024 * 1024, `the package is small (${Math.round(total / 1024)} KB)`);
for (const f of packageFiles()) assert.ok(existsSync(join(out, f)), f);
console.log(`  ok  ${all.length} files, ${Math.round(total / 1024)} KB: everything needed, nothing local or personal`);

// ---------- no identifiers or keys inside ----------
const text = all.filter((f) => /\.(mjs|py|html|js|json|yaml|md)$/.test(f)).map((f) => readFileSync(join(out, f), 'utf8')).join('\n');
assert.ok(!/AKIA[A-Z0-9]{16}/.test(text) && !/sk-or-v1-[A-Za-z0-9]{20,}/.test(text) && !/AQ\.[A-Za-z0-9_-]{30,}/.test(text), 'no keys');
console.log('  ok  no key-shaped text in the package');

// ---------- package.json ----------
const pkg = JSON.parse(readFileSync(join(out, 'package.json'), 'utf8'));
assert.equal(pkg.name, PACKAGE_NAME);
assert.equal(pkg.version, readFileSync(join(repo, 'VERSION'), 'utf8').trim());
assert.equal(pkg.version, JSON.parse(readFileSync(join(repo, 'teacher-mcp', 'package.json'), 'utf8')).version, 'one version everywhere');
assert.equal(pkg.type, 'module');
assert.deepEqual(pkg.bin, { [PACKAGE_NAME]: 'bin.mjs' });
assert.equal(pkg.publishConfig.provenance, true);
assert.ok(!pkg.dependencies && !pkg.scripts, 'no dependencies, no install scripts');
console.log('  ok  package.json: one version, no dependencies, no install scripts, provenance on');

// ---------- the student kit and MCP package built from the package equal the repository\'s ----------
const portal = 'https://deploy.example.test';
const hashes = (dir) => Object.fromEntries(readdirSync(dir).sort().map((f) => [f, createHash('sha256').update(readFileSync(join(dir, f))).digest('hex')]));
const fromRepo = join(work, 'kit-repo');
const fromPackage = join(work, 'kit-package');
const a = buildPublishFiles(fromRepo, portal);
const b = (await import(`file:///${join(out, 'starter', 'publish-files.mjs').replace(/\\/g, '/')}`)).buildPublishFiles(fromPackage, portal);
assert.deepEqual(a.files.sort(), b.files.sort());
assert.deepEqual(hashes(fromPackage), hashes(fromRepo), 'the published kit and MCP package are byte-identical');
assert.equal(a.kitVersion, b.kitVersion);
console.log('  ok  the kit and the MCP package built from the package are identical to the repository\'s');

// ---------- commands from the unpacked folder ----------
const run = (args, env = {}) => spawnSync(process.execPath, [join(out, 'bin.mjs'), ...args], { encoding: 'utf8', cwd: work, env: { ...process.env, ...env } });
assert.equal(run(['version']).stdout.trim(), pkg.version);
assert.match(run(['help']).stdout, /guided install into your own AWS account/);
const home = join(work, 'home');
const editor = run(['editor', '--dry-run', '--editors', 'none']);
assert.match(editor.stdout, /"mcp"/, 'the editor entry runs the package with the mcp command');
assert.ok(editor.stdout.includes(`https://github.com/someone/class-platform/releases/download/v${pkg.version}/class-platform-teacher-${pkg.version}.tgz`), 'pinned to this exact release file on GitHub');
assert.deepEqual(JSON.parse(readFileSync(join(out, 'release.json'), 'utf8')), { repo: 'someone/class-platform', version: pkg.version, tag: `v${pkg.version}`, url: `https://github.com/someone/class-platform/releases/download/v${pkg.version}/class-platform-teacher-${pkg.version}.tgz` });
assert.throws(() => buildTeacherPackage(join(work, 'bad'), { repo: 'not a repo' }), /owner\/name/);
const unknown = run(['teacher']);
assert.notEqual(unknown.status, 0, 'a command with a missing argument fails with a message, not a crash');
const ejected = join(work, 'ejected');
assert.equal(run(['eject', ejected]).status, 0);
assert.ok(existsSync(join(ejected, 'template.yaml')) && !existsSync(join(ejected, '.packaged')), 'an ejected copy is a normal folder (state stays next to it)');
assert.notEqual(run(['eject', ejected]).status, 0, 'eject refuses a folder that is not empty');
console.log('  ok  version, help, editor entry (pinned), eject, and a clear failure for a missing argument');

// ---------- the files of a GitHub Release ----------
const release = join(work, 'release');
const packed = buildTeacherPackage(join(work, 'packed'), { pack: true, repo: 'someone/class-platform', releaseDir: release });
assert.deepEqual(readdirSync(release).sort(), [`${PACKAGE_NAME}-${pkg.version}.tgz`, `${PACKAGE_NAME}.tgz`, 'sha256sums.txt']);
const sums = readFileSync(join(release, 'sha256sums.txt'), 'utf8').trim().split(/\r?\n/).map((l) => l.split('  '));
for (const [hash, file] of sums) assert.equal(createHash('sha256').update(readFileSync(join(release, file))).digest('hex'), hash, `${file} matches its checksum`);
assert.equal(readFileSync(join(release, `${PACKAGE_NAME}.tgz`)).equals(readFileSync(join(release, `${PACKAGE_NAME}-${pkg.version}.tgz`))), true, 'the version-less alias is the same file');
assert.ok(packed.release.length === 3);
console.log('  ok  release files: pinned tarball, version-less alias, checksums that match; repository stamped into the package');

// ---------- state lives in the home folder when run from the package ----------
const engine = await import(`file:///${join(out, 'installer', 'engine.mjs').replace(/\\/g, '/')}`);
assert.equal(engine.stateDir({ repo: out }).replace(/\\/g, '/').includes('.class-platform-teacher'), true);
assert.equal(engine.stateDir({ repo }).replace(/\\/g, '/').endsWith('.class-platform'), true, 'from a clone it stays next to the clone');
process.env.CLASS_PLATFORM_TEACHER_HOME = home;
assert.equal(engine.stateDir({ repo: out }), home);
delete process.env.CLASS_PLATFORM_TEACHER_HOME;
console.log('  ok  install state goes to the home folder for the package and next to the clone for a git checkout');

// ---------- the MCP entry works from the package folder without any open folder ----------
const child = spawn(process.execPath, [join(out, 'bin.mjs'), 'mcp'], { cwd: work, stdio: ['pipe', 'pipe', 'pipe'] });
const lines = [];
let buffer = '';
child.stdout.on('data', (d) => { buffer += d; let i; while ((i = buffer.indexOf('\n')) >= 0) { lines.push(JSON.parse(buffer.slice(0, i))); buffer = buffer.slice(i + 1); } });
const send = (m) => child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', ...m })}\n`);
send({ id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {} } });
send({ id: 2, method: 'tools/list' });
send({ id: 3, method: 'tools/call', params: { name: 'teacher_login', arguments: { username: 'ms-rao' } } });
for (let i = 0; i < 40 && lines.length < 3; i += 1) await new Promise((r) => setTimeout(r, 100));
child.kill();
assert.equal(lines.find((l) => l.id === 1).result.serverInfo.name, 'class-platform-teacher');
assert.equal(lines.find((l) => l.id === 2).result.tools.length, 9);
assert.match(lines.find((l) => l.id === 3).result.content[0].text, /setup\.mjs teacher ms-rao|class-platform-teacher.*teacher ms-rao/);
console.log('  ok  the mcp command serves all nine tools from the package folder itself');

try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may still hold the folder for a moment: the temp folder is cleaned later */ }
console.log('test_teacher_package.mjs: all passed');
