// Project sync: keep the SOURCE FILES of your project on the platform, so the same code is on every computer you use, and a team can share one project
// without everyone needing a GitHub account. It is not Git: it moves whole files and stops when two people changed the same file.
//
//   npm run push                        send the files you changed (new, changed, deleted) to your project
//   npm run pull                        get the files others (or you, on another computer) changed
//   npm run project                     your projects and what is different here (also:  npm run project -- status)
//   npm run pull -- --project team-1    work on a team project your teacher made (remembered for next time)
//   npm run pull -- --theirs            when a file conflicts, take the platform's copy (yours is kept as <file>.mine)
//   npm run push -- --mine              when a file conflicts, send yours over the platform's copy
//   npm run push -- --dry-run           show what would be sent; change nothing   (pull has --dry-run too)
//
// On a new computer: download the starter kit from the portal, open the folder, run  npm run login  and then  npm run pull  (the kit files come from the portal,
// everything else from your project).
//
// After the first yes (npm run push, or the editor tool push_project), every `npm run deploy` also saves the project's files here (never stopping the deploy);
//   npm run project -- --auto off    stops that (and  --auto on  starts it again).
// What is never synced: the kit's own files (they come from the portal), node_modules, .git, build output (dist, build, ...), secrets (.env, .env.local, keys),
// the knowledge/ books and anything listed in a file called .syncignore (one name, folder/ or *.ext per line). A file can be up to 5 MB; a project up to 50 MB and 500 files.
//
// This file belongs to the platform kit (refreshed with it). portal-deploy.mjs calls it; it can also be imported by tests.
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export const STATE_FILE = '.class-project.json';
export const MAX_FILE_BYTES = 5 * 1048576;
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'build', 'out', '.next', '.turbo', '.cache', 'coverage', '.venv', 'venv', '__pycache__', '.class-platform', '.idea', 'knowledge', '.kit-backup', '.class-storage', '.class-knowledge']);
const SKIP_NAMES = /^(\.env(\.(?!example$|sample$|template$).*)?|\.class-project\.json|\.class-token|\.ds_store|thumbs\.db|platform\.json|kit-manifest\.json|\.platform-notice\.json|.*\.(pem|key|p12|pfx|theirs|mine))$/i;
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');
const kb = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

// ---------- what is in the folder ----------
function managedRules(root) {
  try {
    return (JSON.parse(readFileSync(join(root, 'platform.json'), 'utf8')).managed || []).map(String);
  } catch {
    return [];
  }
}

function ignoreRules(root) {
  try {
    return readFileSync(join(root, '.syncignore'), 'utf8').split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
  } catch {
    return [];
  }
}

/** Is this relative path (forward slashes) left out? `managed` are the kit's own files; `rules` come from .syncignore. */
export function skipped(path, managed = [], rules = []) {
  const parts = path.split('/');
  if (parts.slice(0, -1).some((p) => SKIP_DIRS.has(p)) || SKIP_NAMES.test(parts[parts.length - 1])) return true;
  if (managed.some((m) => (m.endsWith('/') ? path.startsWith(m) : path === m))) return true;
  return rules.some((r) => (r.endsWith('/') ? path.startsWith(r) || path.includes(`/${r}`) : r.startsWith('*.') ? path.endsWith(r.slice(1)) : path === r || parts[parts.length - 1] === r));
}

/** The project's files in this folder: { files: Map(path -> { sha, size, abs }), warnings }. Never throws for one bad file. */
export function scan(root) {
  const managed = managedRules(root);
  const rules = ignoreRules(root);
  const files = new Map();
  const warnings = [];
  const walk = (dir, prefix) => {
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      const path = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name) && !skipped(`${path}/x`, managed, rules)) walk(join(dir, entry.name), path);
        continue;
      }
      if (!entry.isFile() || skipped(path, managed, rules)) continue;
      const abs = join(dir, entry.name);
      const size = statSync(abs).size;
      if (path.length > 200 || /[\\\x00-\x1f]/.test(path)) { warnings.push(`${path}: the name is not allowed, so it is skipped.`); continue; }
      if (size > MAX_FILE_BYTES) { warnings.push(`${path} is ${kb(size)}; a project file can be up to ${kb(MAX_FILE_BYTES)}, so it is skipped. Keep big files out of the project (add the name to .syncignore).`); continue; }
      files.set(path, { sha: sha256(readFileSync(abs)), size, abs });
    }
  };
  walk(root, '');
  return { files, warnings };
}

