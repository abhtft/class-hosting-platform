// Moving a class to another AWS account (installer/transfer.mjs) with two fake accounts: the DNS file, the refusals (same account, wrong account numbers,
// a used target), the steps in order (install the target, copy the data, check it, write the file), resuming after the certificate's DNS record, the
// final attach of the domain, and that the old account is never changed.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildDnsRows, CSV_COLUMNS, dnsCsv, transfer } from '../installer/transfer.mjs';
import { sampleHtml } from '../installer/verify.mjs';

const work = mkdtempSync(join(tmpdir(), 'transfer-'));
const repo = join(work, 'repo');
mkdirSync(repo);
writeFileSync(join(repo, 'template.yaml'), 'Resources: {}\n');
writeFileSync(join(repo, 'VERSION'), '1.1.0\n');
const S = (v) => ({ S: v });

// ---------- the DNS file ----------
{
  const source = { outputs: { PortalDistributionDomain: 'old-portal.cloudfront.net', DistributionDomain: 'old-sites.cloudfront.net', PortalUrl: 'https://old/' }, parameters: { RoutingEndpoint: 'old-routing.cloudfront.net' } };
  const target = { outputs: { PortalDistributionDomain: 'new-portal.cloudfront.net', DistributionDomain: 'new-sites.cloudfront.net', PortalUrl: 'https://new/' }, parameters: {} };
  const rows = buildDnsRows({ domain: 'apps.example.com', source, target, certificateRecord: { type: 'CNAME', name: '_abc.apps.example.com.', value: '_xyz.acm.aws.' }, domains: [{ slug: 'alice', domain: 'www.alice.example', state: 'active' }] });
  assert.deepEqual(rows.map((r) => r.who), ['teacher', 'teacher', 'teacher', 'student alice']);
  assert.deepEqual(rows.map((r) => r.name), ['_abc.apps.example.com.', 'deploy.apps.example.com', '*.apps.example.com', 'www.alice.example']);
  assert.match(rows[0].note, /^Step 1/);
  assert.equal(rows[1].old_value, 'old-portal.cloudfront.net');
  assert.equal(rows[1].new_value, 'new-portal.cloudfront.net');
  assert.equal(rows[2].new_value, 'new-sites.cloudfront.net');
  assert.equal(rows[3].new_value, '', 'a student custom domain has no automatic new value in this release');
  assert.match(rows[3].note, /not moved automatically/);
  const none = buildDnsRows({ domain: '', source, target });
  assert.equal(none.length, 1);
  assert.match(none[0].note, /no DNS to change/);
  const csv = dnsCsv([{ who: 'teacher', name: 'a,b', type: 'CNAME', old_value: 'x', new_value: 'say "hi"', note: 'two\nlines' }]);
  assert.ok(csv.startsWith('﻿who,name,type,old_value,new_value,note\r\n'));
  assert.ok(csv.includes('"a,b"') && csv.includes('"say ""hi"""') && csv.includes('"two\nlines"'), 'commas, quotes and line breaks are quoted');
  assert.equal(csv.split('\r\n')[0].replace('﻿', '').split(',').length, CSV_COLUMNS.length);
  console.log('  ok  the DNS file: certificate record first, then the portal and wildcard switch, one row per student domain, spreadsheet-safe CSV');
}

