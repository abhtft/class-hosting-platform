#!/usr/bin/env node
// Maintainer tool for the add-on catalogue (not part of the student kit):
//
//   node starter/addons/pin.mjs                 fill in the checksums of the skills and, for each module, the exact package list
//   node starter/addons/pin.mjs --skills-only   only the skills (works offline)
//   node starter/addons/pin.mjs --check         change nothing; exit 1 if the catalogue is out of date (needs the network for modules)
//   node starter/addons/pin.mjs --only <id>     only one add-on
//
// For a module it resolves the package in a scratch copy of the kit's frontend (package.json + package-lock.json, scripts turned off), then records every
// package that would be new or changed: name, exact version, sha512 integrity and licence. It stops if any of them wants an install script or has a licence
// the catalogue does not accept. `add` in the kit later installs the module and accepts it only if it brings exactly these packages.
import { mkdtempSync, readFileSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { licenseOk, textHash, validateCatalog } from '../template/tools/addons.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const template = resolve(here, '..', 'template');
const catalogFile = join(template, 'tools', 'addons', 'catalog.json');
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const only = args.includes('--only') ? args[args.indexOf('--only') + 1] : null;

function npm(cwd, list) {
  const result = spawnSync(`npm ${list.join(' ')}`, { cwd, shell: true, encoding: 'utf8', timeout: 300000 });
  if (result.status !== 0) throw new Error(`npm ${list.join(' ')} failed:\n${(result.stdout || '') + (result.stderr || '')}`);
  return result.stdout;
}
const nameOf = (key) => key.split('node_modules/').pop();
const lockOf = (dir) => JSON.parse(readFileSync(join(dir, 'package-lock.json'), 'utf8')).packages;

function resolveModule(entry) {
  const dir = mkdtempSync(join(tmpdir(), 'addon-pin-'));
  try {
    copyFileSync(join(template, 'frontend', 'package.json'), join(dir, 'package.json'));
    copyFileSync(join(template, 'frontend', 'package-lock.json'), join(dir, 'package-lock.json'));
    const before = lockOf(dir);
    npm(dir, ['install', '--package-lock-only', '--ignore-scripts', '--save-exact', '--no-audit', '--no-fund', `${entry.package}@${entry.version}`]);
    const after = lockOf(dir);
    const packages = [];
    for (const [key, info] of Object.entries(after)) {
      if (key === '') continue;
      const old = before[key];
      if (old && old.version === info.version && old.integrity === info.integrity) continue;
      const name = nameOf(key);
      if (info.hasInstallScript) throw new Error(`${entry.id}: ${name}@${info.version} has an install script. Such a package cannot be an add-on.`);
      if (!info.integrity) throw new Error(`${entry.id}: ${name}@${info.version} has no integrity checksum.`);
      if (!licenseOk(info.license)) throw new Error(`${entry.id}: ${name}@${info.version} has the licence "${info.license}", which is not accepted.`);
      packages.push({ name, version: info.version, integrity: info.integrity, license: info.license });
    }
    packages.sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version));
    const top = packages.find((p) => p.name === entry.package && p.version === entry.version);
    if (!top) throw new Error(`${entry.id}: ${entry.package}@${entry.version} did not resolve to that exact version.`);
    let bytes = 0;
    for (const p of packages) bytes += Number(JSON.parse(npm(dir, ['view', `${p.name}@${p.version}`, 'dist.unpackedSize', '--json'])) || 0);
    return { integrity: top.integrity, packages, installedKb: Math.max(1, Math.round(bytes / 1024)) };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const original = readFileSync(catalogFile, 'utf8');
const catalog = JSON.parse(original);
const read = (path) => { try { return readFileSync(join(template, 'tools', 'addons', path)); } catch { return null; } };

for (const entry of catalog.addons) {
  if (only && entry.id !== only) continue;
  if (entry.kind === 'skill') {
    const data = read(entry.file);
    if (!data) throw new Error(`${entry.id}: ${entry.file} is missing.`);
    entry.sha256 = textHash(data);
    entry.sizeKb = Math.max(1, Math.round(data.length / 1024));
  } else if (!flag('--skills-only')) {
    console.log(`Resolving ${entry.package}@${entry.version} ...`);
    Object.assign(entry, resolveModule(entry));
    console.log(`  ${entry.packages.length} package(s), about ${entry.installedKb} KB installed`);
  }
}

const problems = validateCatalog(catalog, read).filter((p) => !(flag('--skills-only') && /integrity|package list|installedKb/.test(p)));
if (problems.length) { console.error(`The catalogue has problems:\n  ${problems.join('\n  ')}`); process.exit(1); }
const next = `${JSON.stringify(catalog, null, 2)}\n`;
if (flag('--check')) {
  if (next !== original) { console.error('The catalogue is out of date. Run  node starter/addons/pin.mjs'); process.exit(1); }
  console.log('The catalogue is up to date.');
} else {
  writeFileSync(catalogFile, next);
  console.log('Catalogue written.');
}