// ---------- what was synced last time ----------
export function loadState(root) {
  try {
    const state = JSON.parse(readFileSync(join(root, STATE_FILE), 'utf8'));
    return { project: state.project || 'personal', rev: state.rev || 0, base: state.base || {}, auto: typeof state.auto === 'boolean' ? state.auto : undefined, known: true };
  } catch {
    return { project: 'personal', rev: 0, base: {}, auto: undefined, known: false };
  }
}

/** Writes what was synced. `auto` (the student's answer to "save this project automatically after every deploy?": true, false or not asked yet) is kept as it is unless given. */
export function saveState(root, state) {
  const auto = state.auto !== undefined ? state.auto : loadState(root).auto;
  writeFileSync(join(root, STATE_FILE), `${JSON.stringify({ project: state.project, rev: state.rev, base: state.base, ...(auto === undefined ? {} : { auto }) }, null, 1)}\n`);
}

/** Remember the student's answer to "save this project on the platform automatically after every deploy?" (true, false). */
export function setAuto(root, value) {
  const state = loadState(root);
  saveState(root, { ...state, auto: Boolean(value) });
}

// ---------- the platform ----------
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

class Stop extends Error {}
const stop = (message) => { throw new Stop(message); };

async function remoteProject(call, project) {
  const { status, data } = await call('GET', `/api/student/projects/${encodeURIComponent(project)}`);
  if (status !== 200) stop(data.error || `The platform answered ${status}.`);
  return data;
}

async function upload(form, file) {
  const body = new FormData();
  for (const [k, v] of Object.entries(form.fields)) body.append(k, v);
  body.append('file', new Blob([readFileSync(file.abs)]), 'file'); // S3 needs the file as the LAST field
  const response = await fetch(form.url, { method: 'POST', body, signal: AbortSignal.timeout(120000) });
  if (!response.ok) stop(`An upload was refused (HTTP ${response.status}). Run the command again.`);
}

async function downloadTo(link, target) {
  const response = await fetch(link.url, { signal: AbortSignal.timeout(120000) });
  if (!response.ok) stop(`A download failed (HTTP ${response.status}). Run the command again.`);
  const data = Buffer.from(await response.arrayBuffer());
  if (sha256(data) !== link.sha256) stop('A downloaded file did not match its checksum, so it was not written. Run the command again.');
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(`${target}.part`, data);
  renameSync(`${target}.part`, target);
}

const batches = (list, size) => Array.from({ length: Math.ceil(list.length / size) }, (_, i) => list.slice(i * size, (i + 1) * size));

// ---------- pull ----------
/** What a pull does, per path. Pure. local/base/remote map path -> sha (remote also has by). first = no state file yet.
 *  Returns { get: [path], conflict: [path], removeLocal: [path], forget: [path], note: {path: text} } */
export function planPull(local, base, remote, { first = false, theirs = false } = {}) {
  const out = { get: [], conflict: [], removeLocal: [], forget: [], note: {} };
  for (const [path, r] of Object.entries(remote)) {
    const l = local[path];
    const b = base[path];
    if (l === r.sha256) continue;                                     // already the same
    if (!first && b === r.sha256) continue;                           // the platform's copy did not change; yours is a local edit to push
    if (l === undefined) { out.get.push(path); if (b) out.note[path] = 'you deleted it here, but it changed on the platform: restored'; continue; }
    if (first || l === b || theirs) { out.get.push(path); if (l !== b || first) out.note[path] = `your version is kept as ${path}.mine`; continue; }
    out.conflict.push(path);
  }
  for (const path of Object.keys(base)) {
    if (path in remote) continue;
    if (local[path] === undefined) out.forget.push(path);              // gone on both sides
    else if (local[path] === base[path]) out.removeLocal.push(path);   // deleted by someone else, unchanged here
    else { out.forget.push(path); out.note[path] = 'deleted on the platform but you changed it here: it stays and the next push sends it again'; }
  }
  return out;
}

