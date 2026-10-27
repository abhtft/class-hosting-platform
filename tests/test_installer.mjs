// The installer engine (installer/engine.mjs) and setup.mjs against a fake AWS CLI: try mode, domain mode (certificate, two-phase portal alias),
// the checks that fail early with a hint, plan and apply (bound to the plan that was shown, refusing data-holding replacements), feature switches,
// dry runs that change nothing, and that secrets never appear in what is reported.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as engine from '../installer/engine.mjs';
import { main, parseArgs, versionBanner } from '../setup.mjs';

const repo = mkdtempSync(join(tmpdir(), 'installer-'));
writeFileSync(join(repo, 'template.yaml'), 'Resources: {}\n');
writeFileSync(join(repo, 'VERSION'), '1.0.0\n');

function fakeAws(world = {}) {
  const calls = [];
  const w = { account: '111122223333', stack: null, buckets: new Set(), certs: [], outputs: { PortalUrl: 'https://dp.cloudfront.net/', PortalDistributionDomain: 'dp.cloudfront.net', DistributionDomain: 'd1.cloudfront.net', BucketName: 'sites-bucket' },
    parameters: {}, changeSets: [], cloudfrontError: null, secrets: {}, ...world };
  const fn = (args) => {
    calls.push(args);
    const [svc, op] = args;
    const opt = (name) => args[args.indexOf(name) + 1];
    if (svc === 'sts') return JSON.stringify({ Account: w.account, Arn: `arn:aws:iam::${w.account}:user/me` });
    if (svc === 'cloudformation' && op === 'list-stack-resources') { if (!w.stack) throw new Error('Stack does not exist'); return JSON.stringify({ StackResourceSummaries: w.resources ?? [] }); }
    if (svc === 'cloudformation' && op === 'delete-stack') { w.stack = null; (w.resources ?? []).filter((r) => !w.retain?.includes(r.PhysicalResourceId)).forEach((r) => { w.gone = [...(w.gone ?? []), r.PhysicalResourceId]; }); return ''; }
    if (svc === 's3api' && op === 'list-object-versions') { const v = w.versions?.[opt('--bucket')] ?? []; return JSON.stringify({ Versions: v.slice(0, 100) }); }
    if (svc === 's3api' && op === 'delete-objects') { const gone = new Set(JSON.parse(opt('--delete')).Objects.map((o) => o.VersionId)); w.versions[opt('--bucket')] = w.versions[opt('--bucket')].filter((o) => !gone.has(o.VersionId)); return ''; }
    if (svc === 's3api' && op === 'delete-bucket') { if ((w.versions?.[opt('--bucket')] ?? []).length) throw new Error('BucketNotEmpty'); w.buckets.delete(opt('--bucket')); return ''; }
    if (svc === 'dynamodb' && op === 'describe-table') { if (!w.tables?.has(opt('--table-name'))) throw new Error('ResourceNotFound'); return '{}'; }
    if (svc === 'dynamodb' && op === 'delete-table') { w.tables.delete(opt('--table-name')); return ''; }
    if (svc === 'ssm' && op === 'get-parameter') { if (!w.params?.has(opt('--name'))) throw new Error('ParameterNotFound'); return '{}'; }
    if (svc === 'ssm' && op === 'delete-parameter') { w.params.delete(opt('--name')); return ''; }
    if (svc === 'secretsmanager' && op === 'delete-secret') { delete w.secrets[opt('--secret-id')]; return ''; }
    if (svc === 'cloudfront') { if (w.cloudfrontError) throw new Error(w.cloudfrontError); return '{}'; }
    if (svc === 's3api' && op === 'head-bucket') { if (!w.buckets.has(opt('--bucket')) || (w.gone ?? []).includes(opt('--bucket'))) throw new Error('404'); return ''; }
    if (svc === 's3api' && op === 'create-bucket') { w.buckets.add(opt('--bucket')); return ''; }
    if (svc === 's3api') return '';
    if (svc === 'acm' && op === 'list-certificates') return JSON.stringify({ CertificateSummaryList: w.certs.map((c) => ({ DomainName: c.name, CertificateArn: c.arn })) });
    if (svc === 'acm' && op === 'request-certificate') { const c = { name: opt('--domain-name'), arn: 'arn:aws:acm:us-east-1:1:certificate/new', status: 'PENDING_VALIDATION' }; w.certs.push(c); return JSON.stringify({ CertificateArn: c.arn }); }
    if (svc === 'acm' && op === 'describe-certificate') { const c = w.certs.find((x) => x.arn === opt('--certificate-arn')); return JSON.stringify({ Certificate: { Status: c.status, DomainValidationOptions: [{ ResourceRecord: { Type: 'CNAME', Name: '_abc.apps.example.com.', Value: '_xyz.acm-validations.aws.' } }] } }); }
    if (svc === 'cloudformation' && op === 'describe-stacks') {
      if (!w.stack) throw new Error('Stack does not exist');
      return JSON.stringify({ Stacks: [{ StackStatus: w.stack, Outputs: Object.entries(w.outputs).map(([OutputKey, OutputValue]) => ({ OutputKey, OutputValue })),
        Parameters: Object.entries(w.parameters).map(([ParameterKey, ParameterValue]) => ({ ParameterKey, ParameterValue })) }] });
    }
    if (svc === 'cloudformation' && op === 'package') return '';
    if (svc === 'cloudformation' && op === 'deploy') {
      const params = args.slice(args.indexOf('--parameter-overrides') + 1).filter((a) => !a.startsWith('--') && a.includes('='));
      if (args.includes('--no-execute-changeset')) { if (w.nextChanges) w.changeSets.push({ id: `cs-${w.changeSets.length + 1}`, status: 'CREATE_COMPLETE', changes: w.nextChanges, params }); return ''; }
      w.stack = 'CREATE_COMPLETE';
      w.parameters = Object.fromEntries(params.map((p) => p.split('=')));
      return '';
    }
    if (svc === 'cloudformation' && op === 'list-change-sets') return JSON.stringify({ Summaries: w.changeSets.map((c, i) => ({ ChangeSetId: c.id, Status: c.status, CreationTime: String(1000 + i) })) });
    if (svc === 'cloudformation' && op === 'describe-change-set') { const c = w.changeSets.find((x) => x.id === opt('--change-set-name')); if (!c) throw new Error('no change set'); return JSON.stringify({ Status: c.status, Changes: c.changes.map((rc) => ({ ResourceChange: rc })) }); }
    if (svc === 'cloudformation' && op === 'execute-change-set') { const c = w.changeSets.find((x) => x.id === opt('--change-set-name')); w.parameters = { ...w.parameters, ...Object.fromEntries(c.params.map((p) => p.split('='))) }; return ''; }
    if (svc === 'cloudformation') return '';
    if (svc === 'secretsmanager' && op === 'describe-secret') { if (!w.secrets[opt('--secret-id')]) throw new Error('not found'); return '{}'; }
    if (svc === 'secretsmanager' && op === 'get-secret-value') return w.secrets[opt('--secret-id')];
    if (svc === 'secretsmanager' && op === 'create-secret') { w.secrets[opt('--name')] = opt('--secret-string'); return ''; }
    if (svc === 'secretsmanager' && op === 'put-secret-value') { w.secrets[opt('--secret-id')] = opt('--secret-string'); return ''; }
    if (svc === 's3') return '';
    throw new Error(`fake aws: unexpected ${args.join(' ')}`);
  };
  return { fn, calls, w };
}

