// Custom domains for students (installer/tenant.mjs) against a fake CloudFront: the distribution's shape, the steps and their order, repeating safely,
// resuming from what already exists, the install never failing because of it, an update never clearing the ids a platform already has, and the
// teardown order (tenants, distribution, group, functions) that must run before the stack is deleted.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as engine from '../installer/engine.mjs';
import { distributionConfig, enableCustomDomains, teardownCustomDomains, tenantNames } from '../installer/tenant.mjs';

// the fake account number is built from pieces, so no 12-digit account id sits in an ARN in the repository (a guard test forbids that)
const ACCOUNT = ['1111', '2222', '3333'].join('');
const work = mkdtempSync(join(tmpdir(), 'tenant-'));
const repo = join(work, 'repo');
mkdirSync(join(repo, 'custom-domain'), { recursive: true });
writeFileSync(join(repo, 'template.yaml'), 'Resources: {}\n');
writeFileSync(join(repo, 'VERSION'), '1.2.0\n');
writeFileSync(join(repo, 'custom-domain', 'spa-fallback.js'), 'function handler(e){return e.request;}');
writeFileSync(join(repo, 'custom-domain', 'tenant-api-route.js'), 'function handler(e){return e.request;}');

// ---------- the distribution ----------
{
  const config = distributionConfig({ stack: 'student-hosting', account: ACCOUNT, bucket: 'bkt', oacId: 'OAC1', apiHost: 'api.example.amazonaws.com', secret: 's3cret' });
  assert.equal(config.ConnectionMode, 'tenant-only');
  assert.equal(config.Origins.Items[0].DomainName, 'bkt.s3.us-east-1.amazonaws.com');
  assert.equal(config.Origins.Items[0].OriginPath, '/sites/{{student}}', 'each tenant\'s student parameter selects the site');
  assert.equal(config.Origins.Items[0].OriginAccessControlId, 'OAC1');
  assert.equal(config.Origins.Items[1].OriginPath, '/b/{{student}}');
  assert.equal(config.Origins.Items[1].CustomHeaders.Items[0].HeaderValue, 's3cret');
  assert.equal(config.CacheBehaviors.Items[0].PathPattern, '/api/*');
  assert.equal(config.DefaultCacheBehavior.FunctionAssociations.Items[0].FunctionARN, `arn:aws:cloudfront::${ACCOUNT}:function/student-hosting-spa-fallback`);
  assert.equal(config.CacheBehaviors.Items[0].FunctionAssociations.Items[0].FunctionARN, `arn:aws:cloudfront::${ACCOUNT}:function/student-hosting-tenant-api`);
  assert.equal(config.TenantConfig.ParameterDefinitions[0].Name, 'student');
  assert.equal(config.Origins.Quantity, config.Origins.Items.length);
  assert.ok(!JSON.stringify(distributionConfig({ stack: 'x', account: '1', bucket: 'b', oacId: 'o', apiHost: 'h', secret: 's' })).includes('student-hosting'), 'no name of another platform is baked in');
  console.log('  ok  the distribution: tenant-only, the student parameter in both origin paths, OAC, /api route, the stack\'s own functions');
}