// ---------- two fake accounts ----------
function account(id, { stack = null, parameters = {}, tokens = [], stored = [], portal = '', siteDomain = '', portalDomain = '' } = {}) {
  const calls = [];
  const log = [];
  const w = { id, stack, parameters, tokens, stored, buckets: new Set(), certs: [], deployed: Boolean(stack), written: [], ssm: { '/k': '{"alice":"k"}', '/t': 'teacher-secret' } };
  const outputs = () => ({ PortalUrl: w.parameters.PortalAlias === 'true' ? `https://deploy.${w.parameters.RootDomain}/` : portal, PortalDistributionDomain: portalDomain, DistributionDomain: siteDomain, BucketName: `bucket-${id}`,
    TokensTableName: `tokens-${id}`, ...(w.parameters.StorageEnabled === 'true' ? { StudentDataTableName: `data-${id}` } : {}), DistributionId: `DIST${id}`, KeysParameterName: '/k', TeacherParameterName: '/t' });
  const fn = (args) => {
    calls.push(args);
    const [svc, op] = args;
    const opt = (n) => args[args.indexOf(n) + 1];
    if (svc === 'sts') return JSON.stringify({ Account: id, Arn: `arn:aws:iam::${id}:user/t` });
    if (svc === 'cloudfront' && op === 'list-distributions') return '{}';
    if (svc === 'cloudformation' && op === 'describe-stacks') {
      if (!w.stack) throw new Error('does not exist');
      return JSON.stringify({ Stacks: [{ StackStatus: w.stack, Outputs: Object.entries(outputs()).map(([OutputKey, OutputValue]) => ({ OutputKey, OutputValue })), Parameters: Object.entries(w.parameters).map(([ParameterKey, ParameterValue]) => ({ ParameterKey, ParameterValue })) }] });
    }
    if (svc === 's3api' && op === 'head-bucket') { if (!w.buckets.has(opt('--bucket'))) throw new Error('404'); return ''; }
    if (svc === 's3api' && op === 'create-bucket') { w.buckets.add(opt('--bucket')); return ''; }
    if (svc === 's3api') return '';
    if (svc === 'cloudformation' && op === 'package') return '';
    if (svc === 'cloudformation' && op === 'deploy') {
      w.stack = 'CREATE_COMPLETE';
      w.parameters = Object.fromEntries(args.slice(args.indexOf('--parameter-overrides') + 1).filter((a) => a.includes('=') && !a.startsWith('--')).map((a) => a.split(/=(.*)/s).slice(0, 2)));
      return '';
    }
    if (svc === 'acm' && op === 'list-certificates') return JSON.stringify({ CertificateSummaryList: w.certs.map((c) => ({ DomainName: c.name, CertificateArn: c.arn })) });
    if (svc === 'acm' && op === 'request-certificate') { w.certs.push({ name: opt('--domain-name'), arn: 'arn:cert', status: 'PENDING_VALIDATION' }); return JSON.stringify({ CertificateArn: 'arn:cert' }); }
    if (svc === 'acm' && op === 'describe-certificate') return JSON.stringify({ Certificate: { Status: w.certs[0].status, DomainValidationOptions: [{ ResourceRecord: { Type: 'CNAME', Name: '_v.apps.example.com.', Value: '_w.acm.aws.' } }] } });
    if (svc === 'dynamodb' && op === 'scan') return JSON.stringify({ Items: opt('--table-name').startsWith('tokens') ? w.tokens : w.stored });
    if (svc === 'dynamodb' && op === 'batch-write-item') { const body = JSON.parse(readFileSync(opt('--request-items').replace('file://', ''), 'utf8')); const [table, reqs] = Object.entries(body)[0]; w.written.push({ table, items: reqs.map((r) => r.PutRequest.Item) }); return '{"UnprocessedItems":{}}'; }
    if (svc === 'ssm' && op === 'get-parameter') return `${w.ssm[opt('--name')]}\n`;
    if (svc === 'ssm' && op === 'put-parameter') { const b = JSON.parse(readFileSync(opt('--cli-input-json').replace('file://', ''), 'utf8')); w.ssm[b.Name] = b.Value; return ''; }
    if (svc === 's3' && op === 'sync') { if (args[2].startsWith('s3://')) { mkdirSync(args[3], { recursive: true }); writeFileSync(join(args[3], 'index.html'), 'x'); } return ''; }
    if (svc === 'cloudfront' && op === 'create-invalidation') return '{}';
    throw new Error(`fake aws (${id}): unexpected ${args.join(' ')}`);
  };
  return { fn, calls, w, log };
}
const shared = { slug: '' }; // the throwaway student's name, so the sample site address contains it like a real one
function portalFetch(url) {
  return async (u, init = {}) => {
    const path = u.replace(url, '');
    const method = init.method ?? 'GET';
    const auth = init.headers?.authorization;
    const reply = (status, body, text) => ({ status, json: async () => body, text: async () => text ?? '' });
    if (u.startsWith('https://zz-verify')) return reply(200, null, sampleHtml());
    if (u.startsWith('https://zz-')) return reply(404, null, 'no');
    if (path === '' || path === '/') return reply(200, null, 'x');
    if (path === '/mcp-version.json') return reply(200, { v: 1 });
    if (path === '/kit-version.json') return reply(200, { v: 1 });
    if (path === '/api/admin/tokens' && method === 'GET') return reply(auth ? 200 : 401, {});
    if (path === '/api/student/me') return auth === 'Bearer ST' ? reply(200, { token: { backend: { available: true }, storage: { available: true } } }) : reply(401, {});
    if (path === '/api/teacher/login') return JSON.parse(init.body).password === 'pw' ? reply(200, { session: 'T' }) : reply(401, {});
    if (path === '/api/admin/tokens' && method === 'POST') { shared.slug = JSON.parse(init.body).slug; return reply(201, { token: 'mds_x', item: { id: 'i1' } }); }
    if (path === '/api/student/login') return reply(200, { session: 'ST' });
    if (path.endsWith('/revoke')) return reply(200, {});
    return reply(404, {});
  };
}
const tokenItems = [
  { pk: S('tok#aaa'), slug: S('alice'), backend: { M: { enabled: { BOOL: true }, state: S('ready'), function_name: S('sd-alice') } }, domain: S('www.alice.example'), domain_state: S('active') },
  { pk: S('slug#alice'), token: S('aaa') }];
