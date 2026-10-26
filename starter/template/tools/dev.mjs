#!/usr/bin/env node
// `npm run dev`: start the backend and the frontend together on your computer.
//
//   npm run dev                     site on http://localhost:3000, backend on http://127.0.0.1:8788 (the site forwards /api to it)
//   npm run dev -- --backend-only   just the backend (handy for testing with curl)
//
// Press Ctrl+C to stop both.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const backendOnly = process.argv.includes('--backend-only');
const children = [];

function fail(message) {
  console.error(`\n✖ ${message}`);
  process.exit(1);
}

function backendCommand() {
  if (existsSync(join(root, 'backend', 'app.py'))) {
    const python = ['python', 'python3'].find((p) => spawnSync(p, ['--version']).status === 0);
    if (!python) fail('Python 3.12 was not found. Run `npm run doctor` for help.');
    return [python, [join(root, 'tools', 'dev_server.py')]];
  }
  if (['index.mjs', 'index.js'].some((n) => existsSync(join(root, 'backend', n)))) return [process.execPath, [join(root, 'tools', 'dev_server.mjs')]];
  return fail('No backend entry file found in backend/ (app.py or index.mjs). See AGENTS.md.');
}

function run(command, args, options = {}) {
  // a single command string with shell:true (passing an args array together with shell:true is deprecated in newer Node)
  const child = options.shell ? spawn(command, { stdio: 'inherit', ...options }) : spawn(command, args, { stdio: 'inherit', ...options });
  children.push(child);
  child.on('exit', (code) => {
    if (code) console.error(`\n(${options.label ?? command} stopped with code ${code})`);
  });
  return child;
}

const [command, args] = backendCommand();
run(command, args, { cwd: root, label: 'backend' });

if (!backendOnly) {
  const frontend = join(root, 'frontend');
  if (!existsSync(join(frontend, 'node_modules'))) {
    console.log('Installing frontend packages (first run only)…');
    const install = spawnSync('npm install', { cwd: frontend, stdio: 'inherit', shell: true });
    if (install.status !== 0) fail('npm install failed in frontend/.');
  }
  run('npm run dev', [], { cwd: frontend, shell: true, label: 'frontend' });
}

const stop = () => { for (const child of children) child.kill(); process.exit(0); };
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
