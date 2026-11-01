#!/usr/bin/env node
// Builds every file `node admin.mjs portal-publish` puts next to the portal pages for students, in one place so the real publish and the
// local dev portal (tests/dev_server.py) cannot differ:
//
//   starter-kit.zip, kit-version.json             the student kit and its version file (what `platform update` and the Start here card read)
//   kit-version.json.sig, signing-key.pub         the signature of kit-version.json and the public key it verifies with (when a signer is given)
//   class-platform-mcp-<version>.tgz              the MCP package, one file per version (old ones stay: editor settings point at them)
//   class-platform-mcp.tgz                        always the newest copy; only used for the one-time `setup` command
//   mcp-version.json                              { version, url, alias, sha256 }: what `setup` and the MCP `status` tool compare against
//
//   node starter/publish-files.mjs <folder> <portal url>      (signs with a throwaway key: this is the local dev portal)
import { createHash, generateKeyPairSync, sign as ecdsaSign } from 'node:crypto';
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildKitZip } from './build-kit.mjs';
import { buildMcpPackage } from './build-mcp-package.mjs';
import { fingerprint, verifyBytes } from './template/tools/signing.mjs';

/**
 * A signer is { publicKeyPem, sign(bytes) -> base64 signature }. The real one is kmsSigner() (the key never leaves AWS KMS); tests and the local
 * dev portal use localSigner(). Without a signer the kit is published unsigned, as before 2.1.
 */
/**
 * Every static file `portal-publish` uploads besides the built ones: the pages in web/ and the student downloads (portal-downloads.json maps the
 * published name to its source file). Returns [[published name, absolute path]]. The real publish and the local dev portal both use it.
 */
export function portalPageFiles(root) {
  const web = join(root, 'web');
  const downloads = JSON.parse(readFileSync(join(root, 'portal-downloads.json'), 'utf8'));
  return [
    ...readdirSync(web).filter((f) => statSync(join(web, f)).isFile()).map((f) => [f, join(web, f)]),
    ...Object.entries(downloads).map(([name, source]) => [name, join(root, source)]),
  ];
}

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

/**
 * `pages` is portalPageFiles(): the signed manifest lists the sha256 of every file that is published next to it (`portalFiles`), so a changed page,
 * kit or editor package is found by `verify` (and by anything else that checks the signature) even by someone who can write the portal's files but
 * cannot use the signing key. The manifest cannot list itself or its signature.
 */
export function buildPublishFiles(dir, portalUrl, { signer = null, pages = [], now = () => Math.floor(Date.now() / 1000) } = {}) {
  const base = String(portalUrl).replace(/\/$/, '');
  mkdirSync(dir, { recursive: true });
  const kit = buildKitZip(join(dir, 'starter-kit.zip'), { portalUrl: base, signingKey: signer?.publicKeyPem ?? '' });
  const pinned = `class-platform-mcp-${buildMcpPackageVersion()}.tgz`;
  const pack = buildMcpPackage(join(dir, pinned), { portalUrl: base });
  copyFileSync(join(dir, pinned), join(dir, 'class-platform-mcp.tgz'));
  writeFileSync(join(dir, 'mcp-version.json'), `${JSON.stringify({ version: pack.version, url: `${base}/${pinned}`, alias: `${base}/class-platform-mcp.tgz`, sha256: pack.sha256, kitVersion: pack.kitVersion }, null, 2)}\n`);
  if (signer) writeFileSync(join(dir, 'signing-key.pub'), signer.publicKeyPem);
  const listed = [...pages.map(([name, path]) => [name, path]), ...['starter-kit.zip', pinned, 'class-platform-mcp.tgz', 'mcp-version.json', ...(signer ? ['signing-key.pub'] : [])].map((name) => [name, join(dir, name)])];
  const portalFiles = Object.fromEntries(listed.map(([name, path]) => [name, sha256(readFileSync(path))]).sort(([a], [b]) => (a < b ? -1 : 1)));
  // serial: every publish has a higher one than the one before (a teacher's rollback included), so a replayed old manifest is told apart from a rollback
  const signed = signer ? { serial: now(), keyId: fingerprint(signer.publicKeyPem) } : {};
  const manifest = Buffer.from(`${JSON.stringify({ kit: 'class-platform-starter', kitVersion: kit.version, ...signed, minKit: kit.minKit, scripts: kit.scripts, managed: kit.managed, zipSha256: kit.sha256, files: kit.files, portalFiles }, null, 2)}\n`);
  writeFileSync(join(dir, 'kit-version.json'), manifest);
  if (signer) { // the signature covers these exact bytes; it is checked here too, so a wrong key or a broken signer is never published
    const signature = signer.sign(manifest);
    if (!verifyBytes(manifest, signature, signer.publicKeyPem)) throw new Error('The kit signature does not verify with the signing key, so nothing was published.');
    writeFileSync(join(dir, 'kit-version.json.sig'), `${signature}\n`);
  }
  const files = ['starter-kit.zip', 'kit-version.json', ...(signer ? ['kit-version.json.sig', 'signing-key.pub'] : []), pinned, 'class-platform-mcp.tgz', 'mcp-version.json'];
  return { files, kitVersion: kit.version, mcpVersion: pack.version, signed: Boolean(signer), portalFiles };
}

const pemOf = (der) => `-----BEGIN PUBLIC KEY-----\n${der.toString('base64').match(/.{1,64}/g).join('\n')}\n-----END PUBLIC KEY-----\n`;

/** Signs with the platform's AWS KMS key through the AWS CLI. `aws` is (...args) => stdout text and carries the teacher's profile. */
export function kmsSigner(aws, keyId) {
  const publicKeyPem = pemOf(Buffer.from(JSON.parse(aws('kms', 'get-public-key', '--key-id', keyId)).PublicKey, 'base64'));
  return {
    publicKeyPem,
    sign(bytes) { // KMS signs a digest (a raw message may be at most 4096 bytes; the manifest is longer)
      const dir = mkdtempSync(join(tmpdir(), 'kit-sign-'));
      try {
        writeFileSync(join(dir, 'digest'), createHash('sha256').update(bytes).digest());
        return JSON.parse(aws('kms', 'sign', '--key-id', keyId, '--message', `fileb://${join(dir, 'digest').replace(/\\/g, '/')}`, '--message-type', 'DIGEST', '--signing-algorithm', 'ECDSA_SHA_256')).Signature;
      } finally { rmSync(dir, { recursive: true, force: true }); }
    },
  };
}

/** A throwaway key in memory: for tests and the local dev portal, never for a real platform. */
export function localSigner() {
  const { publicKey, privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
  return {
    publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }),
    sign: (bytes) => ecdsaSign('sha256', bytes, privateKey).toString('base64'),
  };
}

// the package's version is the one in starter/mcp/package.json (read here so the file name is known before it is built)
function buildMcpPackageVersion() {
  return JSON.parse(readFileSync(join(fileURLToPath(new URL('.', import.meta.url)), 'mcp', 'package.json'), 'utf8')).version;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [dir, url] = process.argv.slice(2);
  if (!dir || !url) {
    console.error('Usage: node starter/publish-files.mjs <folder> <portal url>');
    process.exitCode = 1;
  } else {
    const done = buildPublishFiles(resolve(dir), url, { signer: localSigner(), pages: portalPageFiles(resolve(dirname(fileURLToPath(import.meta.url)), '..')) }); // the dev portal signs with a throwaway key, so every local test exercises the signed path
    console.log(`kit ${done.kitVersion}, class-platform-mcp ${done.mcpVersion}: ${done.files.join(', ')}`);
  }
}
