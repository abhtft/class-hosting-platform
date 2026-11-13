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
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
export const RECORD = 'addons.json';
export const SKILLS_DIR = 'skills';
export const MAX_SKILL_BYTES = 12 * 1024;
/** An "app" add-on copies a few small files into the site and adds lines to its index.html. */
export const MAX_APP_FILES = 10;
export const MAX_APP_FILE_BYTES = 512 * 1024;
const APP_LINE_LIMIT = 300;
const MAX_APP_EDITS = 8;
const MAX_EDIT_LINES = 20;
/** The only files an add-on may edit: the starter's own entry points. */
const EDITABLE = ['frontend/src/App.tsx', 'backend/app.py', 'backend/index.mjs'];
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
    if (!['skill', 'module', 'app'].includes(entry.kind)) add(id, 'kind must be "skill", "module" or "app".');
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
    if (entry.kind === 'app') {
      const files = Array.isArray(entry.files) ? entry.files : [];
      if (!files.length || files.length > MAX_APP_FILES) add(id, `between 1 and ${MAX_APP_FILES} files are needed.`);
      for (const f of files) {
        if (!/^frontend\/(public|src)\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(f.to || '')) { add(id, `${f.to} must be a file directly in the site's public or src folder.`); continue; }
        if (!/^files\/[A-Za-z0-9/._-]+$/.test(f.from || '') || f.from.includes('..')) { add(id, `${f.from} must be a file under files/.`); continue; }
        const data = read(f.from);
        if (!data) { add(id, `the file ${f.from} is missing.`); continue; }
        if (data.length > MAX_APP_FILE_BYTES) add(id, `${f.from} is ${data.length} bytes; the limit is ${MAX_APP_FILE_BYTES}.`);
        if (f.sha256 !== fileHash(data)) add(id, `the checksum of ${f.from} does not match (run node starter/addons/pin.mjs after editing).`);
      }
      const page = entry.page;
      if (page) {
        if (page.file !== 'frontend/index.html') add(id, 'page.file must be frontend/index.html.');
        const lines = [...(page.head ?? []), ...(page.body ?? [])];
        if (!lines.length) add(id, 'page lines are needed (what to add to the site\'s index.html).');
        for (const line of lines) {
          if (typeof line !== 'string' || !line.trim() || line.length > APP_LINE_LIMIT) { add(id, 'every page line must be a short line of text.'); continue; }
          for (const [pattern, what] of BANNED) if (pattern.test(line)) add(id, `a page line contains ${what}.`);
        }
      }
      const edits = Array.isArray(entry.edits) ? entry.edits : [];
      if (!page && !edits.length) add(id, 'page lines or edits are needed (what the add-on changes in the project).');
      if (edits.length > MAX_APP_EDITS) add(id, `at most ${MAX_APP_EDITS} edits.`);
      for (const e of edits) {
        if (!EDITABLE.includes(e.file)) add(id, `${e.file} is not a file an add-on may edit (${EDITABLE.join(', ')}).`);
        if (e.needs !== undefined && !/^(backend|frontend)\/[A-Za-z0-9_.-]+$/.test(e.needs)) add(id, `needs ${e.needs} is not a valid project file.`);
        if (typeof e.find !== 'string' || !e.find || e.find.length > APP_LINE_LIMIT) add(id, 'every edit needs a find pattern (up to 300 characters).');
        else { try { new RegExp(e.find); } catch { add(id, `the find pattern ${e.find} is not valid.`); } }
        if (!['before', 'after'].includes(e.where)) add(id, 'every edit needs where: "before" or "after".');
        const lines = Array.isArray(e.lines) ? e.lines : [];
        if (!lines.length || lines.length > MAX_EDIT_LINES || !lines.some((line) => typeof line === 'string' && line.trim())) add(id, `every edit needs 1 to ${MAX_EDIT_LINES} lines of text.`);
        for (const line of lines) {
          if (typeof line !== 'string' || line.length > APP_LINE_LIMIT) { add(id, 'every edit line must be short text.'); continue; }
          for (const [pattern, what] of BANNED) if (pattern.test(line)) add(id, `an edit line contains ${what}.`);
        }
      }
      if (entry.needsAnyOf !== undefined && (!Array.isArray(entry.needsAnyOf) || entry.needsAnyOf.some((x) => !/^(backend|frontend)\/[A-Za-z0-9_.-]+$/.test(x)))) add(id, 'needsAnyOf must list project files.');
      const howto = String(entry.howto || '');
      if (howto.length < 40 || howto.length > 1800) add(id, 'howto (40 to 1800 characters) is needed: it tells the AI what the add-on does and what to change.');
      for (const [pattern, what] of BANNED) if (pattern.test(howto)) add(id, `howto contains ${what}, which add-on text may not contain.`);
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
    : entry.kind === 'app'
      ? `${entry.files.length} small files in your site${entry.page ? `, ${[...(entry.page.head ?? []), ...(entry.page.body ?? [])].length} lines in its index.html` : ''}${entry.edits?.length ? `, and ${entry.edits.length} short edits in the starter's own files (${[...new Set(entry.edits.map((e) => e.file))].join(', ')})` : ''}, with a guide for your AI editor`
      : 'a guide for your AI editor (it adds one file, skills/' + entry.id + '.md)';
  return `${entry.title} [${entry.id}]: ${entry.description}\n    Adds ${what}. Licence ${entry.license}.${entry.with?.length ? ` Goes well with: ${entry.with.join(', ')}.` : ''}`;
}