const names = (calls) => calls.map((a) => `${a[0]} ${a[1]}`);
function context(fake, extra = {}) {
  const adminCalls = [];
  const ctx = { repo, profile: 'new', stack: 'student-hosting', dryRun: false, would: [], now: () => '2026-01-01T00:00:00Z', sleep: async () => {}, aws: fake.fn,
    node: (_exe, args, opts) => { adminCalls.push({ args: args.slice(1), env: opts.env }); return ''; }, fetch: async () => ({ status: 200 }), ...extra };
  return { ctx, adminCalls };
}
const stateOf = () => JSON.parse(readFileSync(join(repo, '.class-platform', 'install.json'), 'utf8'));
const reset = () => rmSync(join(repo, '.class-platform'), { recursive: true, force: true });
const fails = async (fn, pattern) => { try { await fn(); } catch (e) { assert.match(`${e.message} ${e.hint ?? ''}`, pattern); return e; } assert.fail(`expected an error matching ${pattern}`); };

// ---------- choices are checked and completed ----------
{
  assert.deepEqual(engine.normalise({ features: ['gemini'] }).features, ['ai', 'backend', 'gemini'], 'gemini brings ai and backend');
  assert.deepEqual(engine.normalise({ features: ['storage'] }).features, ['backend', 'storage']);
  assert.equal(engine.normalise({ domain: 'https://*.Apps.Example.com/' }).domain, 'apps.example.com');
  assert.throws(() => engine.normalise({ domain: 'not a domain' }), /not a valid domain/);
  assert.throws(() => engine.normalise({ features: ['teleport'] }), /Unknown feature/);
  assert.throws(() => engine.normalise({ stack: '1bad' }), /stack name/);
  const config = engine.normalise({ features: ['ai'], domain: 'apps.example.com', certificateArn: 'arn:x' });
  assert.deepEqual(engine.parametersFor(config), ['RootDomain=apps.example.com', 'CertificateArn=arn:x', 'PortalAlias=false', 'BackendEnabled=true', 'AiEnabled=true', 'AiGeminiEnabled=false', 'StorageEnabled=false', 'TenantDistributionId=', 'ConnectionGroupId=', 'RoutingEndpoint=']);
  assert.ok(engine.parametersFor(config, { aliasOn: true }).includes('PortalAlias=true'));
  assert.ok(engine.parametersFor(engine.normalise({})).includes('PortalAlias=false') && engine.parametersFor(engine.normalise({})).includes('RootDomain='), 'try mode: no domain, no alias');
  assert.deepEqual(parseArgs(['install', '--profile', 'p', '--dry-run', '--features', 'ai']), { command: 'install', args: [], flags: { profile: 'p', 'dry-run': true, features: 'ai' } });
  console.log('  ok  choices are validated and completed; parameters follow them');
}

