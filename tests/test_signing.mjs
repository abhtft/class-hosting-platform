// Kit signing: the teacher's platform signs kit-version.json; a project accepts a kit update only if it is signed by the key it has pinned.
// Unit checks of the decision, the KMS signer against a fake AWS CLI, and the real tools/platform.mjs against tiny portals in this process
// (signed, tampered, unsigned, another key, replayed, rolled back).
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash, generateKeyPairSync, sign as ecdsaSign } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');
const load = (p) => import(pathToFileURL(join(repo, p)).href);
const { buildKit } = await load('starter/build-kit.mjs');
const { buildPublishFiles, kmsSigner, localSigner } = await load('starter/publish-files.mjs');
const { fingerprint, judgeManifest, refusalText, verifyBytes } = await load('starter/template/tools/signing.mjs');
const say = (m) => console.log(`  ok  ${m}`);
const work = mkdtempSync(join(tmpdir(), 'signing-'));
const servers = [];

try {
  // ---------- the decision ----------
  {
    const signer = localSigner();
    const other = localSigner();
    const manifest = (serial, extra = {}) => Buffer.from(JSON.stringify({ kitVersion: '2.1.0', serial, ...extra }));
    const m = manifest(100);
    const sig = signer.sign(m);
    assert.match(fingerprint(signer.publicKeyPem), /^[0-9A-F]{4}(-[0-9A-F]{4}){4}$/, 'a fingerprint is five groups of four');
    assert.notEqual(fingerprint(signer.publicKeyPem), fingerprint(other.publicKeyPem));
    assert.equal(verifyBytes(m, sig, signer.publicKeyPem), true);
    assert.equal(verifyBytes(manifest(101), sig, signer.publicKeyPem), false, 'one changed byte fails');
    assert.equal(verifyBytes(m, sig, other.publicKeyPem), false, 'another key fails');
    assert.equal(verifyBytes(m, 'not base64 at all %%%', signer.publicKeyPem), false);
    assert.equal(verifyBytes(m, sig, 'not a key'), false, 'garbage never throws');
    const judge = (o) => judgeManifest({ pin: signer.publicKeyPem, installedSerial: 100, ...o });
    assert.deepEqual(judge({ rawManifest: m, signature: sig }), { ok: true, reason: 'verified', serial: 100 });
    assert.equal(judge({ rawManifest: manifest(250), signature: signer.sign(manifest(250)) }).ok, true, 'a newer serial (a teacher rollback is published with one) is accepted');
    assert.equal(judge({ rawManifest: manifest(99), signature: signer.sign(manifest(99)) }).reason, 'older', 'a genuine but older manifest is a replay');
    assert.equal(judge({ rawManifest: m, signature: null }).reason, 'unsigned', 'once pinned, a missing signature is refused');
    assert.equal(judge({ rawManifest: m, signature: other.sign(m) }).reason, 'bad-signature');
    assert.equal(judge({ rawManifest: Buffer.from('{'), signature: sig }).reason, 'unreadable');
    assert.equal(judgeManifest({ rawManifest: m, signature: null, pin: '' }).ok, true, 'a project with no pin yet trusts its first kit, as it always did');
    for (const reason of ['unsigned', 'bad-signature', 'older', 'unreadable']) assert.match(refusalText(reason), /Nothing was changed and your current kit keeps working/);
    say('the decision: verified, newer (rollback) and first-use are accepted; unsigned, wrong key, tampered, older and unreadable are refused');
  }

  // ---------- the KMS signer talks to AWS KMS the way KMS expects (a digest, ECDSA_SHA_256) ----------
  {
    const { publicKey, privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
    const bytes = Buffer.from('a manifest that is much longer than the 4096 bytes KMS accepts as a raw message '.repeat(80));
    const calls = [];
    const aws = (...args) => {
      calls.push(args);
      if (args[1] === 'get-public-key') return JSON.stringify({ PublicKey: publicKey.export({ type: 'spki', format: 'der' }).toString('base64') });
      assert.deepEqual([args[1], args.includes('--message-type') && args[args.indexOf('--message-type') + 1], args[args.indexOf('--signing-algorithm') + 1]], ['sign', 'DIGEST', 'ECDSA_SHA_256']);
      const digest = readFileSync(args[args.indexOf('--message') + 1].replace('fileb://', ''));
      assert.deepEqual(digest, createHash('sha256').update(bytes).digest(), 'KMS is given the SHA-256 digest of exactly the bytes being signed');
      return JSON.stringify({ Signature: ecdsaSign("sha256", bytes, privateKey).toString('base64') });
    };
    const signer = kmsSigner(aws, 'key-123');
    assert.equal(signer.publicKeyPem, publicKey.export({ type: 'spki', format: 'pem' }), 'the public key from KMS becomes a PEM');
    assert.equal(verifyBytes(bytes, signer.sign(bytes), signer.publicKeyPem), true, 'a KMS signature (DER, base64) verifies with node:crypto');
    assert.ok(calls.every((c) => c[0] === 'kms' && c.includes('key-123')));
    // publishing refuses to put out a signature that does not verify
    const broken = { publicKeyPem: signer.publicKeyPem, sign: () => localSigner().sign(bytes) };
    assert.throws(() => buildPublishFiles(join(work, 'broken'), 'https://p.example', { signer: broken }), /does not verify/);
    say('the KMS signer sends a digest with ECDSA_SHA_256, its signature verifies, and a signature that does not verify is never published');
  }

  // ---------- the real tools/platform.mjs against tiny portals ----------
  const publish = (name, signer, serial) => {
    const dir = join(work, name);
    buildPublishFiles(dir, 'https://portal.example.test', { signer, now: () => serial });
    return dir;
  };
  const files = (dir, skip = []) => {
    const entries = ['starter-kit.zip', 'kit-version.json', 'kit-version.json.sig', 'signing-key.pub'].filter((n) => !skip.includes(n));
    return new Map(entries.map((n) => [n, readFileSync(join(dir, n))]));
  };
  async function serve(map) {
    const server = createServer((req, res) => {
      const body = map.get(req.url.slice(1));
      res.writeHead(body ? 200 : 404);
      res.end(body);
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    servers.push(server);
    return `http://127.0.0.1:${server.address().port}`;
  }
  const run = (dir, ...args) => new Promise((resolve) => {
    const child = spawn(process.execPath, [join(dir, 'tools', 'platform.mjs'), ...args], { cwd: dir });
    let text = '';
    child.stdout.on('data', (d) => (text += d));
    child.stderr.on('data', (d) => (text += d));
    child.on('close', (status) => resolve({ status, text }));
  });
  let counter = 0;
  /** A project from an older kit: one managed file differs from the portal's (so a sync has something to do). */
  const project = () => {
    const dir = join(work, `project-${counter++}`);
    buildKit(dir, { portalUrl: 'https://portal.example.test' });
    const old = 'old rules\n';
    writeFileSync(join(dir, 'AGENTS.md'), old);
    const manifest = JSON.parse(readFileSync(join(dir, 'kit-manifest.json'), 'utf8'));
    manifest.files['AGENTS.md'] = createHash('sha256').update(old).digest('hex');
    writeFileSync(join(dir, 'kit-manifest.json'), JSON.stringify(manifest));
    return dir;
  };
  const rules = (dir) => readFileSync(join(dir, 'AGENTS.md'), 'utf8');
  const platformJson = (dir) => JSON.parse(readFileSync(join(dir, 'platform.json'), 'utf8'));

  const A = localSigner();
  const B = localSigner();
  const signed1000 = publish('A-1000', A, 1000);
  const url = await serve(files(signed1000));

  // a project with no pin takes the key that came with the signed kit, and from then on holds it
  const first = project();
  let r = await run(first, 'sync', '--portal', url);
  assert.equal(r.status, 0, r.text);
  assert.notEqual(rules(first), 'old rules\n', 'the kit was applied');
  assert.equal(platformJson(first).signingKey, A.publicKeyPem, 'the platform key is pinned');
  assert.equal(platformJson(first).serial, 1000);
  r = await run(first, 'status', '--portal', url);
  assert.match(r.text, new RegExp(`signed by your teacher's key ${fingerprint(A.publicKeyPem)}`));
  say('the first signed kit pins the key and the serial; status shows the fingerprint');

  const refused = async (name, map, expectText, startFrom = first) => {
    // the pinned project, made old again, must refuse and change nothing
    const dir = project();
    writeFileSync(join(dir, 'platform.json'), JSON.stringify({ ...platformJson(first) }, null, 2));
    const before = rules(dir);
    const at = await serve(map);
    const result = await run(dir, 'sync', '--portal', at);
    assert.equal(result.status, 3, `${name}: ${result.text}`);
    assert.match(result.text, expectText, name);
    assert.equal(rules(dir), before, `${name}: nothing was written`);
    assert.equal(JSON.stringify(platformJson(dir)), JSON.stringify(platformJson(first)), `${name}: the pin is unchanged`);
  };

  // a changed manifest keeps the old signature: refused
  const tampered = files(signed1000);
  tampered.set('kit-version.json', Buffer.from(String(tampered.get('kit-version.json')).replace('"files"', '"extra": 1, "files"')));
  await refused('tampered manifest', tampered, /not signed by the key this project trusts/);
  // the signature file removed: refused (otherwise the attacker simply deletes it)
  await refused('signature removed', files(signed1000, ['kit-version.json.sig']), /is not signed\./);
  // a complete, valid kit signed by some other key: refused
  await refused('another key', files(publish('B-3000', B, 3000)), /not signed by the key this project trusts/);
  // a genuine older kit put back (a replay): refused
  await refused('replay', files(publish('A-500', A, 500)), /older than the kit you already have/);
  say('a tampered manifest, a removed signature, another key and a replayed older kit are refused with exit code 3 and nothing is written');

  // a teacher rollback is a new publish (new serial), so it is accepted even though the content could be older
  const rollback = project();
  writeFileSync(join(rollback, 'platform.json'), JSON.stringify(platformJson(first), null, 2));
  r = await run(rollback, 'sync', '--portal', await serve(files(publish('A-2000', A, 2000))));
  assert.equal(r.status, 0, r.text);
  assert.equal(platformJson(rollback).serial, 2000);
  say('a teacher rollback (published with a new serial) is accepted');

  // the zip and the signed manifest must agree even for a project with no pin: a key that does not verify the manifest is refused
  const mismatched = files(signed1000);
  mismatched.set('kit-version.json.sig', Buffer.from(`${B.sign(mismatched.get('kit-version.json'))}\n`));
  const stranger = project();
  r = await run(stranger, 'sync', '--portal', await serve(mismatched));
  assert.equal(r.status, 3, r.text);
  assert.equal(platformJson(stranger).signingKey, undefined, 'nothing was pinned');

  // a platform that does not sign (yet) still works for a project with no pin, and pins nothing
  const unsigned = project();
  r = await run(unsigned, 'sync', '--portal', await serve(files(signed1000, ['kit-version.json.sig', 'signing-key.pub'])));
  assert.equal(r.status, 0, r.text);
  assert.equal(platformJson(unsigned).signingKey, undefined);
  say('a project with no pin still works against a platform that does not sign (and pins nothing); a signature that does not match the kit\'s own key is refused');

  // trust: only in a terminal, and only for a different key
  r = await run(first, 'trust', '--portal', url);
  assert.equal(r.status, 0, r.text);
  assert.match(r.text, /already trust this key/);
  const moved = await serve(files(publish('B-4000', B, 4000)));
  r = await run(first, 'trust', '--portal', moved);
  assert.equal(r.status, 1);
  assert.match(r.text, /Run this yourself in a terminal/, 'an agent or a script cannot re-pin on the student\'s behalf');
  assert.equal(platformJson(first).signingKey, A.publicKeyPem);
  say('trust says so when nothing changed, and refuses to re-pin without a person at a terminal');
} finally {
  for (const s of servers) s.close();
  rmSync(work, { recursive: true, force: true });
}
console.log('test_signing.mjs: all passed');
