#!/usr/bin/env node
// class-platform-mcp: the command the editor starts (no arguments = the MCP server), plus:
//   setup     one-time setup for this computer: add the server to VS Code / Antigravity (the token is saved per project: `npm run login`)
//   version   print the version
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const [command, ...rest] = process.argv.slice(2);
const version = () => JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')).version;

if (command === 'setup') {
  const { main } = await import('./setup.mjs');
  await main(rest);
} else if (command === 'login') {
  console.error('This command was removed: one token for the whole computer let two windows share one student by accident.\nOpen your class project folder and run  npm run login  there (it saves the token in that project\'s .env.local).');
  process.exitCode = 1;
} else if (command === 'version' || command === '--version') {
  console.log(version());
} else if (command === 'help' || command === '--help' || command === '-h') {
  console.log(`class-platform-mcp ${version()}\n\n  (no arguments)   start the MCP server (your editor does this)\n  setup            one-time setup: add the server to VS Code / Antigravity (then, in each project: npm run login)\n  version          print the version`);
} else {
  await import('./server.mjs'); // the MCP server: it reads its own arguments (--root, --print-config)
}