const sourceParams = { RootDomain: 'apps.example.com', CertificateArn: 'arn:old-cert', PortalAlias: 'true', BackendEnabled: 'true', StorageEnabled: 'true', AiEnabled: 'false', AiGeminiEnabled: 'false', RoutingEndpoint: 'old-routing.cloudfront.net' };
const make = (srcExtra = {}) => ({
  src: account('111111111111', { stack: 'UPDATE_COMPLETE', parameters: sourceParams, tokens: tokenItems, stored: [{ s: S('alice#n'), id: S('1') }], portal: 'https://old-portal.cloudfront.net/', siteDomain: 'old-sites.cloudfront.net', portalDomain: 'old-portal.cloudfront.net', ...srcExtra }),
  dst: account('222222222222', { portal: 'https://new-portal.cloudfront.net/', siteDomain: 'new-sites.cloudfront.net', portalDomain: 'new-portal.cloudfront.net' }),
});
const ctxFor = (acct, extra = {}) => ({ repo, profile: acct.w.id, stack: 'student-hosting', dryRun: false, would: [], now: () => '2026-01-01T00:00:00Z', sleep: async () => {}, aws: acct.fn,
  node: () => '', fetch: portalFetch('https://new-portal.cloudfront.net'), publish: () => `https://${shared.slug}.example/`, ...extra });
const confirmBoth = { source: '111111111111', target: '222222222222' };
const stateDir = () => join(work, `state-${Math.random().toString(36).slice(2, 7)}`);

// ---------- refusals before anything changes ----------
{
  const { src, dst } = make();
  const base = { source: ctxFor(src), target: ctxFor(dst), stateFolder: stateDir() };
  await assert.rejects(() => transfer({ ...base, confirm: { source: '111111111111', target: '999999999999' } }), /do not match the two accounts/);
  await assert.rejects(() => transfer({ ...base, confirm: { source: '1', target: '2' } }), /do not match/);
  await assert.rejects(() => transfer({ source: ctxFor(src), target: ctxFor(src), confirm: { source: '111111111111', target: '111111111111' }, stateFolder: stateDir() }), /same AWS account/);
  const empty = account('333333333333');
  await assert.rejects(() => transfer({ source: ctxFor(empty), target: ctxFor(dst), confirm: { source: '333333333333', target: '222222222222' }, stateFolder: stateDir() }), /no working platform/);
  assert.equal(dst.w.stack, null, 'nothing was created in the target by a refused transfer');
  assert.equal(src.calls.filter((a) => /^(put|delete|create|update|batch)/.test(a[1])).length, 0, 'and the source was never written to');
  console.log('  ok  wrong account numbers, the same account, or a source with no platform: refused before anything changes');
}

