#!/usr/bin/env node
// Build the student kit into a folder:   node starter/build-kit.mjs <outDir> [--force]
//
// The kit = starter/template/ (the project skeleton)
//         + student/portal-deploy.mjs                -> portal-deploy.mjs            (single source: the same CLI the portal offers)
//         + student/class_ai.py / class_ai.mjs       -> backend/class_ai.py, variants/node-backend/class_ai.mjs
//         + student/class_storage.py / .mjs          -> backend/class_storage.py, variants/node-backend/class_storage.mjs
//         + starter/mcp/server.mjs                   -> tools/mcp.mjs
//         + starter/rules/AGENTS.md                  -> the rules, under the file names each AI editor reads (identical content)
//         + kit-manifest.json                        -> sha256 of every file, for `update` and integrity checks
//
// Nothing secret and nothing personal goes in: the kit is generic and can live in a public repository.
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyPortal } from './portal-url.mjs';
import { zipEntries } from './zip.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '..');

/** The rule files each AI editor reads. All carry the SAME rules, so it does not matter which one an editor prefers. */
export function ruleFiles(rules) {
  const description = "The class platform's rules for this project: layout, the class AI helper, limits, secrets, deploy. Use when building, changing or deploying anything here.";
  return {
    'AGENTS.md': rules, // the open standard: Codex, Cursor, Copilot, Windsurf, Antigravity and others read this
    'GEMINI.md': rules, // Gemini CLI and Antigravity
    'CLAUDE.md': `@AGENTS.md\n`, // Claude Code imports the shared rules
    '.claude/skills/class-platform/SKILL.md': `---\nname: class-platform\ndescription: ${description}\n---\n\n${rules}`,
    '.cursor/rules/class-platform.mdc': `---\ndescription: ${description}\nalwaysApply: true\n---\n\n${rules}`,
    '.github/copilot-instructions.md': rules, // GitHub Copilot
    '.windsurfrules': rules, // Windsurf
  };
}

function listFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? listFiles(path) : [path];
  });
}

export function buildKit(out, { force = false, portalUrl = '' } = {}) {
  out = resolve(out);
  if (existsSync(out) && readdirSync(out).length) {
    if (!force) throw new Error(`${out} is not empty. Use --force to replace its contents.`);
    rmSync(out, { recursive: true, force: true });
  }
  mkdirSync(out, { recursive: true });
  cpSync(join(here, 'template'), out, { recursive: true });

  const copy = (from, to) => {
    mkdirSync(dirname(join(out, to)), { recursive: true });
    cpSync(join(repo, from), join(out, to));
  };
  copy('student/portal-deploy.mjs', 'portal-deploy.mjs');
  copy('student/class_ai.py', 'backend/class_ai.py');
  copy('student/class_ai.mjs', 'variants/node-backend/class_ai.mjs');
  copy('student/class_storage.py', 'backend/class_storage.py');
  copy('student/class_storage.mjs', 'variants/node-backend/class_storage.mjs');
  copy('starter/mcp/server.mjs', 'tools/mcp.mjs'); // the MCP server: one source, also the entry of the class-platform-mcp package
  mkdirSync(join(out, 'tools'), { recursive: true });
  cpSync(join(here, 'zip.mjs'), join(out, 'tools', 'zip.mjs')); // `platform update` reads the kit zip with it

  const rules = readFileSync(join(here, 'rules', 'AGENTS.md'), 'utf8').replace(/\r\n/g, '\n');
  for (const [name, content] of Object.entries(ruleFiles(rules))) {
    mkdirSync(dirname(join(out, name)), { recursive: true });
    writeFileSync(join(out, name), content);
  }

  // Text files always get LF line endings, so the kit (and its manifest hashes) is identical whichever computer built it.
  for (const file of listFiles(out)) {
    const data = readFileSync(file);
    if (data.includes(0)) continue; // binary
    const text = data.toString('utf8');
    const fixed = applyPortal(text.replace(/\r\n/g, '\n'), portalUrl); // the real portal address replaces the source placeholder
    if (fixed !== text) writeFileSync(file, fixed);
  }

  const platform = JSON.parse(readFileSync(join(out, 'platform.json'), 'utf8'));
  const files = Object.fromEntries(listFiles(out).map((f) => [relative(out, f).split(sep).join('/'), createHash('sha256').update(readFileSync(f)).digest('hex')]).sort());
  writeFileSync(join(out, 'kit-manifest.json'), `${JSON.stringify({ kit: platform.kit, kitVersion: platform.kitVersion, files }, null, 2)}\n`);
  // what the portal publishes next to the zip (kit-version.json): the oldest kit it still fully supports, and the package.json scripts the kit owns
  const pkgScripts = JSON.parse(readFileSync(join(out, 'package.json'), 'utf8')).scripts ?? {};
  const scripts = Object.fromEntries((platform.managedScripts ?? []).filter((name) => name in pkgScripts).map((name) => [name, pkgScripts[name]]));
  return { out, version: platform.kitVersion, minKit: platform.minKit ?? '0', scripts, files: Object.keys(files).length + 1 };
}

/** Build the kit and write it as ONE zip file (deterministic: the same kit always gives the same bytes). Returns {version, sha256, files}. */
export function buildKitZip(zipFile, { portalUrl = '' } = {}) {
  const temp = mkdtempSync(join(tmpdir(), 'kit-zip-'));
  try {
    const kit = join(temp, 'kit');
    const { version, minKit, scripts } = buildKit(kit, { portalUrl });
    const entries = listFiles(kit).map((f) => ({ name: relative(kit, f).split(sep).join('/'), data: readFileSync(f) }));
    const zip = zipEntries(entries);
    mkdirSync(dirname(resolve(zipFile)), { recursive: true });
    writeFileSync(zipFile, zip);
    return { version, minKit, scripts, sha256: createHash('sha256').update(zip).digest('hex'), files: entries.length };
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const zipAt = process.argv.indexOf('--zip');
  const target = zipAt >= 0 ? null : process.argv[2];
  if (!target && zipAt < 0) {
    console.error('Usage: node starter/build-kit.mjs <outDir> [--force]   |   node starter/build-kit.mjs --zip <file.zip>');
    process.exit(1);
  }
  try {
    if (zipAt >= 0) {
      const result = buildKitZip(process.argv[zipAt + 1]);
      console.log(`Kit ${result.version} zipped (${result.files} files, sha256 ${result.sha256.slice(0, 12)}...)`);
    } else {
      const result = buildKit(target, { force: process.argv.includes('--force') });
      console.log(`Kit ${result.version} built in ${result.out} (${result.files} files)`);
    }
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
