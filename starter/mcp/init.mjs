// Write the class starter kit into an EMPTY folder. Used by two callers that must behave the same: the editor tool `init_project` (server.mjs, after the
// student said yes) and the terminal command `npx <package> init` (bin.mjs). It never overwrites anything: a folder that already has files is refused, and a file
// that already exists (for example one the editor made) is left alone. The kit is the package's own kit.zip; readZip refuses any path that would leave the folder.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';

/** Names an editor or git may have put in a folder that is still "empty" for a new project. */
export const HARMLESS = new Set(['.git', '.vscode', '.idea', '.DS_Store', '.mcp.json', '.claude', '.cursor', '.gemini']);

const clean = (text) => String(text).replace(/\x1b\[[0-9;]*m/g, '').trim(); // eslint-disable-line no-control-regex

/** Folders entries that stop a new project from being written here (none when the folder is empty or missing). */
export function blockingEntries(root) {
  return existsSync(root) ? readdirSync(root).filter((name) => !HARMLESS.has(name)) : [];
}

/** Run `npm ci` in frontend/ (a minute). Returns { ok, out }. */
export function installFrontend(root) {
  return new Promise((resolveRun) => {
    const child = spawn('npm ci --no-audit --no-fund', { cwd: join(root, 'frontend'), shell: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (out += d));
    const timer = setTimeout(() => child.kill(), 300000);
    child.on('close', (code) => { clearTimeout(timer); resolveRun({ ok: code === 0, out }); });
  });
}

/**
 * Unpack the starter into `root`. `here` is the folder that holds kit.zip (the package folder). Returns { ok, text, written }.
 * `install: false` skips `npm ci` (tests, or a student who will run it later). `onStep` gets short progress lines (the terminal command prints them).
 */
export async function initProject({ root, here, install = true, onStep = () => {} }) {
  const zipFile = join(here, 'kit.zip');
  if (!existsSync(zipFile)) return { ok: false, text: 'init_project is only available in the class-platform-mcp package (this copy has no starter inside).', written: 0 };
  mkdirSync(root, { recursive: true });
  const blocking = blockingEntries(root);
  if (blocking.length) return { ok: false, text: `${root} is not empty (${blocking.slice(0, 5).join(', ')}${blocking.length > 5 ? ', ...' : ''}). Open an empty folder for a new project, or ask the student what they want.`, written: 0 };
  const { readZip } = await import('./zip.mjs');
  let written = 0;
  onStep('Writing the project files...');
  for (const [name, data] of readZip(readFileSync(zipFile))) { // readZip refuses any path that would leave the folder
    const target = join(root, name);
    if (existsSync(target)) continue; // a file the editor already made (for example .vscode/mcp.json) is left alone
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, data);
    written++;
  }
  let installText = '';
  if (install !== false) {
    onStep("Installing the site's packages (about a minute)...");
    const done = await installFrontend(root);
    installText = done.ok ? " The site's packages are installed." : ` The site's packages could not be installed (run "npm ci" inside frontend/ yourself): ${clean(done.out).slice(-300)}`;
  }
  return { ok: true, written, text: `Wrote ${written} files to ${root}.${installText}\nNext: run check_project, then try it with "npm run dev" (site on http://localhost:3000). The rules for the assistant are in AGENTS.md. To publish: in this folder's terminal run "npm run login" once (it saves the token in this project's .env.local), then deploy.` };
}

/**
 * The terminal command: `npx -y <package> init [folder] [--no-install] [--no-login]`. Sets up a class project in an empty folder (the current one, or the one named),
 * installs the site's packages, and offers `npm run login` right away. Nothing needs the AI editor.
 */
export async function main(argv, here) {
  const { resolve } = await import('node:path');
  const { spawnSync } = await import('node:child_process');
  const { createInterface } = await import('node:readline/promises');
  if (argv.includes('--help') || argv.includes('-h')) {
    console.log('class-platform-mcp init [folder] [--no-install] [--no-login]\n\n  Set up a class project in an EMPTY folder (the current folder when none is named): the site, a backend, the rules for AI editors, the helpers\n  and the check. Then it installs the site\'s packages and offers to log in with your teacher\'s token (npm run login).\n  --no-install   do not run npm ci in frontend/ (run it yourself later)\n  --no-login     do not offer the login at the end');
    return;
  }
  const folder = argv.find((a) => !a.startsWith('--')) || '.';
  const root = resolve(folder);
  console.log(`Class platform: setting up a project in ${root}`);
  const result = await initProject({ root, here, install: !argv.includes('--no-install'), onStep: (line) => console.log(line) });
  if (!result.ok) {
    console.error(`\n${result.text}`);
    process.exitCode = 1;
    return;
  }
  const where = folder === '.' ? '' : `  cd ${folder}\n`;
  console.log(`\nDone: ${result.written} files written.${result.text.includes('could not be installed') ? `\n${result.text.split('\n')[0].replace(/^Wrote \d+ files to [^.]*\.\s*/, '')}` : ''}`);
  const loginNow = async () => {
    if (argv.includes('--no-login') || !process.stdin.isTTY || !process.stdout.isTTY) return false;
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const answer = (await rl.question('\nLog in now? You need the token your teacher gave you. [Y/n] ')).trim().toLowerCase();
    rl.close();
    return answer === '' || answer === 'y' || answer === 'yes';
  };
  if (await loginNow()) {
    const done = spawnSync('npm run login', { cwd: root, shell: true, stdio: 'inherit' });
    if (done.status !== 0) console.log('\nThe login did not finish. Run  npm run login  inside the folder when you have the token.');
    console.log(`\nNext:\n${where}  npm run dev       try the site on http://localhost:3000\n  npm run deploy    publish it`);
  } else {
    console.log(`\nNext:\n${where}  npm run login     once, with the token your teacher gave you\n  npm run dev       try the site on http://localhost:3000\n  npm run deploy    publish it`);
  }
}