// ---------- the whole move: a domain install needs the certificate record first, then continues ----------
{
  const { src, dst } = make();
  const folder = stateDir();
  const said = [];
  const run = (extra = {}) => transfer({ source: ctxFor(src), target: ctxFor(dst), confirm: confirmBoth, login: { username: 'teacher', password: 'pw' }, csvPath: join(work, 'dns.csv'), say: (t) => said.push(t), stateFolder: folder, ...extra });
  const first = await run();
  assert.equal(first.needs, 'certificate');
  assert.equal(first.rows[0].name, '_v.apps.example.com.', 'the certificate record is the first row');
  assert.equal(dst.w.written.length, 0, 'no data is copied before the target exists');
  dst.w.certs[0].status = 'ISSUED'; // the teacher added the record and AWS issued the certificate
  const second = await run();
  assert.ok(second.ok, JSON.stringify(second));
  assert.deepEqual(second.steps, ['target installed', 'data copied', 'target verified'], JSON.stringify(second.verified?.checks?.filter((c) => !c.ok)));
  assert.equal(second.verified.ok, true, JSON.stringify(second.verified.checks.filter((c) => !c.ok)));
  assert.equal(dst.w.parameters.PortalAlias, 'false', 'the domain is not attached to the new platform before the DNS switch');
  assert.equal(dst.w.parameters.StorageEnabled, 'true', 'the same features are switched on in the target');
  const tokens = dst.w.written.find((x) => x.table.startsWith('tokens')).items;
  assert.equal(tokens.length, 2);
  assert.ok(!tokens.find((i) => i.pk.S === 'tok#aaa').domain, 'the old account\'s custom-domain state was dropped');
  assert.ok(dst.w.written.some((x) => x.table.startsWith('data')), 'stored student data was copied');
  assert.equal(dst.w.ssm['/t'], 'teacher-secret', 'the teacher login was copied');
  const csv = readFileSync(join(work, 'dns.csv'), 'utf8');
  assert.ok(csv.includes('deploy.apps.example.com,CNAME,old-portal.cloudfront.net,new-portal.cloudfront.net') && csv.includes('www.alice.example'));
  assert.ok(src.calls.every((a) => !/^(put|delete|create|update|batch|execute|request)/.test(a[1])), 'the source account was only read');
  assert.match(second.message, /The old account is untouched/);
  console.log('  ok  full move: certificate record first, resumes, target installed with the same features, data copied, target checked, DNS file written, source only read');

  // after the DNS switch: the new platform takes over the domain
  const third = await run({ finish: true });
  assert.ok(third.steps.includes('domain attached to the new platform'));
  assert.equal(dst.w.parameters.PortalAlias, 'true');
  console.log('  ok  after the DNS switch the final run attaches the domain to the new platform');
}

// ---------- no domain: nothing to switch ----------
{
  const { src, dst } = make();
  src.w.parameters = { ...sourceParams, RootDomain: '', CertificateArn: '', PortalAlias: 'false' };
  const result = await transfer({ source: ctxFor(src), target: ctxFor(dst), confirm: confirmBoth, login: { username: 'teacher', password: 'pw' }, csvPath: join(work, 'nodns.csv'), stateFolder: stateDir() });
  assert.deepEqual(result.steps, ['target installed', 'data copied', 'target verified'], JSON.stringify(result.verified?.checks?.filter((c) => !c.ok)));
  assert.match(readFileSync(join(work, 'nodns.csv'), 'utf8'), /no DNS to change/);
  console.log('  ok  without a domain the file says there is no DNS to change');
}

// ---------- a transfer between other accounts in the same folder is refused ----------
{
  const folder = stateDir();
  const a = make();
  a.src.w.parameters = { ...sourceParams, RootDomain: '', CertificateArn: '' };
  await transfer({ source: ctxFor(a.src), target: ctxFor(a.dst), confirm: confirmBoth, login: { username: 'teacher', password: 'pw' }, stateFolder: folder });
  const other = make();
  const third = account('444444444444');
  await assert.rejects(() => transfer({ source: ctxFor(other.src), target: ctxFor(third), confirm: { source: '111111111111', target: '444444444444' }, stateFolder: folder }), /already in progress/);
  console.log('  ok  a second transfer to a different account cannot mix its state with the first');
}

try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows may hold the folder a moment */ }
console.log('test_transfer.mjs: all passed');
