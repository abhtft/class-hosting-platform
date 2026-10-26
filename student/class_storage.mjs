// class_storage: save data and files from your backend with the class storage (Node 22, no packages needed).
//
//   import * as storage from './class_storage.mjs';
//
//   await storage.put('scores', 'alice', { points: 3 });        // save a record: any JSON up to 64 KB
//   await storage.get('scores', 'alice', null);                  // read it back (the default when it does not exist)
//   await storage.items('scores');                               // [{ id, value }, ...] (first 100; pass { after: lastId } for more)
//   await storage.del('scores', 'alice');                        // (also exported as storage.remove)
//
//   await storage.files.put('photo.png', bytes, 'image/png');    // save a file (up to 5 MB) from your function
//   await storage.files.uploadUrl('photo.png', 20000, 'image/png'); // or let the BROWSER send it: { url, fields } for a form POST
//   await storage.files.list(); await storage.files.url('photo.png'); await storage.files.delete('photo.png');
//
// On the platform it talks to the class storage with the key the platform gives your function (CLASS_STORAGE_URL, CLASS_STORAGE_KEY): your
// teacher switches it on and sets the size limits. On your computer (npm run dev) it keeps everything in the folder .class-storage/ (git-ignored),
// so you can build and test without a connection. Every problem throws a StorageError with a plain message, a .kind and a .status:
//
//   not_enabled    the teacher has not switched storage on for you          allowance_used  the operations allowance is used up for now
//   quota_exceeded the database or file space is full                       too_large       a value or file is over its size limit
//   token_inactive your token expired or was revoked                        bad_request     a name, id or size is not allowed
//   unavailable    the storage service did not answer (try again)           bad_key         the platform key was refused (tell your teacher)
//
// The key and your data are never logged by this helper.
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const FILE_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const MAX_VALUE_BYTES = 64 * 1024;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const TIMEOUT_MS = 8000;
const MESSAGES = {
  not_enabled: 'Class storage is not switched on for you. Ask your teacher.',
  allowance_used: 'The storage allowance is used up for now. It resets by itself, or ask your teacher.',
  quota_exceeded: 'Your storage is full. Delete something first, or ask your teacher for more space.',
  unavailable: 'The storage service did not answer. Try again in a moment.',
};

export class StorageError extends Error {
  constructor(kind, message, status = 0) {
    super(message || MESSAGES[kind] || kind);
    this.name = 'StorageError';
    this.kind = kind;
    this.status = status;
  }
}

function remote() {
  const url = process.env.CLASS_STORAGE_URL || '';
  const key = process.env.CLASS_STORAGE_KEY || '';
  if (url && key) return { url: url.replace(/\/$/, ''), key };
  if (process.env.AWS_LAMBDA_FUNCTION_NAME) throw new StorageError('not_enabled'); // on the platform but not switched on: no pretending with local files
  return null; // on your computer: the local folder
}

async function request(method, path, { body, query } = {}) {
  const { url, key } = remote();
  const target = url + path + (query && Object.keys(query).length ? `?${new URLSearchParams(query)}` : '');
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const response = await fetch(target, {
        method,
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json', 'User-Agent': 'class-storage/1' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
      const info = await response.json().catch(() => ({}));
      if (response.ok) return info;
      if (response.status >= 500 && attempt === 1) continue;
      throw new StorageError(info.error || (response.status >= 500 ? 'unavailable' : 'bad_request'), info.message, response.status);
    } catch (error) {
      if (error instanceof StorageError) throw error;
      if (attempt === 2) throw new StorageError('unavailable');
    }
  }
  throw new StorageError('unavailable');
}

function check(pattern, value, what) {
  if (typeof value !== 'string' || !pattern.test(value)) throw new StorageError('bad_request', `Invalid ${what}.`);
  return value;
}

// ---------- the local folder (npm run dev) ----------
const folder = () => {
  const path = resolve(process.env.CLASS_STORAGE_DIR || '.class-storage');
  mkdirSync(path, { recursive: true });
  return path;
};
const recordPath = (collection, id) => join(folder(), 'data', check(NAME_RE, collection, 'collection (lowercase letters, digits, - and _)'), `${check(ID_RE, id, 'id')}.json`);

// ---------- data ----------
export async function put(collection, id, value) {
  check(NAME_RE, collection, 'collection (lowercase letters, digits, - and _)');
  check(ID_RE, id, 'id (letters, digits, . - _)');
  if (remote()) return request('PUT', `/v1/data/${collection}/${id}`, { body: { value } });
  const encoded = JSON.stringify(value);
  const bytes = Buffer.byteLength(encoded);
  if (bytes > MAX_VALUE_BYTES) throw new StorageError('too_large', `A value can be up to ${MAX_VALUE_BYTES / 1024} KB.`);
  const path = recordPath(collection, id);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, encoded);
  return { id, bytes };
}

