// The saved AWS access (installer/credentials.mjs): one section of the standard credentials file, after a backup, everything else untouched,
// nothing secret returned or logged, bad keys refused, and the editor entries moved to a new release by updateAll.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PROFILE_NAME, readGlobalConfig, saveProfile, setSection, writeGlobalConfig } from '../installer/credentials.mjs';
import { updateAll } from '../teacher-mcp/setup.mjs';

const dir = mkdtempSync(join(tmpdir(), 'creds-'));
const mkdirSync_ = (p) => mkdirSync(p, { recursive: true });
const env = { AWS_SHARED_CREDENTIALS_FILE: join(dir, 'aws', 'credentials'), AWS_CONFIG_FILE: join(dir, 'aws', 'config'), CLASS_PLATFORM_TEACHER_HOME: join(dir, 'home') };
const KEY = 'AKIA' + 'ABCDEFGHIJKLMNOP'; // pieces: no key-shaped text sits in the repository
const SECRET = 'wJalrXUtnFEMI/K7MDENG' + '/bPxRfiCYEXAMPLEKEY';

// ---------- one section is replaced or added; nothing else changes ----------
assert.equal(setSection('', 'a', ['x = 1']), '[a]\nx = 1\n');
const mixed = '[default]\naws_access_key_id = OLD\n\n[class-platform]\nold = 1\n\n[other]\nkeep = me\n';
const replaced = setSection(mixed, 'class-platform', ['new = 2']);
assert.ok(replaced.includes('[default]\naws_access_key_id = OLD') && replaced.includes('[other]\nkeep = me') && replaced.includes('[class-platform]\nnew = 2') && !replaced.includes('old = 1'));
assert.ok(setSection('[default]\nk = v', 'class-platform', ['n = 1']).startsWith('[default]\nk = v\n\n[class-platform]'), 'added after the existing sections');
assert.equal(setSection('[class-platform]\nold = 1\r\n', 'class-platform', ['n = 2']), '[class-platform]\nn = 2\n', 'Windows line endings are fine');
console.log('  ok  only the class-platform section is replaced or added');

// ---------- saving a key: backup, files, no secret returned ----------
{
  mkdirSync_(join(dir, 'aws'));
  writeFileSync(env.AWS_SHARED_CREDENTIALS_FILE, '[default]\naws_access_key_id = MINE\naws_secret_access_key = MINE2\n');
  const out = saveProfile({ accessKeyId: KEY, secretAccessKey: SECRET, env });
  assert.equal(out.profile, PROFILE_NAME);
  const text = readFileSync(env.AWS_SHARED_CREDENTIALS_FILE, 'utf8');
  assert.ok(text.includes('[default]\naws_access_key_id = MINE') && text.includes(`[class-platform]\naws_access_key_id = ${KEY}`), 'your other profiles are untouched');
  assert.ok(readFileSync(env.AWS_CONFIG_FILE, 'utf8').includes('[profile class-platform]\nregion = us-east-1'));
  assert.ok(readdirSync(join(dir, 'aws')).some((f) => f.startsWith('credentials.bak-')), 'the old credentials file was backed up first');
  assert.ok(!JSON.stringify(out).includes(SECRET) && !JSON.stringify(out).includes(KEY), 'what is returned (and printed) holds no key');
  saveProfile({ accessKeyId: KEY, secretAccessKey: SECRET, env });
  assert.equal((readFileSync(env.AWS_SHARED_CREDENTIALS_FILE, 'utf8').match(/\[class-platform\]/g) || []).length, 1, 'saving twice does not duplicate the profile');
  assert.throws(() => saveProfile({ accessKeyId: 'nope', secretAccessKey: SECRET, env }), /access key id/);
  assert.throws(() => saveProfile({ accessKeyId: KEY, secretAccessKey: 'short', env }), /secret access key/);
  assert.throws(() => saveProfile({ accessKeyId: KEY + ' x', secretAccessKey: SECRET, env }), /access key id/, 'no stray text goes into a file');
  console.log('  ok  the key is saved as its own profile after a backup, your other profiles are untouched, a bad key is refused');
}

// ---------- the profile choice is remembered, without any secret ----------
{
  assert.deepEqual(readGlobalConfig(env), {});
  writeGlobalConfig({ profile: 'class-platform' }, env);
  assert.deepEqual(readGlobalConfig(env), { profile: 'class-platform' });
  assert.ok(!readFileSync(join(env.CLASS_PLATFORM_TEACHER_HOME, 'config.json'), 'utf8').includes(SECRET));
  console.log('  ok  the chosen profile is remembered in a small file with no secret');
}

// ---------- the editor entries move to a release ----------
{
  const vs = { id: 'vscode', name: 'VS Code', installedAt: join(dir, 'vs'), file: join(dir, 'vs', 'mcp.json'), key: 'servers', typed: true };
  const ag = { id: 'antigravity', name: 'Antigravity', installedAt: join(dir, 'ag'), file: join(dir, 'ag', 'mcp_config.json'), key: 'mcpServers', typed: false };
  const absent = { id: 'x', name: 'Nothing', installedAt: join(dir, 'missing'), file: join(dir, 'missing', 'f.json'), key: 'servers', typed: true };
  mkdirSync_(join(dir, 'vs'));
  mkdirSync_(join(dir, 'ag'));
  writeFileSync(vs.file, JSON.stringify({ servers: { other: { command: 'x' } } }));
  writeFileSync(ag.file, '{ // my notes\n "mcpServers": {} }');
  const first = updateAll({ version: '1.1.0', repo: 'someone/class-platform', platform: 'linux', list: [vs, ag, absent] });
  assert.deepEqual(first.map((r) => [r.editor, r.action]), [['VS Code', 'add'], ['Antigravity', 'manual']], 'an editor that is not installed is skipped');
  assert.ok(first[0].backup && existsSync(first[0].backup));
  assert.ok(first[1].entry.args.includes('https://github.com/someone/class-platform/releases/download/v1.1.0/class-platform-teacher-1.1.0.tgz'));
  const doc = JSON.parse(readFileSync(vs.file, 'utf8'));
  assert.deepEqual(Object.keys(doc.servers).sort(), ['class-platform-teacher', 'other']);
  assert.ok(readFileSync(ag.file, 'utf8').includes('my notes'), 'a file with comments is never rewritten');
  const again = updateAll({ version: '1.1.0', repo: 'someone/class-platform', platform: 'linux', list: [vs] });
  assert.equal(again[0].action, 'same');
  const newer = updateAll({ version: '1.2.0', repo: 'someone/class-platform', platform: 'linux', list: [vs] });
  assert.equal(newer[0].action, 'update');
  assert.ok(JSON.parse(readFileSync(vs.file, 'utf8')).servers['class-platform-teacher'].args.includes('https://github.com/someone/class-platform/releases/download/v1.2.0/class-platform-teacher-1.2.0.tgz'));
  console.log('  ok  editor entries move to a release: backup first, other servers kept, comments never rewritten, "same" and "update" told apart');
}

try { rmSync(dir, { recursive: true, force: true }); } catch { /* Windows may hold the folder a moment */ }
console.log('test_credentials.mjs: all passed');