// ---------- try mode ----------
{
  reset();
  const fake = fakeAws();
  const { ctx, adminCalls } = context(fake);
  const result = await engine.install(ctx, { features: [] });
  assert.equal(result.ok, true);
  assert.equal(result.portalUrl, 'https://dp.cloudfront.net/');
  assert.ok(result.checks.every((c) => c.ok), 'the portal answers');
  const order = names(fake.calls);
  assert.ok(order.indexOf('s3api create-bucket') < order.indexOf('cloudformation package') && order.indexOf('cloudformation package') < order.indexOf('cloudformation deploy'), order.join(' > '));
  assert.ok(fake.w.buckets.has('student-hosting-artifacts-111122223333'), 'artifact bucket per account');
  const deploy = fake.calls.find((a) => a[1] === 'deploy');
  assert.ok(deploy.includes('CAPABILITY_IAM') && deploy.includes('CAPABILITY_AUTO_EXPAND') && deploy.includes('RootDomain=') && deploy.includes('PortalAlias=false'));
  assert.deepEqual(adminCalls.map((c) => c.args), [['portal-publish']], 'the portal is published');
  assert.equal(adminCalls[0].env.AWS_PROFILE, 'new', 'the same account is used for the admin script');
  assert.equal(adminCalls[0].env.STACK_NAME, 'student-hosting');
  assert.deepEqual([stateOf().phase, stateOf().installedVersion, stateOf().domain], ['ready', '1.0.0', '']);
  assert.ok(fake.calls.every((a) => a.slice(-2)[0] !== '--profile'), 'the profile flag is added by the runner, not by the engine');
  console.log('  ok  try mode: checks, artifact bucket, package, deploy, publish, smoke check, state saved');

  // a second install is refused with a pointer to upgrade
  await fails(() => engine.install(ctx, {}), /already exists/);
}

// ---------- early failures say what to do ----------
{
  reset();
  await fails(() => engine.install(context(fakeAws({ cloudfrontError: 'Your account must be verified before you can add new CloudFront resources' })).ctx, {}), /not verified this account for CloudFront/);
  await fails(() => engine.install(context(fakeAws({ cloudfrontError: 'An error occurred (AccessDenied) when calling' })).ctx, {}), /AdministratorAccess/);
  await fails(() => engine.install(context(fakeAws({ stack: 'ROLLBACK_COMPLETE' })).ctx, {}), / destroy/);
  const broken = context(fakeAws());
  broken.ctx.aws = (args) => { if (args[0] === 'sts') throw new Error('Unable to locate credentials'); return '{}'; };
  await fails(() => engine.install(broken.ctx, {}), /aws configure/);
  assert.ok(!existsSync(join(repo, '.class-platform', 'install.json')), 'a failed check leaves nothing behind');
  console.log('  ok  unverified account, missing rights, failed earlier attempt and missing credentials each say what to do');
}

// ---------- domain mode ----------
{
  reset();
  const fake = fakeAws();
  const { ctx, adminCalls } = context(fake);
  const first = await engine.install(ctx, { domain: 'apps.example.com', features: ['ai'] });
  assert.equal(first.needs, 'certificate');
  assert.deepEqual(first.record, { type: 'CNAME', name: '_abc.apps.example.com.', value: '_xyz.acm-validations.aws.' });
  assert.equal(fake.w.certs[0].name, '*.apps.example.com');
  assert.ok(!names(fake.calls).includes('cloudformation deploy'), 'nothing is deployed before the certificate exists');
  fake.w.certs[0].status = 'ISSUED';
  const second = await engine.install(ctx, { domain: 'apps.example.com', features: ['ai'] });
  assert.equal(second.needs, 'dns');
  assert.deepEqual(second.records.map((r) => [r.name, r.value]), [['*.apps.example.com', 'd1.cloudfront.net'], ['deploy.apps.example.com', 'dp.cloudfront.net']]);
  assert.ok(fake.w.parameters.PortalAlias === 'false' && fake.w.parameters.CertificateArn.startsWith('arn:aws:acm') && fake.w.parameters.AiEnabled === 'true' && fake.w.parameters.BackendEnabled === 'true');
  assert.deepEqual(adminCalls, [], 'the portal is not published before DNS exists');
  assert.equal(stateOf().phase, 'dns');
  fake.w.outputs.PortalUrl = 'https://deploy.apps.example.com/';
  const done = await engine.finishDomain(ctx);
  assert.equal(fake.w.parameters.PortalAlias, 'true', 'the alias is attached in step two');
  assert.equal(done.portalUrl, 'https://deploy.apps.example.com/');
  assert.deepEqual(adminCalls.map((c) => c.args), [['portal-publish']]);
  assert.equal(stateOf().phase, 'ready');
  await fails(() => engine.finishDomain(ctx), /no domain install waiting/);
  console.log('  ok  domain mode: certificate and its DNS record, deploy without alias, DNS records, then alias and publish');
}

