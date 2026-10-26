#!/usr/bin/env node
// MCP server for your AI editor (VS Code, Antigravity, Cursor, Claude Code...): lets the assistant set up, check and publish a class project when you
// ask it to ("set up my class project", "publish my site"), without you typing commands. It only runs the project's own tools (or the copies bundled
// with this package), so nothing here is new behaviour:
//
//   init_project    write the class starter (site, backend, rules, helpers) into an EMPTY folder (it asks you for a yes first)
//   check_project   the same as `npm run check`
//   deploy          check first, then the same as `npm run deploy` (it asks you for a yes before it publishes)
//   status          is your saved token valid, and for how many days; and whether a newer kit or tool is available
//   update_kit      bring the kit's own files up to date (it asks you for a yes first; same as `npm run platform -- update --apply`)
//
// This one file runs in two places: inside a kit project as tools/mcp.mjs, and as the entry of the `class-platform-mcp` package that the editor
// starts with npx (see starter/mcp/setup.mjs). The token comes from the environment, the project's .env.local, or the computer-wide
// ~/.class-platform/token that `class-platform-mcp setup` saves; it is never in the editor's settings.
//
// Which folder is "the project"? In this order: a `path` the assistant passes to a tool, the folder the editor says is open (MCP "roots"),
// --root <dir>, the kit project this file lives in, then the working directory.
//
//   node server.mjs                  start the server (your editor does this; speaks MCP over stdin/stdout)
//   node server.mjs --print-config   print the settings to paste into an editor
//   node server.mjs --root <dir>     the project folder
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { kitStatus, noticeText } from './update-notice.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
const packageInfo = (() => { try { const p = JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')); return p.name === 'class-platform-mcp' ? p : null; } catch { return null; } })();
const inKit = !packageInfo && existsSync(join(here, '..', 'platform.json')) && here.endsWith('tools');
const flagRoot = option('--root') ? resolve(option('--root')) : null;
let editorRoot = null; // the folder the editor says is open (filled in from the client's "roots", see below)
const MAX_OUTPUT = 8000;
const DEFAULT_PORTAL = 'https://deploy.example.edu';

const versionOf = () => {
  if (packageInfo) return packageInfo.version;
  try { return JSON.parse(readFileSync(join(here, '..', 'platform.json'), 'utf8')).kitVersion || '0'; } catch { return '0'; }
};

/** The project folder for one call. */
function projectRoot(given) {
  if (given) return resolve(given);
  return editorRoot ?? flagRoot ?? (inKit ? resolve(here, '..') : process.cwd());
}

/** The project's own copy of a script if it has one (the kit), else the copy bundled with this package. */
function scriptFor(root, name) {
  const own = { check: join('tools', 'check.mjs'), deploy: 'portal-deploy.mjs', platform: join('tools', 'platform.mjs') }[name];
  if (existsSync(join(root, own))) return join(root, own);
  const bundled = { check: 'check.mjs', deploy: 'portal-deploy.mjs' }[name];
  return bundled && existsSync(join(here, bundled)) ? join(here, bundled) : null;
}

const clean = (text) => {
  const plain = text.replace(/\u001b\[[0-9;]*m/g, '').replace(/mds_[A-Za-z0-9_-]{8,}/g, 'mds_***').trim();
  return plain.length > MAX_OUTPUT ? `...(earlier output cut)\n${plain.slice(-MAX_OUTPUT)}` : plain;
};

/** Run a script with node in the project folder and return its output. stdin is closed, so nothing can wait for typing. */
function run(file, scriptArgs, timeoutMs, cwd) {
  return new Promise((resolveRun) => {
    if (!file) { resolveRun({ ok: false, text: 'That tool is not available in this folder.' }); return; }
    const child = spawn(process.execPath, [file, ...scriptArgs], { cwd, stdio: ['ignore', 'pipe', 'pipe'], env: process.env });
    let output = '';
    child.stdout.on('data', (d) => (output += d));
    child.stderr.on('data', (d) => (output += d));
    const timer = setTimeout(() => { output += `\nStopped: it took longer than ${Math.round(timeoutMs / 1000)} seconds.`; child.kill(); }, timeoutMs);
    child.on('error', (error) => { clearTimeout(timer); resolveRun({ ok: false, text: clean(`${output}\n${error.message}`) }); });
    child.on('close', (code) => { clearTimeout(timer); resolveRun({ ok: code === 0, text: clean(output) }); });
  });
}

const checkRun = (root) => run(scriptFor(root, 'check'), ['--root', root], 120000, root);

/** Is the newest MCP package newer than this one? Asks the portal's mcp-version.json (2 s at most); null when it cannot say. */
async function packageStatus() {
  if (!packageInfo) return null;
  try {
    const portal = String(process.env.PLATFORM_PORTAL || DEFAULT_PORTAL).replace(/\/$/, '');
    const response = await fetch(`${portal}/mcp-version.json`, { signal: AbortSignal.timeout(2000) });
    if (!response.ok) return null;
    const latest = await response.json();
    const { compareVersions } = await import('./update-notice.mjs');
    return { mine: packageInfo.version, latest: latest.version, newer: compareVersions(packageInfo.version, latest.version) < 0, url: latest.alias };
  } catch {
    return null;
  }
}

const PATH_PROPERTY = { type: 'string', description: 'The project folder (absolute path). Leave it out to use the folder open in the editor.' };
let deploying = false;
const TOOLS = [
  {
    name: 'init_project',
    description: 'Set up a new class project: write the starter (a React site in frontend/, a backend, the rules for AI editors, helpers for the class AI and class storage, the check) into an EMPTY folder. IMPORTANT: ask the student first and call this with confirm=true only after they say yes. It refuses a folder that already has files. Afterwards run check_project, and tell the student what to try next (`npm run dev`).',
    inputSchema: { type: 'object', properties: { confirm: { type: 'boolean', description: 'true only after the student agreed to set up the project here' }, path: PATH_PROPERTY, install: { type: 'boolean', description: 'run npm ci in frontend/ afterwards (default true; takes a minute)' } }, required: ['confirm'], additionalProperties: false },
    async run({ confirm, path, install }) {
      const root = projectRoot(path);
      if (confirm !== true) return { ok: false, text: `Nothing written. Ask the student whether to set up the class project in ${root}; if they say yes, call init_project again with confirm=true.` };
      const zipFile = join(here, 'kit.zip');
      if (!existsSync(zipFile)) return { ok: false, text: 'init_project is only available in the class-platform-mcp package (this copy has no starter inside).' };
      mkdirSync(root, { recursive: true });
      const harmless = new Set(['.git', '.vscode', '.idea', '.DS_Store', '.mcp.json', '.claude', '.cursor', '.gemini']);
      const blocking = readdirSync(root).filter((name) => !harmless.has(name));
      if (blocking.length) return { ok: false, text: `${root} is not empty (${blocking.slice(0, 5).join(', ')}${blocking.length > 5 ? ', ...' : ''}). Open an empty folder for a new project, or ask the student what they want.` };
      const { readZip } = await import('./zip.mjs');
      let written = 0;
      for (const [name, data] of readZip(readFileSync(zipFile))) { // readZip refuses any path that would leave the folder
        const target = join(root, name);
        if (existsSync(target)) continue; // a file the editor already made (for example .vscode/mcp.json) is left alone
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, data);
        written++;
      }
      let installText = '';
      if (install !== false) {
        const done = await new Promise((resolveRun) => {
          const child = spawn('npm ci --no-audit --no-fund', { cwd: join(root, 'frontend'), shell: true, stdio: ['ignore', 'pipe', 'pipe'] });
          let out = '';
          child.stdout.on('data', (d) => (out += d));
          child.stderr.on('data', (d) => (out += d));
          const timer = setTimeout(() => child.kill(), 300000);
          child.on('close', (code) => { clearTimeout(timer); resolveRun({ ok: code === 0, out }); });
        });
        installText = done.ok ? ' The site\'s packages are installed.' : ` The site\'s packages could not be installed (run "npm ci" inside frontend/ yourself): ${clean(done.out).slice(-300)}`;
      }
      return { ok: true, text: `Wrote ${written} files to ${root}.${installText}\nNext: run check_project, then try it with "npm run dev" (site on http://localhost:3000). The rules for the assistant are in AGENTS.md. To publish: save the token once ("npx ... class-platform-mcp login" or "npm run login"), then deploy.` };
    },
  },
  {
    name: 'check_project',
    description: 'Run the platform check on this project (layout, limits, imports, secrets). Run it before every deploy and fix every error it lists.',
    inputSchema: { type: 'object', properties: { path: PATH_PROPERTY }, additionalProperties: false },
    run: ({ path } = {}) => checkRun(projectRoot(path)),
  },
  {
    name: 'deploy',
    description: 'Publish this project (backend first, then frontend) to the class platform and return the live address. It runs the check first and refuses to deploy if the check fails. IMPORTANT: ask the student whether to publish now, and call this with confirm=true only after they say yes.',
    inputSchema: {
      type: 'object',
      properties: {
        confirm: { type: 'boolean', description: 'true only after the student agreed to publish now' },
        only: { type: 'string', enum: ['frontend', 'backend'], description: 'publish just one part (optional)' },
        path: PATH_PROPERTY,
      },
      required: ['confirm'],
      additionalProperties: false,
    },
    async run({ confirm, only, path }) {
      if (confirm !== true) return { ok: false, text: 'Not published. Ask the student whether to publish now; if they say yes, call deploy again with confirm=true.' };
      if (only !== undefined && only !== 'frontend' && only !== 'backend') return { ok: false, text: '"only" must be "frontend" or "backend".' };
      if (deploying) return { ok: false, text: 'A deploy is already running. Wait for it to finish.' };
      deploying = true;
      try {
        const root = projectRoot(path);
        const checked = await checkRun(root);
        if (!checked.ok) return { ok: false, text: `Not published: the check found problems. Fix them first.\n\n${checked.text}` };
        return await run(scriptFor(root, 'deploy'), only ? ['--only', only] : [], 300000, root);
      } finally {
        deploying = false;
      }
    },
  },
  {
    name: 'status',
    description: 'Say whether the saved portal token is valid and for how many days, and whether a newer class kit or MCP tool is available. Deploys nothing. If it says no token is found, the student must save it themselves in their own terminal (never ask them to paste the token to you).',
    inputSchema: { type: 'object', properties: { path: PATH_PROPERTY }, additionalProperties: false },
    async run({ path } = {}) {
      const root = projectRoot(path);
      const token = await run(scriptFor(root, 'deploy'), ['status'], 60000, root);
      const lines = [token.text];
      if (existsSync(join(root, 'platform.json'))) {
        const kit = await kitStatus(root, { fresh: true });
        lines.push(!kit ? 'Kit: could not reach the portal to compare versions.' : noticeText(kit) ? `${noticeText(kit).replace(/^[^A-Za-z]+/, 'Kit: ')} (ask the student, then use update_kit)` : `Kit ${kit.mine}: up to date.`);
      }
      const pack = await packageStatus();
      if (pack?.newer) lines.push(`Tool: class-platform-mcp ${pack.mine} is running, ${pack.latest} is available. Ask the student to run  npx -y ${pack.url} setup  once, then restart this tool server.`);
      return { ok: token.ok, text: lines.join('\n').trim() };
    },
  },
  {
    name: 'update_kit',
    description: 'Bring the class kit\'s own files (the rules for AI editors, tools/, the deploy script, the helpers class_ai and class_storage, the check) up to date from the portal. It never touches the student\'s own code, .env files or files the student edited. IMPORTANT: ask the student first and call this with confirm=true only after they say yes. Afterwards tell them to restart this tool server (or reload the editor).',
    inputSchema: { type: 'object', properties: { confirm: { type: 'boolean', description: 'true only after the student agreed to update' }, path: PATH_PROPERTY }, required: ['confirm'], additionalProperties: false },
    async run({ confirm, path }) {
      if (confirm !== true) return { ok: false, text: 'Not updated. Ask the student whether to update the kit now; if they say yes, call update_kit again with confirm=true.' };
      const root = projectRoot(path);
      const file = scriptFor(root, 'platform');
      if (!file) return { ok: false, text: 'This folder has no kit files to update (a project made by init_project, or the kit download, has them).' };
      const done = await run(file, ['update', '--apply'], 120000, root);
      return { ok: done.ok, text: done.ok ? `${done.text}\n\nRestart this tool server (or reload the editor) so the updated tools are used.` : done.text };
    },
  },
];

// ---------- the protocol (JSON-RPC 2.0, one message per line) ----------
function send(message) { process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`); }
const fail = (id, code, message) => send({ id, error: { code, message } });

const waiting = new Map(); // requests WE sent to the editor (roots/list) and still wait for
let nextOwnId = 0;
function ask(method, params, timeoutMs = 1500) {
  return new Promise((resolveAsk) => {
    const id = `srv-${++nextOwnId}`;
    const timer = setTimeout(() => { waiting.delete(id); resolveAsk(null); }, timeoutMs);
    waiting.set(id, (message) => { clearTimeout(timer); resolveAsk(message.result ?? null); });
    send({ id, method, params });
  });
}

/** The editor tells us which folder is open (the "roots" feature of MCP); the first one is the project. */
async function learnRoots() {
  const result = await ask('roots/list', {});
  const uri = result?.roots?.find((r) => typeof r.uri === 'string' && r.uri.startsWith('file:'))?.uri;
  if (uri) { try { editorRoot = fileURLToPath(uri); } catch { /* an odd uri: keep the other ways of finding the folder */ } }
}

let clientHasRoots = false;
async function handle(message) {
  if (message.method === undefined) { // an answer to something we asked
    waiting.get(message.id)?.(message);
    waiting.delete(message.id);
    return;
  }
  const { id, method, params = {} } = message;
  if (id === undefined) {
    if (method === 'notifications/initialized' && clientHasRoots) await learnRoots();
    if (method === 'notifications/roots/list_changed') await learnRoots();
    return;
  }
  switch (method) {
    case 'initialize':
      clientHasRoots = Boolean(params.capabilities?.roots);
      return send({ id, result: {
        protocolVersion: typeof params.protocolVersion === 'string' ? params.protocolVersion : '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: { name: 'class-platform', version: versionOf() },
        instructions: 'Tools for a class project. To start: init_project in an empty folder (ask first). To publish: check_project, ask the student "publish now?", then deploy with confirm=true. If there is no token, the student saves it themselves in a terminal; never ask for it.',
      } });
    case 'ping':
      return send({ id, result: {} });
    case 'tools/list':
      return send({ id, result: { tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })) } });
    case 'tools/call': {
      const tool = TOOLS.find((t) => t.name === params.name);
      if (!tool) return fail(id, -32602, `Unknown tool: ${params.name}`);
      const result = await tool.run(params.arguments ?? {});
      return send({ id, result: { content: [{ type: 'text', text: result.text || (result.ok ? 'Done.' : 'Failed.') }], isError: !result.ok } });
    }
    default:
      return fail(id, -32601, `Method not found: ${method}`);
  }
}

function serve() {
  let buffer = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      let message;
      try { message = JSON.parse(line); } catch { fail(null, -32700, 'Parse error'); continue; }
      handle(message).catch((error) => fail(message.id ?? null, -32603, error.message));
    }
  });
}

/** The editor entry that starts this server: from the package with npx, or from the kit project's own file (same rule as starter/mcp/setup.mjs). */
function editorEntry() {
  if (packageInfo) {
    const portal = String(process.env.PLATFORM_PORTAL || DEFAULT_PORTAL).replace(/\/$/, '');
    const npx = ['-y', `${portal}/class-platform-mcp-${packageInfo.version}.tgz`];
    return process.platform === 'win32' ? { command: 'cmd', args: ['/c', 'npx', ...npx] } : { command: 'npx', args: npx };
  }
  return { command: 'node', args: [join(here, 'mcp.mjs'), '--root', projectRoot(null)] };
}

if (args.includes('--print-config')) {
  const whole = JSON.stringify({ mcpServers: { 'class-platform': editorEntry() } }, null, 2);
  if (args.includes('--json')) {
    console.log(whole); // nothing but the file content, for `npm run mcp-config -- --json > mcp_config.json`
  } else {
    console.log(`VS Code: nothing to do. This folder already has .vscode/mcp.json; open the Chat view in Agent mode and start the "class-platform" server.
         (Newer versions offer a one-click "Migrate" to .mcp.json: accept it.)

Antigravity: open the agent panel's "..." menu > MCP Servers > Manage MCP Servers > View raw config.
  - If the file is empty or has no other servers: replace ALL of it with the JSON between the two lines below.
  - If it already lists other servers: add only the "class-platform": { ... } part inside your existing "mcpServers".
  Copy ONLY the JSON, not these instructions. Then save and press Refresh in the MCP list.

----- copy from the next line -----
${whole}
----- up to the previous line -----

Run  npm run login  once in a terminal first (the server reads your token from .env.local).`);
  }
} else {
  serve();
}
