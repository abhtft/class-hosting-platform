// Pre-upload checks for the student's folder. Pure logic (no DOM) so it can be tested in Node.
// An item is {path, file} (file has .size and .text()), or {path: 'x/node_modules/', skipped: true} for a folder we did not read.
export const LIMITS = { files: 2000, unzippedBytes: 200 * 1024 * 1024, zipBytes: 50 * 1024 * 1024 };

const JUNK = /(^|\/)(\.DS_Store|Thumbs\.db|desktop\.ini)$|(^|\/)__MACOSX\//;
const BUILD_DIRS = ['dist', 'build', 'out'];
const SAFE_NAME = /^[A-Za-z0-9._@+~-]+$/;
const SOURCE_INDEX = /<script[^>]+src=["']?\.?\/?src\//i;
const SECRET = /(^|\/)\.env(\..*)?$/;
const MB = 1024 * 1024;

export const skippedFolders = ['node_modules', '.git'];

async function indexText(item) {
  return item ? (await item.file.text()).slice(0, 200_000) : '';
}

/** Find the folder that holds the built site: the dropped folder, a dist/build/out inside it, or one level down. */
async function findRoot(items, byPath) {
  let prefix = '';
  for (let depth = 0; depth < 3; depth++) {
    const here = items.filter((i) => i.path.startsWith(prefix) && !i.skipped).map((i) => i.path.slice(prefix.length));
    const dirs = new Set(here.filter((p) => p.includes('/')).map((p) => p.split('/')[0]));
    const files = here.filter((p) => !p.includes('/'));
    const built = BUILD_DIRS.find((d) => byPath.has(`${prefix}${d}/index.html`));
    const hasIndex = byPath.has(`${prefix}index.html`);
    if (hasIndex && !(built && SOURCE_INDEX.test(await indexText(byPath.get(`${prefix}index.html`))))) return prefix;
    if (built) return `${prefix}${built}/`;
    if (!files.length && dirs.size === 1) {
      prefix += `${[...dirs][0]}/`;
      continue;
    }
    break;
  }
  return null;
}

/** Returns {ok, root, files: [{path, file}], count, totalBytes, checks: [{level: 'ok'|'warn'|'error', text}]} */
export async function analyze(rawItems) {
  const items = rawItems.filter((i) => !JUNK.test(i.path));
  const real = items.filter((i) => !i.skipped);
  const byPath = new Map(real.map((i) => [i.path, i]));
  const checks = [];
  const add = (level, text) => checks.push({ level, text });
  const root = await findRoot(items, byPath);

  if (root === null) {
    add('error', real.length
      ? 'No index.html found. Upload your build folder (usually “dist” or “build”), not the whole project.'
      : 'No files selected.');
    return { ok: false, root: null, files: [], count: real.length, totalBytes: 0, checks };
  }

  const inRoot = (i) => i.path.startsWith(root);
  const files = real.filter(inRoot).map((i) => ({ path: i.path.slice(root.length), file: i.file }));
  const markers = items.filter((i) => i.skipped && inRoot(i)).map((i) => i.path.slice(root.length));
  const totalBytes = files.reduce((sum, f) => sum + f.file.size, 0);
  const index = byPath.get(`${root}index.html`);
  const html = await indexText(index);

  add('ok', root ? `Found index.html in “${root.replace(/\/$/, '')}”` : 'Found index.html');

  if (SOURCE_INDEX.test(html)) {
    add('error', 'This looks like your source code, not a build: index.html loads files from src/. Run “npm run build” and upload the dist folder.');
  } else {
    add('ok', 'index.html looks like a built site');
  }
  if (/(?:src|href)=["']https?:\/\/(?:localhost|127\.0\.0\.1)/i.test(html)) {
    add('warn', 'index.html points to localhost. Those files will not load for visitors.');
  }

  if (files.length > LIMITS.files) add('error', `Too many files (${files.length}). The limit is ${LIMITS.files}.`);
  else if (totalBytes > LIMITS.unzippedBytes) add('error', `Site is too large (${(totalBytes / MB).toFixed(0)} MB). The limit is ${LIMITS.unzippedBytes / MB} MB.`);
  else if (totalBytes > LIMITS.zipBytes) {
    add('warn', `The site is ${(totalBytes / MB).toFixed(0)} MB. Images and videos barely shrink when zipped, and the upload limit is ${LIMITS.zipBytes / MB} MB. Compress large images first if the upload is refused.`);
  } else add('ok', `${files.length} file${files.length === 1 ? '' : 's'}, ${totalBytes < MB ? `${Math.max(1, Math.round(totalBytes / 1024))} KB` : `${(totalBytes / MB).toFixed(1)} MB`}`);

  const forbidden = [...markers.map((m) => m.replace(/\/$/, '')), ...files.filter((f) => SECRET.test(f.path)).map((f) => f.path)];
  if (forbidden.length) add('error', `Remove ${forbidden.slice(0, 3).join(', ')}${forbidden.length > 3 ? '…' : ''} from the upload (never publish node_modules, .git or .env files).`);
  else add('ok', 'No node_modules, .git or .env files');

  const badNames = files.filter((f) => !f.path.split('/').every((part) => SAFE_NAME.test(part))).map((f) => f.path);
  const upper = files.filter((f) => /[A-Z]/.test(f.path)).map((f) => f.path);
  if (badNames.length) {
    add('error', `Rename files with spaces or special characters: ${badNames.slice(0, 3).join(', ')}${badNames.length > 3 ? ` (+${badNames.length - 3} more)` : ''}. Use letters, digits, “-”, “_” and “.”.`);
  } else if (upper.length) {
    add('warn', `${upper.length} file name${upper.length === 1 ? ' has' : 's have'} capital letters (e.g. ${upper[0]}). Links are case-sensitive online: Logo.png is not logo.png.`);
  } else {
    add('ok', 'File names are clean');
  }

  return { ok: !checks.some((c) => c.level === 'error'), root, files, count: files.length, totalBytes, checks };
}
