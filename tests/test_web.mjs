// Tests for web/check.js (folder checks) and web/zip.js (zip builder). The zip is verified by the real Lambda validation.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { webcrypto as crypto } from 'node:crypto';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyzeBackend } from '../web/backend-check.js';
import { analyze } from '../web/check.js';
import { usd } from '../web/common.js';
import { buildZip } from '../web/zip.js';

const here = dirname(fileURLToPath(import.meta.url));
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const item = (path, text = 'x') => ({ path, file: new Blob([text]) });
const items = (spec) => Object.entries(spec).map(([p, t]) => item(p, t));
const levels = (r) => r.checks.map((c) => c.level);
const errors = (r) => r.checks.filter((c) => c.level === 'error').map((c) => c.text).join(' | ');
const SOURCE = '<script type="module" src="/src/main.jsx"></script>';
const BUILT = '<script type="module" src="/assets/index-a1b2.js"></script>';

// ---- analyze ----
let r = await analyze(items({ 'site/index.html': BUILT, 'site/assets/app.js': '1', 'site/.DS_Store': '' }));
assert.ok(r.ok, errors(r));
assert.equal(r.root, 'site/');
assert.deepEqual(r.files.map((f) => f.path).sort(), ['assets/app.js', 'index.html'], 'top folder stripped, junk ignored');

r = await analyze(items({ 'index.html': BUILT, 'a.js': '1' }));
assert.ok(r.ok && r.root === '', 'loose files at the root');

r = await analyze(items({ 'proj/package.json': '{}', 'proj/index.html': SOURCE, 'proj/src/main.jsx': 'x', 'proj/dist/index.html': BUILT, 'proj/dist/assets/a.js': '1' }));
assert.ok(r.ok, errors(r));
assert.equal(r.root, 'proj/dist/', 'whole project dropped: dist/ is auto-detected');

r = await analyze(items({ 'proj/index.html': SOURCE, 'proj/src/main.jsx': 'x' }));
assert.ok(!r.ok && /source code/.test(errors(r)), 'source folder without a build is rejected');

r = await analyze(items({ 'proj/package.json': '{}', 'proj/src/a.js': 'x' }));
assert.ok(!r.ok && /No index\.html/.test(errors(r)));
assert.ok(!(await analyze([])).ok);

r = await analyze([...items({ 'site/index.html': BUILT }), { path: 'site/node_modules/', skipped: true }]);
assert.ok(!r.ok && /node_modules/.test(errors(r)), 'node_modules inside the upload is rejected');
r = await analyze([...items({ 'proj/dist/index.html': BUILT, 'proj/package.json': '{}' }), { path: 'proj/node_modules/', skipped: true }]);
assert.ok(r.ok, 'node_modules outside dist/ is fine');

r = await analyze(items({ 'index.html': BUILT, '.env.local': 'SECRET=1' }));
assert.ok(!r.ok && /\.env/.test(errors(r)));

r = await analyze(items({ 'index.html': BUILT, 'my photo.png': 'x', 'ok.png': 'x' }));
assert.ok(!r.ok && /my photo\.png/.test(errors(r)), 'spaces in names are blocked');
r = await analyze(items({ 'index.html': BUILT, 'Logo.png': 'x' }));
assert.ok(r.ok && levels(r).includes('warn'), 'capital letters only warn');
r = await analyze(items({ 'index.html': '<img src="http://localhost:5173/a.png">' }));
assert.ok(r.ok && levels(r).includes('warn'), 'localhost reference warns');

const many = Object.fromEntries(Array.from({ length: 2001 }, (_, i) => [`f${i}.txt`, 'x']));
r = await analyze(items({ 'index.html': BUILT, ...many }));
assert.ok(!r.ok && /Too many files/.test(errors(r)));
r = await analyze([item('index.html', BUILT), { path: 'big.bin', file: { size: 201 * 1024 * 1024, text: async () => '' } }]);
assert.ok(!r.ok && /too large/.test(errors(r)));

r = await analyze([item('index.html', BUILT), { path: 'photos.bin', file: { size: 60 * 1024 * 1024, text: async () => '' } }]);
assert.ok(r.ok && r.checks.some((c) => c.level === 'warn' && /60 MB/.test(c.text)), 'big sites get an early warning, not a block');

