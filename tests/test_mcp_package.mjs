// The class-platform-mcp PACKAGE, the way a student gets it: `npx -y <portal>/class-platform-mcp-<version>.tgz` (no kit download, no git), run by an editor
// that tells it which folder is open. Everything runs against the local dev portal, which serves the package files built by the same code as portal-publish.
//   - the tarball installs from a URL like npx does, and builds the same bytes every time
//   - the server finds the open folder through the editor's "roots" (and a `path` argument wins)
//   - init_project writes the starter into an empty folder (asks first, refuses a folder with files)
//   - check_project / deploy work in a folder with NO kit files (the package carries its own check and deploy)
//   - the token comes from the computer-wide store; `setup` saves it and adds the server to VS Code and Antigravity safely
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const say = (m) => console.log(`  ok  ${m}`);
const win = process.platform === 'win32';

const PORT = 8798;
const base = `http://127.0.0.1:${PORT}`;
const portal = spawn(python, [join(here, 'dev_server.py'), String(PORT)], { env: { ...process.env, AWS_DEFAULT_REGION: 'us-east-1' } });
process.on('exit', () => portal.kill());
let out = '';
portal.stdout.on('data', (d) => (out += d));
portal.stderr.on('data', (d) => (out += d));
const work = mkdtempSync(join(tmpdir(), 'mcp-package-'));
const home = join(work, 'home');
mkdirSync(home, { recursive: true });
const classHome = join(work, 'class-home');
const env = {
  ...process.env, PORTAL_URL: base, PLATFORM_PORTAL: base, CLASS_PLATFORM_HOME: classHome, PORTAL_TOKEN: '', PORTAL_READY_TIMEOUT_MS: '60000',
  HOME: home, USERPROFILE: home, APPDATA: join(home, 'AppData', 'Roaming'), XDG_CONFIG_HOME: join(home, '.config'), npm_config_cache: join(work, 'npm-cache'),
};

/** An editor, as far as the server can tell: it sends requests, answers the server's roots/list, and collects the answers. */
function editor(binFile, { cwd = work, roots = null, extraEnv = {} } = {}) {
  const child = spawn(process.execPath, [binFile], { cwd, env: { ...env, ...extraEnv } });
  const waiting = new Map();
  let buffer = '';
  let next = 0;
  child.stdout.on('data', (chunk) => {
    buffer += chunk;
    let at;
    while ((at = buffer.indexOf('\n')) >= 0) {
      const message = JSON.parse(buffer.slice(0, at)); // every line must be a JSON-RPC message
      buffer = buffer.slice(at + 1);
      if (message.method === 'roots/list') {
        child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id: message.id, result: { roots: roots ? [{ uri: pathToFileURL(roots).href, name: 'workspace' }] : [] } })}\n`);
      } else {
        waiting.get(message.id)?.(message);
      }
    }
  });
  const call = (method, params) => new Promise((resolve) => {
    const id = ++next;
    waiting.set(id, resolve);
    child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
  });
  return {
    async start() {
      await call('initialize', { protocolVersion: '2025-06-18', capabilities: roots ? { roots: { listChanged: true } } : {}, clientInfo: { name: 'test', version: '1' } });
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' })}\n`);
      await new Promise((r) => setTimeout(r, 400)); // the server asks for the roots right after this
      return this;
    },
    tool: async (name, args = {}) => (await call('tools/call', { name, arguments: args })).result,
    list: async () => (await call('tools/list')).result.tools.map((t) => t.name),
    close: () => child.kill(),
  };
}

