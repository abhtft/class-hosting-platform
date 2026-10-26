#!/usr/bin/env node
// class-platform-teacher: install and update your own copy of the class hosting platform in your own AWS account, from one command.
//
//   npx -y class-platform-teacher@<version> install          guided install (asks for the AWS account, an optional domain, features, your login)
//   npx -y class-platform-teacher@<version> upgrade          after a new version: show what changes, ask, apply, republish
//   npx -y class-platform-teacher@<version> status | plan | apply <id> | check | teacher <name> | secrets | publish | finish-domain | destroy
//   npx -y class-platform-teacher@<version> editor           add the teacher tools to VS Code / Antigravity (the editor then runs:  ... mcp)
//   npx -y class-platform-teacher@<version> eject <folder>   copy the whole platform into a normal folder (to change it with git)
//   npx -y class-platform-teacher@<version> version | help
//
// The package carries the platform itself (template.yaml, lambda/, web/, the student kit sources, the installer), so nothing is cloned.
// Your install state is kept in ~/.class-platform-teacher (CLASS_PLATFORM_TEACHER_HOME to move it); it can be rebuilt from the AWS stack.
import { cpSync, existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const [command, ...rest] = process.argv.slice(2);
const version = () => JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version;

function eject(target) {
  if (!target) { console.error('Usage: class-platform-teacher eject <folder>'); process.exitCode = 1; return; }
  const dest = resolve(target);
  if (existsSync(dest) && readdirSync(dest).length) { console.error(`${dest} is not empty. Choose a new or empty folder.`); process.exitCode = 1; return; }
  cpSync(root, dest, { recursive: true, filter: (src) => !src.includes('node_modules') && !src.endsWith('.packaged') });
  console.log(`Copied the platform ${version()} to ${dest}.\nIn that folder: git init, then use  node setup.mjs <command>  as usual (your install state moves with it only if you copy ~/.class-platform-teacher/install.json to .class-platform/).`);
}

if (command === 'mcp') {
  process.argv.push('--repo', root); // the tools work on this package's own files: no folder needs to be open
  await import('./mcp/server.mjs');
} else if (command === 'editor') {
  const { main } = await import('./mcp/setup.mjs');
  await main(rest);
} else if (command === 'eject') {
  eject(rest[0]);
} else if (command === 'version' || command === '--version') {
  console.log(version());
} else if (command === 'help' || command === '--help' || command === '-h') {
  console.log(`class-platform-teacher ${version()}\n\n  install            guided install into your own AWS account\n  upgrade            update your install to this version (shows the change first)\n  status | check     what is installed / is this computer and account ready\n  plan [--enable f]  show a change without applying; apply <id> runs exactly that plan\n  teacher <name>     set the teacher login (password asked, hidden)\n  secrets            save the AI keys in AWS Secrets Manager (asked, hidden)\n  publish            republish the portal pages, student kit and MCP package\n  finish-domain      step two of a domain install\n  destroy            delete the stack (typed confirmation)\n  editor             add the teacher tools to VS Code / Antigravity\n  eject <folder>     copy the platform to a folder you can change with git\n\nDocs: docs/TEACHER_SETUP.md inside the package.`);
} else {
  const { main } = await import('./setup.mjs');
  await main(command === undefined ? [] : [command, ...rest]);
}
