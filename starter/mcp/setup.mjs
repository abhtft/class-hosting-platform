// `class-platform-mcp setup`: one command, once per computer.
//   1. saves the student's token in ~/.class-platform/token (masked prompt; checked with the portal; never inside a project)
//   2. finds the editors that are installed (VS Code, Antigravity), shows exactly what it will add to each one's MCP settings,
//      asks, makes a backup, and merges ONE entry called "class-platform" (other servers are never touched)
//
//   npx -y <portal>/class-platform-mcp.tgz setup [--yes] [--dry-run] [--no-token] [--editors vscode,antigravity|none] [--portal <url>]
//
// It never overwrites a file it cannot understand: a settings file with comments (JSONC) is left alone and the exact text to add is printed instead.
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { parseJsonc } from './jsonc.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const DEFAULT_PORTAL = 'https://deploy.example.edu';
const NAME = 'class-platform';

const packageVersion = () => { try { return JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')).version; } catch { return '0.0.0'; } };

/** What the editor runs to start the server: npx with the pinned tarball (Windows editors need cmd /c to find npx). */
export function entryFor(version, portal = DEFAULT_PORTAL, platform = process.platform) {
  const npx = ['-y', `${String(portal).replace(/\/$/, '')}/class-platform-mcp-${version}.tgz`];
  return platform === 'win32' ? { command: 'cmd', args: ['/c', 'npx', ...npx] } : { command: 'npx', args: npx };
}

/** The editors we know how to set up, with where their user-level MCP settings live and which shape they use. */
export function editors(env = process.env, platform = process.platform) {
  const home = homedir();
  const vscodeUser = platform === 'win32' ? join(env.APPDATA || join(home, 'AppData', 'Roaming'), 'Code', 'User')
    : platform === 'darwin' ? join(home, 'Library', 'Application Support', 'Code', 'User') : join(env.XDG_CONFIG_HOME || join(home, '.config'), 'Code', 'User');
  const gemini = join(home, '.gemini');
  const antigravityFile = [join(gemini, 'config', 'mcp_config.json'), join(gemini, 'antigravity', 'mcp_config.json')].find((f) => existsSync(f)) ?? join(gemini, 'config', 'mcp_config.json');
  return [
    { id: 'vscode', name: 'VS Code', installedAt: vscodeUser, file: join(vscodeUser, 'mcp.json'), key: 'servers', typed: true },
    { id: 'antigravity', name: 'Antigravity', installedAt: gemini, file: antigravityFile, key: 'mcpServers', typed: false },
  ];
}

/** The settings text a new file would hold (and what `npm run mcp-config` prints). */
export function configText({ json = false, packageVersion: version, portal = DEFAULT_PORTAL } = {}) {
  const whole = JSON.stringify({ mcpServers: { [NAME]: entryFor(version || packageVersion(), portal) } }, null, 2);
  return json ? whole : `Copy ONLY the JSON between the lines:\n----- copy from the next line -----\n${whole}\n----- up to the previous line -----`;
}

/** Work out what would change in one editor's settings. Pure: reads the file, writes nothing. */
export function plan(editor, entry) {
  const wanted = editor.typed ? { type: 'stdio', ...entry } : entry;
  if (!existsSync(editor.file)) return { editor, action: 'create', wanted, doc: { [editor.key]: { [NAME]: wanted } } };
  let parsed;
  try { parsed = parseJsonc(readFileSync(editor.file, 'utf8')); } catch { return { editor, action: 'manual', wanted, reason: 'the file is not valid JSON' }; }
  if (parsed.hadComments) return { editor, action: 'manual', wanted, reason: 'the file has comments, which saving would remove' };
  const doc = parsed.value && typeof parsed.value === 'object' && !Array.isArray(parsed.value) ? parsed.value : null;
  if (!doc) return { editor, action: 'manual', wanted, reason: 'the file is not a JSON object' };
  const servers = doc[editor.key] ?? {};
  if (typeof servers !== 'object' || Array.isArray(servers)) return { editor, action: 'manual', wanted, reason: `"${editor.key}" in the file is not an object` };
  if (JSON.stringify(servers[NAME]) === JSON.stringify(wanted)) return { editor, action: 'same', wanted };
  return { editor, action: servers[NAME] ? 'update' : 'add', wanted, doc: { ...doc, [editor.key]: { ...servers, [NAME]: wanted } } };
}

/** Write a planned change: a timestamped backup first (when the file exists), then the merged file. Returns the backup path or ''. */
export function apply(change) {
  mkdirSync(dirname(change.editor.file), { recursive: true });
  let backup = '';
  if (existsSync(change.editor.file)) {
    backup = `${change.editor.file}.bak-${new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)}`;
    copyFileSync(change.editor.file, backup);
  }
  writeFileSync(change.editor.file, `${JSON.stringify(change.doc, null, 2)}\n`);
  return backup;
}

async function latestVersion(portal) {
  try {
    const response = await fetch(`${portal}/mcp-version.json`, { signal: AbortSignal.timeout(3000) });
    if (response.ok) { const info = await response.json(); if (info.version) return info.version; }
  } catch { /* offline or no such file: use the version we are running */ }
  return packageVersion();
}

export async function main(argv) {
  const has = (flag) => argv.includes(flag);
  const option = (flag) => (has(flag) ? argv[argv.indexOf(flag) + 1] : undefined);
  const portal = String(option('--portal') || process.env.PLATFORM_PORTAL || DEFAULT_PORTAL).replace(/\/$/, '');
  const dryRun = has('--dry-run');
  const say = (text = '') => console.log(text);
  say('Class platform: one-time setup for this computer\n');

  // 1. the token, once for every project
  if (has('--no-token') || dryRun) {
    say('Token: skipped.');
  } else {
    const env = { ...process.env, PORTAL_URL: portal };
    const saved = spawnSync(process.execPath, [join(here, 'portal-deploy.mjs'), 'status'], { env, encoding: 'utf8' });
    if (saved.status === 0) {
      say(`Token: already saved and valid. (${saved.stdout.trim().split('\n')[0]})`);
    } else {
      const login = spawnSync(process.execPath, [join(here, 'portal-deploy.mjs'), 'login', '--global'], { env, stdio: 'inherit' });
      if (login.status !== 0) { say('\nSetup stopped: the token was not saved. Run the command again with the token your teacher gave you.'); process.exitCode = 1; return; }
    }
  }

  // 2. the editors
  const version = await latestVersion(portal);
  const entry = entryFor(version, portal);
  const wanted = (option('--editors') ?? 'all').split(',').map((x) => x.trim());
  const skipAll = wanted.includes('none');
  const found = editors().filter((e) => !skipAll && (wanted.includes('all') || wanted.includes(e.id)) && (existsSync(e.file) || existsSync(e.installedAt)));
  if (!skipAll && !found.length) say('\nNo VS Code or Antigravity settings were found on this computer. Install one, run this again, or add the entry by hand (see below).');
  const rl = process.stdin.isTTY && !has('--yes') ? createInterface({ input: process.stdin, output: process.stdout }) : null;
  const manual = [];
  for (const editor of found) {
    const change = plan(editor, entry);
    say(`\n${editor.name}: ${editor.file}`);
    if (change.action === 'same') { say('  already set up (nothing to change).'); continue; }
    if (change.action === 'manual') { say(`  left alone: ${change.reason}.`); manual.push(change); continue; }
    say(`  will ${change.action === 'create' ? 'create the file with' : change.action === 'update' ? 'replace the "class-platform" entry with' : 'add this entry to'} "${NAME}":\n${JSON.stringify(change.wanted, null, 2).replace(/^/gm, '    ')}`);
    if (dryRun) { say('  (dry run: nothing written)'); continue; }
    const yes = has('--yes') || (rl ? /^(y|yes|)$/i.test((await rl.question(`  Add it to ${editor.name}? [Y/n] `)).trim()) : false);
    if (!yes) { say(rl ? '  skipped.' : '  skipped (no terminal to ask: run again with --yes to apply).'); continue; }
    const backup = apply(change);
    say(`  done.${backup ? ` The old file is saved as ${backup}` : ''}`);
  }
  rl?.close();

  if (manual.length || skipAll || !found.length) {
    say(`\nTo add it yourself, put this entry into the editor's MCP settings (inside "servers" for VS Code, inside "mcpServers" for Antigravity):`);
    say(JSON.stringify({ [NAME]: entry }, null, 2));
  }
  say('\nNext: restart the editor (or refresh its MCP list). Then open an EMPTY folder and tell the AI:  set up my class project');
}
