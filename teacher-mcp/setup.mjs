// `class-platform-teacher-mcp setup`: add the server to VS Code and Antigravity (one entry called "class-platform-teacher"; other servers are never
// touched; a backup is made; a settings file with comments is left alone and the exact text is printed instead).
//
//   npx -y <release tarball or class-platform-teacher@<version>> editor [--yes] [--dry-run] [--editors vscode,antigravity|none]
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline/promises';
import { parseJsonc } from './jsonc.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const NAME = 'class-platform-teacher';
const PACKAGE = 'class-platform-teacher';
const packageVersion = () => {
  for (const file of [join(here, '..', 'package.json'), join(here, 'package.json')]) { // inside the npm package this file sits in mcp/, the package.json one level up
    try { const p = JSON.parse(readFileSync(file, 'utf8')); if (p.name === PACKAGE || p.name === 'class-platform-teacher-mcp') return p.version; } catch { /* try the next place */ }
  }
  return '0.0.0';
};

/** Where this package was released (stamped into release.json when it was built): a GitHub repository, or '' for the npm registry. */
const releaseRepo = () => {
  for (const file of [join(here, '..', 'release.json'), join(here, 'release.json')]) {
    try { const r = JSON.parse(readFileSync(file, 'utf8')); if (r.repo) return r.repo; } catch { /* try the next place */ }
  }
  return '';
};

/** What the editor runs: npx with the pinned release (a GitHub Release file when the package knows its repository, else the npm name). Windows editors need cmd /c to find npx. */
export function entryFor(version = packageVersion(), platform = process.platform, repo = releaseRepo()) {
  const source = repo ? `https://github.com/${repo}/releases/download/v${version}/${PACKAGE}-${version}.tgz` : `${PACKAGE}@${version}`;
  const npx = ['-y', source, 'mcp'];
  return platform === 'win32' ? { command: 'cmd', args: ['/c', 'npx', ...npx] } : { command: 'npx', args: npx };
}

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

export async function main(argv) {
  const has = (flag) => argv.includes(flag);
  const option = (flag) => (has(flag) ? argv[argv.indexOf(flag) + 1] : undefined);
  const say = (text = '') => console.log(text);
  const entry = entryFor();
  const wanted = (option('--editors') ?? 'all').split(',').map((x) => x.trim());
  const skipAll = wanted.includes('none');
  const found = editors().filter((e) => !skipAll && (wanted.includes('all') || wanted.includes(e.id)) && (existsSync(e.file) || existsSync(e.installedAt)));
  say('Class platform: teacher tools for your AI editor\n');
  if (!skipAll && !found.length) say('No VS Code or Antigravity settings were found on this computer. Add the entry by hand (below).');
  const rl = process.stdin.isTTY && !has('--yes') ? createInterface({ input: process.stdin, output: process.stdout }) : null;
  const manual = [];
  for (const editor of found) {
    const change = plan(editor, entry);
    say(`${editor.name}: ${editor.file}`);
    if (change.action === 'same') { say('  already set up.'); continue; }
    if (change.action === 'manual') { say(`  left alone: ${change.reason}.`); manual.push(change); continue; }
    say(`  will ${change.action === 'create' ? 'create the file with' : change.action === 'update' ? 'replace the entry' : 'add the entry'} "${NAME}":\n${JSON.stringify(change.wanted, null, 2).replace(/^/gm, '    ')}`);
    if (has('--dry-run')) { say('  (dry run: nothing written)'); continue; }
    const yes = has('--yes') || (rl ? /^(y|yes|)$/i.test((await rl.question(`  Add it to ${editor.name}? [Y/n] `)).trim()) : false);
    if (!yes) { say(rl ? '  skipped.' : '  skipped (no terminal to ask: run again with --yes).'); continue; }
    const backup = apply(change);
    say(`  done.${backup ? ` The old file is saved as ${backup}` : ''}`);
  }
  rl?.close();
  if (manual.length || skipAll || !found.length) { say(`\nPut this into the editor's MCP settings (inside "servers" for VS Code, "mcpServers" for Antigravity):`); say(JSON.stringify({ [NAME]: entry }, null, 2)); }
  say('\nNext: restart the editor and tell the AI:  check my AWS account for the class platform');
}
