// Export and import (installer/data.mjs) with a fake AWS CLI: a backup folder with everything a class made, an import that adds it to a target and
// repeats safely, the transfer variant that drops what only exists in the old account, secrets that never appear on a command line, and a dry run.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { exportData, importData, transformForTransfer, writeItems } from '../installer/data.mjs';

const work = mkdtempSync(join(tmpdir(), 'data-'));
const repo = join(work, 'repo');
mkdirSync(repo);
writeFileSync(join(repo, 'VERSION'), '1.1.0\n');
const S = (v) => ({ S: v });
const TEACHER_SECRET = 'teacher-hash-and-secret-value';

const tokenItems = [
  { pk: S('tok#aaa'), slug: S('alice'), ai: { M: { enabled: { BOOL: true }, key: S('sk-or-student-key'), provider: S('openrouter') } },
    backend: { M: { enabled: { BOOL: true }, runs_limit: { N: '25' }, runs_period: S('month'), state: S('ready'), function_name: S('sd-alice'), env_names: { L: [S('A')] } } },
    domain: S('www.alice.example'), domain_state: S('active'), tenant_id: S('t-1'), domain_enabled: { BOOL: true } },
  { pk: S('slug#alice'), token: S('aaa') },
  { pk: S('tok#bbb'), slug: S('bob'), backend: { M: { state: S('failed'), function_name: S('sd-bob') } } },
  { pk: S('domain#www.alice.example'), token: S('aaa') },
  { pk: S('sbytes#alice'), count: { N: '1234' } },
];
const stored = [{ s: S('alice#notes'), id: S('n1'), n: { N: '10' } }];

function fakeAws({ outputs = {}, status = 'CREATE_COMPLETE', parameters = {}, unprocessedOnce = false, tokens = tokenItems } = {}) {
  const calls = [];
  const writes = [];
  const putValues = [];
  let retried = false;
  const out = { PortalUrl: 'https://p.example/', BucketName: 'bkt', TokensTableName: 'TokTable', DistributionId: 'DIST', KeysParameterName: '/k', TeacherParameterName: '/t', StudentDataTableName: 'DataTable', ...outputs };
  const fn = (args) => {
    calls.push(args);
    const [svc, op] = args;
    const opt = (n) => args[args.indexOf(n) + 1];
    if (svc === 'sts') return JSON.stringify({ Account: '111122223333' });
    if (svc === 'cloudformation' && op === 'describe-stacks') {
      if (!status) throw new Error('does not exist');
      return JSON.stringify({ Stacks: [{ StackStatus: status, Outputs: Object.entries(out).filter(([, v]) => v).map(([OutputKey, OutputValue]) => ({ OutputKey, OutputValue })), Parameters: Object.entries(parameters).map(([ParameterKey, ParameterValue]) => ({ ParameterKey, ParameterValue })) }] });
    }
    if (svc === 'dynamodb' && op === 'scan') return JSON.stringify({ Items: opt('--table-name') === 'TokTable' ? tokens : stored });
    if (svc === 'dynamodb' && op === 'batch-write-item') {
      const body = JSON.parse(readFileSync(opt('--request-items').replace('file://', ''), 'utf8'));
      const [table, requests] = Object.entries(body)[0];
      if (unprocessedOnce && !retried) { retried = true; writes.push({ table, items: requests.slice(0, 1).map((r) => r.PutRequest.Item) }); return JSON.stringify({ UnprocessedItems: { [table]: requests.slice(1) } }); }
      writes.push({ table, items: requests.map((r) => r.PutRequest.Item) });
      return JSON.stringify({ UnprocessedItems: {} });
    }
    if (svc === 'ssm' && op === 'get-parameter') return `${opt('--name') === '/t' ? TEACHER_SECRET : '{"alice":"key"}'}\n`;
    if (svc === 'ssm' && op === 'put-parameter') { putValues.push(JSON.parse(readFileSync(opt('--cli-input-json').replace('file://', ''), 'utf8'))); return ''; }
    if (svc === 's3' && op === 'sync') { if (args[2].startsWith('s3://')) { mkdirSync(args[3], { recursive: true }); writeFileSync(join(args[3], 'index.html'), '<h1>site</h1>'); } return ''; }
    if (svc === 'cloudfront' && op === 'create-invalidation') return '{}';
    throw new Error(`fake aws: unexpected ${args.join(' ')}`);
  };
  return { fn, calls, writes, putValues };
}
const context = (fake, extra = {}) => ({ repo, profile: 'p', stack: 'student-hosting', dryRun: false, would: [], now: () => '2026-01-01T00:00:00Z', sleep: async () => {}, aws: fake.fn, ...extra });

