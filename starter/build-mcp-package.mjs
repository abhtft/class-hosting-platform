#!/usr/bin/env node
// Builds the `class-platform-mcp` package as one .tgz that `npx -y <url>` can run (no npm registry, no git).
//
//   node starter/build-mcp-package.mjs <out.tgz>
//
// What goes in (package/ is the folder npm expects at the top of a tarball):
//   package.json, bin.mjs, server.mjs, setup.mjs, jsonc.mjs     starter/mcp/   (the package's own code; server.mjs is also the kit's tools/mcp.mjs)
//   portal-deploy.mjs                                            student/       (single source, the same file the kit and the portal use)
//   check.mjs, update-notice.mjs                                 starter/template/tools/
//   zip.mjs                                                      starter/zip.mjs
//   kit.zip                                                      the student starter, for the init_project tool (built with buildKitZip)
// The tarball is deterministic (fixed times and order), so the same sources always give the same bytes and the same hash.
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { buildKitZip } from './build-kit.mjs';
import { applyPortal } from './portal-url.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, '..');

/** A minimal ustar writer: regular files only, mode 0644, time 0. Names are limited to 100 characters (ours are short). */
export function tarEntries(entries) {
  const blocks = [];
  for (const { name, data } of entries) {
    if (Buffer.byteLength(name) > 100) throw new Error(`tar name too long: ${name}`);
    const header = Buffer.alloc(512);
    header.write(name, 0, 'utf8');
    header.write('0000644\0', 100);
    header.write('0000000\0', 108);
    header.write('0000000\0', 116);
    header.write(`${data.length.toString(8).padStart(11, '0')}\0`, 124);
    header.write('00000000000\0', 136);
    header.write('        ', 148); // the checksum is computed with these 8 bytes as spaces
    header.write('0', 156);
    header.write('ustar\0', 257);
    header.write('00', 263);
    let sum = 0;
    for (const byte of header) sum += byte;
    header.write(`${sum.toString(8).padStart(6, '0')}\0 `, 148);
    blocks.push(header, data, Buffer.alloc((512 - (data.length % 512)) % 512));
  }
  blocks.push(Buffer.alloc(1024));
  return Buffer.concat(blocks);
}

export function buildMcpPackage(outFile, { portalUrl = '' } = {}) {
  const temp = mkdtempSync(join(tmpdir(), 'mcp-package-'));
  try {
    const kitZip = join(temp, 'kit.zip');
    const kit = buildKitZip(kitZip, { portalUrl });
    const text = (path) => readFileSync(join(repo, path));
    const files = {
      'package.json': text('starter/mcp/package.json'),
      'bin.mjs': text('starter/mcp/bin.mjs'),
      'server.mjs': text('starter/mcp/server.mjs'),
      'setup.mjs': text('starter/mcp/setup.mjs'),
      'jsonc.mjs': text('starter/mcp/jsonc.mjs'),
      'portal-deploy.mjs': text('student/portal-deploy.mjs'),
      'check.mjs': text('starter/template/tools/check.mjs'),
      'update-notice.mjs': text('starter/template/tools/update-notice.mjs'),
      'zip.mjs': text('starter/zip.mjs'),
      'kit.zip': readFileSync(kitZip),
    };
    const lf = (buffer) => Buffer.from(applyPortal(buffer.toString('utf8').replace(/\r\n/g, '\n'), portalUrl)); // same bytes on every computer
    const entries = Object.keys(files).sort().map((name) => ({ name: `package/${name}`, data: name.endsWith('.zip') ? files[name] : lf(files[name]) }));
    const tgz = gzipSync(tarEntries(entries), { level: 9 });
    mkdirSync(dirname(resolve(outFile)), { recursive: true });
    writeFileSync(outFile, tgz);
    const version = JSON.parse(files['package.json'].toString('utf8')).version;
    return { file: outFile, version, kitVersion: kit.version, sha256: createHash('sha256').update(tgz).digest('hex'), bytes: tgz.length, files: entries.length };
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const out = process.argv[2];
  if (!out) {
    console.error('Usage: node starter/build-mcp-package.mjs <out.tgz>');
    process.exitCode = 1;
  } else {
    const result = buildMcpPackage(out);
    console.log(`class-platform-mcp ${result.version} (kit ${result.kitVersion} inside): ${result.files} files, ${result.bytes} bytes, sha256 ${result.sha256.slice(0, 12)}...`);
  }
}