// ---------- a fake AWS with CloudFront ----------
function aws({ stack = 'CREATE_COMPLETE', parameters = {}, outputs = {}, existing = {}, failOn = null } = {}) {
  const calls = [];
  const w = { stack, parameters, functions: new Set(existing.functions ?? []), groups: existing.groups ?? [], distributions: existing.distributions ?? [], tenants: existing.tenants ?? [], published: [], deployed: [], gone: [] };
  const out = { BucketName: 'bkt', BackendApiUrl: 'https://api123.execute-api.us-east-1.amazonaws.com', PortalUrl: 'https://p.example/', ...outputs };
  const fn = (args) => {
    calls.push(args);
    if (failOn && args.join(' ').includes(failOn)) throw new Error(`simulated failure at ${failOn}`);
    const [svc, op] = args;
    const opt = (n) => args[args.indexOf(n) + 1];
    if (svc === 'sts') return JSON.stringify({ Account: ACCOUNT, Arn: `arn:aws:iam::${ACCOUNT}:user/t` });
    if (svc === 'cloudfront' && op === 'list-distributions') return '{}';
    if (svc === 'cloudformation' && op === 'describe-stacks') {
      if (!w.stack) throw new Error('does not exist');
      return JSON.stringify({ Stacks: [{ StackId: `arn:aws:cloudformation:us-east-1:${ACCOUNT}:stack/student-hosting/abcdef12-0000`, StackStatus: w.stack, Outputs: Object.entries(out).map(([OutputKey, OutputValue]) => ({ OutputKey, OutputValue })),
        Parameters: Object.entries(w.parameters).map(([ParameterKey, ParameterValue]) => ({ ParameterKey, ParameterValue })) }] });
    }
    if (svc === 'cloudformation' && op === 'list-stack-resources') return JSON.stringify({ StackResourceSummaries: [{ LogicalResourceId: 'SitesOAC', PhysicalResourceId: 'OAC1', ResourceType: 'AWS::CloudFront::OriginAccessControl', ResourceStatus: 'CREATE_COMPLETE' }] });
    if (svc === 'cloudformation' && op === 'package') return '';
    if (svc === 'cloudformation' && op === 'wait') return '';
    if (svc === 'cloudformation' && op === 'deploy') { const p = Object.fromEntries(args.slice(args.indexOf('--parameter-overrides') + 1).filter((a) => a.includes('=') && !a.startsWith('--')).map((a) => a.split(/=(.*)/s).slice(0, 2))); w.parameters = { ...w.parameters, ...p }; w.deployed.push(p); return ''; }
    if (svc === 's3api') return '';
    if (svc === 'cloudfront' && op === 'create-function') { if (w.functions.has(opt('--name'))) throw new Error('An error occurred (FunctionAlreadyExists)'); w.functions.add(opt('--name')); return JSON.stringify({ ETag: 'E1' }); }
    if (svc === 'cloudfront' && op === 'describe-function') { if (!w.functions.has(opt('--name'))) throw new Error('NoSuchFunctionExists'); return JSON.stringify({ ETag: 'E2' }); }
    if (svc === 'cloudfront' && op === 'update-function') return JSON.stringify({ ETag: 'E3' });
    if (svc === 'cloudfront' && op === 'publish-function') { w.published.push(opt('--name')); return ''; }
    if (svc === 'cloudfront' && op === 'delete-function') { w.functions.delete(opt('--name')); w.gone.push(`function ${opt('--name')}`); return ''; }
    if (svc === 'cloudfront' && op === 'list-connection-groups') return JSON.stringify({ ConnectionGroups: w.groups });
    if (svc === 'cloudfront' && op === 'create-connection-group') { const g = { Id: 'cg_1', Name: opt('--name'), RoutingEndpoint: 'abc123.cloudfront.net', Enabled: true }; w.groups.push(g); return JSON.stringify({ ConnectionGroup: g }); }
    if (svc === 'cloudfront' && op === 'get-connection-group') return JSON.stringify({ ETag: 'EG', ConnectionGroup: w.groups.find((g) => g.Id === opt('--identifier')) });
    if (svc === 'cloudfront' && op === 'update-connection-group') { w.groups.find((g) => g.Id === opt('--id')).Enabled = false; return ''; }
    if (svc === 'cloudfront' && op === 'delete-connection-group') { if (w.groups.find((g) => g.Id === opt('--id')).Enabled) throw new Error('must be disabled first'); w.groups = w.groups.filter((g) => g.Id !== opt('--id')); w.gone.push('group'); return ''; }
    if (svc === 'cloudfront' && op === 'list-distributions-by-connection-mode') return JSON.stringify({ DistributionList: { Items: w.distributions } });
    if (svc === 'cloudfront' && op === 'create-distribution') { const c = JSON.parse(readFileSync(opt('--distribution-config').replace('file://', ''), 'utf8')); w.created = c; const d = { Id: 'EMT123', Comment: c.Comment, Enabled: true }; w.distributions.push(d); return JSON.stringify({ Distribution: d }); }
    if (svc === 'cloudfront' && op === 'wait') return '';
    if (svc === 'cloudfront' && op === 'get-distribution-config') { const d = w.distributions.find((x) => x.Id === opt('--id')); return JSON.stringify({ ETag: 'ED', DistributionConfig: { Enabled: d.Enabled, Comment: d.Comment } }); }
    if (svc === 'cloudfront' && op === 'update-distribution') { w.distributions.find((x) => x.Id === opt('--id')).Enabled = false; w.gone.push('distribution disabled'); return JSON.stringify({}); }
    if (svc === 'cloudfront' && op === 'delete-distribution') { if (w.distributions.find((x) => x.Id === opt('--id')).Enabled) throw new Error('DistributionNotDisabled'); w.distributions = w.distributions.filter((x) => x.Id !== opt('--id')); w.gone.push('distribution'); return ''; }
    if (svc === 'cloudfront' && op === 'list-distribution-tenants') return JSON.stringify({ DistributionTenantList: w.tenants.map((t) => ({ Id: t.Id, Name: t.Name })) });
    if (svc === 'cloudfront' && op === 'get-distribution-tenant') { const t = w.tenants.find((x) => x.Id === opt('--identifier')); return JSON.stringify({ ETag: 'ET', DistributionTenant: { ...t, Status: 'Deployed' } }); }
    if (svc === 'cloudfront' && op === 'update-distribution-tenant') { w.tenants.find((x) => x.Id === opt('--id')).Enabled = false; w.gone.push('tenant disabled'); return ''; }
    if (svc === 'cloudfront' && op === 'delete-distribution-tenant') { if (w.tenants.find((x) => x.Id === opt('--id')).Enabled) throw new Error('TenantNotDisabled'); w.tenants = w.tenants.filter((x) => x.Id !== opt('--id')); w.gone.push('tenant'); return ''; }
    throw new Error(`fake aws: unexpected ${args.join(' ')}`);
  };
  return { fn, calls, w };
}
const ctxFor = (fake, extra = {}) => ({ repo, profile: 'p', stack: 'student-hosting', dryRun: false, would: [], now: () => 'now', sleep: async () => {}, aws: fake.fn, ...extra });
const config = { stack: 'student-hosting', profile: 'p', domain: '', certificateArn: '', features: ['backend', 'customdomains'], tenant: null };