// ---------- transfer transform ----------
{
  const { items, domains } = transformForTransfer(tokenItems);
  assert.ok(!items.some((i) => i.pk.S.startsWith('domain#')), 'custom-domain locks are dropped');
  const alice = items.find((i) => i.pk.S === 'tok#aaa');
  assert.deepEqual(Object.keys(alice.backend.M).sort(), ['enabled', 'runs_limit', 'runs_period'], 'what the teacher chose stays; the old account\'s function and state go');
  assert.ok(!alice.domain && !alice.domain_state && !alice.tenant_id, 'the old tenant and domain state go');
  assert.equal(alice.domain_enabled.BOOL, true, 'the teacher\'s switch stays');
  assert.equal(alice.ai.M.key.S, 'sk-or-student-key', 'the student AI key stays (it belongs to the AI provider, not to the old AWS account)');
  assert.ok(!items.find((i) => i.pk.S === 'tok#bbb').backend, 'a backend with only platform state disappears entirely');
  assert.deepEqual(domains, [{ slug: 'alice', domain: 'www.alice.example', state: 'active' }]);
  assert.ok(tokenItems[0].backend.M.function_name, 'the input is not modified');
  console.log('  ok  transfer transform: old-account functions, tenants and domain locks dropped; teacher settings and AI keys kept; domains listed');
}

// ---------- export ----------
const backup = join(work, 'backup');
{
  const fake = fakeAws({ parameters: { RootDomain: 'apps.example.com', BackendEnabled: 'true', StorageEnabled: 'true', AiEnabled: 'false' } });
  const said = [];
  const result = exportData(context(fake), backup, { say: (t) => said.push(t) });
  assert.equal(result.ok, true);
  assert.deepEqual(result.counts, { tokens: 5, storedItems: 1 });
  for (const f of ['manifest.json', 'tokens.json', 'studentdata.json', 'ssm.json', 's3/sites/index.html', 's3/archive/index.html', 's3/userdata/index.html']) assert.ok(existsSync(join(backup, f)), f);
  const manifest = JSON.parse(readFileSync(join(backup, 'manifest.json'), 'utf8'));
  assert.deepEqual([manifest.format, manifest.platformVersion, manifest.domain, manifest.features.sort()], [1, '1.1.0', 'apps.example.com', ['BackendEnabled', 'StorageEnabled']]);
  assert.equal(JSON.parse(readFileSync(join(backup, 'ssm.json'), 'utf8')).teacher, TEACHER_SECRET);
  assert.ok(!result.message.includes(TEACHER_SECRET) && !JSON.stringify(manifest).includes(TEACHER_SECRET), 'the secret is in its file only, never in messages or the manifest');
  assert.match(result.message, /keep it private/);
  assert.ok(said.length >= 3, 'progress is reported');
  try { if (process.platform !== 'win32') assert.equal(statSync(backup).mode & 0o077, 0, 'the folder is private'); } catch (e) { throw e; }
  assert.throws(() => exportData(context(fakeAws()), backup), /not empty/, 'an old backup is never mixed with a new one');
  assert.throws(() => exportData(context(fakeAws({ status: null })), join(work, 'x')), /no stack/);
  console.log('  ok  export: tokens, stored data, files, teacher login and key map in a private folder; secrets stay in their file; a used folder is refused');
}

// ---------- import: a faithful restore, repeatable, secrets off the command line ----------
{
  const many = Array.from({ length: 60 }, (_, i) => ({ pk: S(`tok#${i}`), slug: S(`s${i}`) }));
  const big = join(work, 'big');
  const src = fakeAws({ tokens: many });
  exportData(context(src), big);
  const fake = fakeAws({ unprocessedOnce: true, tokens: [] }); // a freshly installed platform: no students yet
  const result = importData(context(fake), big);
  const tokenWrites = fake.writes.filter((w) => w.table === 'TokTable');
  assert.equal(tokenWrites.reduce((n, w) => n + w.items.length, 0), 60 + 0, 'every item written exactly once (the unprocessed one was retried)');
  assert.ok(tokenWrites.every((w) => w.items.length <= 25), 'batches of at most 25');
  assert.ok(fake.writes.some((w) => w.table === 'DataTable'), 'stored student data is restored');
  assert.deepEqual(fake.putValues.map((v) => v.Name).sort(), ['/k', '/t']);
  assert.ok(fake.putValues.every((v) => v.Overwrite === true));
  assert.ok(!fake.calls.flat().join(' ').includes(TEACHER_SECRET), 'the teacher secret never appears on a command line');
  assert.ok(fake.calls.some((a) => a[1] === 'create-invalidation'), 'the CDN is refreshed');
  assert.ok(fake.calls.filter((a) => a[1] === 'sync').every((a) => !a.join(' ').includes('--delete')), 'import never deletes anything');
  assert.deepEqual(result.counts, { tokens: 60, storedItems: 1 });
  console.log('  ok  import: all 60 items written once in batches of 25 (unprocessed ones retried); no secret on a command line; nothing deleted');
}