// ---------- status ----------
{
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', parameters: { RootDomain: '', StorageEnabled: 'true', BackendEnabled: 'true', AiEnabled: 'false', AiGeminiEnabled: 'false' } });
  const { ctx } = context(fake);
  const s = await engine.status(ctx);
  assert.deepEqual([s.installed, s.stackStatus, s.features, s.upgradeAvailable], [true, 'UPDATE_COMPLETE', ['backend', 'storage'], false]);
  writeFileSync(join(repo, 'VERSION'), '1.1.0\n');
  assert.equal((await engine.status(ctx)).upgradeAvailable, true);
  assert.match((await engine.status(ctx)).message, /1\.1\.0.*1\.0\.0/);
  console.log('  ok  status reads the account, lists the features and notices a newer version in the folder');
}

// ---------- an install whose window was closed carries on; the progress lines tell the teacher what is happening ----------
{
  reset();
  const fake = fakeAws();
  const said = [];
  const { ctx } = context(fake, { progress: (t) => said.push(t) });
  await engine.install(ctx, { features: [] });
  assert.ok(said.some((t) => /10 to 15 minutes.*run the same command again/.test(t)) && said.some((t) => /Publishing the portal/.test(t)), said.join(' | '));
  // simulate: the window closed during the deploy (state says deploying) but AWS finished
  reset();
  const f2 = fakeAws({ stack: 'CREATE_COMPLETE' });
  const c2 = context(f2);
  mkdirSync(join(repo, '.class-platform'), { recursive: true });
  writeFileSync(join(repo, '.class-platform', 'install.json'), JSON.stringify({ stack: 'student-hosting', profile: 'new', domain: '', certificateArn: '', features: [], phase: 'deploying', account: '111122223333' }));
  const resumed = await engine.install(c2.ctx, {});
  assert.equal(resumed.ok, true);
  assert.ok(!names(f2.calls).includes('cloudformation deploy'), 'a finished stack is not deployed again');
  assert.deepEqual(c2.adminCalls.map((c) => c.args), [['portal-publish']], 'the portal is published');
  assert.equal(stateOf().phase, 'ready');
  // still being built by the earlier window: wait for it
  reset();
  const f3 = fakeAws({ stack: 'CREATE_IN_PROGRESS' });
  const c3 = context(f3, { progress: () => {} });
  f3.fn.__wait = true;
  const origin = f3.fn;
  const waiting = (args) => { if (args[1] === 'wait') { f3.w.stack = 'CREATE_COMPLETE'; } return origin(args); };
  c3.ctx.aws = waiting;
  mkdirSync(join(repo, '.class-platform'), { recursive: true });
  writeFileSync(join(repo, '.class-platform', 'install.json'), JSON.stringify({ stack: 'student-hosting', profile: 'new', domain: '', certificateArn: '', features: [], phase: 'deploying', account: '111122223333' }));
  await engine.install(c3.ctx, {});
  assert.ok(f3.calls.some((a) => a[1] === 'wait' && a.includes('stack-create-complete')), 'it waits for the stack the earlier window started');
  console.log('  ok  progress lines are shown; a closed window is resumed (finished or still building); nothing is deployed twice');
}

// ---------- destroy leaves the account clean ----------
{
  const resources = [
    { ResourceType: 'AWS::S3::Bucket', PhysicalResourceId: 'sites-b', ResourceStatus: 'CREATE_COMPLETE' },
    { ResourceType: 'AWS::DynamoDB::Table', PhysicalResourceId: 'tokens-t', ResourceStatus: 'CREATE_COMPLETE' },
    { ResourceType: 'AWS::DynamoDB::Table', PhysicalResourceId: 'data-t', ResourceStatus: 'CREATE_COMPLETE' },
    { ResourceType: 'AWS::SSM::Parameter', PhysicalResourceId: '/student-deploy/keys', ResourceStatus: 'CREATE_COMPLETE' },
    { ResourceType: 'AWS::Lambda::Function', PhysicalResourceId: 'fn', ResourceStatus: 'CREATE_COMPLETE' }];
  const make = () => fakeAws({ stack: 'CREATE_COMPLETE', resources, retain: ['data-t'], tables: new Set(['data-t']), params: new Set(),
    buckets: new Set(['sites-b', 'student-hosting-artifacts-111122223333']),
    versions: { 'sites-b': Array.from({ length: 230 }, (_, i) => ({ Key: `f${i}`, VersionId: `v${i}` })), 'student-hosting-artifacts-111122223333': [{ Key: 'code.zip', VersionId: 'a1' }] },
    secrets: { openrouter: '{"X":"y"}' } });
  // default: keeps what holds data
  reset();
  let fake = make();
  let { ctx } = context(fake, { progress: () => {} });
  let out = await engine.destroy(ctx);
  assert.equal(fake.w.stack, null, 'the stack is deleted');
  assert.equal((fake.w.versions['sites-b'] ?? []).length, 0, 'every version of the sites bucket was removed first (the stack could not be deleted otherwise)');
  assert.ok(!fake.w.buckets.has('student-hosting-artifacts-111122223333'), 'the installer\'s own staging bucket is removed');
  assert.deepEqual(out.kept.sort(), ['secret openrouter (your AI keys)', 'table data-t'], out.kept.join());
  assert.ok(fake.w.tables.has('data-t') && fake.w.secrets.openrouter, 'the data table and the AI keys are kept by default');
  // everything: a test account is left as it was before the install
  reset();
  fake = make();
  ({ ctx } = context(fake, { progress: () => {} }));
  out = await engine.destroy(ctx, { everything: true });
  assert.deepEqual(out.kept, []);
  assert.ok(!fake.w.tables.has('data-t') && !fake.w.secrets.openrouter && !fake.w.buckets.has('student-hosting-artifacts-111122223333'), 'nothing is left');
  assert.ok(!existsSync(join(repo, '.class-platform', 'install.json')), 'the saved install state is removed, so the next install starts clean');
  // an install after destroy is accepted (a missing stack is not "already exists")
  const again = await engine.install(context(fake, { progress: () => {} }).ctx, {});
  assert.equal(again.ok, true);
  // dry run changes nothing
  fake = make();
  ({ ctx } = context(fake, { dryRun: true, progress: () => {} }));
  await engine.destroy(ctx, { everything: true });
  assert.equal(fake.w.stack, 'CREATE_COMPLETE', 'a dry run deletes nothing');
  assert.equal(fake.w.versions['sites-b'].length, 230);
  console.log('  ok  destroy: all bucket versions, the stack, the staging bucket; data table and AI keys kept unless --everything; state removed; dry run safe');
}

