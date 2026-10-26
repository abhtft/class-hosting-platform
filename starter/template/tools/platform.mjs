#!/usr/bin/env node
// platform: keep this project's rules and tools up to date with the class platform. Your own code is never touched.
//
//   node tools/platform.mjs status             which kit version you have and which one the portal offers
//   node tools/platform.mjs update             show what would change (nothing is written)
//   node tools/platform.mjs update --apply     write the changes
//   node tools/platform.mjs update --apply --force   also replace platform files you edited yourself
//
// What it can change: only the platform-managed files listed in platform.json ("managed"): the rules for your AI editor, tools/,
// the deploy workflow, portal-deploy.mjs and class_ai. Never frontend/, your backend code, .env files or anything else.
// Safety: it downloads over HTTPS from your portal, checks the zip against the hash the portal publishes, refuses any path that is
// not a managed file, and shows you the list before writing (nothing is written without --apply).
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { missingScripts } from './update-notice.mjs';
import { readZip } from './zip.mjs';

const args = process.argv.slice(2);
const command = args[0] ?? 'status';
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const root = resolve(option('--root', resolve(dirname(fileURLToPath(import.meta.url)), '..')));
const sha = (data) => createHash('sha256').update(data).digest('hex');
const readJson = (path, fallback = {}) => { try { return JSON.parse(readFileSync(join(root, path), 'utf8')); } catch { return fallback; } };

/** fail() ends the run by throwing, so the process exits by itself (calling process.exit() while network connections are still closing
 *  can print a scary libuv assertion on Windows with newer Node versions). */
class Stop extends Error {}
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

  const remoteInfo = JSON.parse((await download('kit-version.json')).toString('utf8'));
  console.log(`Your kit: ${platform.kitVersion ?? 'unknown'}   Portal kit: ${remoteInfo.kitVersion}`);
  if (command === 'status') {
    const lacking = Object.keys(missingScripts(root, remoteInfo));
    if (lacking.length) console.log(`package.json is missing kit scripts: ${lacking.join(', ')}  (run: node tools/platform.mjs update --apply)`);
    console.log(platform.kitVersion === remoteInfo.kitVersion && !lacking.length ? '✔ Up to date.' : platform.kitVersion === remoteInfo.kitVersion ? '' : 'A newer kit is available: run  node tools/platform.mjs update');
    return;
  }
  if (command !== 'update') fail('Usage: node tools/platform.mjs status | update [--apply] [--force]');

  const apply = args.includes('--apply');
  const force = args.includes('--force');
  const zip = await download('starter-kit.zip');
  if (sha(zip) !== remoteInfo.zipSha256) fail('The downloaded kit does not match the hash the portal published, so nothing was changed. Try again; if it repeats, tell your teacher.');
  let files;
  try { files = readZip(zip); } catch (error) { fail(`The kit zip could not be read: ${error.message}`); }

  const remotePlatform = JSON.parse(files.get('platform.json')?.toString('utf8') ?? '{}');
  const managed = remotePlatform.managed ?? [];
  if (!managed.length) fail('The portal kit lists no managed files, so nothing was changed.');
  const isManaged = (path) => managed.some((m) => (m.endsWith('/') ? path.startsWith(m) : path === m));
  const installed = readJson('kit-manifest.json', { files: {} }).files ?? {};

  const plan = [];
  for (const [path, data] of [...files].sort(([a], [b]) => (a < b ? -1 : 1))) {
    if (!isManaged(path)) continue;
    const target = join(root, path);
    if (!existsSync(target)) { plan.push({ path, data, state: 'new' }); continue; }
    const local = readFileSync(target);
    if (sha(local) === sha(data)) plan.push({ path, data, state: 'same' });
    else if (installed[path] && sha(local) !== installed[path] && !force) plan.push({ path, data, state: 'edited' });
    else plan.push({ path, data, state: 'update' });
  }
  const obsolete = Object.keys(installed).filter((p) => isManaged(p) && !files.has(p));

  // scripts: add the ones package.json lacks (never change or remove one the student has; only plain `node tools/...` commands from the portal)
  const newScripts = missingScripts(root, remoteInfo);
  const label = { new: 'new      ', update: 'changed  ', same: 'same     ', edited: 'EDITED by you (skipped; --force replaces it)' };
  for (const item of plan.filter((i) => i.state !== 'same')) console.log(`  ${label[item.state].padEnd(9)} ${item.path}`);
  for (const path of obsolete) console.log(`  no longer in the kit (left alone): ${path}`);
  const toWrite = plan.filter((i) => i.state === 'new' || i.state === 'update');
  const skipped = plan.filter((i) => i.state === 'edited');
  for (const [name, command] of Object.entries(newScripts)) console.log(`  script    npm run ${name}  (${command})`);
  console.log(`${toWrite.length} file(s) to write, ${plan.filter((i) => i.state === 'same').length} already current, ${skipped.length} skipped.`);

  if (!apply) {
    console.log(toWrite.length || platform.kitVersion !== remoteInfo.kitVersion || Object.keys(newScripts).length ? '\nNothing was written. Run again with --apply to update.' : '\nNothing to do.');
    return;
  }
  for (const item of toWrite) {
    mkdirSync(dirname(join(root, item.path)), { recursive: true });
    writeFileSync(join(root, item.path), item.data);
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
  writeFileSync(join(root, 'platform.json'), `${JSON.stringify({ ...platform, kitVersion: skipped.length ? platform.kitVersion : remoteInfo.kitVersion, managed }, null, 2)}\n`);
  console.log(`\n✔ Updated ${toWrite.length} file(s)${skipped.length ? `; ${skipped.length} edited file(s) kept as they are (kit version stays ${platform.kitVersion})` : ` to kit ${remoteInfo.kitVersion}`}. Run: npm run check`);
}

main().catch((error) => {
  if (!(error instanceof Stop)) console.error(error);
  process.exitCode = 1;
});
