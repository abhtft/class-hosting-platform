#!/usr/bin/env node
// Builds every file `node admin.mjs portal-publish` puts next to the portal pages for students, in one place so the real publish and the
// local dev portal (tests/dev_server.py) cannot differ:
//
//   starter-kit.zip, kit-version.json             the student kit and its version file (what `platform update` and the Start here card read)
//   class-platform-mcp-<version>.tgz              the MCP package, one file per version (old ones stay: editor settings point at them)
//   class-platform-mcp.tgz                        always the newest copy; only used for the one-time `setup` command
//   mcp-version.json                              { version, url, alias, sha256 }: what `setup` and the MCP `status` tool compare against
//
//   node starter/publish-files.mjs <folder> <portal url>
import { copyFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildKitZip } from './build-kit.mjs';
import { buildMcpPackage } from './build-mcp-package.mjs';

export function buildPublishFiles(dir, portalUrl) {
  const base = String(portalUrl).replace(/\/$/, '');
  mkdirSync(dir, { recursive: true });
  const kit = buildKitZip(join(dir, 'starter-kit.zip'), { portalUrl: base });
  writeFileSync(join(dir, 'kit-version.json'), `${JSON.stringify({ kit: 'class-platform-starter', kitVersion: kit.version, minKit: kit.minKit, scripts: kit.scripts, zipSha256: kit.sha256, files: kit.files }, null, 2)}\n`);
  const pinned = `class-platform-mcp-${buildMcpPackageVersion()}.tgz`;
  const pack = buildMcpPackage(join(dir, pinned), { portalUrl: base });
  copyFileSync(join(dir, pinned), join(dir, 'class-platform-mcp.tgz'));
  writeFileSync(join(dir, 'mcp-version.json'), `${JSON.stringify({ version: pack.version, url: `${base}/${pinned}`, alias: `${base}/class-platform-mcp.tgz`, sha256: pack.sha256, kitVersion: pack.kitVersion }, null, 2)}\n`);
  return { files: ['starter-kit.zip', 'kit-version.json', pinned, 'class-platform-mcp.tgz', 'mcp-version.json'], kitVersion: kit.version, mcpVersion: pack.version };
}

// the package's version is the one in starter/mcp/package.json (read here so the file name is known before it is built)
import { readFileSync } from 'node:fs';
function buildMcpPackageVersion() {
  return JSON.parse(readFileSync(join(fileURLToPath(new URL('.', import.meta.url)), 'mcp', 'package.json'), 'utf8')).version;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [dir, url] = process.argv.slice(2);
  if (!dir || !url) {
    console.error('Usage: node starter/publish-files.mjs <folder> <portal url>');
    process.exitCode = 1;
  } else {
    const done = buildPublishFiles(resolve(dir), url);
    console.log(`kit ${done.kitVersion}, class-platform-mcp ${done.mcpVersion}: ${done.files.join(', ')}`);
  }
}
