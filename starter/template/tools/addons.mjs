// Add-ons: teacher-approved skills (guides for your AI editor) and open-source modules (packages for your site).
//
//   npm run addons                    list what can be added, and what this project already has
//   npm run addons -- show <name>     what one add-on is, its licence and what it adds
//   npm run addons -- add <name>      add it to this project (a guide in skills/, and for a module the package in frontend/)
//   npm run addons -- remove <name>   take it out again
//   npm run addons -- verify          check the catalogue and what this project has against it
//
// Only add-ons in tools/addons/catalog.json can be added: the teacher approved them, with an exact version and a checksum. A module is installed with
// its scripts turned off, and only if every package it brings is exactly one the teacher approved; otherwise nothing is kept. What was added is written
// to addons.json (so another computer or a teammate can see it), and the guides go to skills/ (your AI editor reads them; they sync with the project).
//
// This file belongs to the platform kit (refreshed with it). Your AI editor's tools list_addons, add_addon and remove_addon run it.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const RECORD = 'addons.json';
export const SKILLS_DIR = 'skills';
export const MAX_SKILL_BYTES = 12 * 1024;
/** Licences the catalogue accepts. */
export const LICENSES = ['MIT', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'OFL-1.1', '0BSD', 'CC0-1.0', 'BlueOak-1.0.0'];
/** A skill is text for an AI: it may advise, it may not give orders about commands, links, secrets or the AI's own rules. */
export const BANNED = [
  [/https?:\/\//i, 'a web link'],
  [/\b(curl|wget|sudo|powershell|chmod|rm\s+-rf)\b/i, 'a shell command'],
  [/\beval\s*\(/i, 'eval('],
  [/\bmds_[A-Za-z0-9_-]+/, 'a platform token'],
  [/\.env\b/i, 'an env file'],
  [/\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\b/i, 'a request to ignore earlier instructions'],
  [/\bsystem\s+prompt\b/i, 'the system prompt'],
  [/\b(api\s*key|your\s+token|the\s+token|send\s+it\s+to)\b/i, 'a secret or where to send data'],
];

const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');
/** The checksum of a text file with LF line endings (the kit is built that way on every computer). */
export const textHash = (buffer) => sha256(Buffer.from(buffer.toString('utf8').replace(/\r\n/g, '\n')));
const ID_RE = /^[a-z0-9][a-z0-9-]{1,39}$/;
const PACKAGE_RE = /^(@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*$/;
const VERSION_RE = /^\d+\.\d+\.\d+$/;
const INTEGRITY_RE = /^sha512-[A-Za-z0-9+/]{86}==$/;

/** Is a licence text (an SPDX name or "(A OR B)") one of the accepted ones? "A OR B": one of them is enough; "A AND B": all must be. */
export function licenseOk(text) {
  const value = String(text || '').trim();
  if (!value) return false;
  const ids = value.replace(/[()]/g, ' ').split(/\s+(?:AND|OR)\s+|\s+/).filter((part) => part && part !== 'AND' && part !== 'OR');
  if (!ids.length) return false;
  return /\sAND\s/.test(value) ? ids.every((id) => LICENSES.includes(id)) : ids.some((id) => LICENSES.includes(id));
}

/** Problems with a catalogue (an empty list means it is fine). `read(path)` returns the bytes of a file next to the catalogue, or null. */
export function validateCatalog(catalog, read) {
  const problems = [];
  const add = (id, text) => problems.push(`${id}: ${text}`);
  if (!catalog || !Array.isArray(catalog.addons) || !catalog.addons.length) return ['The catalogue has no add-ons.'];
  const ids = new Set(catalog.addons.map((entry) => entry.id));
  const seen = new Set();
  for (const entry of catalog.addons) {
    const id = String(entry.id);
    if (!ID_RE.test(id)) { add(id, 'the id must be lowercase letters, digits and - (2 to 40 characters).'); continue; }
    if (seen.has(id)) add(id, 'the id is used twice.');
    seen.add(id);
    if (!['skill', 'module'].includes(entry.kind)) add(id, 'kind must be "skill" or "module".');
    if (!entry.title || String(entry.title).length > 60) add(id, 'a title (up to 60 characters) is needed.');
    if (!entry.description || String(entry.description).length > 220) add(id, 'a description (up to 220 characters) is needed.');
    if (!licenseOk(entry.license)) add(id, `the licence "${entry.license}" is not accepted (accepted: ${LICENSES.join(', ')}).`);
    if (!entry.source || typeof entry.source !== 'string') add(id, 'the source (where it comes from) is needed.');
    for (const other of entry.with ?? []) if (!ids.has(other)) add(id, `"with" names ${other}, which is not in the catalogue.`);
    if (entry.kind === 'skill') {
      if (!/^skills\/[a-z0-9-]+\.md$/.test(entry.file || '')) { add(id, 'file must be skills/<name>.md.'); continue; }
      const data = read(entry.file);
      if (!data) { add(id, `the file ${entry.file} is missing.`); continue; }
      if (data.length > MAX_SKILL_BYTES) add(id, `the file is ${data.length} bytes; the limit is ${MAX_SKILL_BYTES}.`);
      if (entry.sha256 !== textHash(data)) add(id, 'the checksum does not match the file (run node starter/addons/pin.mjs after editing).');
      const text = data.toString('utf8');
      for (const [pattern, what] of BANNED) if (pattern.test(text)) add(id, `the text contains ${what}, which skills may not contain.`);
    }
    if (entry.kind === 'module') {
      if (!PACKAGE_RE.test(entry.package || '')) add(id, 'the package name is not valid.');
      if (!VERSION_RE.test(entry.version || '')) add(id, 'the version must be exact (for example 1.2.3).');
      if (!INTEGRITY_RE.test(entry.integrity || '')) add(id, 'the integrity (sha512) is missing (run node starter/addons/pin.mjs).');
      const howto = String(entry.howto || '');
      if (howto.length < 40 || howto.length > 1800) add(id, 'howto (40 to 1800 characters) is needed: it tells the AI how to use the package.');
      for (const [pattern, what] of BANNED) if (pattern.test(howto)) add(id, `howto contains ${what}, which add-on text may not contain.`);
      const tree = Array.isArray(entry.packages) ? entry.packages : [];
      if (!tree.some((p) => p.name === entry.package && p.version === entry.version && p.integrity === entry.integrity)) add(id, 'the package list must include the package itself, with the same version and integrity.');
      for (const p of tree) {
        if (!PACKAGE_RE.test(p.name || '') || !VERSION_RE.test(p.version || '') || !INTEGRITY_RE.test(p.integrity || '')) add(id, `the package entry ${JSON.stringify(p.name)} is not valid.`);
        if (!licenseOk(p.license)) add(id, `${p.name}@${p.version} has the licence "${p.license}", which is not accepted.`);
      }
      if (!Number.isFinite(entry.installedKb) || entry.installedKb <= 0) add(id, 'installedKb is missing (run node starter/addons/pin.mjs).');
    }
  }
  return problems;
}

export function loadCatalog(root) {
  const base = join(root, 'tools', 'addons');
  const file = join(base, 'catalog.json');
  if (!existsSync(file)) throw new Error('The add-on catalogue is missing (tools/addons/catalog.json). Run  npm run platform -- update --apply  to restore the kit files.');
  const catalog = JSON.parse(readFileSync(file, 'utf8'));
  const read = (path) => { const target = join(base, path); return existsSync(target) ? readFileSync(target) : null; };
  return { catalog, read, base };
}

export function loadRecord(root) {
  try {
    const record = JSON.parse(readFileSync(join(root, RECORD), 'utf8'));
    return record && typeof record.added === 'object' && record.added ? record : { added: {} };
  } catch {
    return { added: {} };
  }
}
function saveRecord(root, record) {
  const sorted = Object.fromEntries(Object.entries(record.added).sort(([a], [b]) => a.localeCompare(b)));
  if (!Object.keys(sorted).length) { rmSync(join(root, RECORD), { force: true }); return; }
  writeFileSync(join(root, RECORD), `${JSON.stringify({ added: sorted }, null, 2)}\n`);
}

function platformFolder(root, key, fallback) {
  try { return JSON.parse(readFileSync(join(root, 'platform.json'), 'utf8'))[key] || fallback; } catch { return fallback; }
}

/** Every add-on with its state in this project: { entry, state: 'added' | 'available' }. */
export function list(root) {
  const { catalog } = loadCatalog(root);
  const record = loadRecord(root);
  return catalog.addons.map((entry) => ({ entry, state: record.added[entry.id] ? 'added' : 'available' }));
}

const sizeText = (kb) => (kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(kb))} KB`);

export function describe(entry) {
  const what = entry.kind === 'module'
    ? `a package for your site (${entry.package} ${entry.version}, about ${sizeText(entry.installedKb)} installed) with a guide for your AI editor`
    : 'a guide for your AI editor (it adds one file, skills/' + entry.id + '.md)';
  return `${entry.title} [${entry.id}]: ${entry.description}\n    Adds ${what}. Licence ${entry.license}.${entry.with?.length ? ` Goes well with: ${entry.with.join(', ')}.` : ''}`;
}

function guideText(entry) {
  const related = (entry.with ?? []).length ? `\nRelated add-ons: ${entry.with.join(', ')}.\n` : '';
  return `# ${entry.title} (add-on)\n\nThis project uses ${entry.package}@${entry.version} (licence ${entry.license}). It was added from the class platform's approved add-ons.\n\n${entry.howto}\n${related}`;
}

function findEntry(catalog, id) {
  const entry = catalog.addons.find((candidate) => candidate.id === id);
  if (!entry) throw new Error(`There is no add-on called "${id}". Run  npm run addons  to see the list.`);
  return entry;
}

// ---------- the npm side (replaceable in tests) ----------
function runNpm(cwd, args) {
  const result = spawnSync(`npm ${args.join(' ')}`, { cwd, shell: true, encoding: 'utf8', timeout: 300000 });
  return { ok: result.status === 0, out: `${result.stdout || ''}${result.stderr || ''}`.trim() };
}
const defaultNpm = {
  install: (cwd, spec) => runNpm(cwd, ['install', '--save-exact', '--ignore-scripts', '--no-audit', '--no-fund', spec]),
  uninstall: (cwd, name) => runNpm(cwd, ['uninstall', '--ignore-scripts', '--no-audit', '--no-fund', name]),
  restore: (cwd) => runNpm(cwd, ['ci', '--ignore-scripts', '--no-audit', '--no-fund']),
};

const nameOf = (key) => key.split('node_modules/').pop();
function readLock(frontend) {
  try { return JSON.parse(readFileSync(join(frontend, 'package-lock.json'), 'utf8')).packages ?? {}; } catch { return null; }
}

/** What an install changed in the lock file and is not approved: [] means every new or changed package is exactly one the teacher approved. */
export function unapproved(before, after, approved) {
  const bad = [];
  for (const [key, info] of Object.entries(after)) {
    if (key === '') continue;
    const old = before[key];
    if (old && old.version === info.version && old.integrity === info.integrity) continue;
    const name = nameOf(key);
    if (info.hasInstallScript) bad.push(`${name}@${info.version} wants to run an install script`);
    else if (!info.integrity || info.link) bad.push(`${name}@${info.version} does not come from the package registry with a checksum`);
    else if (!approved.some((p) => p.name === name && p.version === info.version && p.integrity === info.integrity)) bad.push(`${name}@${info.version} is not on the teacher's approved list for this add-on`);
  }
  return bad;
}

/** Add one add-on. Returns { ok, message }. Never throws for an expected problem (it returns ok:false and leaves the project as it was). */
export function add(root, id, { npm = defaultNpm } = {}) {
  const { catalog, read } = loadCatalog(root);
  const entry = findEntry(catalog, id);
  const problems = validateCatalog({ addons: [entry] }, read).filter((p) => !p.includes('"with" names'));
  if (problems.length) return { ok: false, message: `The catalogue entry for ${id} is not valid, so it was not added:\n  ${problems.join('\n  ')}\nRun  npm run platform -- update --apply  to get the teacher's current catalogue.` };
  const record = loadRecord(root);
  if (record.added[id]) return { ok: true, message: `${entry.title} is already in this project.` };
  const guide = join(root, SKILLS_DIR, `${id}.md`);
  if (existsSync(guide)) return { ok: false, message: `${SKILLS_DIR}/${id}.md already exists in your project and is not from an add-on. Rename or move it, then run the command again.` };

  let note = '';
  if (entry.kind === 'module') {
    const frontend = join(root, platformFolder(root, 'frontend', 'frontend'));
    const packageFile = join(frontend, 'package.json');
    const lockFile = join(frontend, 'package-lock.json');
    if (!existsSync(packageFile) || !existsSync(lockFile)) return { ok: false, message: `${platformFolder(root, 'frontend', 'frontend')}/package.json and package-lock.json are needed. Run  npm ci  inside ${platformFolder(root, 'frontend', 'frontend')}/ first.` };
    const before = readLock(frontend);
    if (!before) return { ok: false, message: 'The frontend package-lock.json cannot be read.' };
    const savedPackage = readFileSync(packageFile);
    const savedLock = readFileSync(lockFile);
    const undo = (why) => {
      writeFileSync(packageFile, savedPackage);
      writeFileSync(lockFile, savedLock);
      npm.restore(frontend);
      return { ok: false, message: `${entry.title} was NOT added: ${why}\nYour project is as it was before.` };
    };
    const done = npm.install(frontend, `${entry.package}@${entry.version}`);
    if (!done.ok) return undo(`npm could not install it.\n${done.out.split('\n').slice(-6).join('\n')}`);
    const after = readLock(frontend);
    if (!after) return undo('the lock file could not be read after the install.');
    const bad = unapproved(before, after, entry.packages);
    if (bad.length) return undo(`it would bring packages the teacher did not approve:\n  ${bad.slice(0, 6).join('\n  ')}${bad.length > 6 ? `\n  ...and ${bad.length - 6} more` : ''}\nTell your teacher; the catalogue may need a newer version of this add-on.`);
    const top = JSON.parse(readFileSync(packageFile, 'utf8')).dependencies?.[entry.package];
    if (top !== entry.version) return undo(`the package is recorded as "${top}" instead of exactly ${entry.version}.`);
    note = ` The package ${entry.package} ${entry.version} is in ${platformFolder(root, 'frontend', 'frontend')}/package.json.`;
  }

  const content = entry.kind === 'skill' ? readFileSync(join(root, 'tools', 'addons', entry.file)).toString('utf8').replace(/\r\n/g, '\n') : guideText(entry);
  mkdirSync(dirname(guide), { recursive: true });
  writeFileSync(guide, content);
  record.added[id] = { kind: entry.kind, ...(entry.kind === 'module' ? { package: entry.package, version: entry.version } : {}), guide: `${SKILLS_DIR}/${id}.md`, sha256: textHash(Buffer.from(content)) };
  saveRecord(root, record);
  return { ok: true, message: `Added ${entry.title}.${note} The guide is ${SKILLS_DIR}/${id}.md: ask your AI editor to read it before it builds.` };
}

/** Take one add-on out again. */
export function remove(root, id, { npm = defaultNpm } = {}) {
  const { catalog } = loadCatalog(root);
  const record = loadRecord(root);
  const have = record.added[id];
  if (!have) return { ok: false, message: `${id} is not in this project (see  npm run addons ).` };
  const entry = catalog.addons.find((candidate) => candidate.id === id);
  let note = '';
  if (have.kind === 'module') {
    const frontend = join(root, platformFolder(root, 'frontend', 'frontend'));
    const done = npm.uninstall(frontend, have.package);
    if (!done.ok) return { ok: false, message: `npm could not remove ${have.package}.\n${done.out.split('\n').slice(-6).join('\n')}` };
    note = ` The package ${have.package} is removed. If your own code imports it, remove those lines too.`;
  }
  const guide = join(root, have.guide);
  if (existsSync(guide)) {
    if (textHash(readFileSync(guide)) === have.sha256) {
      rmSync(guide);
      if (!readdirSync(dirname(guide)).length) rmSync(dirname(guide), { recursive: true }); // the skills folder goes with its last guide
    } else note += ` ${have.guide} was changed by you, so it was kept.`;
  }
  delete record.added[id];
  saveRecord(root, record);
  return { ok: true, message: `Removed ${entry?.title ?? id}.${note}` };
}

/** Check the catalogue, and this project against it. Returns { ok, lines }. */
export function verify(root) {
  const { catalog, read } = loadCatalog(root);
  const lines = [];
  const problems = validateCatalog(catalog, read);
  for (const p of problems) lines.push(`catalogue: ${p}`);
  const record = loadRecord(root);
  for (const [id, have] of Object.entries(record.added)) {
    const entry = catalog.addons.find((candidate) => candidate.id === id);
    if (!entry) lines.push(`${id}: no longer in the teacher's catalogue (it keeps working; no new projects can add it).`);
    if (!existsSync(join(root, have.guide))) lines.push(`${id}: the guide ${have.guide} is missing. Run  npm run addons -- remove ${id}  and add it again.`);
    if (have.kind === 'module') {
      try {
        const frontend = join(root, platformFolder(root, 'frontend', 'frontend'));
        const installed = JSON.parse(readFileSync(join(frontend, 'package.json'), 'utf8')).dependencies?.[have.package];
        if (installed !== have.version) lines.push(`${id}: ${have.package} is ${installed ?? 'not installed'} but ${have.version} was added. Run  npm ci  inside the frontend folder, or add it again.`);
      } catch { lines.push(`${id}: the frontend package.json cannot be read.`); }
    }
    if (entry && entry.kind === 'module' && have.version !== entry.version) lines.push(`${id}: the teacher now approves ${entry.version} (this project has ${have.version}).`);
  }
  return { ok: !lines.some((line) => !line.includes('no longer in') && !line.includes('now approves')), lines };
}

// ---------- the command ----------
function main(argv) {
  const args = [...argv];
  const rootAt = args.indexOf('--root');
  const root = resolve(rootAt >= 0 ? args.splice(rootAt, 2)[1] : resolve(here, '..'));
  const command = args[0] && !args[0].startsWith('-') ? args.shift() : 'list';
  const out = (text) => console.log(text);
  try {
    if (command === 'list') {
      const rows = list(root);
      out('Add-ons your teacher approved:\n');
      for (const kind of ['skill', 'module']) {
        out(kind === 'skill' ? 'Guides for your AI editor:' : '\nPackages for your site (each comes with a guide):');
        for (const { entry, state } of rows.filter((row) => row.entry.kind === kind)) out(`  ${state === 'added' ? '✔ added  ' : '          '}${describe(entry)}`);
      }
      out('\nAdd one with  npm run addons -- add <name>  (or ask your AI editor: "add the charts add-on").');
    } else if (command === 'show') {
      const { catalog } = loadCatalog(root);
      const entry = findEntry(catalog, args[0] || '');
      out(describe(entry));
      if (entry.kind === 'module') out(`\nPackages it brings (all approved, none runs an install script):\n${entry.packages.map((p) => `  ${p.name} ${p.version} (${p.license})`).join('\n')}`);
    } else if (command === 'add' || command === 'remove') {
      if (!args.length) throw new Error(`Say which one:  npm run addons -- ${command} <name>`);
      let failed = false;
      for (const id of args) {
        const result = command === 'add' ? add(root, id) : remove(root, id);
        out(result.message);
        if (!result.ok) failed = true;
      }
      if (failed) process.exitCode = 1;
    } else if (command === 'verify') {
      const result = verify(root);
      out(result.lines.length ? result.lines.join('\n') : 'The catalogue and this project are in order.');
      if (!result.ok) process.exitCode = 1;
    } else {
      throw new Error('Commands: list, show <name>, add <name>, remove <name>, verify.');
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2));
