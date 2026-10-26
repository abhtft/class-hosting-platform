#!/usr/bin/env node
// `npm run doctor`: checks that your computer has what this project needs, and says how to fix what is missing.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let problems = 0;
const say = (ok, text, fix = '') => {
  console.log(`${ok ? '✔' : '✖'} ${text}${!ok && fix ? `\n    fix: ${fix}` : ''}`);
  if (!ok) problems++;
};
const version = (command, args = ['--version']) => {
  const windows = process.platform === 'win32'; // npm and git are .cmd/.exe launchers there: run them through the shell, as one string
  const r = windows ? spawnSync([command, ...args].join(' '), { encoding: 'utf8', shell: true }) : spawnSync(command, args, { encoding: 'utf8' });
  return r.status === 0 ? (r.stdout || r.stderr).trim() : null;
};

const node = Number(process.versions.node.split('.')[0]);
say(node >= 22, `Node ${process.versions.node} (needs 22 or newer)`, 'Install Node 22 from https://nodejs.org');
say(Boolean(version('npm')), `npm ${version('npm') ?? 'not found'}`, 'npm comes with Node: reinstall Node from https://nodejs.org');
say(Boolean(version('git')), `git ${version('git')?.replace('git version ', '') ?? 'not found'}`, 'Install git from https://git-scm.com');

if (existsSync(join(root, 'backend', 'app.py'))) {
  const python = ['python', 'python3'].map((p) => [p, version(p)]).find(([, v]) => v);
  const major = python ? python[1].match(/Python (\d+)\.(\d+)/) : null;
  say(Boolean(major) && (Number(major[1]) > 3 || Number(major[2]) >= 12), `${python ? python[1] : 'Python not found'} (needs 3.12 or newer)`, 'Install Python 3.12 from https://python.org');
}

say(existsSync(join(root, 'frontend', 'node_modules')), 'frontend packages installed', 'run: npm run dev (it installs them) or cd frontend && npm install');
const gh = version('gh');
console.log(gh ? `✔ GitHub CLI found (optional): ${gh.split('\n')[0]}` : '• GitHub CLI not installed (optional: you can do everything in the browser)');
console.log(problems ? `\n${problems} thing(s) to fix above.` : '\nAll good. Run: npm run dev');
process.exit(problems ? 1 : 0);