export async function get(collection, id, fallback = null) {
  check(NAME_RE, collection, 'collection');
  check(ID_RE, id, 'id');
  if (remote()) {
    try {
      return (await request('GET', `/v1/data/${collection}/${id}`)).value;
    } catch (error) {
      if (error.kind === 'not_found') return fallback;
      throw error;
    }
  }
  const path = recordPath(collection, id);
  return existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : fallback;
}

/** Up to `limit` records (at most 100), ordered by id. With more records, pass { after: id of the last one } for the next page. */
export async function items(collection, { limit = 100, after } = {}) {
  check(NAME_RE, collection, 'collection');
  const count = Math.max(1, Math.min(Number(limit) || 100, 100));
  if (remote()) return (await request('GET', `/v1/data/${collection}`, { query: { limit: String(count), ...(after ? { after } : {}) } })).items;
  const dir = join(folder(), 'data', collection);
  const ids = existsSync(dir) ? readdirSync(dir).map((n) => n.slice(0, -5)).sort() : [];
  return Promise.all(ids.filter((id) => after === undefined || id > after).slice(0, count).map(async (id) => ({ id, value: await get(collection, id) })));
}

export async function del(collection, id) {
  check(NAME_RE, collection, 'collection');
  check(ID_RE, id, 'id');
  if (remote()) return (await request('DELETE', `/v1/data/${collection}/${id}`)).deleted;
  const path = recordPath(collection, id);
  if (!existsSync(path)) return false;
  rmSync(path);
  return true;
}
export { del as remove };

// ---------- files ----------
export const files = {
  /** A one-time upload form target. The browser (or your function) POSTs a multipart form to url with every field in `fields`, file last. */
  async uploadUrl(name, size, contentType = 'application/octet-stream') {
    check(FILE_RE, name, 'file name (letters, digits, . - _, no folders)');
    if (remote()) return request('POST', '/v1/files/upload-url', { body: { name, size, content_type: contentType } });
    return { url: '', fields: {}, max_bytes: size, local: true }; // on your computer there is nothing to upload to: use files.put
  },

  /** Save bytes (Buffer, Uint8Array or string) as a file, up to 5 MB. Counts against your file space. */
  async put(name, data, contentType = 'application/octet-stream') {
    check(FILE_RE, name, 'file name (letters, digits, . - _, no folders)');
    const bytes = typeof data === 'string' ? Buffer.from(data) : Buffer.from(data);
    if (bytes.length > MAX_FILE_BYTES) throw new StorageError('too_large', `A file can be up to ${MAX_FILE_BYTES / 1048576} MB.`);
    if (!remote()) {
      const path = join(folder(), 'files', name);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, bytes);
      return { name, size: bytes.length };
    }
    const target = await this.uploadUrl(name, bytes.length, contentType);
    const form = new FormData();
    for (const [field, value] of Object.entries(target.fields)) form.append(field, value);
    form.append('file', new Blob([bytes], { type: contentType }), name); // S3 needs the file as the LAST field
    try {
      const response = await fetch(target.url, { method: 'POST', body: form, signal: AbortSignal.timeout(TIMEOUT_MS * 2) });
      if (!response.ok) throw new Error(String(response.status));
    } catch {
      throw new StorageError('unavailable', 'The file could not be uploaded. Try again.');
    }
    return { name, size: bytes.length };
  },

  async list() {
    if (remote()) return (await request('GET', '/v1/files')).files;
    const dir = join(folder(), 'files');
    return existsSync(dir) ? readdirSync(dir).sort().map((name) => ({ name, size: statSync(join(dir, name)).size })) : [];
  },

  /** A link that downloads the file for 5 minutes (works in <img src> and for fetch). On your computer: the local path. */
  async url(name) {
    check(FILE_RE, name, 'file name');
    if (remote()) return (await request('GET', `/v1/files/${encodeURIComponent(name)}`)).url;
    const path = join(folder(), 'files', name);
    if (!existsSync(path)) throw new StorageError('not_found', 'No such file.', 404);
    return `file:///${path.replace(/\\/g, '/').replace(/^\//, '')}`;
  },

  async delete(name) {
    check(FILE_RE, name, 'file name');
    if (remote()) return (await request('DELETE', `/v1/files/${encodeURIComponent(name)}`)).deleted;
    rmSync(join(folder(), 'files', name), { force: true });
    return true;
  },
};
