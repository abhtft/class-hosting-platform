// Real-AWS check of the KMS signer, without installing anything. Usage:
//   AWS_PROFILE=teacher-test node .temp/kms-check.mjs <kms key id>
// It signs a manifest longer than 4096 bytes through AWS KMS (DIGEST mode), verifies it with node:crypto, and checks a changed byte fails.
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const keyId = process.argv[2];
if (!keyId) { console.error('Usage: AWS_PROFILE=teacher-test node .temp/kms-check.mjs <kms key id>'); process.exit(1); }
const repo = resolve(import.meta.dirname, '..');
const { kmsSigner } = await import(pathToFileURL(resolve(repo, 'starter/publish-files.mjs')).href);
const { fingerprint, verifyBytes } = await import(pathToFileURL(resolve(repo, 'starter/template/tools/signing.mjs')).href);

const who = JSON.parse(execFileSync('aws', ['sts', 'get-caller-identity', '--output', 'json'], { encoding: 'utf8' }));
console.log(`Account ${who.Account} (profile ${process.env.AWS_PROFILE ?? '(default!)'}). Is this your TEST account? Ctrl+C now if not.`);
await new Promise((r) => setTimeout(r, 4000));

const aws = (...args) => execFileSync('aws', [...args, '--output', 'json'], { encoding: 'utf8' });
const signer = kmsSigner(aws, keyId);
const manifest = Buffer.from(JSON.stringify({ kitVersion: 'test', serial: 1, padding: 'x'.repeat(6000) }));
const signature = signer.sign(manifest);
console.log(`Key fingerprint: ${fingerprint(signer.publicKeyPem)}`);
console.log(`Signature verifies:          ${verifyBytes(manifest, signature, signer.publicKeyPem)}   (expected true)`);
console.log(`Changed byte verifies:       ${verifyBytes(Buffer.concat([manifest, Buffer.from(' ')]), signature, signer.publicKeyPem)}   (expected false)`);