// ---------- every feature is installed by default; "none" and explicit lists still work ----------
{
  assert.deepEqual(engine.normalise({}).features, ['ai', 'backend', 'customdomains', 'gemini', 'storage'], 'no choice given: every feature is available (each stays off per student)');
  assert.deepEqual(engine.normalise({ features: [] }).features, [], 'an explicit empty list means none');
  const { parseFeatures } = await import('../setup.mjs');
  assert.deepEqual(parseFeatures('none'), []);
  assert.deepEqual(parseFeatures('ai, storage'), ['ai', 'storage']);
  reset();
  const fake = fakeAws();
  await engine.install(context(fake).ctx, {});
  const deploy = fake.calls.find((a) => a[1] === 'deploy');
  for (const p of ['BackendEnabled=true', 'AiEnabled=true', 'AiGeminiEnabled=true', 'StorageEnabled=true']) assert.ok(deploy.includes(p), p);
  reset();
  const lean = fakeAws();
  await engine.install(context(lean).ctx, { features: [] });
  assert.ok(lean.calls.find((a) => a[1] === 'deploy').includes('StorageEnabled=false'), 'a teacher who chose none gets none');
  // several features switched on in one plan
  reset();
  const base = fakeAws({ stack: 'UPDATE_COMPLETE', nextChanges: [{ Action: 'Add', LogicalResourceId: 'X', ResourceType: 'AWS::Lambda::Function' }] });
  mkdirSync(join(repo, '.class-platform'), { recursive: true });
  writeFileSync(join(repo, '.class-platform', 'install.json'), JSON.stringify({ stack: 'student-hosting', profile: 'new', domain: '', certificateArn: '', features: [], phase: 'ready', installedVersion: '1.1.0' }));
  await engine.featurePlan(context(base).ctx, ['backend', 'ai', 'gemini', 'storage'], true);
  const planned = base.calls.filter((a) => a[1] === 'deploy').pop();
  for (const p of ['BackendEnabled=true', 'AiEnabled=true', 'AiGeminiEnabled=true', 'StorageEnabled=true']) assert.ok(planned.includes(p), p);
  await fails(() => engine.featurePlan(context(base).ctx, ['storage', 'teleport'], true), /Unknown feature/);
  console.log('  ok  every feature is installed by default (off per student); "none" and explicit lists work; several features can be switched on in one plan');
}

