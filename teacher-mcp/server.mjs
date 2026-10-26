#!/usr/bin/env node
// MCP server for the TEACHER of the class hosting platform (VS Code, Antigravity, Cursor, Claude Code...). It lets the assistant install the
// platform into your own AWS account, show what an update would change, apply it and switch features on, by running the repository's own
// `node setup.mjs <command> --json`. It adds no powers of its own.
//
//   check_environment   is this computer and AWS account ready                                   (read only)
//   status              what is installed, which version, which features, is an update waiting   (read only)
//   install             first install into your AWS account (try mode, or with a domain)       asks you first, runs in the background
//   finish_domain       step two of a domain install, after the DNS records are added           asks you first, runs in the background
//   plan_update         show what an update or a feature switch would change; changes nothing   (read only)
//   apply_update        run exactly the plan that was shown                                      asks you first, runs in the background
//   publish_portal      publish the portal pages, student kit and MCP package again             asks you first
//   job_status          progress and result of a background job
//   teacher_login       how to set the teacher password (typed in YOUR terminal, never here)
//
// Never available here on purpose: deleting the stack, typing passwords or API keys, anything with IAM users. Those stay in your own terminal.
//
// Which folder is the platform repository? A `path` the assistant passes, the folder open in the editor (MCP "roots"), --repo <dir>, the working
// directory. It must contain template.yaml and setup.mjs (a `git clone` of the platform).
import { existsSync, readFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
const flagRepo = option('--repo') ? resolve(option('--repo')) : null;
let editorRoot = null;
const FEATURES = ['backend', 'ai', 'gemini', 'storage'];
const MAX_TAIL = 4000;

const version = () => { try { return JSON.parse(readFileSync(join(here, 'package.json'), 'utf8')).version; } catch { return '0'; } };
const isRepo = (dir) => Boolean(dir) && existsSync(join(dir, 'template.yaml')) && existsSync(join(dir, 'setup.mjs'));

export function repoFor(given) {
  if (given && !isRepo(resolve(given))) throw new Error(`${given} is not the platform folder (it has no template.yaml and setup.mjs). Leave the path out to use the folder open in the editor.`);
  const found = [given && resolve(given), editorRoot, flagRepo, process.cwd()].filter(Boolean).find(isRepo);
  if (!found) throw new Error('Open the platform folder in your editor first (a `git clone` of the platform repository: it contains template.yaml and setup.mjs), or pass its path.');
  return found;
}

/** Keep anything shaped like a key out of what the assistant sees. */
const clean = (text) => String(text).replace(/\u001b\[[0-9;]*m/g, '').replace(/(sk-or-[A-Za-z0-9_-]{8,}|AQ\.[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{12,})/g, '***').trim();

/** Run `node setup.mjs <command> --json` in the repository and return its single JSON answer. */
function runSetup(repo, command, flags = [], { timeoutMs = 120000, onData } = {}) {
  return new Promise((resolveRun) => {
    const child = spawn(process.execPath, [join(repo, 'setup.mjs'), command, ...flags, '--json'], { cwd: repo, stdio: ['ignore', 'pipe', 'pipe'], env: process.env });
    let out = '';
    let err = '';
    child.stdout.on('data', (d) => { out += d; onData?.(String(d)); });
    child.stderr.on('data', (d) => { err += d; onData?.(String(d)); });
    const timer = setTimeout(() => { child.kill(); err += `\nStopped: it took longer than ${Math.round(timeoutMs / 60000)} minutes.`; }, timeoutMs);
    child.on('error', (e) => { clearTimeout(timer); resolveRun({ ok: false, error: e.message }); });
    child.on('close', () => {
      clearTimeout(timer);
      const last = out.trim().split('\n').filter(Boolean).pop();
      try { resolveRun(JSON.parse(last)); } catch { resolveRun({ ok: false, error: clean(err || out || 'No answer from setup.mjs.').slice(-MAX_TAIL) }); }
    });
  });
}

const flagsFor = (a) => [...(a.profile ? ['--profile', a.profile] : []), ...(a.stack ? ['--stack', a.stack] : []), ...(a.dry_run ? ['--dry-run'] : [])];
const show = (result) => ({ ok: result.ok !== false, text: clean(JSON.stringify(result, null, 2)) });

// ---------- background jobs: an install takes 10 minutes or more, longer than an editor waits for one answer ----------
let job = null;
function startJob(name, repo, command, flags) {
  if (job && !job.done) return { ok: false, text: `A job is already running (${job.name}). Ask for job_status.` };
  job = { name, started: Date.now(), done: false, tail: '', result: null };
  const current = job;
  runSetup(repo, command, flags, { timeoutMs: 45 * 60000, onData: (d) => { current.tail = clean(current.tail + d).slice(-MAX_TAIL); } }).then((result) => { current.done = true; current.result = result; });
  return { ok: true, text: `Started "${name}". It can take 10 to 15 minutes (CloudFront is slow to create). Ask for job_status every minute or two and tell the teacher what it says.` };
}

const PATH = { type: 'string', description: 'The platform repository folder (absolute path). Leave out to use the folder open in the editor.' };
const PROFILE = { type: 'string', description: 'The AWS CLI profile (the account to use). Leave out to use the one chosen at install or the default.' };
const CONFIRM = { type: 'boolean', description: 'true only after the teacher agreed' };
const TOOLS = [
  { name: 'check_environment', description: 'Check that this computer and the AWS account are ready: Node, the AWS CLI, which account, CloudFront access. Changes nothing. Run it before install.',
    inputSchema: { type: 'object', properties: { path: PATH, profile: PROFILE }, additionalProperties: false },
    run: async (a) => show(await runSetup(repoFor(a.path), 'check', flagsFor(a))) },
  { name: 'status', description: 'Say what is installed in the AWS account, its version, which features are on, the portal address, and whether the repository holds a newer version (after `git pull`). Changes nothing.',
    inputSchema: { type: 'object', properties: { path: PATH, profile: PROFILE }, additionalProperties: false },
    run: async (a) => show(await runSetup(repoFor(a.path), 'status', flagsFor(a))) },
  { name: 'install', description: 'Install the platform into the teacher\'s AWS account (CloudFront, S3, Lambda, DynamoDB; costs are small and idle cost is near zero). IMPORTANT: first run check_environment, tell the teacher which AWS account it will use, and call this with confirm=true only after they say yes. With no domain it uses free cloudfront.net addresses (best for a first try). With a domain it first returns a DNS record for the certificate, then the DNS records for the sites; the teacher adds them at their DNS host. Runs in the background: use job_status.',
    inputSchema: { type: 'object', properties: { confirm: CONFIRM, path: PATH, profile: PROFILE, stack: { type: 'string', description: 'Stack name (default student-hosting)' },
      domain: { type: 'string', description: 'Optional domain for student sites, for example apps.example.com' }, features: { type: 'array', items: { type: 'string', enum: FEATURES }, description: 'Optional features to switch on (default none)' }, dry_run: { type: 'boolean', description: 'only list what would run' } }, required: ['confirm'], additionalProperties: false },
    run: async (a) => {
      if (a.confirm !== true) return { ok: false, text: 'Nothing installed. Tell the teacher which AWS account and domain (if any) will be used, ask whether to install, and call install again with confirm=true.' };
      const flags = [...flagsFor(a), ...(a.domain ? ['--domain', a.domain] : []), ...((a.features ?? []).length ? ['--features', a.features.join(',')] : [])];
      return a.dry_run ? show(await runSetup(repoFor(a.path), 'install', flags)) : startJob('install', repoFor(a.path), 'install', flags);
    } },
  { name: 'finish_domain', description: 'Step two of a domain install: attach the portal address once the DNS records returned by install have been added. IMPORTANT: ask the teacher whether the records are added; confirm=true only after they say yes. Runs in the background: use job_status.',
    inputSchema: { type: 'object', properties: { confirm: CONFIRM, path: PATH, profile: PROFILE }, required: ['confirm'], additionalProperties: false },
    run: async (a) => (a.confirm === true ? startJob('finish_domain', repoFor(a.path), 'finish-domain', flagsFor(a)) : { ok: false, text: 'Not done. Ask the teacher whether the DNS records are added, then call again with confirm=true.' }) },
  { name: 'plan_update', description: 'Show, in plain words, what updating the account to the version in the repository (after `git pull`) would change, or what switching one feature on or off would change. Changes nothing. Returns a plan_id. Tell the teacher the summary; if "blocked" is true explain nothing was changed and why.',
    inputSchema: { type: 'object', properties: { path: PATH, profile: PROFILE, enable: { type: 'string', enum: FEATURES }, disable: { type: 'string', enum: FEATURES } }, additionalProperties: false },
    run: async (a) => show(await runSetup(repoFor(a.path), 'plan', [...flagsFor(a), ...(a.enable ? ['--enable', a.enable] : []), ...(a.disable ? ['--disable', a.disable] : [])], { timeoutMs: 600000 })) },
  { name: 'apply_update', description: 'Run exactly the plan that plan_update showed (give its plan_id), then publish the portal pages, student kit and MCP package. IMPORTANT: read the plan to the teacher and call this with confirm=true only after they say yes. A changed or old plan is refused; data-holding resources are never replaced. Runs in the background: use job_status.',
    inputSchema: { type: 'object', properties: { confirm: CONFIRM, plan_id: { type: 'string', description: 'The plan_id from plan_update' }, path: PATH, profile: PROFILE }, required: ['confirm', 'plan_id'], additionalProperties: false },
    run: async (a) => {
      if (a.confirm !== true) return { ok: false, text: 'Not applied. Read the plan to the teacher, ask whether to apply it, then call again with confirm=true.' };
      if (!/^[0-9a-f]{8,64}$/.test(String(a.plan_id))) return { ok: false, text: 'plan_id must be the id that plan_update returned.' };
      return startJob('apply_update', repoFor(a.path), 'apply', [a.plan_id, ...flagsFor(a)]);
    } },
  { name: 'publish_portal', description: 'Publish the portal pages, the student kit and the MCP package from the repository again (no AWS stack change). IMPORTANT: ask first; confirm=true only after they say yes.',
    inputSchema: { type: 'object', properties: { confirm: CONFIRM, path: PATH, profile: PROFILE }, required: ['confirm'], additionalProperties: false },
    run: async (a) => (a.confirm === true ? show(await runSetup(repoFor(a.path), 'publish', flagsFor(a), { timeoutMs: 300000 })) : { ok: false, text: 'Not published. Ask the teacher first, then call again with confirm=true.' }) },
  { name: 'job_status', description: 'Progress and result of the running or last background job (install, finish_domain, apply_update).',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    run: async () => {
      if (!job) return { ok: true, text: 'No job has been started in this session.' };
      const minutes = Math.round((Date.now() - job.started) / 60000);
      if (!job.done) return { ok: true, text: `"${job.name}" is still running (${minutes} min).\n${job.tail ? `Last output:\n${job.tail.slice(-800)}` : ''}`.trim() };
      return { ...show(job.result), text: `"${job.name}" finished after ${minutes} min.\n${show(job.result).text}` };
    } },
  { name: 'teacher_login', description: 'Explain how to set or reset the teacher password. The password must be typed in the teacher\'s own terminal; never ask for it and never put it in the chat.',
    inputSchema: { type: 'object', properties: { username: { type: 'string', description: 'The login name (3-40 letters, digits . _ -)' } }, additionalProperties: false },
    run: async (a) => ({ ok: true, text: `In your own terminal, inside the platform folder, run:\n  node setup.mjs teacher ${/^[A-Za-z0-9._-]{3,40}$/.test(a?.username ?? '') ? a.username : '<username>'}\nIt asks for the password (hidden). The same applies to the AI keys: run  node setup.mjs secrets  yourself. Never type either into this chat.` }) },
];

// ---------- the protocol (JSON-RPC 2.0, one message per line) ----------
function send(message) { process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`); }
const fail = (id, code, message) => send({ id, error: { code, message } });
const waiting = new Map();
let nextOwnId = 0;
function ask(method, params, timeoutMs = 1500) {
  return new Promise((resolveAsk) => {
    const id = `srv-${++nextOwnId}`;
    const timer = setTimeout(() => { waiting.delete(id); resolveAsk(null); }, timeoutMs);
    waiting.set(id, (message) => { clearTimeout(timer); resolveAsk(message.result ?? null); });
    send({ id, method, params });
  });
}
async function learnRoots() {
  const result = await ask('roots/list', {});
  const uri = result?.roots?.find((r) => typeof r.uri === 'string' && r.uri.startsWith('file:'))?.uri;
  if (uri) { try { editorRoot = fileURLToPath(uri); } catch { /* keep the other ways of finding the folder */ } }
}

let clientHasRoots = false;
async function handle(message) {
  if (message.method === undefined) { waiting.get(message.id)?.(message); waiting.delete(message.id); return; }
  const { id, method, params = {} } = message;
  if (id === undefined) {
    if (method === 'notifications/initialized' && clientHasRoots) await learnRoots();
    if (method === 'notifications/roots/list_changed') await learnRoots();
    return;
  }
  switch (method) {
    case 'initialize':
      clientHasRoots = Boolean(params.capabilities?.roots);
      return send({ id, result: { protocolVersion: typeof params.protocolVersion === 'string' ? params.protocolVersion : '2025-06-18', capabilities: { tools: {} },
        serverInfo: { name: 'class-platform-teacher', version: version() },
        instructions: 'Tools for the TEACHER of the class platform. Install: check_environment, then install (ask first). Update: the teacher runs `git pull`, then plan_update, read the plan out, ask, apply_update. Passwords and API keys are typed only in the teacher\'s own terminal; never ask for them.' } });
    case 'ping': return send({ id, result: {} });
    case 'tools/list': return send({ id, result: { tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })) } });
    case 'tools/call': {
      const tool = TOOLS.find((t) => t.name === params.name);
      if (!tool) return fail(id, -32602, `Unknown tool: ${params.name}`);
      let result;
      try { result = await tool.run(params.arguments ?? {}); } catch (e) { result = { ok: false, text: e.message }; }
      return send({ id, result: { content: [{ type: 'text', text: result.text || (result.ok ? 'Done.' : 'Failed.') }], isError: !result.ok } });
    }
    default: return fail(id, -32601, `Method not found: ${method}`);
  }
}

export const tools = TOOLS;

if (!process.env.TEACHER_MCP_NO_SERVE) {
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