try {
  for (let i = 0; i < 300 && !out.includes('Backend demo:'); i++) await new Promise((r) => setTimeout(r, 100));
  assert.ok(out.includes('Backend demo:'), `the dev portal did not start within 30 s:\n${out}`);
  const alice = out.match(/Alice\s+(mds_\S+)/)[1];

  // ---------- the tarball: published by the same code, installable from a URL like npx does ----------
  const version = JSON.parse(readFileSync(join(repo, 'starter', 'mcp', 'package.json'), 'utf8')).version;
  const url = `${base}/class-platform-mcp-${version}.tgz`;
  const info = await (await fetch(`${base}/mcp-version.json`)).json();
  assert.equal(info.version, version);
  assert.equal(info.url, url);
  const served = Buffer.from(await (await fetch(url)).arrayBuffer());
  assert.equal(createHash('sha256').update(served).digest('hex'), info.sha256, 'the version file carries the tarball hash');
  assert.deepEqual(Buffer.from(await (await fetch(`${base}/class-platform-mcp.tgz`)).arrayBuffer()), served, 'the alias is the newest copy');
  const { buildMcpPackage } = await import(pathToFileURL(join(repo, 'starter', 'build-mcp-package.mjs')).href);
  assert.equal(buildMcpPackage(join(work, 'again.tgz'), { portalUrl: base }).sha256, info.sha256, 'building twice gives the same bytes');
  say('the package is served with its version file and hash, and builds to the same bytes every time');

  const via = spawnSync(`npx -y ${url} version`, { env, encoding: 'utf8', shell: true, cwd: work });
  assert.equal(via.status, 0, via.stdout + via.stderr);
  assert.equal(via.stdout.trim().split('\n').pop(), version);
  say('`npx -y <url> version` runs the package straight from the portal URL (no kit, no git)');

  const prefix = join(work, 'installed');
  const installed = spawnSync(`npm install --prefix "${prefix}" ${url} --no-audit --no-fund`, { env, encoding: 'utf8', shell: true });
  assert.equal(installed.status, 0, installed.stdout + installed.stderr);
  const bin = join(prefix, 'node_modules', 'class-platform-mcp', 'bin.mjs');
  assert.ok(existsSync(join(prefix, 'node_modules', 'class-platform-mcp', 'kit.zip')));

  // ---------- init_project in an empty folder, found through the editor's roots ----------
  const projectA = join(work, 'my-first-project');
  mkdirSync(projectA);
  const a = await editor(bin, { roots: projectA }).start();
  assert.deepEqual(await a.list(), ['init_project', 'check_project', 'deploy', 'status', 'update_kit']);
  const refused = await a.tool('init_project', {});
  assert.equal(refused.isError, true);
  assert.match(refused.content[0].text, /Nothing written.*confirm=true/s);
  assert.deepEqual(readdirSync(projectA), [], 'nothing is written without a yes');
  const made = await a.tool('init_project', { confirm: true, install: false });
  assert.equal(made.isError, false, made.content[0].text);
  assert.match(made.content[0].text, /Wrote \d+ files to/);
  for (const file of ['AGENTS.md', 'frontend/package.json', 'backend/app.py', 'backend/class_storage.py', 'tools/check.mjs', 'portal-deploy.mjs', 'platform.json']) assert.ok(existsSync(join(projectA, file)), file);
  const again = await a.tool('init_project', { confirm: true, install: false });
  assert.equal(again.isError, true);
  assert.match(again.content[0].text, /is not empty/);
  say('init_project writes the starter into the folder the editor has open, only after a yes, and refuses a folder that already has files');

  const checked = await a.tool('check_project');
  assert.equal(checked.isError, false, checked.content[0].text);
  say('check_project works on the new project');

  // ---------- no kit files at all: the package brings its own check, and refuses to update a kit that is not there ----------
  const projectB = join(work, 'second');
  mkdirSync(projectB);
  await a.tool('init_project', { confirm: true, install: false, path: projectB });
  rmSync(join(projectB, 'tools', 'check.mjs'));
  rmSync(join(projectB, 'portal-deploy.mjs'));
  const bundled = await a.tool('check_project', { path: projectB });
  assert.equal(bundled.isError, false, bundled.content[0].text);
  const emptyFolder = join(work, 'plain');
  mkdirSync(emptyFolder);
  const nothing = await a.tool('update_kit', { confirm: true, path: emptyFolder });
  assert.equal(nothing.isError, true);
  assert.match(nothing.content[0].text, /no kit files to update/);
  const wrong = await a.tool('check_project', { path: emptyFolder });
  assert.equal(wrong.isError, true, 'a path argument wins over the open folder: an empty folder is not a valid project');
  say('without kit files the bundled check and deploy are used; `path` overrides the open folder; update_kit says there is nothing to update');

  // ---------- the token: saved once for the computer, used by every project ----------
  const before = await a.tool('status');
  assert.equal(before.isError, true);
  assert.match(before.content[0].text, /No token found/);
  const login = spawnSync(process.execPath, [bin, 'login'], { env, input: `${alice}\n`, encoding: 'utf8' });
  assert.equal(login.status, 0, login.stdout + login.stderr);
  assert.ok(!login.stdout.includes(alice), 'the token is never printed');
  assert.match(readFileSync(join(classHome, 'token'), 'utf8'), new RegExp(alice));
  assert.ok(!existsSync(join(projectA, '.env.local')), 'nothing is written into the project');
  const after = await a.tool('status');
  assert.equal(after.isError, false, after.content[0].text);
  assert.match(after.content[0].text, /Signed in as .*alice/i);
  assert.ok(!JSON.stringify(after).includes(alice));
  say('`login` saves the token once in the home folder (not in a project); every project and the status tool use it, and it is never printed');

  const deployed = await a.tool('deploy', { confirm: true, only: 'backend' });
  assert.equal(deployed.isError, false, deployed.content[0].text);
  assert.match(deployed.content[0].text, /Backend ready/);
  assert.equal((await fetch(`${base}/b/alice/health`)).status, 200);
  say('deploy publishes the new project\'s backend with the computer-wide token');

  // ---------- a newer package is mentioned by status ----------
  const fake = createServer((req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ version: '9.9.9', alias: 'http://example.invalid/class-platform-mcp.tgz' })); });
  await new Promise((resolve) => fake.listen(0, '127.0.0.1', resolve));
  const b = await editor(bin, { roots: projectA, extraEnv: { PLATFORM_PORTAL: `http://127.0.0.1:${fake.address().port}` } }).start();
  const newer = await b.tool('status');
  assert.match(newer.content[0].text, new RegExp(`class-platform-mcp ${version.replace(/\./g, '\\.')} is running, 9\\.9\\.9 is available.*setup`, 's'));
  b.close();
  fake.close();
  a.close();
  say('status tells the student when a newer package exists and what to run');

  // ---------- setup: the token and the editors ----------
  const where = JSON.parse(spawnSync(process.execPath, ['-e', `import(${JSON.stringify(pathToFileURL(join(prefix, 'node_modules', 'class-platform-mcp', 'setup.mjs')).href)}).then((m) => console.log(JSON.stringify(m.editors())))`], { env, encoding: 'utf8' }).stdout);
  const vscode = where.find((e) => e.id === 'vscode');
  const antigravity = where.find((e) => e.id === 'antigravity');
  mkdirSync(vscode.installedAt, { recursive: true });
  mkdirSync(antigravity.installedAt, { recursive: true });
  const setup = (args, input = '') => spawnSync(process.execPath, [bin, 'setup', '--portal', base, ...args], { env, input, encoding: 'utf8' });
  const readJson = (file) => JSON.parse(readFileSync(file, 'utf8'));
  const expectedArgs = win ? ['/c', 'npx', '-y', url] : ['-y', url];
  const expectedCommand = win ? 'cmd' : 'npx';

  let r = setup(['--dry-run']);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /will create the file/);
  assert.ok(!existsSync(vscode.file) && !existsSync(antigravity.file), 'a dry run writes nothing');
  r = setup(['--no-token']);
  assert.match(r.stdout, /skipped \(no terminal to ask/);
  assert.ok(!existsSync(vscode.file), 'without a yes (and no terminal) nothing is written');
  r = setup(['--editors', 'none', '--no-token']);
  assert.match(r.stdout, /To add it yourself/);
  assert.ok(!existsSync(antigravity.file));
  rmSync(classHome, { recursive: true, force: true });
  r = setup(['--yes'], `${alice}\n`);
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.ok(existsSync(join(classHome, 'token')), 'setup also saved the token');
  assert.ok(!r.stdout.includes(alice));
  assert.deepEqual(readJson(vscode.file).servers['class-platform'], { type: 'stdio', command: expectedCommand, args: expectedArgs });
  assert.deepEqual(readJson(antigravity.file).mcpServers['class-platform'], { command: expectedCommand, args: expectedArgs });
  say('setup: saves the token, then creates the VS Code (servers, type stdio) and Antigravity (mcpServers) entries with the pinned URL; dry run, no terminal and --editors none write nothing');

  r = setup(['--yes', '--no-token']);
  assert.equal((r.stdout.match(/already set up/g) ?? []).length, 2);
  assert.ok(readdirSync(dirname(antigravity.file)).every((f) => !f.includes('.bak-')), 'an unchanged file is not rewritten or backed up');
  say('running setup again changes nothing');

  writeFileSync(antigravity.file, JSON.stringify({ mcpServers: { other: { command: 'x', args: ['1'] }, 'class-platform': { command: 'node', args: ['old.mjs'] } } }, null, 2));
  r = setup(['--yes', '--no-token']);
  const merged = readJson(antigravity.file).mcpServers;
  assert.deepEqual(merged.other, { command: 'x', args: ['1'] }, 'other servers are never touched');
  assert.deepEqual(merged['class-platform'], { command: expectedCommand, args: expectedArgs }, 'an old class-platform entry is replaced');
  assert.match(r.stdout, /replace the "class-platform" entry/);
  assert.ok(readdirSync(dirname(antigravity.file)).some((f) => f.includes('mcp_config.json.bak-')), 'the old file is kept as a backup');
  say('setup merges one entry, keeps every other server, replaces an outdated class-platform entry and keeps a backup');

  const commented = '// my notes\n{\n  "servers": { "mine": { "command": "x" } }\n}\n';
  writeFileSync(vscode.file, commented);
  r = setup(['--yes', '--no-token']);
  assert.equal(readFileSync(vscode.file, 'utf8'), commented, 'a file with comments is not rewritten');
  assert.match(r.stdout, /left alone: the file has comments/);
  assert.match(r.stdout, /To add it yourself/);
  writeFileSync(vscode.file, '{ not json');
  r = setup(['--yes', '--no-token']);
  assert.equal(readFileSync(vscode.file, 'utf8'), '{ not json');
  assert.match(r.stdout, /not valid JSON/);
  say('a settings file with comments or invalid JSON is left alone and the exact entry is printed instead');

  const printed = JSON.parse(spawnSync(process.execPath, [bin, '--print-config', '--json'], { env, encoding: 'utf8' }).stdout);
  assert.deepEqual(printed.mcpServers['class-platform'], { command: expectedCommand, args: expectedArgs }, 'npm run mcp-config and setup agree');
  say('--print-config prints the same entry that setup writes');

  console.log('mcp package: all passed');
} finally {
  portal.kill();
  rmSync(work, { recursive: true, force: true });
}
process.exit(0);
