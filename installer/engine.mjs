// The installer engine: everything `node setup.mjs` and the teacher MCP do, as small functions that take a context and return plain data.
// Nothing here prompts or prints on its own (the context's `say` and `ask` do), so the same code serves a wizard, an editor tool and tests.
//
// What it needs on the teacher's computer: Node, the AWS CLI with working credentials, and an AWS account. No SAM CLI (the Lambda code is plain
// Python in one folder, so `aws cloudformation package` + `deploy` is enough; see docs/NEW_ACCOUNT_AND_TRANSFER.md section 4).
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

export const REGION = 'us-east-1'; // CloudFront certificates must live here, so the whole stack does
export const STATEFUL = new Set(['AWS::DynamoDB::Table', 'AWS::S3::Bucket', 'AWS::SSM::Parameter', 'AWS::SecretsManager::Secret', 'AWS::CloudFront::Distribution']);
export const FEATURES = { backend: 'BackendEnabled', ai: 'AiEnabled', gemini: 'AiGeminiEnabled', storage: 'StorageEnabled' };
const NEEDS = { ai: ['backend'], gemini: ['backend', 'ai'], storage: ['backend'] };
const STACK_RE = /^[A-Za-z][A-Za-z0-9-]{0,40}$/;
const DOMAIN_RE = /^(?=.{4,100}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/;
const MUTATING = /^(create|put|delete|request|execute|update|remove|empty|import|tag)-|^(mb|rb|rm|sync|cp|package|deploy|wait)$/;

export class InstallError extends Error {
  constructor(message, hint) { super(message); this.hint = hint; }
}

// ---------- the aws command ----------
/** The real runner: the AWS CLI with the chosen profile. Throws an Error with the CLI's own message. */
export function realAws({ profile, region = REGION, exec = execFileSync } = {}) {
  return (args) => {
    const full = [...args, '--region', region, ...(profile ? ['--profile', profile] : [])];
    try {
      return exec('aws', full, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (e) {
      if (e.code === 'ENOENT') throw new InstallError('The AWS CLI is not installed.', 'Install it from https://aws.amazon.com/cli/ and run `aws configure`.');
      throw new InstallError(String(e.stderr || e.message).trim().split('\n').slice(-4).join('\n'));
    }
  };
}

export const isMutating = (args) => MUTATING.test(args[1] ?? '') || (args[0] === 'cloudformation' && ['package', 'deploy'].includes(args[1]));

/** Run an AWS call. In a dry run anything that would change something is only reported. */
function call(ctx, args, { json = false } = {}) {
  if (ctx.dryRun && isMutating(args)) {
    ctx.would.push(`aws ${args.join(' ')}`);
    return json ? {} : '';
  }
  const out = ctx.aws(json ? [...args, '--output', 'json'] : args);
  return json ? (out.trim() ? JSON.parse(out) : {}) : out;
}

// ---------- configuration ----------
/** Where the install state lives: next to the clone, or (when run from the npm package, whose folder is a read-only cache) in the teacher's home folder. */
export const stateDir = (ctx) => ctx.stateDir ?? (existsSync(join(ctx.repo, '.packaged')) ? (process.env.CLASS_PLATFORM_TEACHER_HOME || join(homedir(), '.class-platform-teacher')) : join(ctx.repo, '.class-platform'));
const statePath = (ctx) => join(stateDir(ctx), 'install.json');

export function readState(ctx) {
  try { return JSON.parse(readFileSync(statePath(ctx), 'utf8')); } catch { return null; }
}

function writeState(ctx, state) {
  if (ctx.dryRun) return;
  mkdirSync(stateDir(ctx), { recursive: true });
  writeFileSync(statePath(ctx), `${JSON.stringify(state, null, 2)}\n`);
}

/** A fresh clone has no saved state. If the stack exists in the account, rebuild the state from it (parameters tell the domain and features). */
export function adoptState(ctx) {
  const known = readState(ctx);
  if (known) return known;
  let found;
  try { found = stackOutputs(ctx); } catch { return null; }
  if (!found.status || !/COMPLETE$/.test(found.status) || /ROLLBACK|DELETE/.test(found.status)) return null;
  const p = found.parameters;
  const features = Object.entries(FEATURES).filter(([, name]) => p[name] === 'true').map(([f]) => f).sort();
  const state = { stack: ctx.stack, profile: ctx.profile, domain: p.RootDomain || '', certificateArn: p.CertificateArn || '', features, phase: 'ready', installedVersion: null, adopted: true };
  writeState(ctx, state);
  return state;
}

/** How the teacher runs this tool, for messages: from the npm/GitHub package it is an npx command, from a clone `node setup.mjs`. */
export function cli(ctx) {
  if (!existsSync(join(ctx.repo, '.packaged'))) return 'node setup.mjs';
  let repo = '';
  try { repo = JSON.parse(readFileSync(join(ctx.repo, 'release.json'), 'utf8')).repo; } catch { /* no stamp */ }
  return repo ? `npx -y https://github.com/${repo}/releases/latest/download/class-platform-teacher.tgz` : 'npx -y class-platform-teacher';
}

export const repoVersion = (ctx) => { try { return readFileSync(join(ctx.repo, 'VERSION'), 'utf8').trim(); } catch { return '0.0.0'; } };

/** Check the choices and return them in their final form. Everything a teacher can choose is here. */
export function normalise(input) {
  const features = new Set(input.features ?? []);
  for (const f of [...features]) {
    if (!FEATURES[f]) throw new InstallError(`Unknown feature "${f}".`, `Choose from: ${Object.keys(FEATURES).join(', ')}.`);
    for (const need of NEEDS[f] ?? []) features.add(need); // gemini brings ai and backend, ai brings backend
  }
  const stack = input.stack || 'student-hosting';
  if (!STACK_RE.test(stack)) throw new InstallError('The stack name must start with a letter and use letters, digits and "-" (up to 41 characters).');
  const domain = (input.domain || '').trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/^\*\./, '');
  if (domain && !DOMAIN_RE.test(domain)) throw new InstallError(`"${input.domain}" is not a valid domain name (for example apps.example.com).`);
  return { stack, profile: input.profile || '', domain, features: [...features].sort(), certificateArn: input.certificateArn || '' };
}

/** The CloudFormation parameters for a configuration. `aliasOn` is the second step of the domain install (see PortalAlias in template.yaml). */
export function parametersFor(config, { aliasOn = false } = {}) {
  const p = { RootDomain: config.domain, CertificateArn: config.domain ? config.certificateArn : '', PortalAlias: config.domain && aliasOn ? 'true' : 'false' };
  for (const [name, parameter] of Object.entries(FEATURES)) p[parameter] = config.features.includes(name) ? 'true' : 'false';
  return Object.entries(p).map(([k, v]) => `${k}=${v}`);
}

// ---------- steps ----------
/** Is this computer and account ready? Returns what it found; throws InstallError with a hint when something is wrong. */
export function preflight(ctx) {
  const major = Number(process.versions.node.split('.')[0]);
  if (major < 20) throw new InstallError(`Node ${process.versions.node} is too old.`, 'Install Node 20 or newer from https://nodejs.org/.');
  let identity;
  try { identity = call(ctx, ['sts', 'get-caller-identity'], { json: true }); } catch (e) {
    if (e instanceof InstallError && e.hint) throw e;
    throw new InstallError(`The AWS CLI could not sign in${ctx.profile ? ` with the profile "${ctx.profile}"` : ''}: ${e.message}`, 'Run `aws configure` (or `aws configure --profile <name>`) with an IAM user that has AdministratorAccess.');
  }
  const notes = [];
  try { call(ctx, ['cloudfront', 'list-distributions', '--max-items', '1'], { json: true }); } catch (e) {
    if (/verif/i.test(e.message)) throw new InstallError('AWS has not verified this account for CloudFront yet.', 'Open an AWS Support case (Account and billing) and ask them to verify the account for CloudFront; it clears in hours to days.');
    if (/AccessDenied|not authorized/i.test(e.message)) throw new InstallError('This AWS user is not allowed to use CloudFront.', 'Attach AdministratorAccess to the IAM user (a user with no policy can sign in but every call fails).');
    notes.push(`CloudFront check: ${e.message}`);
  }
  let stack = null;
  try { stack = call(ctx, ['cloudformation', 'describe-stacks', '--stack-name', ctx.stack], { json: true }).Stacks?.[0] ?? null; } catch { /* no such stack: a fresh install */ }
  return { account: identity.Account, arn: identity.Arn, stackStatus: stack?.StackStatus ?? null, notes };
}

export function ensureArtifactBucket(ctx, account) {
  const bucket = `${ctx.stack.toLowerCase()}-artifacts-${account}`;
  try { call(ctx, ['s3api', 'head-bucket', '--bucket', bucket]); return bucket; } catch { /* not there yet */ }
  call(ctx, ['s3api', 'create-bucket', '--bucket', bucket]);
  call(ctx, ['s3api', 'put-public-access-block', '--bucket', bucket, '--public-access-block-configuration', 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true']);
  return bucket;
}

function packageTemplate(ctx, bucket) {
  const out = join(ctx.work ?? tmpdir(), `${ctx.stack}-packaged.yaml`);
  call(ctx, ['cloudformation', 'package', '--template-file', join(ctx.repo, 'template.yaml'), '--s3-bucket', bucket, '--output-template-file', out]);
  return out;
}

const deployArgs = (ctx, packaged, params, extra = []) => ['cloudformation', 'deploy', '--template-file', packaged, '--stack-name', ctx.stack, '--capabilities', 'CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND',
  '--no-fail-on-empty-changeset', '--parameter-overrides', ...params, ...extra];

export function stackOutputs(ctx) {
  const stack = call(ctx, ['cloudformation', 'describe-stacks', '--stack-name', ctx.stack], { json: true }).Stacks?.[0];
  return { status: stack?.StackStatus ?? null, outputs: Object.fromEntries((stack?.Outputs ?? []).map((o) => [o.OutputKey, o.OutputValue])),
    parameters: Object.fromEntries((stack?.Parameters ?? []).map((p) => [p.ParameterKey, p.ParameterValue])) };
}

/** A wildcard certificate for the domain (us-east-1). Reuses one that exists. Returns its ARN and the DNS record that proves ownership. */
export async function ensureCertificate(ctx, domain) {
  const name = `*.${domain}`;
  const listed = call(ctx, ['acm', 'list-certificates', '--certificate-statuses', 'ISSUED', 'PENDING_VALIDATION'], { json: true }).CertificateSummaryList ?? [];
  let arn = listed.find((c) => c.DomainName === name)?.CertificateArn;
  if (!arn) arn = call(ctx, ['acm', 'request-certificate', '--domain-name', name, '--validation-method', 'DNS'], { json: true }).CertificateArn;
  if (ctx.dryRun && !arn) return { arn: 'arn:aws:acm:us-east-1:<account>:certificate/dry-run', record: null, issued: false };
  let cert;
  for (let i = 0; i < 12; i += 1) { // the validation record appears a few seconds after the request
    cert = call(ctx, ['acm', 'describe-certificate', '--certificate-arn', arn], { json: true }).Certificate;
    if (cert?.Status === 'ISSUED' || cert?.DomainValidationOptions?.[0]?.ResourceRecord) break;
    await ctx.sleep(5000);
  }
  const record = cert?.DomainValidationOptions?.[0]?.ResourceRecord;
  return { arn, issued: cert?.Status === 'ISSUED', record: record ? { type: record.Type, name: record.Name, value: record.Value } : null };
}

export const dnsRecords = (domain, outputs) => [
  { name: `*.${domain}`, type: 'CNAME', value: outputs.DistributionDomain, for: 'student sites' },
  { name: `deploy.${domain}`, type: 'CNAME', value: outputs.PortalDistributionDomain, for: 'the portal' },
];

/** Run the portal's own admin script (teacher login, publishing pages) against the same account and stack. */
export function adminScript(ctx, args, { env = {}, inherit = false } = {}) {
  if (ctx.dryRun) { ctx.would.push(`node admin.mjs ${args.join(' ')}`); return ''; }
  const childEnv = { ...process.env, STACK_NAME: ctx.stack, AWS_DEFAULT_REGION: REGION, ...(ctx.profile ? { AWS_PROFILE: ctx.profile } : {}), ...env };
  try {
    return (ctx.node ?? execFileSync)(process.execPath, [join(ctx.repo, 'admin.mjs'), ...args], { encoding: 'utf8', env: childEnv, cwd: ctx.repo, stdio: inherit ? 'inherit' : ['ignore', 'pipe', 'pipe'] }) ?? '';
  } catch (e) {
    throw new InstallError(`admin.mjs ${args[0]} failed: ${String(e.stderr || e.message).trim().split('\n').slice(-3).join(' ')}`);
  }
}

/** Fetch the portal's front page (and the MCP version file) and say whether it works. */
export async function smoke(ctx, portalUrl) {
  const get = ctx.fetch ?? fetch;
  const checks = [];
  for (const path of ['', 'mcp-version.json']) {
    try { const r = await get(new URL(path, portalUrl).href); checks.push({ path: `/${path}`, ok: r.status === 200, status: r.status }); } catch (e) { checks.push({ path: `/${path}`, ok: false, status: String(e.message) }); }
  }
  return checks;
}

// ---------- install ----------
/**
 * First install. Try mode (no domain) deploys once. With a domain it stops after the first deploy and returns the DNS records to add
 * (`needs: 'dns'`); `finishDomain` then attaches the portal's address. A wizard calls both in a row; an editor tool calls them separately.
 */
export async function install(ctx, input) {
  const config = normalise({ ...input, profile: ctx.profile, stack: ctx.stack });
  const found = preflight(ctx);
  if (found.stackStatus === 'ROLLBACK_COMPLETE') throw new InstallError(`The stack "${ctx.stack}" is in a failed state (ROLLBACK_COMPLETE) from an earlier attempt.`, `Delete it first: aws cloudformation delete-stack --stack-name ${ctx.stack}${ctx.profile ? ` --profile ${ctx.profile}` : ''}`);
  if (found.stackStatus && !found.stackStatus.endsWith('_FAILED') && !/ROLLBACK/.test(found.stackStatus)) throw new InstallError(`The stack "${ctx.stack}" already exists (${found.stackStatus}).`, `Use \`${cli(ctx)} upgrade\` to change or update it.`);
  const result = { ok: true, account: found.account, stack: ctx.stack, steps: [], notes: found.notes };
  if (config.domain) {
    const cert = await ensureCertificate(ctx, config.domain);
    result.certificate = cert;
    if (!cert.issued) {
      writeState(ctx, { ...config, certificateArn: cert.arn, phase: 'certificate', account: found.account });
      return { ...result, needs: 'certificate', message: `Add this DNS record at your DNS host so AWS can issue the certificate, then run the install again.`, record: cert.record };
    }
    config.certificateArn = cert.arn;
  }
  const bucket = ensureArtifactBucket(ctx, found.account);
  const packaged = packageTemplate(ctx, bucket);
  call(ctx, deployArgs(ctx, packaged, parametersFor(config)));
  result.steps.push('stack deployed');
  const { outputs } = ctx.dryRun ? { outputs: {} } : stackOutputs(ctx);
  writeState(ctx, { ...config, account: found.account, phase: config.domain ? 'dns' : 'ready', installedVersion: repoVersion(ctx), installedAt: ctx.now() });
  if (config.domain) return { ...result, needs: 'dns', records: dnsRecords(config.domain, outputs), message: 'Add these DNS records, then finish the domain step.' };
  return { ...result, ...(await finishInstall(ctx, config, outputs)) };
}

/** After the stack exists (and, with a domain, after DNS): publish the portal pages and check it answers. */
async function finishInstall(ctx, config, outputs) {
  adminScript(ctx, ['portal-publish']);
  const portalUrl = outputs.PortalUrl ?? (config.domain ? `https://deploy.${config.domain}/` : '');
  const checks = ctx.dryRun || !portalUrl ? [] : await smoke(ctx, portalUrl);
  return { portalUrl, checks, steps: ['portal published'], next: `Create your teacher login: ${cli(ctx)} teacher <username>` };
}

/** The second step of a domain install: attach deploy.<domain> to the portal now that DNS points at it. */
export async function finishDomain(ctx) {
  const state = readState(ctx);
  if (!state?.domain || state.phase !== 'dns') throw new InstallError('There is no domain install waiting for DNS.', `Run \`${cli(ctx)} install --domain <name>\` first.`);
  const bucket = ensureArtifactBucket(ctx, state.account);
  const packaged = packageTemplate(ctx, bucket);
  call(ctx, deployArgs(ctx, packaged, parametersFor(state, { aliasOn: true })));
  const { outputs } = ctx.dryRun ? { outputs: {} } : stackOutputs(ctx);
  writeState(ctx, { ...state, phase: 'ready', installedVersion: repoVersion(ctx), installedAt: ctx.now() });
  return { ok: true, ...(await finishInstall(ctx, state, outputs)) };
}

/** Set the teacher login. The password never travels through an editor tool: the wizard reads it with a hidden prompt, or the admin script asks. */
export function teacherLogin(ctx, username, password) {
  adminScript(ctx, ['teacher-setup', username], { env: password ? { TEACHER_PASSWORD: password } : {}, inherit: !password });
  return { ok: true, username };
}

// ---------- is a newer release out? ----------
export const newerThan = (a, b) => {
  const pa = String(a).split('.').map(Number);
  const pb = String(b).split('.').map(Number);
  for (let i = 0; i < 3; i += 1) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0); }
  return false;
};

/** The repository this package was released from (stamped when it was built), or null for a git checkout. */
export function releaseInfo(ctx) {
  try { const r = JSON.parse(readFileSync(join(ctx.repo, 'release.json'), 'utf8')); return r.repo ? r : null; } catch { return null; }
}

/** Ask GitHub for the newest release of that repository (two seconds at most; null when it cannot say). Only a version number is read from the answer. */
export async function latestRelease(ctx) {
  const info = releaseInfo(ctx);
  if (!info) return null;
  try {
    const response = await (ctx.fetch ?? fetch)(`https://api.github.com/repos/${info.repo}/releases/latest`, { signal: AbortSignal.timeout(2000), headers: { accept: 'application/vnd.github+json' } });
    if (response.status !== 200) return null;
    const version = String((await response.json()).tag_name ?? '').replace(/^v/, '');
    if (!/^\d+\.\d+\.\d+$/.test(version)) return null;
    return { version, command: `npx -y https://github.com/${info.repo}/releases/download/v${version}/class-platform-teacher-${version}.tgz upgrade` };
  } catch { return null; }
}

// ---------- status ----------
export async function status(ctx) {
  const state = adoptState(ctx);
  const info = { ok: true, repoVersion: repoVersion(ctx), installedVersion: state?.installedVersion ?? null, installed: false };
  if (!state) return { ...info, message: `Nothing is installed yet. Run \`${cli(ctx)} install\`.` };
  const stackName = state.stack ?? ctx.stack;
  const now = stackOutputs({ ...ctx, stack: stackName });
  const features = Object.entries(FEATURES).filter(([, p]) => now.parameters[p] === 'true').map(([f]) => f);
  const upgrade = info.installedVersion !== info.repoVersion; // an adopted install has no recorded version: the next plan will say what differs
  const latest = await latestRelease(ctx);
  const newer = latest && newerThan(latest.version, info.repoVersion);
  return { ...info, installed: true, stack: stackName, stackStatus: now.status, portalUrl: now.outputs.PortalUrl ?? null, domain: now.parameters.RootDomain || null, features, phase: state.phase,
    upgradeAvailable: upgrade, latestVersion: latest?.version ?? null, newerRelease: Boolean(newer), upgradeCommand: newer ? latest.command : null,
    message: newer ? `Version ${latest.version} is out (this is ${info.repoVersion}). To update: ${latest.command}` : upgrade ? (info.installedVersion ? `This copy is version ${info.repoVersion}; the account runs ${info.installedVersion}. Run \`${cli(ctx)} upgrade\`.` : `Installed from another copy. To compare with this version (${info.repoVersion}) run \`${cli(ctx)} plan\`; nothing changes until you apply.`) : 'Up to date with this folder.' };
}

// ---------- plan, apply ----------
const summarise = (changes) => changes.map((c) => c.ResourceChange).filter(Boolean).map((r) => ({ action: r.Action, id: r.LogicalResourceId, type: r.ResourceType, replacement: r.Replacement ?? 'False' }));

export const planId = (changeSetId, items) => createHash('sha256').update(JSON.stringify([changeSetId, items])).digest('hex').slice(0, 16);

/** Describe what a deploy would change, in plain words, without changing anything. `change` edits the stored configuration (for example turning a feature on). */
export async function plan(ctx, change = {}) {
  const state = adoptState(ctx);
  if (!state) throw new InstallError('Nothing is installed from this folder yet.', `Run \`${cli(ctx)} install\` first.`);
  if (state.phase !== 'ready') throw new InstallError(`The install is not finished (step: ${state.phase}).`, 'Finish the install first.');
  const config = normalise({ ...state, ...change, features: change.features ?? state.features, stack: state.stack, profile: ctx.profile || state.profile });
  const found = preflight({ ...ctx, stack: config.stack });
  const bucket = ensureArtifactBucket({ ...ctx, stack: config.stack }, found.account);
  const packaged = packageTemplate({ ...ctx, stack: config.stack }, bucket);
  call(ctx, deployArgs({ ...ctx, stack: config.stack }, packaged, parametersFor(config, { aliasOn: Boolean(config.domain) }), ['--no-execute-changeset']));
  if (ctx.dryRun) return { ok: true, dryRun: true, would: ctx.would };
  const listed = call(ctx, ['cloudformation', 'list-change-sets', '--stack-name', config.stack], { json: true }).Summaries ?? [];
  const latest = listed.filter((s) => s.Status === 'CREATE_COMPLETE').sort((a, b) => String(b.CreationTime).localeCompare(String(a.CreationTime)))[0];
  if (!latest) return { ok: true, empty: true, message: 'Nothing would change.', fromVersion: state.installedVersion, toVersion: repoVersion(ctx) };
  const described = call(ctx, ['cloudformation', 'describe-change-set', '--stack-name', config.stack, '--change-set-name', latest.ChangeSetId], { json: true });
  const items = summarise(described.Changes ?? []);
  const risky = items.filter((i) => STATEFUL.has(i.type) && (i.action === 'Remove' || i.replacement !== 'False'));
  const id = planId(latest.ChangeSetId, items);
  const record = { id, changeSetId: latest.ChangeSetId, config, items, risky: risky.length, fromVersion: state.installedVersion, toVersion: repoVersion(ctx), at: ctx.now() };
  writeState(ctx, { ...state, plan: record });
  const count = (a) => items.filter((i) => i.action === a).length;
  return { ok: true, planId: id, fromVersion: state.installedVersion, toVersion: repoVersion(ctx), adds: count('Add'), modifies: count('Modify'), removes: count('Remove'),
    replacements: items.filter((i) => i.replacement !== 'False').length, blocked: risky.length > 0, items,
    message: risky.length ? `Blocked: this change would replace or remove data-holding resources (${risky.map((r) => r.id).join(', ')}). Nothing was changed.`
      : `This update adds ${count('Add')}, modifies ${count('Modify')} and removes ${count('Remove')} resources and replaces no data. Apply it with plan id ${id}.` };
}

/** Run exactly the plan that was shown (same id), then publish the portal. A changed or missing plan is refused. */
export async function apply(ctx, id) {
  const state = readState(ctx);
  const saved = state?.plan;
  if (!saved || saved.id !== id) throw new InstallError('That plan is not the one that was last shown.', 'Create a new plan and apply the id it prints.');
  if (saved.risky) throw new InstallError('This plan would replace or remove data-holding resources, so it will not be applied.');
  const described = call(ctx, ['cloudformation', 'describe-change-set', '--stack-name', saved.config.stack, '--change-set-name', saved.changeSetId], { json: true });
  if (described.Status !== 'CREATE_COMPLETE' || planId(saved.changeSetId, summarise(described.Changes ?? [])) !== id) throw new InstallError('The change set changed or expired since it was shown.', 'Create a new plan.');
  call(ctx, ['cloudformation', 'execute-change-set', '--stack-name', saved.config.stack, '--change-set-name', saved.changeSetId]);
  call(ctx, ['cloudformation', 'wait', 'stack-update-complete', '--stack-name', saved.config.stack]);
  const { plan: _done, ...rest } = state;
  const { outputs } = stackOutputs({ ...ctx, stack: saved.config.stack });
  writeState(ctx, { ...rest, ...saved.config, installedVersion: saved.toVersion, installedAt: ctx.now() });
  adminScript({ ...ctx, stack: saved.config.stack }, ['portal-publish']);
  return { ok: true, version: saved.toVersion, portalUrl: outputs.PortalUrl, message: `Updated to ${saved.toVersion} and published the portal pages, the student kit and the MCP package.` };
}

/** Feature switch = a plan with that feature turned on or off. */
export const featurePlan = (ctx, feature, on) => {
  const state = readState(ctx) ?? adoptState(ctx);
  if (!FEATURES[feature]) throw new InstallError(`Unknown feature "${feature}".`, `Choose from: ${Object.keys(FEATURES).join(', ')}.`);
  const features = new Set(state?.features ?? []);
  if (on) features.add(feature); else features.delete(feature);
  return plan(ctx, { features: [...features] });
};

// ---------- secrets (AI keys) ----------
/** Store the AI keys in Secrets Manager (the name the template reads). Values come from a hidden prompt in the teacher's terminal, never from an editor. */
export function saveAiSecret(ctx, { managementKey, googleKey, name = 'openrouter' }) {
  const value = JSON.stringify({ ...(managementKey ? { OPENROUTER_MANAGEMENT_KEY: managementKey } : {}), ...(googleKey ? { GOOGLE_API_KEY: googleKey } : {}) });
  let exists = true;
  try { call(ctx, ['secretsmanager', 'describe-secret', '--secret-id', name]); } catch { exists = false; }
  if (ctx.dryRun) { ctx.would.push(`aws secretsmanager ${exists ? 'put-secret-value' : 'create-secret'} --name ${name} (value hidden)`); return { ok: true, name }; }
  if (exists) {
    const old = JSON.parse(call(ctx, ['secretsmanager', 'get-secret-value', '--secret-id', name, '--query', 'SecretString', '--output', 'text']) || '{}');
    ctx.aws(['secretsmanager', 'put-secret-value', '--secret-id', name, '--secret-string', JSON.stringify({ ...old, ...JSON.parse(value) })]);
  } else {
    ctx.aws(['secretsmanager', 'create-secret', '--name', name, '--secret-string', value]);
  }
  return { ok: true, name };
}

// ---------- environment check, destroy ----------
export function checkEnvironment(ctx) {
  const out = { ok: true, node: process.versions.node, repo: ctx.repo, template: existsSync(join(ctx.repo, 'template.yaml')) };
  if (!out.template) return { ...out, ok: false, message: 'This folder is not the platform repository (no template.yaml).' };
  const found = preflight(ctx);
  return { ...out, account: found.account, identity: found.arn, stackStatus: found.stackStatus, notes: found.notes, message: `Ready. AWS account ${found.account}${found.stackStatus ? `, stack ${ctx.stack}: ${found.stackStatus}` : ', no stack yet'}.` };
}

/** Delete the stack. Buckets are emptied first (CloudFormation cannot delete a full bucket); tables and secrets marked Retain stay and are reported. */
export function destroy(ctx) {
  const { outputs } = stackOutputs(ctx);
  if (outputs.BucketName) call(ctx, ['s3', 'rm', `s3://${outputs.BucketName}`, '--recursive']);
  call(ctx, ['cloudformation', 'delete-stack', '--stack-name', ctx.stack]);
  call(ctx, ['cloudformation', 'wait', 'stack-delete-complete', '--stack-name', ctx.stack]);
  return { ok: true, message: `Stack ${ctx.stack} deleted. Resources marked Retain (the data tables) and any DNS records and the AI secret are still there on purpose; remove them by hand if you want a clean account.` };
}
