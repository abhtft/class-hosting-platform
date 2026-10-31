// Kit signing: the teacher's platform signs kit-version.json (which holds the zip's hash and the hash of every file the kit owns) with a key that
// lives in AWS KMS, and a project accepts a kit update only if the signature matches the key it has pinned. Being able to write the portal's
// files is then not enough to change what runs on a student's computer.
//
// This one file is used by the student's tools/platform.mjs (to check), by the teacher's publish (to build and check what it signs) and by
// `verify`, so they can never disagree. ECDSA P-256 with SHA-256; the signature is the DER form AWS KMS returns, base64 encoded.
import { createHash, createPublicKey, verify } from 'node:crypto';

/** The key's fingerprint in groups of four (for people to compare), from a PEM public key. */
export function fingerprint(publicKeyPem) {
  const der = createPublicKey(publicKeyPem).export({ type: 'spki', format: 'der' });
  return createHash('sha256').update(der).digest('hex').slice(0, 20).toUpperCase().match(/.{4}/g).join('-');
}

/** Is `signatureBase64` a valid signature of exactly these bytes by this key? Never throws. */
export function verifyBytes(bytes, signatureBase64, publicKeyPem) {
  try {
    return verify('sha256', bytes, createPublicKey(publicKeyPem), Buffer.from(String(signatureBase64).trim(), 'base64'));
  } catch { return false; }
}

/**
 * Decide whether to accept the portal's kit-version.json.
 *   rawManifest   the exact bytes downloaded
 *   signature     the text of kit-version.json.sig, or null when the portal has none
 *   pin           the public key (PEM) this project trusts, or '' for a project that has none yet
 *   installedSerial  the serial of the kit this project has (0 when unknown)
 * Returns { ok, reason, serial }. A project with a pin never accepts an unsigned or wrongly signed manifest, nor an older one.
 */
export function judgeManifest({ rawManifest, signature, pin = '', installedSerial = 0 }) {
  let manifest;
  try { manifest = JSON.parse(Buffer.from(rawManifest).toString('utf8')); } catch { return { ok: false, reason: 'unreadable', serial: 0 }; }
  const serial = Number(manifest.serial) || 0;
  if (!pin) return { ok: true, reason: signature ? 'first-use' : 'unsigned-first-use', serial }; // nothing pinned yet: the first kit is trusted as it always was
  if (!signature) return { ok: false, reason: 'unsigned', serial };
  if (!verifyBytes(rawManifest, signature, pin)) return { ok: false, reason: 'bad-signature', serial };
  if (serial < Number(installedSerial || 0)) return { ok: false, reason: 'older', serial };
  return { ok: true, reason: 'verified', serial };
}

/** One sentence a student can act on for each refusal. */
export function refusalText(reason) {
  const what = {
    unsigned: 'is not signed',
    'bad-signature': 'is not signed by the key this project trusts',
    older: 'is older than the kit you already have',
    unreadable: 'could not be read',
  }[reason] ?? 'could not be checked';
  return `The kit from your portal ${what}. Nothing was changed and your current kit keeps working. Tell your teacher. (If your teacher says the platform moved to a new account, run  npm run trust.)`;
}
