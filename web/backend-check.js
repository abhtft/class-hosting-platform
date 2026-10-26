// Pre-upload checks for a student's backend folder. Pure logic (no DOM), so it can be tested in Node.
// An item is {path, file} where file has .size. The server validates everything again; this only saves a round trip.
export const BACKEND_LIMITS = { files: 500, unzippedBytes: 50 * 1024 * 1024, zipBytes: 5 * 1024 * 1024 };

const ENTRIES = { 'index.mjs': 'Node 22', 'index.js': 'Node 22', 'app.py': 'Python 3.12' };
const JUNK = /(^|\/)(\.DS_Store|Thumbs\.db)$|(^|\/)__MACOSX\/|(^|\/)\.git\/|(^|\/)__pycache__\/|\.py[co]$/;
const MB = 1024 * 1024;

/** The folder the entry file is in: the dropped folder itself, or the single folder inside it. */
function findRoot(paths) {
  const has = (prefix) => Object.keys(ENTRIES).some((entry) => paths.has(prefix + entry));
  if (has('')) return '';
  const tops = new Set([...paths].filter((p) => p.includes('/')).map((p) => p.split('/')[0]));
  const loose = [...paths].some((p) => !p.includes('/'));
  if (!loose && tops.size === 1) {
    const prefix = `${[...tops][0]}/`;
    if (has(prefix)) return prefix;
  }
  return null;
}

export function analyzeBackend(rawItems) {
  const items = rawItems.filter((i) => !JUNK.test(i.path));
  const paths = new Set(items.map((i) => i.path));
  const checks = [];
  const add = (level, text) => checks.push({ level, text });
  const root = findRoot(paths);

  if (root === null) {
    add('error', items.length ? 'No entry file found. Add index.mjs (Node 22) or app.py (Python 3.12) at the top of your backend folder.' : 'No files selected.');
    return { ok: false, root: null, files: [], count: items.length, totalBytes: 0, checks };
  }
  const files = items.filter((i) => i.path.startsWith(root)).map((i) => ({ path: i.path.slice(root.length), file: i.file }));
  const names = new Set(files.map((f) => f.path));
  const entries = Object.keys(ENTRIES).filter((e) => names.has(e));
  const runtimes = new Set(entries.map((e) => ENTRIES[e]));
  const totalBytes = files.reduce((sum, f) => sum + f.file.size, 0);

  if (runtimes.size > 1) add('error', 'Include only one entry file: index.mjs or app.py, not both.');
  else add('ok', `Entry file ${entries[0]} found (${ENTRIES[entries[0]]})`);

  const secrets = files.filter((f) => /(^|\/)\.env(\..*)?$/.test(f.path)).map((f) => f.path);
  if (secrets.length) add('error', `Remove ${secrets.slice(0, 3).join(', ')}. Set secrets as environment variables below instead.`);
  else add('ok', 'No .env files');

  if (files.length > BACKEND_LIMITS.files) add('error', `Too many files (${files.length}). The limit is ${BACKEND_LIMITS.files}.`);
  else if (totalBytes > BACKEND_LIMITS.unzippedBytes) add('error', `The backend is ${(totalBytes / MB).toFixed(0)} MB. The limit is ${BACKEND_LIMITS.unzippedBytes / MB} MB.`);
  else add('ok', `${files.length} file${files.length === 1 ? '' : 's'}, ${totalBytes < MB ? `${Math.max(1, Math.round(totalBytes / 1024))} KB` : `${(totalBytes / MB).toFixed(1)} MB`}`);

  if (totalBytes > BACKEND_LIMITS.zipBytes) add('warn', `Over ${BACKEND_LIMITS.zipBytes / MB} MB before zipping. If the upload is refused, bundle your dependencies (for example with esbuild).`);
  if (files.some((f) => f.path.startsWith('node_modules/')) && !names.has('package.json')) add('warn', 'node_modules is included but package.json is not. That is fine, but double-check the entry file.');

  return { ok: !checks.some((c) => c.level === 'error'), root, files, count: files.length, totalBytes, checks };
}