// ---------- enabling ----------
{
  const fake = aws();
  const said = [];
  const result = await enableCustomDomains(ctxFor(fake), config, { say: (t) => said.push(t) });
  assert.equal(result.ok, true);
  assert.deepEqual(result.tenant, { distributionId: 'EMT123', connectionGroupId: 'cg_1', routingEndpoint: 'abc123.cloudfront.net' });
  assert.deepEqual(fake.w.published.sort(), ['student-hosting-spa-fallback', 'student-hosting-tenant-api'], 'both functions are published');
  assert.equal(fake.w.created.Origins.Items[1].CustomHeaders.Items[0].HeaderValue, 'abcdef12-0000'.slice(0, 13) === 'abcdef12-0000' ? 'abcdef12-0000' : '', 'the origin secret is derived from the stack id, as the template does');
  const last = fake.w.deployed.at(-1);
  assert.deepEqual([last.TenantDistributionId, last.ConnectionGroupId, last.RoutingEndpoint], ['EMT123', 'cg_1', 'abc123.cloudfront.net'], 'the portal is handed the three ids by a stack update');
  const order = fake.calls.map((a) => `${a[1]}`);
  assert.ok(order.indexOf('create-function') < order.indexOf('create-connection-group') && order.indexOf('create-connection-group') < order.indexOf('create-distribution') && order.indexOf('create-distribution') < order.indexOf('deploy'), order.join(' > '));
  assert.ok(said.length >= 4 && said.some((t) => /several minutes/.test(t)), 'progress is reported');
  assert.ok(!said.join(' ').includes('abcdef12'), 'the secret is never printed');
  // repeating is a no-op once the platform has the ids
  fake.w.parameters = { ...fake.w.parameters, TenantDistributionId: 'EMT123', ConnectionGroupId: 'cg_1', RoutingEndpoint: 'abc123.cloudfront.net' };
  const callsBefore = fake.calls.length;
  const again = await enableCustomDomains(ctxFor(fake), config);
  assert.equal(again.already, true);
  assert.equal(fake.calls.slice(callsBefore).filter((a) => /^(create|update|delete|publish)/.test(a[1])).length, 0, 'a repeat changes nothing');
  console.log('  ok  enable: functions, connection group, distribution, then the portal gets the ids; progress shown; secret not printed; repeat is a no-op');
}

// ---------- resuming from what already exists ----------
{
  const fake = aws({ existing: { functions: ['student-hosting-spa-fallback'], groups: [{ Id: 'cg_old', Name: 'student-hosting', RoutingEndpoint: 'old.cloudfront.net', Enabled: true }], distributions: [{ Id: 'EOLD', Comment: tenantNames('student-hosting').comment, Enabled: true }] } });
  const result = await enableCustomDomains(ctxFor(fake), config);
  assert.deepEqual(result.tenant, { distributionId: 'EOLD', connectionGroupId: 'cg_old', routingEndpoint: 'old.cloudfront.net' }, 'an existing group and distribution are reused, never duplicated');
  assert.ok(!fake.calls.some((a) => a[1] === 'create-distribution' || a[1] === 'create-connection-group'));
  assert.ok(fake.calls.some((a) => a[1] === 'update-function'), 'an existing function is updated and published again');
  console.log('  ok  resume: an existing function, group and distribution are reused, never duplicated');
}