export async function pull({ portalUrl, session, root = '.', project, theirs = false, dryRun = false, log = console.log }) {
  const call = http(portalUrl, session);
  const state = loadState(root);
  const wanted = project || state.project;
  const first = !state.known || wanted !== state.project;
  const remote = await remoteProject(call, wanted);
  const { files } = scan(root);
  const local = Object.fromEntries([...files].map(([p, f]) => [p, f.sha]));
  const base = first ? {} : state.base;
  const plan = planPull(local, base, remote.files, { first, theirs });
  const total = Object.keys(remote.files).length;
  log(`Project ${remote.project.id} (${remote.project.title}), revision ${remote.project.rev}, ${total} file${total === 1 ? '' : 's'}.`);
  if (!plan.get.length && !plan.conflict.length && !plan.removeLocal.length) {
    log('Nothing to pull: you are up to date.');
    if (!dryRun) saveState(root, { project: wanted, rev: remote.project.rev, base: { ...base, ...Object.fromEntries(Object.entries(remote.files).filter(([p, r]) => local[p] === r.sha256).map(([p, r]) => [p, r.sha256])) } });
    return { conflicts: [], got: 0 };
  }
  for (const path of plan.get) log(`  ${dryRun ? 'would get' : 'get'}   ${path}${plan.note[path] ? `   (${plan.note[path]})` : ''}`);
  for (const path of plan.removeLocal) log(`  ${dryRun ? 'would remove' : 'remove'}  ${path}   (deleted on the platform by someone else)`);
  for (const path of plan.conflict) log(`  CONFLICT ${path}   (changed by ${remote.files[path].by || 'someone'} and by you)`);
  for (const path of plan.forget) if (plan.note[path]) log(`  note    ${path}: ${plan.note[path]}`);
  if (dryRun) return { conflicts: plan.conflict, got: 0, dryRun: true };

  const next = { ...base };
  for (const path of Object.keys(remote.files)) if (local[path] === remote.files[path].sha256) next[path] = remote.files[path].sha256;
  const want = [...plan.get, ...plan.conflict];
  for (const group of batches(want, 100)) {
    const { status, data } = await call('POST', `/api/student/projects/${encodeURIComponent(wanted)}/download`, { paths: group });
    if (status !== 200) stop(data.error || `The platform answered ${status}.`);
    for (const path of group) {
      const target = join(root, ...path.split('/'));
      const isConflict = plan.conflict.includes(path);
      if (existsSync(target) && !isConflict && local[path] !== undefined && local[path] !== remote.files[path].sha256 && (first || theirs)) renameSync(target, `${target}.mine`);
      await downloadTo(data.files[path], isConflict ? `${target}.theirs` : target);
      if (!isConflict) next[path] = remote.files[path].sha256;
    }
  }
  for (const path of plan.removeLocal) { rmSync(join(root, ...path.split('/')), { force: true }); delete next[path]; }
  for (const path of plan.forget) delete next[path];
  saveState(root, { project: wanted, rev: remote.project.rev, base: next });
  if (plan.conflict.length) {
    log(`\n${plan.conflict.length} file${plan.conflict.length === 1 ? '' : 's'} changed in both places. The platform's copy was saved next to yours as <file>.theirs. Open both, keep what you want in your file, delete the .theirs copy, then run  npm run push -- --mine.`);
    log('Or take the platform\'s copy:  npm run pull -- --theirs   (yours is kept as <file>.mine).');
  }
  log(`Pulled ${plan.get.length + plan.conflict.length} file${plan.get.length + plan.conflict.length === 1 ? '' : 's'}.`);
  return { conflicts: plan.conflict, got: plan.get.length };
}

// ---------- push ----------
/** The changes a push sends. Pure. */
export function planPush(local, base) {
  const changes = [];
  for (const [path, f] of Object.entries(local)) if (base[path] !== f.sha) changes.push({ path, op: 'put', sha256: f.sha, size: f.size, base: base[path] ?? null });
  for (const path of Object.keys(base)) if (!(path in local)) changes.push({ path, op: 'delete', base: base[path] });
  return changes;
}