// ---- backend folder checks ----
const file = (path, size = 10) => ({ path, file: { size } });
let b = analyzeBackend([file('index.mjs'), file('lib/util.mjs')]);
assert.ok(b.ok && b.root === '' && b.count === 2, 'entry at the top');
b = analyzeBackend([file('api/app.py'), file('api/helpers.py'), file('api/__MACOSX/x'), file('api/.DS_Store')]);
assert.ok(b.ok && b.root === 'api/' && b.files.map((f) => f.path).sort().join() === 'app.py,helpers.py', 'one folder down, junk ignored');
b = analyzeBackend([file('proj/index.js'), file('proj/node_modules/x/y.js')]);
assert.ok(b.ok && b.files.length === 2, 'bundled node_modules are allowed');
b = analyzeBackend([file('readme.txt')]);
assert.ok(!b.ok && /No entry file/.test(b.checks[0].text));
b = analyzeBackend([file('index.mjs'), file('app.py')]);
assert.ok(!b.ok && /only one entry file/.test(b.checks.find((c) => c.level === 'error').text));
b = analyzeBackend([file('app.py'), file('.env'), file('sub/.env.local')]);
assert.ok(!b.ok && /\.env/.test(b.checks.find((c) => c.level === 'error').text), 'secrets are refused');
b = analyzeBackend(Array.from({ length: 501 }, (_, i) => file(i ? `f${i}.py` : 'app.py')));
assert.ok(!b.ok && /Too many files/.test(b.checks.find((c) => c.level === 'error').text));
b = analyzeBackend([file('app.py', 51 * 1024 * 1024)]);
assert.ok(!b.ok && /limit is 50 MB/.test(b.checks.find((c) => c.level === 'error').text));
b = analyzeBackend([file('app.py', 6 * 1024 * 1024)]);
assert.ok(b.ok && b.checks.some((c) => c.level === 'warn'), 'over the zip limit only warns before zipping');
assert.ok(!analyzeBackend([]).ok);
b = analyzeBackend([file('backend/app.py'), file('backend/__pycache__/app.cpython-312.pyc'), file('backend/helpers.pyc')]);
assert.ok(b.ok && b.files.map((f) => f.path).join() === 'app.py', 'Python bytecode folders are skipped automatically');

// ---- local backend runner (student/backend-test.mjs) on both starter templates ----
const runner = join(here, '..', 'student', 'backend-test.mjs');
const templates = join(here, '..', 'student', 'backend-template');
const runBackend = (folder, ...args) => spawnSync('node', [runner, join(templates, folder), ...args], { encoding: 'utf8' });
for (const folder of ['node', 'python']) {
  let r = runBackend(folder, 'GET', '/hello?name=Sam');
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /^200\n\{"message":\s?"Hello, Sam!"\}/m, `${folder}: ${r.stdout}`);
  r = runBackend(folder, 'POST', '/echo', '{"a":1}');
  assert.match(r.stdout, /"youSent":\s?\{"a":\s?1\}/, folder);
  assert.match(runBackend(folder, 'GET', '/missing').stdout, /^404/m, folder);
}
assert.notEqual(spawnSync('node', [runner, tmpdir()], { encoding: 'utf8' }).status, 0, 'a folder without an entry file fails clearly');

// ---- zip: build in Node, then let the real Lambda code validate and read it ----
const files = [
  { path: 'index.html', file: new Blob([BUILT.repeat(50)]) },
  { path: 'assets/app.js', file: new Blob(['console.log("héllo");'.repeat(100)]) },
  { path: 'assets/empty.txt', file: new Blob([]) },
  { path: 'assets/ünï.svg', file: new Blob(['<svg/>']) },
  { path: 'img/photo.bin', file: new Blob([crypto.getRandomValues(new Uint8Array(5000))]) }, // incompressible -> stored
];
const progress = [];
const zip = await buildZip(files, (done, total) => progress.push([done, total]));
assert.deepEqual(progress.at(-1), [5, 5]);
const dir = mkdtempSync(join(tmpdir(), 'zip-test-'));
try {
  writeFileSync(join(dir, 'site.zip'), Buffer.from(await zip.arrayBuffer()));
  const script = `
import os, sys, zipfile
os.environ.update(AWS_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", BUCKET="b", DISTRIBUTION_ID="E", DISTRIBUTION_DOMAIN="d", KEYS_PARAM="/k")
sys.path.insert(0, sys.argv[1])
import app
z = zipfile.ZipFile(sys.argv[2])
assert z.testzip() is None, "CRC check failed"
entries = app.site_entries(z)
assert sorted(entries) == ["assets/app.js", "assets/empty.txt", "assets/ünï.svg", "img/photo.bin", "index.html"], sorted(entries)
assert z.read("assets/app.js").decode() == 'console.log("héllo");' * 100
assert z.getinfo("index.html").compress_type == 8 and z.getinfo("img/photo.bin").compress_type == 0
print("ok")`;
  const py = spawnSync(python, ['-c', script, join(here, '..', 'lambda'), join(dir, 'site.zip')], { encoding: 'utf8' });
  assert.equal(py.status, 0, py.stderr || py.stdout);
} finally {
  rmSync(dir, { recursive: true, force: true });
}
console.log('test_web.mjs: all passed');

