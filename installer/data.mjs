// Export and import of everything a class has made: the student tokens and their settings, stored student data, the published sites and files, the
// teacher login and the student key map. A folder on the teacher's computer holds it. It is a backup on its own and the data step of a move between AWS
// accounts (see transfer). Nothing here deletes anything: import only adds or overwrites on the target.
//
// What is NOT in a backup, on purpose: the AI keys in Secrets Manager (they are re-entered with `secrets`), student backend functions and custom
// domains (they live in the old account's Lambda and CloudFront; students publish their backend again and add one DNS record, see the transfer CSV).
//
// The folder contains student AI keys and the teacher login hash: keep it private and delete it when the move is done.
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { awsCall, InstallError, repoVersion, stackOutputs } from './engine.mjs';

export const FORMAT = 1;
const BATCH = 25;
const FOLDERS = ['sites', 'archive', 'userdata']; // the bucket's prefixes that hold a class's files (portal/ is republished, uploads/ is temporary)
// settings a TEACHER chooses stay; everything the PLATFORM made in the old account (functions, tenants, states) is dropped on a transfer
const BACKEND_TEACHER_KEYS = new Set(['enabled', 'runs_limit', 'runs_period']);
const DOMAIN_FIELDS = ['domain', 'domain_state', 'tenant_id', 'domain_removing'];

const text = (value) => `${JSON.stringify(value, null, 1)}\n`;
const readJson = (file) => JSON.parse(readFileSync(file, 'utf8'));
const secretFile = (data) => { // a temporary file for a value that must not appear on a command line
  const dir = mkdtempSync(join(tmpdir(), 'cp-secret-'));
  const file = join(dir, 'v.json');
  writeFileSync(file, data);
  try { chmodSync(file, 0o600); } catch { /* Windows */ }
  return { file, done: () => { try { rmSync(dir, { recursive: true, force: true }); } catch { /* removed with the system's temp clean-up */ } } };
};

/** The stack's outputs and parameters, or {status: null} when there is no such stack (the CLI reports that as an error). */
function lookup(ctx) {
  try { return stackOutputs(ctx); } catch { return { status: null, outputs: {}, parameters: {} }; }
}

export function scanAll(ctx, table) {
  return awsCall(ctx, ['dynamodb', 'scan', '--table-name', table], { json: true }).Items ?? []; // the CLI follows every page
}

/** Write items in batches of 25, retrying the ones DynamoDB sends back unprocessed. */
export function writeItems(ctx, table, items) {
  for (let i = 0; i < items.length; i += BATCH) {
    let pending = items.slice(i, i + BATCH).map((Item) => ({ PutRequest: { Item } }));
    for (let attempt = 0; pending.length && attempt < 8; attempt += 1) {
      const file = secretFile(JSON.stringify({ [table]: pending }));
      try {
        const answer = awsCall(ctx, ['dynamodb', 'batch-write-item', '--request-items', `file://${file.file}`], { json: true });
        pending = ctx.dryRun ? [] : (answer.UnprocessedItems?.[table] ?? []);
      } finally { file.done(); }
    }
    if (pending.length) throw new InstallError(`DynamoDB did not accept ${pending.length} items of ${table} after several tries.`, 'Run the import again; it is safe to repeat.');
  }
}

/** For a move to another account: drop what only exists in the old account, and note the custom domains for the DNS file. */
export function transformForTransfer(items) {
  const domains = [];
  const out = [];
  for (const raw of items) {
    if (String(raw.pk?.S ?? '').startsWith('domain#')) continue; // custom-domain locks: they point at the old account's CloudFront
    const item = JSON.parse(JSON.stringify(raw));
    const slug = item.slug?.S;
    if (item.backend?.M) {
      item.backend.M = Object.fromEntries(Object.entries(item.backend.M).filter(([k]) => BACKEND_TEACHER_KEYS.has(k)));
      if (!Object.keys(item.backend.M).length) delete item.backend;
    }
    if (item.domain?.S) domains.push({ slug, domain: item.domain.S, state: item.domain_state?.S ?? '' });
    for (const field of DOMAIN_FIELDS) delete item[field];
    out.push(item);
  }
  return { items: out, domains };
}

