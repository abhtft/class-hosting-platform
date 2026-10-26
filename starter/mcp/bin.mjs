#!/usr/bin/env node
// class-platform-mcp: the command the editor starts (no arguments = the MCP server), plus:
//   setup     one-time setup for this computer: save the token, add the server to VS Code / Antigravity
//   login     save the token for this whole computer (~/.class-platform/token)
//   version   print the version
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const [command, ...rest] = process.argv.slice(2);
const version = () => JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')).version;

if (command === 'setup') {
  const { main } = await import('./setup.mjs');
  await main(rest);
} else if (command === 'login') {
  const result = spawnSync(process.execPath, [join(here, 'portal-deploy.mjs'), 'login', '--global', ...rest], { stdio: 'inherit' });
  process.exitCode = result.status ?? 1;
} else if (command === 'version' || command === '--version') {
  console.log(version());
} else if (command === 'help' || command === '--help' || command === '-h') {
  console.log(`class-platform-mcp ${version()}\n\n  (no arguments)   start the MCP server (your editor does this)\n  setup            one-time setup: save your token, add the server to VS Code / Antigravity\n  login            save your token for this whole computer\n  version          print the version`);
} else {
  await import('./server.mjs'); // the MCP server: it reads its own arguments (--root, --print-config)
}
