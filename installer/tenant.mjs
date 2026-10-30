// Custom domains for students ("www.theirname.com"): the pieces CloudFront needs that CloudFormation does not create in this template, made by the
// installer with the AWS CLI exactly as the original, hand-made setup was (see custom-domain/HANDOFF.md), but repeatable and removable:
//
//   1. two CloudFront functions: the single-page-app fallback and the /api route (their source is in custom-domain/)
//   2. one CloudFront connection group: its routing endpoint is what students point their CNAME at
//   3. one multi-tenant distribution ("tenant-only"): the template every student domain (a "tenant") is created from at run time by the portal
//   4. a stack update that hands the portal the ids (TenantDistributionId, ConnectionGroupId, RoutingEndpoint)
//
// Every step is safe to repeat (it finds what already exists). If anything fails the platform still works without custom domains and the message says
// how to try again. `teardownCustomDomains` removes it all, in the order CloudFront requires (tenants, then the distribution, the group, the functions),
// and must run before the stack is deleted: the stack's origin access control cannot be deleted while a distribution still uses it.
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { awsCall, deployArgs, deployStack, ensureArtifactBucket, InstallError, packageTemplate, parametersFor, REGION, stackOutputs, tenantFromParameters } from './engine.mjs';

const CACHING_OPTIMIZED = '658327ea-f89d-4fab-a63d-7e88639e58f6';
const CACHING_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad';
const ALL_VIEWER_EXCEPT_HOST = 'b689b0a8-53d0-40ab-baf2-68738e2966ac';
const METHODS_ALL = ['GET', 'HEAD', 'OPTIONS', 'PUT', 'POST', 'PATCH', 'DELETE'];

export const tenantNames = (stack) => ({
  spa: `${stack}-spa-fallback`,
  api: `${stack}-tenant-api`,
  group: stack,
  comment: `Student custom domains (${stack})`,
});

const methods = (items) => ({ Quantity: items.length, Items: items, CachedMethods: { Quantity: 2, Items: ['GET', 'HEAD'] } });

/** The multi-tenant distribution: the student's site from S3 (origin path /sites/<student>) and /api/* to the backend gateway. */
export function distributionConfig({ stack, account, bucket, oacId, apiHost, secret }) {
  const names = tenantNames(stack);
  const fn = (name) => `arn:aws:cloudfront::${account}:function/${name}`;
  return {
    CallerReference: `${stack}-tenant-template-${account}`,
    Comment: names.comment,
    Enabled: true,
    ConnectionMode: 'tenant-only',
    HttpVersion: 'http2and3',
    TenantConfig: { ParameterDefinitions: [{ Name: 'student', Definition: { StringSchema: { Comment: 'Student name; the site lives at s3://bucket/sites/<student>/', Required: true } } }] },
    Origins: {
      Quantity: 2,
      Items: [
        { Id: 'sites', DomainName: `${bucket}.s3.${REGION}.amazonaws.com`, OriginPath: '/sites/{{student}}', OriginAccessControlId: oacId, S3OriginConfig: { OriginAccessIdentity: '' } },
        { Id: 'backend', DomainName: apiHost, OriginPath: '/b/{{student}}', CustomHeaders: { Quantity: 1, Items: [{ HeaderName: 'x-origin-secret', HeaderValue: secret }] },
          CustomOriginConfig: { HTTPPort: 80, HTTPSPort: 443, OriginProtocolPolicy: 'https-only', OriginSslProtocols: { Quantity: 1, Items: ['TLSv1.2'] }, OriginReadTimeout: 30, OriginKeepaliveTimeout: 5 },
          ConnectionAttempts: 3, ConnectionTimeout: 10 },
      ],
    },
    DefaultCacheBehavior: {
      TargetOriginId: 'sites', ViewerProtocolPolicy: 'redirect-to-https', AllowedMethods: methods(['GET', 'HEAD']), Compress: true, CachePolicyId: CACHING_OPTIMIZED,
      FunctionAssociations: { Quantity: 1, Items: [{ EventType: 'viewer-request', FunctionARN: fn(names.spa) }] },
    },
    CacheBehaviors: {
      Quantity: 1,
      Items: [{
        PathPattern: '/api/*', TargetOriginId: 'backend', ViewerProtocolPolicy: 'https-only', AllowedMethods: methods(METHODS_ALL), Compress: false,
        CachePolicyId: CACHING_DISABLED, OriginRequestPolicyId: ALL_VIEWER_EXCEPT_HOST,
        FunctionAssociations: { Quantity: 1, Items: [{ EventType: 'viewer-request', FunctionARN: fn(names.api) }] },
      }],
    },
  };
}

