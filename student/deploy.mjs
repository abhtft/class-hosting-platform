#!/usr/bin/env node
// Builds your frontend and publishes it to the class hosting.
//
//   node deploy.mjs              build (npm run build) + deploy
//   node deploy.mjs --skip-build deploy the existing build folder
//   node deploy.mjs --dir out    deploy a specific folder
//
// Your key is read from DEPLOY_KEY (environment, .env.local or .env).
// No npm install needed - only Node 18+.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { deflateRawSync } from 'node:zlib';

// Teacher: set this once to the DeployApiUrl stack output before sharing the file.
const API_URL = process.env.DEPLOY_API_URL || 'https://<api-id>.execute-api.us-east-1.amazonaws.com';

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);

function fail(message) {
  console.error(`\n✖ ${message}`);
  process.exit(1);
}

function readKey() {
  if (process.env.DEPLOY_KEY) return process.env.DEPLOY_KEY.trim();
  for (const file of ['.env.local', '.env']) {
    if (!existsSync(file)) continue;
    const match = readFileSync(file, 'utf8').match(/^\s*DEPLOY_KEY\s*=\s*["']?([^"'\s]+)/m);
    if (match) return match[1];
  }
  fail('DEPLOY_KEY not found. Add this line to .env.local (never commit it):\n  DEPLOY_KEY=yourname.xxxxxxxx');
}

function build() {
  const pkg = existsSync('package.json') ? JSON.parse(readFileSync('package.json', 'utf8')) : {};
  if (!pkg.scripts?.build) return;
  console.log('▶ npm run build');
  const result = spawnSync('npm run build', { stdio: 'inherit', shell: true });
  if (result.status !== 0) fail('Build failed - fix the errors above and try again.');
}

function findBuildDir() {
  const dir = option('--dir');
  if (dir) return dir;
  const found = ['dist', 'build', 'out'].find((d) => existsSync(join(d, 'index.html')));
  if (!found) fail('No build folder with index.html found (looked in dist/, build/, out/). Use --dir <folder>.');
  return found;
}

function listFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? listFiles(path) : entry.isFile() ? [path] : [];
  });
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// Minimal ZIP writer (deflate, UTF-8 names) so students need no extra tools.
function zipDirectory(root) {
  const files = listFiles(root);
  const dosDate = ((2024 - 1980) << 9) | (1 << 5) | 1;
  const parts = [];
  const central = [];
  let offset = 0;

  for (const file of files) {
    const name = Buffer.from(relative(root, file).split(sep).join('/'));
    const data = readFileSync(file);
    const packed = deflateRawSync(data);
    const crc = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(8, 8);
    local.writeUInt16LE(dosDate, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(packed.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(name.length, 26);
    parts.push(local, name, packed);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt16LE(8, 10);
    entry.writeUInt16LE(dosDate, 14);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(packed.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);

    offset += local.length + name.length + packed.length;
  }

  const directory = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(directory.length, 12);
  end.writeUInt32LE(offset, 16);
  return { zip: Buffer.concat([...parts, directory, end]), count: files.length };
}

async function callApi(path, key) {
  const res = await fetch(API_URL.replace(/\/$/, '') + path, { method: 'POST', headers: { 'x-deploy-key': key } }).catch(
    (e) => fail(`Cannot reach the deploy API (${e.cause?.code || e.message}). Check your internet connection.`),
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) fail(body.error || body.message || `${path} failed (HTTP ${res.status})`);
  return body;
}

const key = readKey();
if (API_URL.includes('REPLACE_ME')) fail('API_URL in deploy.mjs is not set - ask your teacher for the updated file.');
if (!flag('--skip-build')) build();

const dir = findBuildDir();
const { zip, count } = zipDirectory(dir);
console.log(`▶ Packed ${count} files from ${dir}/ (${(zip.length / 1024).toFixed(0)} KB)`);

const { uploadUrl, maxBytes } = await callApi('/upload-url', key);
if (zip.length > maxBytes) fail(`Build is too large (${(zip.length / 1048576).toFixed(1)} MB, limit ${maxBytes / 1048576} MB).`);

const put = await fetch(uploadUrl, { method: 'PUT', body: zip });
if (!put.ok) fail(`Upload failed (HTTP ${put.status}).`);

console.log('▶ Publishing...');
const result = await callApi('/publish', key);
if (result.warning) console.warn(`\n⚠ ${result.warning}`);
console.log(`\n✔ Live at ${result.url}  (${result.files} files, may take ~30s to refresh everywhere)`);
