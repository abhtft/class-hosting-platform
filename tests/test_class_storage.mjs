// The Node helper (student/class_storage.mjs) against the real storage API: data and files, every error kind, the local folder on a
// student's computer, and that no key ever shows up in an error.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const storage = await import(pathToFileURL(join(here, '..', 'student', 'class_storage.mjs')).href);
const say = (m) => console.log(`  ok  ${m}`);

const fixture = spawn(python, [join(here, 'storage_fixture.py')], { stdio: ['ignore', 'pipe', 'inherit'] });
const info = await new Promise((resolve, reject) => {
  let buffer = '';
  fixture.stdout.on('data', (d) => { buffer += d; if (buffer.includes('\n')) resolve(JSON.parse(buffer.split('\n')[0])); });
  fixture.on('exit', () => reject(new Error('fixture exited')));
});
const folder = mkdtempSync(join(tmpdir(), 'class-storage-'));

const use = (key, url = info.url) => { process.env.CLASS_STORAGE_URL = url; process.env.CLASS_STORAGE_KEY = key; };
async function fails(kind, fn) {
  try {
    await fn();
  } catch (error) {
    assert.ok(error instanceof storage.StorageError, String(error));
    assert.equal(error.kind, kind, `${kind} expected, got ${error.kind}: ${error.message}`);
    assert.ok(!error.message.includes(info.key) && !error.message.includes(info.tight_key), 'a key must never be in an error');
    return error;
  }
  throw new Error(`expected StorageError(${kind})`);
}

try {
  // ---------- on the platform ----------
  use(info.key);
  assert.equal((await storage.put('scores', 'alice', { points: 3, name: 'Zoë' })).id, 'alice');
  assert.deepEqual(await storage.get('scores', 'alice'), { points: 3, name: 'Zoë' });
  assert.equal(await storage.get('scores', 'nobody'), null);
  assert.equal(await storage.get('scores', 'nobody', 'd'), 'd');
  for (let i = 0; i < 3; i++) await storage.put('scores', `p${i}`, i);
  assert.deepEqual((await storage.items('scores')).map((r) => r.id), ['alice', 'p0', 'p1', 'p2']);
  assert.deepEqual((await storage.items('scores', { limit: 2 })).map((r) => r.id), ['alice', 'p0']);
  assert.deepEqual((await storage.items('scores', { after: 'p0' })).map((r) => r.id), ['p1', 'p2']);
  assert.equal(await storage.del('scores', 'alice'), true);
  assert.equal(await storage.remove('scores', 'alice'), false);
  say('put/get/default/items/paging/delete through the real API');

  await fails('bad_request', () => storage.put('Bad Name', 'x', 1));
  await fails('bad_request', () => storage.get('scores', '../x'));
  await fails('too_large', () => storage.put('scores', 'big', 'x'.repeat(70000)));
  say('names are checked and a value over 64 KB is refused with too_large');

  await storage.files.put('note.txt', 'hello file', 'text/plain');
  assert.deepEqual((await storage.files.list()).map((f) => [f.name, f.size]), [['note.txt', 10]]);
  assert.equal(await (await fetch(await storage.files.url('note.txt'))).text(), 'hello file');
  const target = await storage.files.uploadUrl('pic.png', 100, 'image/png');
  assert.ok(target.url.endsWith('/s3-post') && target.fields['Content-Type'] === 'image/png' && target.fields.key.endsWith('/pic.png'));
  await fails('too_large', () => storage.files.put('huge.bin', Buffer.alloc(5 * 1024 * 1024 + 1)));
  await fails('bad_request', () => storage.files.put('a/b.txt', 'x'));
  assert.equal(await storage.files.delete('note.txt'), true);
  assert.deepEqual(await storage.files.list(), []);
  await fails('not_found', () => storage.files.url('note.txt'));
  say('files: put (multipart, file last), list, download link, upload target, delete, limits');

  use(info.tight_key);
  await storage.put('t', 'a', 1);
  await storage.get('t', 'a');
  await fails('allowance_used', () => storage.get('t', 'a'));
  say('allowance_used when the allowance is gone');

  use(info.off_key);
  await fails('not_enabled', () => storage.put('t', 'a', 1));
  use(`${info.key.split('.')[0]}.${'A'.repeat(32)}`); // right student, wrong secret
  await fails('bad_key', () => storage.get('t', 'a'));
  use(info.key, 'http://127.0.0.1:9/storage'); // nothing listens there
  await fails('unavailable', () => storage.get('t', 'a'));
  say('not_enabled, bad_key and unavailable (after one retry) are reported with a kind');

  // ---------- on the platform, storage not switched on: no pretending with local files ----------
  delete process.env.CLASS_STORAGE_URL;
  delete process.env.CLASS_STORAGE_KEY;
  process.env.AWS_LAMBDA_FUNCTION_NAME = 'sd-test';
  await fails('not_enabled', () => storage.put('t', 'a', 1));
  delete process.env.AWS_LAMBDA_FUNCTION_NAME;

  // ---------- on the student's own computer: a local folder ----------
  process.env.CLASS_STORAGE_DIR = join(folder, '.class-storage');
  await storage.put('notes', 'n1', { text: 'local' });
  assert.deepEqual(await storage.get('notes', 'n1'), { text: 'local' });
  assert.equal(await storage.get('notes', 'n2', 5), 5);
  await storage.put('notes', 'n2', [1, 2]);
  assert.deepEqual((await storage.items('notes')).map((r) => r.id), ['n1', 'n2']);
  assert.deepEqual((await storage.items('notes', { after: 'n1' })).map((r) => r.id), ['n2']);
  assert.equal(await storage.del('notes', 'n1'), true);
  assert.equal(await storage.del('notes', 'n1'), false);
  await storage.files.put('a.txt', 'x');
  assert.deepEqual(await storage.files.list(), [{ name: 'a.txt', size: 1 }]);
  assert.match(await storage.files.url('a.txt'), /^file:\/\/\//);
  assert.equal((await storage.files.uploadUrl('a.txt', 1)).local, true);
  await storage.files.delete('a.txt');
  assert.deepEqual(await storage.files.list(), []);
  await fails('bad_request', () => storage.put('Bad', 'x', 1));
  await fails('too_large', () => storage.put('notes', 'big', 'x'.repeat(70000)));
  assert.ok(existsSync(process.env.CLASS_STORAGE_DIR));
  say('on a student\'s computer everything works from a local folder, with the same rules');
  console.log('test_class_storage.mjs: all passed');
} finally {
  fixture.kill();
  rmSync(folder, { recursive: true, force: true });
}