function guideText(entry) {
  const related = (entry.with ?? []).length ? `\nRelated add-ons: ${entry.with.join(', ')}.\n` : '';
  const origin = entry.kind === 'app'
    ? `This add-on was added from the class platform's approved add-ons (licence ${entry.license}).`
    : `This project uses ${entry.package}@${entry.version} (licence ${entry.license}). It was added from the class platform's approved add-ons.`;
  return `# ${entry.title} (add-on)\n\n${origin}\n\n${entry.howto}\n${related}`;
}

const sha256Hex = (buffer) => createHash('sha256').update(buffer).digest('hex');
/** The checksum of an add-on file: text is checked without its line endings (so a Windows checkout with CRLF gives the same value), binary as it is. */
const fileHash = (buffer) => (buffer.includes(0) ? sha256Hex(buffer) : textHash(buffer));
export const fileHashOf = fileHash;

/** The index.html lines an "app" add-on adds, placed before a closing tag, each only if the page does not have it yet. */
function insertLines(html, marker, lines) {
  const found = html.match(new RegExp(`^([ \\t]*)${marker}`, 'm'));
  if (!found) return { error: `${marker} was not found in the page.` };
  if (html.split(marker).length !== 2) return { error: `${marker} appears more than once in the page.` };
  const present = new Set(html.split('\n').map((line) => line.trim()));
  const fresh = lines.filter((line) => !present.has(line));
  const indented = fresh.map((line) => `${found[1]}  ${line}`);
  return { html: html.replace(new RegExp(`^[ \\t]*${marker}`, 'm'), (match) => `${indented.map((line) => `${line}\n`).join('')}${match}`), inserted: indented };
}