// ---------- import: one platform, one class ----------
{
  const target = (tokens) => fakeAws({ tokens });
  const otherStudent = [{ pk: S('tok#zzz'), slug: S('carol') }];
  // the same backup again, into the platform it was restored to, is a safe repeat
  const repeat = target(tokenItems);
  assert.equal(importData(context(repeat), backup).ok, true, 'repeating the same backup is allowed');
  // a freshly installed and verified platform only holds the revoked throwaway student of `verify`
  const verified = target([{ pk: S('tok#vvv'), slug: S('zz-verify-4821'), revoked: { BOOL: true } }]);
  assert.equal(importData(context(verified), backup).ok, true, 'the throwaway student of verify does not count');
  // another class in the target: refused, nothing written, the reason and the way out are named
  const live = target(otherStudent);
  assert.throws(() => importData(context(live), backup), (e) => /already has 1 student that is not in this backup \(carol\)/.test(e.message) && /--merge/.test(e.hint) && /fresh/.test(e.hint));
  assert.equal(live.writes.length + live.putValues.length, 0, 'a refused import writes nothing');
  // the same site name for a different student: refused even with --merge
  const clash = target([{ pk: S('tok#new'), slug: S('alice') }]);
  assert.throws(() => importData(context(clash), backup, { merge: true }), /same site name.*alice/);
  assert.equal(clash.writes.length + clash.putValues.length, 0);
  // --merge adds the class and keeps the platform's own teacher login and key map
  const merged = target(otherStudent);
  const said = [];
  const result = importData(context(merged), backup, { merge: true, say: (m) => said.push(m) });
  assert.equal(result.ok, true);
  assert.ok(merged.writes.some((w) => w.table === 'TokTable'), 'the backup students are written');
  assert.equal(merged.putValues.length, 0, 'the teacher login and the key map of the existing platform are not replaced');
  assert.ok(said.some((m) => /Keeping the teacher login/.test(m)));
  // a transfer goes into a platform that was just installed for it and is not checked this way
  assert.equal(importData(context(target(otherStudent)), backup, { forTransfer: true }).ok, true);
  console.log('  ok  import is for a freshly installed platform: a repeat and a verified install pass; other students are refused; a name clash is refused even with --merge; --merge keeps the existing teacher login');
}

// ---------- import for a transfer, and its refusals ----------
{
  const fake = fakeAws();
  const result = importData(context(fake), backup, { forTransfer: true });
  const written = fake.writes.find((w) => w.table === 'TokTable').items;
  assert.ok(!written.some((i) => i.pk.S.startsWith('domain#')) && !written.find((i) => i.pk.S === 'tok#aaa').domain);
  assert.deepEqual(result.domains.map((d) => d.domain), ['www.alice.example']);
  assert.match(result.message, /1 student custom domain must be added again/);
  assert.throws(() => importData(context(fakeAws()), join(work, 'nothing')), /not a backup/);
  assert.throws(() => importData(context(fakeAws({ status: null })), backup), /no stack/);
  assert.throws(() => importData(context(fakeAws({ outputs: { StudentDataTableName: '' } })), backup), /class storage is not switched on/);
  const future = join(work, 'future');
  mkdirSync(future);
  writeFileSync(join(future, 'manifest.json'), JSON.stringify({ format: 99 }));
  assert.throws(() => importData(context(fakeAws()), future), /format 99/);
  console.log('  ok  transfer import drops old-account state and lists the domains; a missing, empty-target or newer-format backup is refused with the reason');
}

// ---------- a dry run changes nothing ----------
{
  const fake = fakeAws();
  const dry = join(work, 'dry-export');
  exportData(context(fake, { dryRun: true }), dry);
  assert.ok(!existsSync(dry), 'a dry export writes no folder');
  const target = fakeAws();
  importData(context(target, { dryRun: true }), backup);
  assert.equal(target.writes.length, 0, 'a dry import writes no items');
  assert.equal(target.putValues.length, 0);
  console.log('  ok  dry run: no folder, no writes');
}

try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may hold the folder a moment */ }
console.log('test_data.mjs: all passed');