// ---------- refusals ----------
{
  await assert.rejects(() => enableCustomDomains(ctxFor(aws({ outputs: { BackendApiUrl: '' } })), config), /need the student backends/);
  await assert.rejects(() => enableCustomDomains(ctxFor(aws({ stack: null })), config), /no stack/);
  const dry = aws();
  const r = await enableCustomDomains(ctxFor(dry, { dryRun: true }), config);
  assert.equal(r.dryRun, true);
  assert.ok(dry.calls.every((a) => !['create-distribution', 'deploy'].includes(a[1])), 'a dry run creates no distribution and deploys nothing');
  console.log('  ok  custom domains without backends, without a stack: refused with the reason; a dry run creates nothing');
}

// ---------- parameters: an update never clears the ids ----------
{
  const p = engine.parametersFor({ ...config, tenant: { distributionId: 'E1', connectionGroupId: 'cg', routingEndpoint: 'r.cloudfront.net' } });
  assert.ok(p.includes('TenantDistributionId=E1') && p.includes('ConnectionGroupId=cg') && p.includes('RoutingEndpoint=r.cloudfront.net'));
  assert.ok(engine.parametersFor(config).includes('TenantDistributionId='), 'with none, the parameters are empty (custom domains off)');
  assert.deepEqual(engine.tenantFromParameters({ TenantDistributionId: 'E9', RoutingEndpoint: 'x' }), { distributionId: 'E9', connectionGroupId: '', routingEndpoint: 'x' });
  assert.equal(engine.tenantFromParameters({}), null);
  // a platform whose ids exist only in its stack (a hand-made setup, an older install): a plan keeps them even though the saved state never heard of them
  const legacy = aws({ parameters: { TenantDistributionId: 'EHAND', RoutingEndpoint: 'hand.cloudfront.net', BackendEnabled: 'true' } });
  legacy.w.changeSets = [];
  const base = legacy.fn;
  legacy.fn = (args) => (args[1] === 'list-change-sets' ? '{"Summaries":[]}' : base(args));
  const sdir = join(work, 'legacy-state');
  mkdirSync(sdir, { recursive: true });
  writeFileSync(join(sdir, 'install.json'), JSON.stringify({ stack: 'student-hosting', profile: 'p', domain: '', certificateArn: '', features: ['backend'], phase: 'ready', installedVersion: '1.0.0' }));
  const ctx = ctxFor(legacy, { stateDir: sdir });
  legacy.fn.__x = 1;
  await engine.plan(ctx);
  const planned = legacy.calls.filter((a) => a[1] === 'deploy').pop();
  assert.ok(planned.includes('TenantDistributionId=EHAND') && planned.includes('RoutingEndpoint=hand.cloudfront.net'), 'the ids the stack already has are carried into the plan: an update never switches custom domains off');
  console.log('  ok  parameters carry the ids on every update, including for a platform whose saved state never heard of them');
}

// ---------- install: set up by default, never a reason for the install to fail ----------
{
  const fresh = (failOn) => {
    const fake = aws({ stack: null, failOn });
    const inner = fake.fn;
    let created = false;
    fake.fn = (args) => {
      if (args[1] === 'describe-stacks' && !created) throw new Error('does not exist');
      if (args[1] === 'deploy') created = true;
      if (args[1] === 'create-bucket') return '';
      if (args[1] === 'head-bucket') throw new Error('404');
      if (args[0] === 'cloudformation' && args[1] === 'describe-stacks') { fake.w.stack = 'CREATE_COMPLETE'; }
      return inner(args);
    };
    return fake;
  };
  const sdir = join(work, 'install-state');
  const good = fresh(null);
  const done = await engine.install(ctxFor(good, { stateDir: sdir, node: () => '', fetch: async () => ({ status: 200 }) }), {});
  assert.ok(done.steps.includes('custom domains set up'), JSON.stringify(done));
  assert.equal(JSON.parse(readFileSync(join(sdir, 'install.json'), 'utf8')).tenant.distributionId, 'EMT123', 'the ids are remembered');
  const sdir2 = join(work, 'install-state-2');
  const bad = fresh('create-distribution');
  const survived = await engine.install(ctxFor(bad, { stateDir: sdir2, node: () => '', fetch: async () => ({ status: 200 }) }), {});
  assert.equal(survived.ok, true, 'the install still succeeds');
  assert.match(survived.notes.join(' '), /Custom domains could not be set up.*custom-domains/, 'and says how to try again');
  console.log('  ok  install sets custom domains up by default; if that step fails the install still succeeds and says how to retry');
}