/** Where an edit goes in the project: the add-on names starter paths (frontend/..., backend/...); a project may have renamed those two folders. */
const projectPath = (root, rel) => rel.replace(/^(frontend|backend)\//, (_, folder) => `${platformFolder(root, folder, folder)}/`);

/** Insert an edit's lines before or after the ONE line that matches its pattern, indented like that line. A page that already has the first line is left alone. */
function applyEdit(text, edit) {
  const lines = text.split('\n');
  const first = edit.lines.find((line) => line.trim());
  if (first && lines.some((line) => line.trim() === first.trim())) return { text, block: null };
  const pattern = new RegExp(edit.find);
  const hits = lines.map((line, i) => (pattern.test(line.replace(/\r$/, '')) ? i : -1)).filter((i) => i >= 0);
  if (hits.length !== 1) return { error: hits.length ? `the place to add it was found ${hits.length} times, not once` : 'the place to add it was not found' };
  const indent = lines[hits[0]].match(/^[ \t]*/)[0];
  const block = `${edit.lines.map((line) => (line ? indent + line : '')).join('\n')}\n`;
  lines.splice(edit.where === 'after' ? hits[0] + 1 : hits[0], 0, ...block.slice(0, -1).split('\n'));
  return { text: lines.join('\n'), block };
}

/** Add an "app" add-on: copy its files into the site, add its lines to index.html, and make its edits in the starter's entry files.
 *  Nothing is changed unless every step is possible; a refusal says why and shows the manual steps. */
function addApp(root, entry, record, guide, read) {
  const frontend = platformFolder(root, 'frontend', 'frontend');
  const refuse = (why) => ({ ok: false, message: `${entry.title} was NOT added: ${why}\nNothing was changed. To do it by hand (or ask your AI editor to):\n${entry.howto}` });
  if (entry.needsAnyOf?.length && !entry.needsAnyOf.some((p) => existsSync(join(root, projectPath(root, p))))) {
    return refuse(`it needs one of ${entry.needsAnyOf.map((p) => projectPath(root, p)).join(', ')}. Update the kit first (npm run platform -- update --apply), then add it again.`);
  }
  const indexFile = join(root, frontend, 'index.html');
  let original = null;
  let eol = '\n';
  let text = '';
  if (entry.page) {
    if (!existsSync(indexFile)) return { ok: false, message: `${frontend}/index.html is needed. Set the site up first (npm run dev, or the AI editor's init), then add this again.` };
    original = readFileSync(indexFile, 'utf8');
    eol = original.includes('\r\n') ? '\r\n' : '\n';
    text = original.replace(/\r\n/g, '\n');
  }
  const files = [];
  for (const f of entry.files) {
    const rel = `${frontend}/${f.to.split('/')[1]}/${basename(f.to)}`;
    const target = join(root, rel);
    if (existsSync(target)) {
      if (fileHash(readFileSync(target)) === f.sha256) { files.push({ rel, target, data: null, sha256: f.sha256, created: false }); continue; }
      return refuse(`${rel} already exists in your project and is different. Move your own file somewhere else first.`);
    }
    files.push({ rel, target, data: read(f.from), sha256: f.sha256, created: true });
  }
  let html = text;
  const inserted = { head: [], body: [] };
  if (entry.page) {
    for (const [marker, key] of [['</head>', 'head'], ['</body>', 'body']]) {
      const result = insertLines(html, marker, entry.page[key] ?? []);
      if (result.error) return refuse(result.error);
      html = result.html ?? html;
      inserted[key] = result.inserted ?? [];
    }
  }
  const edited = new Map(); // project path -> { target, original, text, eol, blocks }
  for (const edit of entry.edits ?? []) {
    const rel = projectPath(root, edit.file);
    const target = join(root, rel);
    if (!existsSync(target)) {
      if (entry.needsAnyOf?.includes(edit.file) && entry.needsAnyOf.some((p) => p !== edit.file && existsSync(join(root, projectPath(root, p))))) continue; // the project has the other runtime (Python or Node): this edit does not apply
      return refuse(`${rel} was not found.`);
    }
    if (edit.needs && !existsSync(join(root, projectPath(root, edit.needs)))) return refuse(`${projectPath(root, edit.needs)} is missing. Update the kit first (npm run platform -- update --apply), then add it again.`);
    if (!edited.has(rel)) {
      const raw = readFileSync(target, 'utf8');
      edited.set(rel, { target, original: raw, text: raw.replace(/\r\n/g, '\n'), eol: raw.includes('\r\n') ? '\r\n' : '\n', blocks: [] });
    }
    const state = edited.get(rel);
    const result = applyEdit(state.text, edit);
    if (result.error) return refuse(`${rel}: ${result.error}.`);
    state.text = result.text;
    if (result.block) state.blocks.push(result.block);
  }
  const written = [];
  try {
    for (const f of files) if (f.created) { mkdirSync(dirname(f.target), { recursive: true }); writeFileSync(f.target, f.data); written.push(f.target); }
    for (const state of edited.values()) if (state.blocks.length) writeFileSync(state.target, state.text.replace(/\n/g, state.eol));
    if (entry.page && html !== text) writeFileSync(indexFile, html.replace(/\n/g, eol));
    mkdirSync(dirname(guide), { recursive: true });
    writeFileSync(guide, guideText(entry));
  } catch (error) {
    for (const file of written) rmSync(file, { force: true });
    for (const state of edited.values()) writeFileSync(state.target, state.original);
    if (original !== null) writeFileSync(indexFile, original);
    rmSync(guide, { force: true });
    return { ok: false, message: `${entry.title} was NOT added (${error.message}). Your project is as it was before.` };
  }
  record.added[entry.id] = {
    kind: 'app',
    guide: `${SKILLS_DIR}/${entry.id}.md`,
    sha256: sha256Hex(Buffer.from(guideText(entry))),
    files: files.map((f) => ({ to: f.rel, sha256: f.sha256, created: f.created })),
    ...(entry.page ? { page: { file: `${frontend}/index.html`, inserted } } : {}),
    ...(edited.size ? { edits: [...edited.entries()].filter(([, state]) => state.blocks.length).map(([file, state]) => ({ file, blocks: state.blocks })) } : {}),
  };
  saveRecord(root, record);
  const count = files.filter((f) => f.created).length;
  const lineCount = inserted.head.length + inserted.body.length;
  const editCount = [...edited.values()].reduce((sum, state) => sum + state.blocks.length, 0);
  const parts = [];
  if (files.length) parts.push(`${count} new file${count === 1 ? '' : 's'} in ${[...new Set(files.map((f) => dirname(f.rel)))].join(' and ')}${files.length > count ? ` (${files.length - count} already there)` : ''}`);
  if (editCount) parts.push(`${editCount} edit${editCount === 1 ? '' : 's'} in ${[...edited.entries()].filter(([, state]) => state.blocks.length).map(([file]) => file).join(', ')}`);
  if (lineCount) parts.push(`${lineCount} line${lineCount === 1 ? '' : 's'} in ${frontend}/index.html`);
  return { ok: true, message: `Added ${entry.title}: ${parts.join('; ')}. Your AI editor should read ${SKILLS_DIR}/${entry.id}.md. ${entry.after ?? ''}`.trim() };
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

  if (entry.kind === 'app') return addApp(root, entry, record, guide, read);
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
  if (have.kind === 'app') {
    const kept = [];
    for (const f of have.files ?? []) {
      const target = join(root, f.to);
      if (!existsSync(target)) continue;
      if (f.created && fileHash(readFileSync(target)) === f.sha256) rmSync(target);
      else kept.push(f.to);
    }
    if (have.page) {
      const indexFile = join(root, have.page.file);
      if (existsSync(indexFile)) {
        const lines = new Set([...(have.page.inserted.head ?? []), ...(have.page.inserted.body ?? [])].map((line) => line.trim()));
        const original = readFileSync(indexFile, 'utf8');
        const keptLines = original.split('\n').filter((line) => !lines.has(line.trim()));
        if (keptLines.length !== original.split('\n').length) writeFileSync(indexFile, keptLines.join('\n'));
      }
      note = ` The page lines it added to ${have.page.file} are removed.`;
    }
    for (const edit of have.edits ?? []) {
      const target = join(root, edit.file);
      if (!existsSync(target)) continue;
      const raw = readFileSync(target, 'utf8');
      const lf = raw.replace(/\r\n/g, '\n');
      let next = lf;
      const stuck = [];
      for (const block of edit.blocks) {
        if (next.includes(block)) next = next.replace(block, '');
        else stuck.push(block);
      }
      if (next !== lf) writeFileSync(target, next.replace(/\n/g, raw.includes('\r\n') ? '\r\n' : '\n'));
      note += stuck.length ? ` Some lines it added to ${edit.file} were changed by you, so they were kept: remove them by hand if you want them gone.` : ` The lines it added to ${edit.file} are removed.`;
    }
    if (kept.length) note += ` ${kept.join(', ')} ${kept.length === 1 ? 'was' : 'were'} changed by you, so ${kept.length === 1 ? 'it was' : 'they were'} kept.`;
  }
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
    if (have.kind === 'app') {
      for (const f of have.files ?? []) {
        const target = join(root, f.to);
        if (!existsSync(target) || fileHash(readFileSync(target)) !== f.sha256) lines.push(`${id}: ${f.to} is missing or changed. Remove the add-on and add it again to restore it.`);
      }
      if (have.page) {
        const html = existsSync(join(root, have.page.file)) ? readFileSync(join(root, have.page.file), 'utf8') : '';
        const present = new Set(html.split('\n').map((line) => line.trim()));
        const missing = [...(have.page.inserted.head ?? []), ...(have.page.inserted.body ?? [])].filter((line) => !present.has(line.trim()));
        if (missing.length) lines.push(`${id}: ${have.page.file} no longer has ${missing.length} line${missing.length === 1 ? '' : 's'} the add-on added. Remove the add-on and add it again to restore ${missing.length === 1 ? 'it' : 'them'}.`);
      }
      for (const edit of have.edits ?? []) {
        const raw = existsSync(join(root, edit.file)) ? readFileSync(join(root, edit.file), 'utf8').replace(/\r\n/g, '\n') : '';
        if (edit.blocks.some((block) => !raw.includes(block))) lines.push(`${id}: ${edit.file} no longer has the lines the add-on added. Remove the add-on and add it again to restore them.`);
      }
    }
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
      for (const kind of ['skill', 'module', 'app']) {
        out(kind === 'skill' ? 'Guides for your AI editor:' : kind === 'module' ? '\nPackages for your site (each comes with a guide):' : '\nFeatures for your site (files and a guide):');
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
