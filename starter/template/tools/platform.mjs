#!/usr/bin/env node
// platform: keep this project's rules and tools up to date with the class platform. Your own code is never touched.
//
//   node tools/platform.mjs status             which kit version you have and which one the portal offers
//   node tools/platform.mjs update             show what would change (nothing is written)
//   node tools/platform.mjs update --apply     write the changes
//   node tools/platform.mjs update --apply --force   also replace platform files you edited yourself
//   node tools/platform.mjs trust              pin a new signing key after your teacher moved the platform (shows both keys, asks you to confirm)
//   node tools/platform.mjs sync               make every platform file match the teacher's platform, no questions (what `npm run deploy` does by itself):
//                                              files you edited are replaced, and every replaced file is first copied to .kit-backup/<time>/
//
// What it can change: only the platform-managed files listed in platform.json ("managed"): the rules for your AI editor, tools/,
// the deploy workflow, portal-deploy.mjs and class_ai. Never frontend/, your backend code, .env files or anything else.
// Safety: it downloads over HTTPS from your portal, checks the zip against the hash the portal publishes, refuses any path that is
// not a managed file, and shows you the list before writing (nothing is written without --apply).
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { fingerprint, judgeManifest, refusalText, verifyBytes } from './signing.mjs';
import { missingScripts } from './update-notice.mjs';
import { readZip } from './zip.mjs';

const args = process.argv.slice(2);
const command = args[0] ?? 'status';
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const root = resolve(option('--root', resolve(dirname(fileURLToPath(import.meta.url)), '..')));
const KEEP_BACKUPS = 5; // how many .kit-backup/<time> folders are kept
const sha = (data) => createHash('sha256').update(data).digest('hex');
/** The same file with Windows line endings is the same file (a checkout may convert them), so compare text files with LF. */
const lf = (data) => (data.includes(0) ? data : Buffer.from(data.toString('utf8').replace(/\r\n/g, '\n')));
const readJson = (path, fallback = {}) => { try { return JSON.parse(readFileSync(join(root, path), 'utf8')); } catch { return fallback; } };

/** fail() ends the run by throwing, so the process exits by itself (calling process.exit() while network connections are still closing
 *  can print a scary libuv assertion on Windows with newer Node versions). */
class Stop extends Error {}
/** A kit not signed by the key this project trusts: exit code 3, so `npm run deploy` can tell it from a broken download and carry on with the current kit. */
class Refused extends Stop {}
function refuse(message) {
  console.error(`
✖ ${message}`);
  throw new Refused(message);
}
function fail(message) {
  console.error(`
✖ ${message}`);
  throw new Stop(message);
}