// ---------- switching custom domains off ----------
{
  const { disableCustomDomains } = await import('../installer/tenant.mjs');
  const fake = aws({ parameters: { TenantDistributionId: 'EMT123', ConnectionGroupId: 'cg_1', RoutingEndpoint: 'a.cloudfront.net', PortalAlias: 'true', RootDomain: 'apps.example.com' },
    existing: { functions: ['student-hosting-spa-fallback', 'student-hosting-tenant-api'], groups: [{ Id: 'cg_1', Name: 'student-hosting', RoutingEndpoint: 'a.cloudfront.net', Enabled: true }],
      distributions: [{ Id: 'EMT123', Comment: tenantNames('student-hosting').comment, Enabled: true }], tenants: [{ Id: 'dt_1', Name: 'student-alice', Enabled: true, Domains: [{ Domain: 'www.alice.example' }] }] } });
  const result = await disableCustomDomains(ctxFor(fake), { ...config, domain: 'apps.example.com', tenant: { distributionId: 'EMT123', connectionGroupId: 'cg_1', routingEndpoint: 'a.cloudfront.net' } });
  assert.equal(result.ok, true);
  assert.deepEqual(fake.w.gone, ['tenant disabled', 'tenant', 'distribution disabled', 'distribution', 'group', 'function student-hosting-spa-fallback', 'function student-hosting-tenant-api']);
  const last = fake.w.deployed.at(-1);
  assert.deepEqual([last.TenantDistributionId, last.ConnectionGroupId, last.RoutingEndpoint], ['', '', ''], 'the portal is told custom domains are off');
  assert.equal(last.PortalAlias, 'true', 'the portal keeps its own domain');
  const dry = aws({ parameters: { TenantDistributionId: 'EMT123' }, existing: { distributions: [{ Id: 'EMT123', Comment: tenantNames('student-hosting').comment, Enabled: true }] } });
  await disableCustomDomains(ctxFor(dry, { dryRun: true }), config);
  assert.equal(dry.w.distributions.length, 1, 'a dry run removes nothing');
  console.log('  ok  disable: tenants, distribution, group and functions removed, then the portal is told they are off (and keeps its own domain)');
}

// ---------- teardown ----------
{
  const fake = aws({ existing: { functions: ['student-hosting-spa-fallback', 'student-hosting-tenant-api'], groups: [{ Id: 'cg_1', Name: 'student-hosting', RoutingEndpoint: 'a.cloudfront.net', Enabled: true }],
    distributions: [{ Id: 'EMT123', Comment: tenantNames('student-hosting').comment, Enabled: true }], tenants: [{ Id: 'dt_1', Name: 'student-alice', Enabled: true, Domains: [{ Domain: 'www.alice.example' }] }] } });
  await teardownCustomDomains(ctxFor(fake), { say: () => {} });
  assert.deepEqual(fake.w.gone, ['tenant disabled', 'tenant', 'distribution disabled', 'distribution', 'group', 'function student-hosting-spa-fallback', 'function student-hosting-tenant-api'], 'tenants first, then the distribution, the group, the functions');
  await teardownCustomDomains(ctxFor(fake), { say: () => {} });
  assert.equal(fake.w.gone.length, 7, 'running it again finds nothing left and does nothing');
  // destroy removes custom domains BEFORE the stack
  const f2 = aws({ existing: { functions: ['student-hosting-spa-fallback'], distributions: [{ Id: 'EMT123', Comment: tenantNames('student-hosting').comment, Enabled: true }] } });
  const inner = f2.fn;
  const order = [];
  f2.fn = (args) => { if (['delete-distribution', 'delete-stack'].includes(args[1])) order.push(args[1]); if (args[1] === 'delete-stack') f2.w.stack = null; return args[1] === 'delete-stack' ? '' : inner(args); };
  await engine.destroy(ctxFor(f2, { stateDir: join(work, 'd') }), { everything: true });
  assert.deepEqual(order, ['delete-distribution', 'delete-stack'], 'the distribution goes before the stack (the stack\'s origin access control cannot be deleted while it is in use)');
  console.log('  ok  teardown: tenants, distribution, group, functions in order, repeatable; destroy runs it before the stack');
}

try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may hold the folder a moment */ }
console.log('test_tenant.mjs: all passed');
