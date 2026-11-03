// Folder sync for class knowledge: put books in knowledge/<shelf>/ and they are uploaded, read and made searchable for your app.
//
//   knowledge/physics/chapter1.pdf        ->  shelf "physics", book "chapter1.pdf"
//   knowledge/notes/week1.md
//
//   npm run knowledge                      show the plan, then sync (new and changed files are uploaded and read in the background)
//   npm run knowledge -- --dry-run         show the plan only, change nothing
//   npm run knowledge -- status            every book on your shelves and whether it is ready
//   npm run knowledge -- --wait            wait until the books are read (a big PDF takes a few minutes)
//   npm run knowledge -- --prune           also remove books from the shelves of your folder that are no longer in it (never done by itself)
//   npm run knowledge -- --retry           try a book again that failed
//   npm run deploy                         syncs knowledge/ too (it never stops the deploy); --no-knowledge skips it
//
// Rules: a shelf name is the folder name (lowercase letters, digits, - and _, up to 40). Only PDF, text, Markdown and HTML files directly inside a shelf folder
// are read. A file that did not change (same content) is not uploaded or read again, so nothing is paid for twice. A changed file replaces the book of the
// same name once the new one is ready. Shelves that are not folders here (for example one your backend creates) are never touched.
// Only add books you may share: knowledge/ is git-ignored by default so it is not pushed to GitHub. The teacher sees counts, never your files.
//
// This file belongs to the platform kit (refreshed with it). portal-deploy.mjs calls it; it can also be imported by tests.
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

export const FOLDER = 'knowledge';
export const EXTENSIONS = ['pdf', 'txt', 'text', 'md', 'markdown', 'html', 'htm'];
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const FILE_RE = /^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,99}$/;
const PENDING = ['uploading', 'queued', 'processing'];
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');
const size = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