async function main() {
  const platform = readJson('platform.json');
  const portal = String(option('--portal', process.env.PLATFORM_PORTAL || platform.portal || '')).replace(/\/$/, '');
  if (!portal) fail('platform.json has no "portal" address.');

  async function download(path) {
    const response = await fetch(`${portal}/${path}`).catch((e) => fail(`Cannot reach ${portal} (${e.cause?.code || e.message}). Check your internet connection.`));
    if (!response.ok) fail(`${portal}/${path} answered HTTP ${response.status}.`);
    return Buffer.from(await response.arrayBuffer());
  }

  const rawInfo = await download('kit-version.json');
  const remoteInfo = JSON.parse(rawInfo.toString('utf8'));
  const signature = await fetch(`${portal}/kit-version.json.sig`).then((r) => (r.ok ? r.text() : null), () => null); // none on a platform that does not sign (yet)
  const pin = platform.signingKey ?? ''; // the public key this project trusts: set by the first signed kit, changed only by `trust`
  const judged = judgeManifest({ rawManifest: rawInfo, signature, pin, installedSerial: platform.serial ?? 0 });
  if (command === 'trust') return trust({ rawInfo, signature, download, platform });
  console.log(`Your kit: ${platform.kitVersion ?? 'unknown'}   Portal kit: ${remoteInfo.kitVersion}`);
  if (command === 'status') {
    console.log(!pin ? 'Kit signing: not pinned yet (your next kit update pins your teacher\'s key).' : judged.ok ? `✔ Kit signing: signed by your teacher's key ${fingerprint(pin)}.` : `✖ Kit signing: ${refusalText(judged.reason)}`);
    const lacking = Object.keys(missingScripts(root, remoteInfo));
    if (lacking.length) console.log(`package.json is missing kit scripts: ${lacking.join(', ')}  (run: node tools/platform.mjs update --apply)`);
    console.log(platform.kitVersion === remoteInfo.kitVersion && !lacking.length ? '✔ Up to date.' : platform.kitVersion === remoteInfo.kitVersion ? '' : 'A newer kit is available: run  node tools/platform.mjs update');
    return;
  }
  if (command !== 'update' && command !== 'sync') fail('Usage: node tools/platform.mjs status | update [--apply] [--force] | sync | trust');
  if (!judged.ok) refuse(refusalText(judged.reason)); // before anything is downloaded or written

  const syncing = command === 'sync'; // no questions: apply, replace edited files too, keep a copy of everything replaced
  const apply = syncing || args.includes('--apply');
  const force = syncing || args.includes('--force');
  const zip = await download('starter-kit.zip');
  if (sha(zip) !== remoteInfo.zipSha256) fail('The downloaded kit does not match the hash the portal published, so nothing was changed. Try again; if it repeats, tell your teacher.');
  let files;
  try { files = readZip(zip); } catch (error) { fail(`The kit zip could not be read: ${error.message}`); }

  const remotePlatform = JSON.parse(files.get('platform.json')?.toString('utf8') ?? '{}');
  // A project with no pin yet takes the key that came with the kit (it always trusted its first kit), but only if this manifest really is signed by it.
  let adoptedKey = '';
  if (!pin && signature && remotePlatform.signingKey) {
    if (!verifyBytes(rawInfo, signature, remotePlatform.signingKey)) refuse(refusalText('bad-signature'));
    adoptedKey = remotePlatform.signingKey;
  }
  const managed = remotePlatform.managed ?? [];
  if (!managed.length) fail('The portal kit lists no managed files, so nothing was changed.');
  const isManaged = (path) => managed.some((m) => (m.endsWith('/') ? path.startsWith(m) : path === m));
  const installed = readJson('kit-manifest.json', { files: {} }).files ?? {};

  const plan = [];
  for (const [path, data] of [...files].sort(([a], [b]) => (a < b ? -1 : 1))) {
    if (!isManaged(path)) continue;
    const target = join(root, path);
    if (!existsSync(target)) { plan.push({ path, data, state: syncing && path in installed ? 'deleted' : 'new' }); continue; } // sync leaves a file you (or your editor) deleted deleted
    const local = readFileSync(target);
    if (sha(lf(local)) === sha(lf(data))) plan.push({ path, data, state: 'same' });
    else if (installed[path] && sha(local) !== installed[path] && !force) plan.push({ path, data, state: 'edited' });
    else plan.push({ path, data, state: 'update' });
  }
  const obsolete = Object.keys(installed).filter((p) => isManaged(p) && !files.has(p));

  // scripts: add the ones package.json lacks (never change or remove one the student has; only plain `node tools/...` commands from the portal)
  const newScripts = missingScripts(root, remoteInfo);
  const label = { new: 'new      ', update: 'changed  ', same: 'same     ', edited: force ? 'replaced (you had edited it)' : 'EDITED by you (skipped; --force replaces it)', deleted: 'deleted by you (left deleted)' };
  for (const item of plan.filter((i) => i.state !== 'same' && !(syncing && i.state === 'deleted'))) console.log(`  ${label[item.state].padEnd(9)} ${item.path}`);
  for (const path of obsolete) console.log(`  no longer in the kit (left alone): ${path}`);
  const toWrite = plan.filter((i) => i.state === 'new' || i.state === 'update');
  const skipped = plan.filter((i) => i.state === 'edited');
  for (const [name, command] of Object.entries(newScripts)) console.log(`  script    npm run ${name}  (${command})`);
  console.log(`${toWrite.length} file(s) to write, ${plan.filter((i) => i.state === 'same').length} already current, ${skipped.length} skipped.`);

  if (!apply) {
    console.log(toWrite.length || platform.kitVersion !== remoteInfo.kitVersion || Object.keys(newScripts).length ? '\nNothing was written. Run again with --apply to update.' : '\nNothing to do.');
    return;
  }
  // every file that is replaced is first copied to .kit-backup/<time>/<path>, so nothing a student or an AI editor changed is ever lost
  const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14).replace(/^(\d{8})/, '$1-');
  const backupRoot = join('.kit-backup', stamp);
  let backedUp = 0;
  let editedReplaced = 0;
  for (const item of toWrite) {
    const target = join(root, item.path);
    if (existsSync(target)) {
      if (installed[item.path] && sha(readFileSync(target)) !== installed[item.path]) editedReplaced++;
      mkdirSync(dirname(join(root, backupRoot, item.path)), { recursive: true });
      copyFileSync(target, join(root, backupRoot, item.path));
      backedUp++;
    }
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, item.data);
    installed[item.path] = sha(item.data);
  }
  if (Object.keys(newScripts).length) {
    const raw = readFileSync(join(root, 'package.json'), 'utf8');
    const pkg = JSON.parse(raw);
    pkg.scripts = { ...(pkg.scripts ?? {}), ...newScripts };
    writeFileSync(join(root, 'package.json'), `${JSON.stringify(pkg, null, /\n(\t)/.test(raw) ? '\t' : 2)}\n`);
    console.log(`Added ${Object.keys(newScripts).length} script(s) to package.json: ${Object.keys(newScripts).join(', ')}`);
  }
  const manifest = readJson('kit-manifest.json', { files: {} });
  writeFileSync(join(root, 'kit-manifest.json'), `${JSON.stringify({ ...manifest, kitVersion: remoteInfo.kitVersion, files: installed }, null, 2)}\n`);
  writeFileSync(join(root, 'platform.json'), `${JSON.stringify({ ...platform, kitVersion: skipped.length ? platform.kitVersion : remoteInfo.kitVersion, managed, ...(pin || adoptedKey ? { signingKey: pin || adoptedKey, serial: judged.serial } : {}) }, null, 2)}\n`);
  if (backedUp) { // keep only the newest few backup folders (their names are timestamps), so repeated syncs never fill the disk
    const folders = readdirSync(join(root, '.kit-backup'), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort();
    for (const old of folders.slice(0, Math.max(0, folders.length - KEEP_BACKUPS))) rmSync(join(root, '.kit-backup', old), { recursive: true, force: true });
  }
  const saved = backedUp ? ` The ${backedUp} old file(s) are saved in ${backupRoot.replace(/\\/g, '/')}/${editedReplaced ? ` (${editedReplaced} of them had been edited by you)` : ''}.` : '';
  if (syncing) console.log(`\n✔ Your kit now matches your teacher's platform (kit ${remoteInfo.kitVersion}): ${toWrite.length} file(s) updated.${saved}`);
  else console.log(`\n✔ Updated ${toWrite.length} file(s)${skipped.length ? `; ${skipped.length} edited file(s) kept as they are (kit version stays ${platform.kitVersion})` : ` to kit ${remoteInfo.kitVersion}`}.${saved} Run: npm run check`);
}