// ---------- a newer GitHub release is announced with the exact command ----------
{
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', parameters: {} });
  const answer = (tag) => async () => ({ status: 200, json: async () => ({ tag_name: tag }) });
  writeFileSync(join(repo, 'release.json'), JSON.stringify({ repo: 'someone/class-platform', version: '1.1.0' }));
  const { ctx } = context(fake, { fetch: answer('v1.2.0') });
  const newer = await engine.status(ctx);
  assert.equal(newer.newerRelease, true);
  assert.equal(newer.upgradeCommand, 'npx -y https://github.com/someone/class-platform/releases/download/v1.2.0/class-platform-teacher-1.2.0.tgz upgrade');
  assert.match(newer.message, /Version 1\.2\.0 is out/);
  assert.equal(newer.editorCommand, 'npx -y https://github.com/someone/class-platform/releases/download/v1.2.0/class-platform-teacher-1.2.0.tgz editor');
  assert.match(newer.message, /Then update your editor's teacher tools.*tgz editor/);
  assert.equal((await engine.status({ ...ctx, fetch: answer('v1.1.0') })).newerRelease, false, 'the same version is not news');
  assert.equal((await engine.status({ ...ctx, fetch: answer('latest; rm -rf /') })).latestVersion, null, 'only a plain version number is accepted from the answer');
  assert.equal((await engine.status({ ...ctx, fetch: async () => { throw new Error('offline'); } })).newerRelease, false, 'offline is not an error');
  assert.ok(engine.newerThan('1.10.0', '1.9.9') && !engine.newerThan('1.0.0', '1.0.0') && !engine.newerThan('0.9.0', '1.0.0'));
  rmSync(join(repo, 'release.json'));
  assert.equal((await engine.status(context(fake).ctx)).latestVersion, null, 'a git checkout has no release to compare with');
  console.log('  ok  status announces a newer GitHub release with the exact command, ignores odd answers and offline');
}

// ---------- every command prints its version first; install and upgrade warn when the copy is old ----------
{
  const answer = (tag) => async () => ({ status: 200, json: async () => ({ tag_name: tag }) });
  const printed = async (ctx, command, flags = {}, state = {}) => {
    const lines = [];
    const original = console.log;
    console.log = (...parts) => lines.push(parts.join(' '));
    try { await versionBanner(ctx, command, flags, state); } finally { console.log = original; }
    return lines.join('\n');
  };
  writeFileSync(join(repo, 'release.json'), JSON.stringify({ repo: 'someone/class-platform', version: '1.1.0' }));
  const { ctx } = context(fakeAws({ stack: 'UPDATE_COMPLETE', parameters: {} }), { fetch: answer('v9.9.9') });
  const version = engine.repoVersion(ctx);
  const old = await printed(ctx, 'install');
  assert.ok(old.startsWith(`Class platform teacher tools ${version}`), 'the version is the first line');
  assert.match(old, /9\.9\.9 is the newest release.*class-platform-teacher-9\.9\.9\.tgz install/);
  assert.match(await printed(ctx, 'upgrade'), /class-platform-teacher-9\.9\.9\.tgz upgrade/);
  const statusOnly = await printed(ctx, 'status');
  assert.equal(statusOnly, `Class platform teacher tools ${version}`, 'other commands print the version and make no network call for the warning');
  assert.equal(await printed(ctx, 'install', { json: true }), '', 'the JSON mode stays clean');
  assert.doesNotMatch(await printed(ctx, 'install', {}, { updateNotices: false }), /newest release/, 'a teacher who declined notices gets none');
  assert.doesNotMatch(await printed({ ...ctx, fetch: answer(`v${version}`) }, 'install'), /newest release/, 'the newest copy gets no warning');
  assert.doesNotMatch(await printed({ ...ctx, fetch: async () => { throw new Error('offline'); } }, 'install'), /newest release/, 'offline is silent');
  rmSync(join(repo, 'release.json'));
  console.log('  ok  every command prints its version first; install and upgrade warn about an older copy');
}

// ---------- a fresh clone adopts the stack that already exists ----------
{
  reset();
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', parameters: { RootDomain: 'apps.example.com', CertificateArn: 'arn:c', AiEnabled: 'true', BackendEnabled: 'true', AiGeminiEnabled: 'false', StorageEnabled: 'false' } });
  const { ctx } = context(fake);
  const s = await engine.status(ctx);
  assert.deepEqual([s.installed, s.domain, s.features], [true, 'apps.example.com', ['backend', 'ai']]);
  assert.equal(stateOf().adopted, true);
  reset();
  assert.equal(engine.adoptState(context(fakeAws()).ctx), null, 'no stack, nothing to adopt');
  console.log('  ok  a fresh clone adopts the existing stack (domain and features read from the account)');
}

// ---------- plan and apply ----------
{
  const safe = [{ Action: 'Modify', LogicalResourceId: 'PortalFunction', ResourceType: 'AWS::Lambda::Function', Replacement: 'False' }, { Action: 'Add', LogicalResourceId: 'AiProxyApi', ResourceType: 'AWS::ApiGatewayV2::Api' }];
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', nextChanges: safe });
  const { ctx, adminCalls } = context(fake);
  const planned = await engine.plan(ctx);
  assert.deepEqual([planned.adds, planned.modifies, planned.removes, planned.replacements, planned.blocked], [1, 1, 0, 0, false]);
  assert.match(planned.message, /replaces no data/);
  assert.ok(!names(fake.calls).includes('cloudformation execute-change-set'), 'planning changes nothing');
  await fails(() => engine.apply(ctx, 'deadbeef'), /not the one that was last shown/);
  const applied = await engine.apply(ctx, planned.planId);
  assert.equal(applied.ok, true);
  assert.ok(names(fake.calls).includes('cloudformation execute-change-set') && names(fake.calls).includes('cloudformation wait'));
  assert.deepEqual(adminCalls.map((c) => c.args), [['portal-publish']], 'applying publishes the portal, kit and MCP package');
  assert.equal(applied.editorCommand, null, 'a git checkout has no release file to point the editor at');
  assert.equal(stateOf().installedVersion, '1.1.0');
  assert.ok(!stateOf().plan, 'a plan is used once');
  await fails(() => engine.apply(ctx, planned.planId), /not the one that was last shown/);

  // an update that would replace a table or bucket is refused, and apply refuses it too
  const risky = [{ Action: 'Modify', LogicalResourceId: 'TokensTable', ResourceType: 'AWS::DynamoDB::Table', Replacement: 'True' }];
  const f2 = fakeAws({ stack: 'UPDATE_COMPLETE', nextChanges: risky });
  const c2 = context(f2).ctx;
  const blocked = await engine.plan(c2);
  assert.equal(blocked.blocked, true);
  assert.match(blocked.message, /Blocked.*TokensTable/);
  await fails(() => engine.apply(c2, blocked.planId), /will not be applied/);
  assert.ok(!names(f2.calls).includes('cloudformation execute-change-set'));

  // the change set changing after it was shown is refused
  const f3 = fakeAws({ stack: 'UPDATE_COMPLETE', nextChanges: safe });
  const c3 = context(f3).ctx;
  const p3 = await engine.plan(c3);
  f3.w.changeSets[0].changes = [...safe, { Action: 'Remove', LogicalResourceId: 'X', ResourceType: 'AWS::Lambda::Function' }];
  await fails(() => engine.apply(c3, p3.planId), /changed or expired/);

  // nothing to do
  const f4 = fakeAws({ stack: 'UPDATE_COMPLETE' });
  assert.equal((await engine.plan(context(f4).ctx)).empty, true);
  console.log('  ok  plan changes nothing; apply runs only the plan shown, once; data-holding replacements and changed plans are refused');
}

// ---------- after an upgrade from a release package the teacher is told how to update the editor tools ----------
{
  writeFileSync(join(repo, 'release.json'), JSON.stringify({ repo: 'someone/class-platform', version: '1.2.0', tag: 'v1.2.0', url: 'https://github.com/someone/class-platform/releases/download/v1.2.0/class-platform-teacher-1.2.0.tgz' }));
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', nextChanges: [{ Action: 'Modify', LogicalResourceId: 'PortalFunction', ResourceType: 'AWS::Lambda::Function', Replacement: 'False' }] });
  const { ctx } = context(fake);
  const p = await engine.plan(ctx);
  const done = await engine.apply(ctx, p.planId);
  assert.equal(done.editorCommand, 'npx -y https://github.com/someone/class-platform/releases/download/v1.2.0/class-platform-teacher-1.2.0.tgz editor');
  assert.match(done.message, /make your editor's teacher tools use this version too, run: npx -y .*tgz editor/);
  rmSync(join(repo, 'release.json'));
  console.log('  ok  upgrade and status tell the teacher the exact command that moves the editor tools to the new version');
}

// ---------- feature switch ----------
{
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', nextChanges: [{ Action: 'Add', LogicalResourceId: 'AiProxyFunction', ResourceType: 'AWS::Lambda::Function' }] });
  const { ctx } = context(fake);
  const p = await engine.featurePlan(ctx, 'gemini', true);
  const deploy = fake.calls.filter((a) => a[1] === 'deploy').pop();
  for (const x of ['BackendEnabled=true', 'AiEnabled=true', 'AiGeminiEnabled=true']) assert.ok(deploy.includes(x), x);
  assert.ok(deploy.includes('--no-execute-changeset'));
  await engine.apply(ctx, p.planId);
  assert.deepEqual(stateOf().features, ['ai', 'backend', 'gemini'], 'the new choice is remembered after apply');
  await fails(() => engine.featurePlan(ctx, 'teleport', true), /Unknown feature/);
  console.log('  ok  switching a feature on is a plan with its dependencies; it is remembered once applied');
}

// ---------- dry run ----------
{
  reset();
  const fake = fakeAws();
  const { ctx } = context(fake, { dryRun: true });
  const out = await engine.install(ctx, { features: ['storage'] });
  assert.equal(out.ok, true);
  const ran = names(fake.calls);
  for (const mutating of ['s3api create-bucket', 'cloudformation package', 'cloudformation deploy']) assert.ok(!ran.includes(mutating), `${mutating} must not run in a dry run`);
  assert.ok(ctx.would.some((c) => c.includes('cloudformation deploy')) && ctx.would.some((c) => c.includes('portal-publish')));
  assert.ok(!existsSync(join(repo, '.class-platform', 'install.json')), 'a dry run writes no state');
  console.log('  ok  dry run reads the account but changes and writes nothing');
}

// ---------- destroy: what it is about to remove, shown first ----------
{
  const resources = [
    { ResourceType: 'AWS::S3::Bucket', PhysicalResourceId: 'b1', ResourceStatus: 'CREATE_COMPLETE', LogicalResourceId: 'SitesBucket' },
    { ResourceType: 'AWS::CloudFront::Distribution', PhysicalResourceId: 'd1', ResourceStatus: 'CREATE_COMPLETE', LogicalResourceId: 'Distribution' },
    { ResourceType: 'AWS::CloudFront::Distribution', PhysicalResourceId: 'd2', ResourceStatus: 'CREATE_COMPLETE', LogicalResourceId: 'PortalDistribution' },
    { ResourceType: 'AWS::DynamoDB::Table', PhysicalResourceId: 'tok', ResourceStatus: 'CREATE_COMPLETE', LogicalResourceId: 'TokensTable' },
    { ResourceType: 'AWS::Lambda::Function', PhysicalResourceId: 'f1', ResourceStatus: 'CREATE_COMPLETE', LogicalResourceId: 'Fn' }];
  const fake = fakeAws({ stack: 'CREATE_COMPLETE', resources });
  const inner = fake.fn;
  fake.fn = (args) => (args[0] === 'dynamodb' && args[1] === 'scan' ? JSON.stringify({ Count: 7 }) : inner(args));
  const inv = engine.inventory(context(fake).ctx);
  assert.deepEqual([inv.exists, inv.students, inv.buckets, inv.distributions, inv.tables, inv.functions], [true, 7, 1, 2, 1, 1]);
  assert.equal(inv.portalUrl, 'https://dp.cloudfront.net/');
  assert.equal(engine.inventory(context(fakeAws()).ctx).exists, false, 'no stack, nothing to show');
  console.log('  ok  destroy first shows how many students, distributions, buckets, tables and functions it will remove');
}

// ---------- the update-notice choice made at install ----------
{
  assert.equal(engine.normalise({}).updateNotices, true, 'the default is yes');
  assert.equal(engine.normalise({ updateNotices: false }).updateNotices, false);
  writeFileSync(join(repo, 'release.json'), JSON.stringify({ repo: 'someone/class-platform', version: '1.1.0' }));
  let asked = 0;
  const fake = fakeAws({ stack: 'UPDATE_COMPLETE', parameters: {} });
  const answer = async () => { asked += 1; return { status: 200, json: async () => ({ tag_name: 'v9.0.0' }) }; };
  reset();
  mkdirSync(join(repo, '.class-platform'), { recursive: true });
  writeFileSync(join(repo, '.class-platform', 'install.json'), JSON.stringify({ stack: 'student-hosting', profile: 'new', domain: '', certificateArn: '', features: [], phase: 'ready', installedVersion: '1.1.0', updateNotices: false }));
  const quiet = await engine.status(context(fake, { fetch: answer }).ctx);
  assert.equal(asked, 0, 'a teacher who said no is never checked for news');
  assert.equal(quiet.newerRelease, false);
  writeFileSync(join(repo, '.class-platform', 'install.json'), JSON.stringify({ stack: 'student-hosting', profile: 'new', domain: '', certificateArn: '', features: [], phase: 'ready', installedVersion: '1.1.0', updateNotices: true }));
  assert.equal((await engine.status(context(fake, { fetch: answer }).ctx)).newerRelease, true);
  rmSync(join(repo, 'release.json'));
  console.log('  ok  update notices: default yes; a teacher who said no is not checked for news and sees no notice');
}

// ---------- secrets ----------
{
  const fake = fakeAws();
  const { ctx } = context(fake);
  engine.saveAiSecret(ctx, { managementKey: 'mgmt-1', googleKey: '' });
  assert.deepEqual(JSON.parse(fake.w.secrets.openrouter), { OPENROUTER_MANAGEMENT_KEY: 'mgmt-1' });
  engine.saveAiSecret(ctx, { googleKey: 'google-1' });
  assert.deepEqual(JSON.parse(fake.w.secrets.openrouter), { OPENROUTER_MANAGEMENT_KEY: 'mgmt-1', GOOGLE_API_KEY: 'google-1' }, 'adding one key keeps the other');
  const dry = context(fakeAws(), { dryRun: true }).ctx;
  engine.saveAiSecret(dry, { managementKey: 'mgmt-secret-value' });
  assert.ok(!JSON.stringify(dry.would).includes('mgmt-secret-value'), 'a dry run never prints a key');
  console.log('  ok  AI keys go to Secrets Manager, merge with what is there, and are never printed');
}

// ---------- setup.mjs: the machine-readable mode used by the teacher MCP ----------
{
  const lines = [];
  const original = process.stdout.write.bind(process.stdout);
  process.stdout.write = (chunk) => { lines.push(String(chunk)); return true; };
  try {
    await main(['teacher', 'bob', '--json']);
    await main(['secrets', '--json']);
    await main(['destroy', '--json']);
  } finally { process.stdout.write = original; }
  const results = lines.join('').trim().split('\n').map((l) => JSON.parse(l));
  assert.ok(results.length === 3 && results.every((r) => r.ok === false && r.hint), 'password, key and delete are terminal-only');
  assert.match(results[0].hint, /node setup\.mjs teacher bob/);
  process.exitCode = 0;
  console.log('  ok  teacher password, AI keys and stack deletion refuse to run through the JSON mode');
}

rmSync(repo, { recursive: true, force: true });
console.log('test_installer.mjs: all passed');