/** What is in the folder: { shelves: { name: [{ name, path, bytes, sha256 }] }, warnings: [] }. Reads files only to hash them. Never throws for a bad file or folder: it warns. */
export function scan(root, { uploadMb = 25 } = {}) {
  const base = join(root, FOLDER);
  const shelves = {};
  const warnings = [];
  for (const entry of readdirSync(base, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (entry.name.startsWith('.')) continue;
    if (!entry.isDirectory()) {
      warnings.push(`${FOLDER}/${entry.name} is not inside a shelf folder, so it is skipped. Put it in ${FOLDER}/<shelf>/ (for example ${FOLDER}/notes/${entry.name}).`);
      continue;
    }
    if (!NAME_RE.test(entry.name)) {
      warnings.push(`The folder ${FOLDER}/${entry.name} is not a valid shelf name (lowercase letters, digits, - and _, up to 40), so it is skipped. Rename it, for example ${FOLDER}/${entry.name.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'books'}.`);
      continue;
    }
    const files = [];
    for (const file of readdirSync(join(base, entry.name), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const where = `${FOLDER}/${entry.name}/${file.name}`;
      if (file.name.startsWith('.')) continue;
      if (file.isDirectory()) { warnings.push(`${where}/ is a folder inside a shelf: subfolders are not read.`); continue; }
      const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
      if (!EXTENSIONS.includes(ext)) { warnings.push(`${where}: only PDF, text, Markdown and HTML files are read, so it is skipped.`); continue; }
      if (!FILE_RE.test(file.name)) { warnings.push(`${where}: the file name may use letters, digits, spaces and . - _ ( ) only, up to 100 characters, so it is skipped. Rename it.`); continue; }
      const path = join(base, entry.name, file.name);
      const bytes = statSync(path).size;
      if (bytes === 0) { warnings.push(`${where} is empty, so it is skipped.`); continue; }
      if (bytes > uploadMb * 1048576) { warnings.push(`${where} is ${size(bytes)}; your limit is ${uploadMb} MB per file, so it is skipped. Split it into parts.`); continue; }
      files.push({ name: file.name, path, bytes, sha256: sha256(readFileSync(path)) });
    }
    shelves[entry.name] = files;
  }
  return { shelves, warnings };
}

/** Compare the local files of ONE shelf with the books the platform has for it. Pure. `docs` is the platform's list for that shelf (or [] for a new shelf).
 *  Returns { upload: [file], keep: [file], reading: [file], failed: [{ file, message }], remove: [doc], later: [doc], orphans: [doc] }:
 *   upload   new or changed files (and failed ones with --retry); remove: old copies and leftovers to delete NOW (the new copy is ready, or a failed/unfinished upload);
 *   later    old copies to delete once the new copy is ready; orphans: books the folder no longer has (deleted only with prune). */
export function reconcile(files, docs, { retry = false } = {}) {
  const out = { upload: [], keep: [], reading: [], failed: [], remove: [], later: [], orphans: [] };
  const names = new Set(files.map((f) => f.name));
  for (const doc of docs) if (!names.has(doc.filename)) out.orphans.push(doc);
  for (const file of files) {
    const same = docs.filter((d) => d.filename === file.name);
    const current = same.find((d) => d.sha256 === file.sha256 && d.state === 'ready')
      || same.find((d) => d.sha256 === file.sha256 && ['queued', 'processing'].includes(d.state))
      || same.find((d) => d.sha256 === file.sha256 && d.state === 'failed');
    const others = same.filter((d) => d !== current);
    const stale = others.filter((d) => d.state === 'failed' || d.state === 'uploading'); // leftovers: they hold a slot and are of no use
    const older = others.filter((d) => d.state === 'ready' || d.state === 'queued' || d.state === 'processing');
    out.remove.push(...stale);
    if (current?.state === 'ready') { out.keep.push(file); out.remove.push(...older); continue; }
    if (current && ['queued', 'processing'].includes(current.state)) { out.reading.push(file); out.later.push(...older); continue; }
    if (current?.state === 'failed' && !retry) { out.failed.push({ file, message: current.message || 'It could not be read.' }); continue; }
    if (current?.state === 'failed') out.remove.push(current);
    out.upload.push(file);
    out.later.push(...older);
  }
  return out;
}

function http(portalUrl, session) {
  return async function call(method, path, body) {
    let response;
    try {
      response = await fetch(portalUrl + path, { method, headers: { 'content-type': 'application/json', authorization: `Bearer ${session}` }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000) });
    } catch (error) {
      return { status: 0, data: { error: `Cannot reach the portal (${error.cause?.code || error.message}).` } };
    }
    return { status: response.status, data: await response.json().catch(() => ({})) };
  };
}

async function sendFile(target, file) {
  const form = new FormData();
  for (const [field, value] of Object.entries(target.fields)) form.append(field, value);
  form.append('file', new Blob([readFileSync(file.path)]), file.name); // S3 needs the file as the LAST field
  try {
    const response = await fetch(target.url, { method: 'POST', body: form, signal: AbortSignal.timeout(120000) });
    return response.ok ? null : `The upload was refused (HTTP ${response.status}).`;
  } catch (error) {
    return `The upload failed (${error.cause?.code || error.message}).`;
  }
}

/** Does this answer mean that no further upload can work right now (busy, allowance or budget used, shelf full, switched off, no connection)? A problem with
 *  one file only (too big, not allowed) does not stop the others. */
function endsUploads(status, message = '') {
  if ([0, 403, 429].includes(status)) return true;
  return status === 413 && !/file can be up to/i.test(message);
}

const pause = (ms) => new Promise((done) => setTimeout(done, ms));

/** The state of every book on the shelves of your folder (or all shelves with allShelves). Returns { shelves: { name: [doc] }, error? }. */
export async function status({ portalUrl, session, root = '.', allShelves = false }) {
  const call = http(portalUrl, session);
  const top = await call('GET', '/api/student/knowledge');
  if (top.status !== 200) return { shelves: {}, error: top.data.error || `HTTP ${top.status}` };
  const local = existsSync(join(root, FOLDER)) ? Object.keys(scan(root).shelves) : [];
  const names = top.data.collections.map((c) => c.name).filter((n) => allShelves || local.includes(n));
  const shelves = {};
  for (const name of names) shelves[name] = (await call('GET', `/api/student/knowledge/${name}/documents`)).data.documents || [];
  return { shelves, limits: top.data.knowledge.limits, usage: top.data.knowledge.usage };
}

/**
 * Sync the knowledge/ folder with your shelves. Returns a summary and never throws; every problem is in `problems` (and printed with warn).
 * Options: portalUrl, session, root, dryRun, prune, retry, wait (poll until books are read), waitSeconds, log, warn.
 */
export async function sync({ portalUrl, session, root = '.', dryRun = false, prune = false, retry = false, wait = false, waitSeconds = 600, log = console.log, warn = console.warn }) {
  const result = { skipped: false, uploaded: [], kept: 0, reading: [], failed: [], removed: [], orphans: [], waiting: [], problems: [], warnings: [] };
  const problem = (message) => { result.problems.push(message); warn(`⚠ ${message}`); };
  if (!existsSync(join(root, FOLDER))) { result.skipped = true; return result; }
  const call = http(portalUrl, session);
  const top = await call('GET', '/api/student/knowledge');
  if (top.status !== 200) { problem(`Books were not synced: ${top.data.error || `HTTP ${top.status}`}`); return result; }
  const limits = top.data.knowledge.limits;
  const local = scan(root, { uploadMb: limits.upload_mb });
  result.warnings = local.warnings;
  for (const warning of local.warnings) warn(`⚠ ${warning}`);
  const remoteShelves = new Set(top.data.collections.map((c) => c.name));
  const plans = {};
  for (const [name, files] of Object.entries(local.shelves)) {
    const docs = remoteShelves.has(name) ? (await call('GET', `/api/student/knowledge/${name}/documents`)).data.documents || [] : [];
    plans[name] = reconcile(files, docs, { retry });
  }
  const count = (key) => Object.values(plans).reduce((n, p) => n + p[key].length, 0);
  const shelfCount = Object.keys(local.shelves).length;
  if (!shelfCount) { log(`▶ Books: ${FOLDER}/ has no shelf folders yet. Put books in ${FOLDER}/<shelf>/ (for example ${FOLDER}/notes/week1.pdf).`); return result; }
  log(`▶ Books: ${shelfCount === 1 ? '1 shelf' : `${shelfCount} shelves`} in ${FOLDER}/: ${count('upload')} to upload, ${count('keep')} up to date, ${count('reading')} being read${count('failed') ? `, ${count('failed')} failed` : ''}.`);
  for (const [name, plan] of Object.entries(plans)) {
    for (const file of plan.upload) log(`  + ${name}/${file.name} (${size(file.bytes)})${remoteShelves.has(name) ? '' : ' (new shelf)'}`);
    for (const { file, message } of plan.failed) log(`  ✖ ${name}/${file.name} failed earlier: ${message} (run with --retry to try again)`);
    for (const doc of plan.orphans) log(`  ? ${name}/${doc.filename} is on your shelf but not in your folder${prune ? ': it will be removed' : ' (kept; --prune removes it)'}`);
  }
  result.failed = Object.entries(plans).flatMap(([name, p]) => p.failed.map(({ file, message }) => ({ shelf: name, name: file.name, message })));
  result.orphans = Object.entries(plans).flatMap(([name, p]) => p.orphans.map((d) => ({ shelf: name, name: d.filename })));
  result.kept = count('keep');
  if (dryRun) { log('Dry run: nothing was changed.'); return result; }

  // shelves and uploads, one at a time; a problem that will not go away by itself (busy, allowance, full) stops the uploads, anything else only skips that file
  let stop = false;
  const queued = [];
  for (const [name, plan] of Object.entries(plans)) {
    if (!plan.upload.length) continue;
    if (!remoteShelves.has(name)) {
      const made = await call('POST', '/api/student/knowledge/collections', { name, title: name });
      if (made.status !== 201 && made.status !== 409) { problem(`Shelf ${name} could not be created: ${made.data.error || `HTTP ${made.status}`}`); result.waiting.push(...plan.upload.map((f) => `${name}/${f.name}`)); continue; }
    }
    for (const doc of plan.remove) await call('DELETE', `/api/student/knowledge/${name}/documents/${doc.id}`); // leftovers first: they hold slots
    plan.remove = [];
    for (const file of plan.upload) {
      const label = `${name}/${file.name}`;
      if (stop) { result.waiting.push(label); continue; }
      const form = await call('POST', `/api/student/knowledge/${name}/documents`, { filename: file.name, size: file.bytes, sha256: file.sha256 });
      if (form.status !== 200) {
        problem(`${label} was not uploaded: ${form.data.error || `HTTP ${form.status}`}`);
        if (endsUploads(form.status, form.data.error)) { stop = true; result.waiting.push(label); }
        continue;
      }
      const failed = await sendFile(form.data, file);
      if (failed) { problem(`${label}: ${failed}`); continue; }
      const started = await call('POST', `/api/student/knowledge/${name}/documents/${form.data.doc_id}/start`, {});
      if (started.status !== 200) {
        await call('DELETE', `/api/student/knowledge/${name}/documents/${form.data.doc_id}`); // do not keep a slot for a book that never started
        problem(`${label} could not be started: ${started.data.error || `HTTP ${started.status}`}`);
        if (endsUploads(started.status, started.data.error)) { stop = true; result.waiting.push(label); }
        continue;
      }
      result.uploaded.push(label);
      queued.push({ shelf: name, file });
      log(`  ↑ ${label} uploaded, being read…`);
    }
  }
  for (const [name, plan] of Object.entries(plans)) for (const doc of plan.remove) { await call('DELETE', `/api/student/knowledge/${name}/documents/${doc.id}`); result.removed.push(`${name}/${doc.filename} (old copy)`); }
  if (prune) for (const [name, plan] of Object.entries(plans)) for (const doc of plan.orphans) { const gone = await call('DELETE', `/api/student/knowledge/${name}/documents/${doc.id}`); if (gone.status === 200) result.removed.push(`${name}/${doc.filename}`); else problem(`${name}/${doc.filename} could not be removed: ${gone.data.error || `HTTP ${gone.status}`}`); }

  // optionally wait for the reader, then the old copies of changed books can go
  const stillReading = async () => {
    const pending = [];
    for (const name of Object.keys(plans)) {
      const docs = (await call('GET', `/api/student/knowledge/${name}/documents`)).data.documents || [];
      for (const doc of docs) if (PENDING.includes(doc.state)) pending.push({ shelf: name, doc });
    }
    return pending;
  };
  const deadline = Date.now() + waitSeconds * 1000;
  if (wait && (queued.length || count('reading'))) {
    log('Waiting for the books to be read…');
    while (Date.now() < deadline && (await stillReading()).length) await pause(2000);
  }
  for (const [name, plan] of Object.entries(plans)) {
    if (!plan.later.length) continue;
    const docs = (await call('GET', `/api/student/knowledge/${name}/documents`)).data.documents || [];
    for (const old of plan.later) {
      const replaced = docs.some((d) => d.filename === old.filename && d.state === 'ready' && d.sha256 !== old.sha256);
      if (replaced) { await call('DELETE', `/api/student/knowledge/${name}/documents/${old.id}`); result.removed.push(`${name}/${old.filename} (old copy)`); }
    }
  }
  result.reading = (await stillReading()).map((p) => `${p.shelf}/${p.doc.filename}`);
  if (queued.length || result.reading.length) log(`${result.reading.length ? `${result.reading.length} book${result.reading.length === 1 ? ' is' : 's are'} still being read in the background (a big PDF takes a few minutes). ` : ''}Check:  npm run knowledge -- status`);
  if (result.waiting.length) log(`${result.waiting.length} file${result.waiting.length === 1 ? '' : 's'} waiting: run  npm run knowledge  again in a few minutes.`);
  return result;
}

/** The lines `npm run knowledge -- status` prints. */
export function statusLines(report) {
  if (report.error) return [`Could not read your shelves: ${report.error}`];
  const lines = [];
  for (const [name, docs] of Object.entries(report.shelves)) {
    lines.push(`${name}: ${docs.length} book${docs.length === 1 ? '' : 's'}`);
    for (const d of docs) lines.push(`  ${{ ready: '✔', failed: '✖' }[d.state] || '…'} ${d.filename}  ${d.state}${d.passages ? `, ${d.passages} passages` : ''}${d.message ? `: ${d.message}` : ''}`);
  }
  if (!lines.length) lines.push(`No shelves from ${FOLDER}/ yet. Put books in ${FOLDER}/<shelf>/ and run  npm run knowledge.`);
  if (report.usage && report.limits) lines.push(`Using ${report.usage.documents} of ${report.limits.documents} books and ${report.usage.passages.toLocaleString()} of ${report.limits.passages.toLocaleString()} passages.`);
  return lines;
}