// dollar amounts on the AI cards: never round a tiny amount to $0.00, always two decimals for round amounts
for (const [n, want] of [[0, '$0.00'], [0.25, '$0.25'], [0.5, '$0.50'], [0.1, '$0.10'], [0.0123, '$0.0123'], [0.000095, '$0.0001'], [12.5, '$12.50'], [20, '$20.00']]) {
  assert.equal(usd(n), want, `usd(${n})`);
}

// the "Start here" card: only well-formed GitHub repository addresses produce links, and links never carry anything but owner/repo
const { parseRepo, repoLinks, KIT_FILES, setupCommand, mcpEntry, vscodeInstallLink, antigravityConfig } = await import('../web/github.js');
// connecting an editor without a download: the command, the editor entry and the VS Code install link
assert.equal(setupCommand('https://deploy.example.com/'), 'npx -y https://deploy.example.com/class-platform-mcp.tgz setup');
assert.deepEqual(mcpEntry('1.2.3', 'https://deploy.example.com/'), { command: 'npx', args: ['-y', 'https://deploy.example.com/class-platform-mcp-1.2.3.tgz'] });
assert.deepEqual(mcpEntry('1.2.3', 'https://deploy.example.com', true), { command: 'cmd', args: ['/c', 'npx', '-y', 'https://deploy.example.com/class-platform-mcp-1.2.3.tgz'] });
const link = vscodeInstallLink(mcpEntry('1.2.3', 'https://deploy.example.com'));
assert.ok(link.startsWith('vscode:mcp/install?'));
assert.deepEqual(JSON.parse(decodeURIComponent(link.slice('vscode:mcp/install?'.length))), { name: 'class-platform', command: 'npx', args: ['-y', 'https://deploy.example.com/class-platform-mcp-1.2.3.tgz'] });
assert.deepEqual(JSON.parse(antigravityConfig(mcpEntry('1.2.3', 'https://x.example'))).mcpServers['class-platform'].command, 'npx');
assert.ok(!/mds_/.test(link + antigravityConfig(mcpEntry('1', 'https://x.example'))), 'no token in any link or config text');
assert.deepEqual(parseRepo('https://github.com/alice/my-site'), { owner: 'alice', repo: 'my-site', base: 'https://github.com/alice/my-site' });
assert.equal(parseRepo('github.com/alice/my-site.git/').base, 'https://github.com/alice/my-site');
assert.equal(parseRepo('  https://www.github.com/Alice-1/repo_name.v2  ').repo, 'repo_name.v2');
for (const bad of ['', 'https://github.com/alice', 'https://evil.com/alice/repo', 'https://github.com/alice/repo/extra', 'http://github.com/alice/repo', 'https://github.com/-bad-/repo',
  'https://github.com/alice/..', 'https://github.com/alice/repo?x=1', 'https://github.com/alice/repo#frag', 'javascript:alert(1)', 'https://github.com/alice/re po', 'https://github.com/a"b/repo']) {
  assert.equal(parseRepo(bad), null, `must reject: ${bad}`);
}
assert.deepEqual(repoLinks(parseRepo('https://github.com/alice/site')), {
  secret: 'https://github.com/alice/site/settings/secrets/actions/new', actions: 'https://github.com/alice/site/actions', settings: 'https://github.com/alice/site/settings' });
assert.deepEqual(KIT_FILES, { zip: 'starter-kit.zip', version: 'kit-version.json' });
