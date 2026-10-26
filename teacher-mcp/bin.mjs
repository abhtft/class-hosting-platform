#!/usr/bin/env node
// class-platform-teacher-mcp: no arguments = the MCP server (your editor starts it); `setup` adds it to VS Code / Antigravity; `version`.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const [command, ...rest] = process.argv.slice(2);
const version = () => JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')).version;

if (command === 'setup') {
  const { main } = await import('./setup.mjs');
  await main(rest);
} else if (command === 'version' || command === '--version') {
  console.log(version());
} else if (command === 'help' || command === '--help' || command === '-h') {
  console.log(`class-platform-teacher-mcp ${version()}\n\n  (no arguments)   start the MCP server (your editor does this)\n  setup            add the server to VS Code / Antigravity\n  version          print the version`);
} else {
  await import('./server.mjs');
}