export async function push({ portalUrl, session, root = '.', project, mine = false, dryRun = false, remember = false, log = console.log, warn = console.warn }) {
  const call = http(portalUrl, session);
  if (remember && !dryRun) setAuto(root, true); // the student said yes: this project is saved now and after every deploy
  const state = loadState(root);
  const wanted = project || state.project;
  if (state.known && wanted !== state.project) stop(`This folder is synced with "${state.project}". Run  npm run pull -- --project ${wanted}  first, or work in another folder.`);
  const { files, warnings } = scan(root);
  for (const w of warnings) warn(`⚠ ${w}`);
  const changes = planPush(Object.fromEntries(files), state.base);
  if (!changes.length) { log('Nothing to push: the platform already has your files.'); return { pushed: 0, conflicts: [] }; }
  const puts = changes.filter((c) => c.op === 'put');
  log(`Project ${wanted === 'personal' ? 'personal' : wanted}: ${puts.filter((c) => c.base === null).length} new, ${puts.filter((c) => c.base !== null).length} changed, ${changes.length - puts.length} deleted.`);
  for (const c of changes) log(`  ${dryRun ? 'would send' : 'send'}  ${c.op === 'delete' ? 'delete ' : ''}${c.path}`);
  if (dryRun) return { pushed: 0, conflicts: [], dryRun: true };

  const base = { ...state.base };
  let rev = state.rev;
  const adopted = new Map(); // --mine: path -> the platform's current sha (or null), used as the base of the retry
  for (const group of batches(changes, 100)) {
    let attempt = 0;
    for (;;) {
      const sending = group.map((c) => (adopted.has(c.path) ? { ...c, base: adopted.get(c.path) } : c));
      const prepared = await call('POST', `/api/student/projects/${encodeURIComponent(wanted)}/prepare`, { changes: sending });
      let result = prepared;
      if (prepared.status === 200) {
        const forms = Object.entries(prepared.data.uploads);
        for (const part of batches(forms, 4)) {
          await Promise.all(part.map(([digest, form]) => upload(form, files.get(sending.find((c) => c.sha256 === digest).path))));
        }
        result = await call('POST', `/api/student/projects/${encodeURIComponent(wanted)}/commit`, { changes: sending });
      }
      if (result.status === 200) {
        rev = result.data.rev;
        for (const c of group) { if (c.op === 'delete') delete base[c.path]; else base[c.path] = c.sha256; }
        break;
      }
      if (result.status === 409 && result.data.error === 'conflict') {
        if (mine && attempt === 0) {
          attempt++;
          for (const k of result.data.conflicts) adopted.set(k.path, k.theirs ? k.theirs.sha256 : null);
          log(`  --mine: sending your version over the platform's for ${result.data.conflicts.length} file${result.data.conflicts.length === 1 ? '' : 's'}.`);
          continue;
        }
        saveState(root, { project: wanted, rev, base });
        for (const k of result.data.conflicts) log(`  CONFLICT ${k.path}   (changed by ${k.theirs?.by || 'someone'}${k.theirs ? '' : ' (deleted)'} since you last synced)`);
        log('\nNothing from this batch was sent. Run  npm run pull  to get their changes (conflicting files arrive as <file>.theirs), fix your files, then  npm run push  again (or  npm run push -- --mine  to send yours over theirs).');
        return { pushed: 0, conflicts: result.data.conflicts };
      }
      saveState(root, { project: wanted, rev, base });
      stop(result.data.error || `The platform answered ${result.status}.`);
    }
  }
  saveState(root, { project: wanted, rev, base });
  log(`Pushed ${changes.length} change${changes.length === 1 ? '' : 's'}. The project is now at revision ${rev}.`);
  return { pushed: changes.length, conflicts: [], rev };
}

// ---------- status ----------
export async function status({ portalUrl, session, root = '.', log = console.log }) {
  const call = http(portalUrl, session);
  const { status: code, data } = await call('GET', '/api/student/projects');
  if (code !== 200) stop(data.error || `The platform answered ${code}.`);
  const state = loadState(root);
  log('Your projects:');
  for (const p of data.projects) log(`  ${p.id === state.project ? '*' : ' '} ${p.id}${p.kind === 'team' ? `  (team: ${p.title}, with ${p.members.filter((m) => m).join(', ')})` : '  (only yours)'}   revision ${p.rev}, ${p.files} file${p.files === 1 ? '' : 's'}, ${kb(p.bytes)}${p.frozen ? '  FROZEN' : ''}`);
  const { files } = scan(root);
  const changes = planPush(Object.fromEntries(files), state.base);
  const mineChanged = changes.filter((c) => c.op === 'put' && c.base !== null).length;
  const mineNew = changes.filter((c) => c.op === 'put' && c.base === null).length;
  const gone = changes.filter((c) => c.op === 'delete').length;
  log(`\nThis folder is synced with "${state.project}"${state.known ? `, last at revision ${state.rev}` : ' (not synced yet: run  npm run pull  or  npm run push)'}.`);
  log(`Not yet sent: ${mineNew} new, ${mineChanged} changed, ${gone} deleted.`);
  const here = data.projects.find((p) => p.id === state.project);
  if (here && state.known && here.rev > state.rev) log(`The platform has newer changes (revision ${here.rev}): run  npm run pull.`);
  return { projects: data.projects, changes };
}

export { Stop };