/** A file that holds a secret (the origin secret is part of the distribution): private, removed straight after use. */
function privateFile(name, text) {
  const dir = mkdtempSync(join(tmpdir(), 'cp-tenant-'));
  const file = join(dir, name);
  writeFileSync(file, text, { mode: 0o600 });
  return { path: file.replace(/\\/g, '/'), done: () => { try { rmSync(dir, { recursive: true, force: true }); } catch { /* removed with the system's temp clean-up */ } } };
}

/** Create or update a CloudFront function and publish it. */
function ensureFunction(ctx, name, source, comment) {
  const code = privateFile('function.js', source);
  try {
    const config = `Comment=${comment},Runtime=cloudfront-js-2.0`;
    let etag;
    try {
      etag = awsCall(ctx, ['cloudfront', 'create-function', '--name', name, '--function-config', config, '--function-code', `fileb://${code.path}`], { json: true }).ETag;
    } catch (e) {
      if (!/FunctionAlreadyExists|already exists/i.test(e.message)) throw e;
      const current = awsCall(ctx, ['cloudfront', 'describe-function', '--name', name], { json: true }).ETag;
      etag = awsCall(ctx, ['cloudfront', 'update-function', '--name', name, '--if-match', current, '--function-config', config, '--function-code', `fileb://${code.path}`], { json: true }).ETag;
    }
    awsCall(ctx, ['cloudfront', 'publish-function', '--name', name, '--if-match', etag ?? '']);
  } finally { code.done(); }
}

const findGroup = (ctx, name) => (awsCall(ctx, ['cloudfront', 'list-connection-groups'], { json: true }).ConnectionGroups ?? []).find((g) => g.Name === name);
const findDistribution = (ctx, comment) => {
  const list = awsCall(ctx, ['cloudfront', 'list-distributions-by-connection-mode', '--connection-mode', 'tenant-only'], { json: true });
  return (list.DistributionList?.Items ?? []).find((d) => d.Comment === comment);
};

/**
 * Make the custom-domain pieces and hand the portal their ids. `config` is the install's configuration (it is returned with `tenant` filled in).
 * Returns {ok, tenant, message}; throws InstallError with the reason when a step fails.
 */