export function exportData(ctx, folder, { say = () => {} } = {}) {
  const found = lookup(ctx);
  if (!found.status) throw new InstallError(`There is no stack "${ctx.stack}" in this account.`, 'Check the profile and stack name.');
  if (existsSync(folder) && readdirSync(folder).length) throw new InstallError(`${folder} is not empty.`, 'Choose a new or empty folder, so an old backup is never mixed with a new one.');
  const o = found.outputs;
  if (!ctx.dryRun) {
    mkdirSync(folder, { recursive: true });
    try { chmodSync(folder, 0o700); } catch { /* Windows */ }
  }
  say('Reading the student tokens and settings...');
  const tokens = scanAll(ctx, o.TokensTableName);
  const stored = o.StudentDataTableName ? scanAll(ctx, o.StudentDataTableName) : [];
  say('Copying the published sites and files...');
  for (const prefix of FOLDERS) awsCall(ctx, ['s3', 'sync', `s3://${o.BucketName}/${prefix}`, join(folder, 's3', prefix), '--only-show-errors']);
  say('Reading the teacher login and the key map...');
  const ssm = {};
  for (const [name, param] of [['keys', o.KeysParameterName], ['teacher', o.TeacherParameterName]]) {
    if (!param) continue;
    ssm[name] = ctx.dryRun ? '' : awsCall(ctx, ['ssm', 'get-parameter', '--name', param, '--query', 'Parameter.Value', '--output', 'text']).trim();
  }
  const manifest = { format: FORMAT, createdAt: ctx.now(), platformVersion: repoVersion(ctx), stack: ctx.stack, account: awsCall(ctx, ['sts', 'get-caller-identity'], { json: true }).Account ?? '',
    domain: found.parameters.RootDomain || '', features: Object.entries(found.parameters).filter(([k, v]) => /Enabled$/.test(k) && v === 'true').map(([k]) => k),
    counts: { tokens: tokens.length, storedItems: stored.length }, hasStudentData: Boolean(o.StudentDataTableName) };
  if (!ctx.dryRun) {
    writeFileSync(join(folder, 'tokens.json'), text(tokens));
    writeFileSync(join(folder, 'studentdata.json'), text(stored));
    writeFileSync(join(folder, 'ssm.json'), text(ssm));
    writeFileSync(join(folder, 'manifest.json'), text(manifest));
  }
  return { ok: true, folder, counts: manifest.counts, manifest,
    message: `Backed up ${tokens.length} token records, ${stored.length} stored student items, the published files and the teacher login to ${folder}. It holds student AI keys and the teacher login hash: keep it private and delete it when you no longer need it.` };
}

export function importData(ctx, folder, { forTransfer = false, say = () => {} } = {}) {
  if (!existsSync(join(folder, 'manifest.json'))) throw new InstallError(`${folder} is not a backup made by export (no manifest.json).`);
  const manifest = readJson(join(folder, 'manifest.json'));
  if (manifest.format !== FORMAT) throw new InstallError(`This backup has format ${manifest.format}; this version reads format ${FORMAT}.`, 'Use the same or a newer release.');
  const found = lookup(ctx);
  if (!found.status) throw new InstallError(`There is no stack "${ctx.stack}" in the target account.`, 'Install the platform there first.');
  const o = found.outputs;
  if (manifest.hasStudentData && !o.StudentDataTableName && readJson(join(folder, 'studentdata.json')).length) {
    throw new InstallError('The backup holds stored student data, but class storage is not switched on in the target.', 'Switch it on first (plan --enable storage, then apply), then import.');
  }
  let items = readJson(join(folder, 'tokens.json'));
  let domains = [];
  if (forTransfer) ({ items, domains } = transformForTransfer(items));
  say(`Writing ${items.length} token records...`);
  writeItems(ctx, o.TokensTableName, items);
  const stored = readJson(join(folder, 'studentdata.json'));
  if (stored.length && o.StudentDataTableName) { say(`Writing ${stored.length} stored student items...`); writeItems(ctx, o.StudentDataTableName, stored); }
  say('Copying the published sites and files...');
  for (const prefix of FOLDERS) {
    if (ctx.dryRun || existsSync(join(folder, 's3', prefix))) awsCall(ctx, ['s3', 'sync', join(folder, 's3', prefix), `s3://${o.BucketName}/${prefix}`, '--only-show-errors']);
  }
  const ssm = readJson(join(folder, 'ssm.json'));
  for (const [name, param] of [['keys', o.KeysParameterName], ['teacher', o.TeacherParameterName]]) {
    if (!ssm[name] || !param) continue;
    const file = secretFile(JSON.stringify({ Name: param, Type: 'String', Overwrite: true, Value: ssm[name] }));
    try { awsCall(ctx, ['ssm', 'put-parameter', '--cli-input-json', `file://${file.file}`]); } finally { file.done(); }
  }
  if (o.DistributionId) awsCall(ctx, ['cloudfront', 'create-invalidation', '--distribution-id', o.DistributionId, '--paths', '/*']);
  return { ok: true, counts: { tokens: items.length, storedItems: stored.length }, domains,
    message: `Imported ${items.length} token records and ${stored.length} stored student items, the published files and the teacher login into ${ctx.stack}.${domains.length ? ` ${domains.length} student custom domain${domains.length === 1 ? '' : 's'} must be added again (see the DNS file).` : ''}` };
}