/** `trust`: the platform now signs with a different key (it moved to a new account). Shows both fingerprints and asks the student to confirm with the teacher. */
async function trust({ rawInfo, signature, download, platform }) {
  const published = (await download('signing-key.pub')).toString('utf8');
  if (!signature || !verifyBytes(rawInfo, signature, published)) fail('The portal does not publish a kit signed by its own key, so there is nothing to trust. Tell your teacher.');
  const now = fingerprint(published);
  const old = platform.signingKey ? fingerprint(platform.signingKey) : '';
  if (old === now) { console.log(`✔ You already trust this key (${now}). Nothing to do.`); return; }
  if (!process.stdin.isTTY) fail('Run this yourself in a terminal (npm run trust): it needs your confirmation.');
  console.log(`The key your project trusts: ${old || '(none yet)'}\nThe key your portal now signs with: ${now}\n\nOnly continue if your teacher told you the platform moved or the key changed.`);
  const answer = await new Promise((done) => { const rl = createInterface({ input: process.stdin, output: process.stdout }); rl.question(`Type ${now.slice(0, 4)} to trust the new key: `, (a) => { rl.close(); done(a.trim().toUpperCase()); }); });
  if (answer !== now.slice(0, 4)) fail('Not confirmed. Nothing was changed.');
  writeFileSync(join(root, 'platform.json'), `${JSON.stringify({ ...platform, signingKey: published, serial: Number(JSON.parse(rawInfo.toString('utf8')).serial) || 0 }, null, 2)}\n`);
  console.log('✔ Done. Your next  npm run deploy  brings the kit in line with the new platform.');
}

main().catch((error) => {
  if (!(error instanceof Stop)) console.error(error);
  process.exitCode = error instanceof Refused ? 3 : 1;
});