export async function enableCustomDomains(ctx, config, { say = () => {} } = {}) {
  const names = tenantNames(ctx.stack);
  let found;
  try { found = stackOutputs(ctx); } catch { found = { status: null, outputs: {}, parameters: {} }; } // the CLI reports a missing stack as an error
  if (!found.status) throw new InstallError(`There is no stack "${ctx.stack}" in this account.`, 'Install the platform first.');
  if (found.parameters.TenantDistributionId) return { ok: true, already: true, tenant: tenantFromParameters(found.parameters), message: 'Custom domains are already set up.' };
  if (!found.outputs.BackendApiUrl) throw new InstallError('Custom domains need the student backends (they route /api).', 'Switch backends on first: plan --enable backend, then apply.');
  const account = awsCall(ctx, ['sts', 'get-caller-identity'], { json: true }).Account;
  const resources = awsCall(ctx, ['cloudformation', 'list-stack-resources', '--stack-name', ctx.stack], { json: true }).StackResourceSummaries ?? [];
  const oacId = resources.find((r) => r.LogicalResourceId === 'SitesOAC')?.PhysicalResourceId;
  if (!oacId) throw new InstallError('The stack has no origin access control (SitesOAC).', 'Update the platform first.');
  const secret = String(found.stackId).split('/')[2]; // the same shared value the template puts in the student distribution

  say('Custom domains: publishing the two CloudFront functions...');
  ensureFunction(ctx, names.spa, readFileSync(join(ctx.repo, 'custom-domain', 'spa-fallback.js'), 'utf8'), 'Single-page-app fallback for custom domains');
  ensureFunction(ctx, names.api, readFileSync(join(ctx.repo, 'custom-domain', 'tenant-api-route.js'), 'utf8'), 'The /api route for custom domains');

  say('Custom domains: creating the connection group...');
  let group = findGroup(ctx, names.group);
  if (!group) {
    const made = awsCall(ctx, ['cloudfront', 'create-connection-group', '--name', names.group, '--enabled'], { json: true });
    group = made.ConnectionGroup ?? made;
  }
  if (ctx.dryRun) return { ok: true, dryRun: true, tenant: null, message: 'Dry run: the custom-domain pieces would be created.' };
  if (!group?.Id || !group?.RoutingEndpoint) throw new InstallError('CloudFront did not return the connection group\'s id and routing endpoint.');

  say('Custom domains: creating the multi-tenant distribution (CloudFront takes several minutes)...');
  let distribution = findDistribution(ctx, names.comment);
  if (!distribution) {
    const body = privateFile('distribution.json', JSON.stringify(distributionConfig({ stack: ctx.stack, account, bucket: found.outputs.BucketName, oacId,
      apiHost: found.outputs.BackendApiUrl.split('//')[1], secret })));
    try {
      const made = awsCall(ctx, ['cloudfront', 'create-distribution', '--distribution-config', `file://${body.path}`], { json: true });
      distribution = made.Distribution ?? made;
    } finally { body.done(); }
  }
  if (!distribution?.Id) throw new InstallError('CloudFront did not return the distribution id.');
  awsCall(ctx, ['cloudfront', 'wait', 'distribution-deployed', '--id', distribution.Id]);

  const tenant = { distributionId: distribution.Id, connectionGroupId: group.Id, routingEndpoint: group.RoutingEndpoint };
  say('Custom domains: handing the ids to the portal...');
  const next = { ...config, tenant };
  const bucket = ensureArtifactBucket(ctx, account);
  deployStack(ctx, deployArgs(ctx, packageTemplate(ctx, bucket), parametersFor(next, { aliasOn: Boolean(config.domain) && found.parameters.PortalAlias === 'true' })));
  return { ok: true, tenant, message: `Custom domains are ready. Students point a CNAME at ${tenant.routingEndpoint}.` };
}


/**
 * Switch custom domains off: remove every student's custom domain and the pieces (see teardownCustomDomains), then update the stack so the portal no
 * longer offers the feature. Students' own DNS records are theirs to remove. Safe to run again after a stop.
 */
export async function disableCustomDomains(ctx, config, { say = () => {} } = {}) {
  let found;
  try { found = stackOutputs(ctx); } catch { found = { status: null, outputs: {}, parameters: {} }; }
  if (!found.status) throw new InstallError(`There is no stack "${ctx.stack}" in this account.`);
  await teardownCustomDomains(ctx, { say });
  if (ctx.dryRun) return { ok: true, dryRun: true, message: 'Dry run: custom domains would be removed.' };
  say('Telling the portal that custom domains are off...');
  const account = awsCall(ctx, ['sts', 'get-caller-identity'], { json: true }).Account;
  const next = { ...config, tenant: null, features: (config.features ?? []).filter((f) => f !== 'customdomains') };
  deployStack(ctx, deployArgs(ctx, packageTemplate(ctx, ensureArtifactBucket(ctx, account)), parametersFor(next, { aliasOn: Boolean(config.domain) && found.parameters.PortalAlias === 'true' })));
  return { ok: true, message: 'Custom domains are switched off and their CloudFront pieces are removed.' };
}

/** Wait until a tenant or distribution reports Deployed (CloudFront changes take minutes). */
async function untilDeployed(ctx, read, what, { tries = 120, wait = 15000 } = {}) {
  for (let i = 0; i < tries; i += 1) {
    if (read() === 'Deployed') return;
    await ctx.sleep(wait);
  }
  throw new InstallError(`${what} did not finish updating in time.`, 'Run destroy again in a few minutes: it carries on.');
}

/** Remove every student tenant, the multi-tenant distribution, the connection group and the two functions. Safe to run again after a stop. */
export async function teardownCustomDomains(ctx, { say = () => {} } = {}) {
  const names = tenantNames(ctx.stack);
  const distribution = findDistribution(ctx, names.comment);
  if (distribution && !ctx.dryRun) {
    const tenants = awsCall(ctx, ['cloudfront', 'list-distribution-tenants', '--association-filter', `DistributionId=${distribution.Id}`], { json: true }).DistributionTenantList ?? [];
    for (const t of tenants) {
      say(`Removing the custom domain of tenant ${t.Name ?? t.Id}...`);
      const read = () => awsCall(ctx, ['cloudfront', 'get-distribution-tenant', '--identifier', t.Id], { json: true });
      let current = read();
      if (current.DistributionTenant?.Enabled) {
        awsCall(ctx, ['cloudfront', 'update-distribution-tenant', '--id', t.Id, '--if-match', current.ETag, '--no-enabled', '--domains', ...(current.DistributionTenant.Domains ?? []).map((d) => `Domain=${d.Domain}`)]);
        await untilDeployed(ctx, () => read().DistributionTenant?.Status, 'A custom domain');
        current = read();
      }
      awsCall(ctx, ['cloudfront', 'delete-distribution-tenant', '--id', t.Id, '--if-match', current.ETag]);
    }
    say('Removing the multi-tenant distribution (CloudFront takes several minutes)...');
    const config = () => awsCall(ctx, ['cloudfront', 'get-distribution-config', '--id', distribution.Id], { json: true });
    let cfg = config();
    if (cfg.DistributionConfig.Enabled) {
      const body = privateFile('distribution.json', JSON.stringify({ ...cfg.DistributionConfig, Enabled: false }));
      try { awsCall(ctx, ['cloudfront', 'update-distribution', '--id', distribution.Id, '--if-match', cfg.ETag, '--distribution-config', `file://${body.path}`]); } finally { body.done(); }
    }
    awsCall(ctx, ['cloudfront', 'wait', 'distribution-deployed', '--id', distribution.Id]);
    cfg = config();
    awsCall(ctx, ['cloudfront', 'delete-distribution', '--id', distribution.Id, '--if-match', cfg.ETag]);
  } else if (distribution) ctx.would.push(`remove the multi-tenant distribution ${distribution.Id} and its tenants`);
  const group = findGroup(ctx, names.group);
  if (group && !ctx.dryRun) {
    const current = awsCall(ctx, ['cloudfront', 'get-connection-group', '--identifier', group.Id], { json: true });
    if (current.ConnectionGroup?.Enabled) awsCall(ctx, ['cloudfront', 'update-connection-group', '--id', group.Id, '--if-match', current.ETag, '--no-enabled']);
    awsCall(ctx, ['cloudfront', 'delete-connection-group', '--id', group.Id, '--if-match', awsCall(ctx, ['cloudfront', 'get-connection-group', '--identifier', group.Id], { json: true }).ETag]);
  }
  for (const name of [names.spa, names.api]) {
    try {
      const etag = awsCall(ctx, ['cloudfront', 'describe-function', '--name', name], { json: true }).ETag;
      awsCall(ctx, ['cloudfront', 'delete-function', '--name', name, '--if-match', etag]);
    } catch { /* not there */ }
  }
  return { ok: true };
}
