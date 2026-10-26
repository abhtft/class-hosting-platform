#!/usr/bin/env node
// Local test suite - no AWS needed.  Usage: node tests/run.mjs   (needs Node 22+, Python 3 + boto3)
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
// boto3 clients are created when the Lambda modules are imported; the tests never call AWS, but a region must be set (CI has none)
process.env.AWS_DEFAULT_REGION ??= 'us-east-1';
const PORT = 8765;
let failures = 0;

async function step(name, fn) {
  try {
    await fn();
    console.log(`✔ ${name}`);
  } catch (e) {
    failures++;
    console.log(`✖ ${name}\n  ${e.message.split('\n').join('\n  ')}`);
  }
}

// ---- 1. Lambda unit tests ----
await step('lambda: zip validation, content types, warnings', () => {
  const r = spawnSync(python, [join(here, 'test_lambda.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('portal: tokens, teacher/student login, sessions, lifecycle', () => {
  const r = spawnSync(python, [join(here, 'test_portal.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('portal deploy: session-bound upload/publish, expiry, revoke', () => {
  const r = spawnSync(python, [join(here, 'test_portal_deploy.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('custom domains: switch, setup states, removal, cache invalidation, sweeper', () => {
  const r = spawnSync(python, [join(here, 'test_domains.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('backends: allowance, limiter, gateway, deploy flow, Python and Node handlers', () => {
  const r = spawnSync(python, [join(here, 'test_backend.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('backends: template IAM scoping and switches', () => {
  const r = spawnSync(python, [join(here, 'test_template_backend.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('custom domains: tenant distribution config routes /api/* to the backend (no secrets in the repo)', () => {
  const r = spawnSync(python, [join(here, 'test_tenant_config.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('operations: versioning, budget and alarms are in place (no email in the repo)', () => {
  const r = spawnSync(python, [join(here, 'test_template_ops.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class AI connector: template keeps it off by default and the management key out of reach', () => {
  const r = spawnSync(python, [join(here, 'test_template_ai.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class AI connector: capped keys, function environment, lifecycle, failures, no key exposed', () => {
  const r = spawnSync(python, [join(here, 'test_ai.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class AI with Gemini: provider switch, private-key proxy, forced model, caps, thinking-token cost, lifecycle; template guards', () => {
  for (const test of ['test_ai_gemini.py', 'test_template_gemini.py']) {
    const r = spawnSync(python, [join(here, test)], { encoding: 'utf8' });
    assert.equal(r.status, 0, `${test}: ${r.stderr || r.stdout}`);
  }
});

await step('teacher installer and updater (fake AWS CLI): try and domain install, plan/apply bound to the shown plan, feature switches; teacher MCP; the npm package', () => {
  for (const test of ['test_installer.mjs', 'test_teacher_mcp.mjs', 'test_teacher_package.mjs']) {
    const r = spawnSync('node', [join(here, test)], { encoding: 'utf8' });
    assert.equal(r.status, 0, `${test}: ${r.stderr || r.stdout}`);
  }
});

await step('web: folder checks and zip builder (zip validated by the Lambda code)', () => {
  const r = spawnSync('node', [join(here, 'test_web.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class AI helper (Python): friendly errors, retries, offline mock, key never printed', () => {
  const r = spawnSync(python, [join(here, 'test_class_ai.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class AI helper (Node): friendly errors, retries, offline mock, key never printed', () => {
  const r = spawnSync('node', [join(here, 'test_class_ai.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student kit: contents, IDE rule copies, checker rules, local dev servers', () => {
  const r = spawnSync('node', [join(here, 'test_kit.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student kit update: platform status and update, managed files only, hash and path checks', () => {
  const r = spawnSync('node', [join(here, 'test_kit_update.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student kit end to end: deploy through portal-deploy.mjs, class AI on and off, Python and Node (needs network for npm)', () => {
  const r = spawnSync('node', [join(here, 'test_kit_e2e.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class storage: switch and caps, key isolation between students, exact quotas, allowance, files, lifecycle, no key exposed', () => {
  const r = spawnSync(python, [join(here, 'test_storage.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class storage template: off by default, tightly scoped IAM, private bucket, retained data table', () => {
  const r = spawnSync(python, [join(here, 'test_template_storage.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('class storage helpers (Python and Node) against the real storage API: data, files, every error kind, the local folder', () => {
  for (const test of ['test_class_storage.py', 'test_class_storage.mjs']) {
    const r = test.endsWith('.py') ? spawnSync(python, [join(here, test)], { encoding: 'utf8' }) : spawnSync('node', [join(here, test)], { encoding: 'utf8' });
    assert.equal(r.status, 0, `${test}: ${r.stderr || r.stdout}`);
  }
});

await step('class-platform-mcp package: npx from a URL, editor roots, init_project, bundled check and deploy, computer-wide token, setup for VS Code and Antigravity', () => {
  const r = spawnSync('node', [join(here, 'test_mcp_package.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student kit update notice: one quiet line once a day, never in CI, piped output or --json, silent offline; minimum kit version', () => {
  const r = spawnSync('node', [join(here, 'test_update_notice.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student kit MCP server: handshake, check, status and deploy tools, confirm required, token never echoed', () => {
  const r = spawnSync('node', [join(here, 'test_mcp.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('specification: the machine-checked facts in docs/TECHNICAL_SPECIFICATION.md equal the code', () => {
  const r = spawnSync(python, [join(here, 'test_spec.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('identifiers: no account id, domain or distribution id of the maintainer in any tracked file', () => {
  const r = spawnSync(python, [join(here, 'test_no_identifiers.py')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('diagrams: every Mermaid diagram in the docs has a current static PNG', () => {
  const r = spawnSync('node', [join(here, 'test_diagrams.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student downloads: published files, the card and both workflows agree', () => {
  const r = spawnSync('node', [join(here, 'test_downloads.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

await step('student CLI: portal-deploy.mjs publishes backend and frontend with a token', () => {
  const r = spawnSync('node', [join(here, 'test_portal_cli.mjs')], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr || r.stdout);
});

// ---- 2. CloudFront router function (extracted from template.yaml) ----
function loadRouter(rootDomain) {
  const template = readFileSync(join(root, 'template.yaml'), 'utf8').replace(/\r\n/g, '\n');
  const body = template.split('FunctionCode: !Sub |\n')[1].split('\n  Distribution:')[0];
  const code = body.split('\n').map((l) => l.slice(8)).join('\n').replace('${RootDomain}', rootDomain);
  return new Function(`${code}\nreturn handler;`)();
}
const route = (handler, host, uri) => {
  const r = handler({ request: { uri, headers: { host: { value: host } } } });
  return r.uri ?? `${r.statusCode}${r.headers ? ' ' + r.headers.location.value : ''}`;
};

await step('router: subdomain mode (RootDomain set)', () => {
  const h = loadRouter('example.edu');
  assert.equal(route(h, 'alice.example.edu', '/'), '/sites/alice/index.html');
  assert.equal(route(h, 'alice.example.edu', '/cart/42'), '/sites/alice/index.html'); // SPA route
  assert.equal(route(h, 'alice.example.edu', '/assets/x.css'), '/sites/alice/assets/x.css');
  assert.equal(route(h, 'a.b.example.edu', '/'), '404');
  assert.equal(route(h, 'Bad_Name.example.edu', '/'), '404');
  assert.equal(route(h, 'd123.cloudfront.net', '/alice/'), '/sites/alice/index.html'); // path mode still works
});
await step('router: path mode (RootDomain empty)', () => {
  const h = loadRouter('');
  assert.equal(route(h, 'd.cloudfront.net', '/alice/'), '/sites/alice/index.html');
  assert.equal(route(h, 'd.cloudfront.net', '/alice'), '302 /alice/');
  assert.equal(route(h, 'd.cloudfront.net', '/alice/assets/a.js'), '/sites/alice/assets/a.js');
  assert.equal(route(h, 'd.cloudfront.net', '/'), '404');
});

// ---- 2b. Portal CloudFront function (clean URLs + viewer IP for the API) ----
await step('portal function: clean URLs, API viewer IP', () => {
  const template = readFileSync(join(root, 'template.yaml'), 'utf8').replaceAll('\r\n', '\n');
  const body = template.split('FunctionCode: |\n')[1].split('\n\n  PortalDistribution:')[0];
  const code = body.split('\n').map((l) => l.slice(8)).join('\n');
  const handler = new Function(`${code}\nreturn handler;`)();
  const run = (uri) => handler({ request: { uri, headers: {} }, viewer: { ip: '203.0.113.9' } }).uri;
  assert.equal(run('/'), '/');
  assert.equal(run('/teacher'), '/teacher.html');
  assert.equal(run('/student/'), '/student.html');
  assert.equal(run('/shared.css'), '/shared.css');
  assert.equal(run('/common.js'), '/common.js');
  const api = handler({ request: { uri: '/api/teacher/login', headers: { 'x-viewer-ip': { value: '6.6.6.6' } } }, viewer: { ip: '203.0.113.9' } });
  assert.equal(api.uri, '/api/teacher/login');
  assert.equal(api.headers['x-viewer-ip'].value, '203.0.113.9', 'a client-supplied value is overwritten');
});

await step('backend route function: /api/* to /b/<slug>/*', () => {
  const template = readFileSync(join(root, 'template.yaml'), 'utf8').replaceAll('\r\n', '\n');
  const after = template.split('${AWS::StackName}-backend-route')[1];
  const body = after.split('FunctionCode: !Sub |\n')[1].split('\n\n  # Upper limit')[0];
  const code = body.split('\n').map((l) => l.slice(8)).join('\n').replace('${RootDomain}', 'example.com');
  const handler = new Function(`${code}\nreturn handler;`)();
  const run = (host, uri, ip = '203.0.113.9') => handler({ request: { uri, headers: { host: { value: host } } }, viewer: { ip } });
  assert.equal(run('alice.example.com', '/api/hello').uri, '/b/alice/hello');
  assert.equal(run('alice.example.com', '/api').uri, '/b/alice/');
  assert.equal(run('alice.example.com', '/api/').uri, '/b/alice/');
  assert.equal(run('alice-2.example.com', '/api/a/b/c').uri, '/b/alice-2/a/b/c');
  assert.equal(run('ALICE.Example.com', '/api/x').uri, '/b/alice/x', 'host matching is case-insensitive');
  for (const host of ['a.b.example.com', 'example.com', 'alice.other.com', '-bad.example.com', '']) {
    assert.equal(run(host, '/api/x').statusCode, 404, host);
  }
  const forged = handler({ request: { uri: '/api/x', headers: { host: { value: 'alice.example.com' }, 'x-viewer-ip': { value: '6.6.6.6' } } }, viewer: { ip: '203.0.113.9' } });
  assert.equal(forged.headers['x-viewer-ip'].value, '203.0.113.9', 'a client-supplied viewer IP is overwritten');
});

await step('tenant api route function: strips /api, passes the viewer IP', () => {
  const code = readFileSync(join(root, 'custom-domain', 'tenant-api-route.js'), 'utf8');
  const handler = new Function(`${code}\nreturn handler;`)();
  const run = (uri, headers = {}) => handler({ request: { uri, headers }, viewer: { ip: '203.0.113.9' } });
  assert.equal(run('/api/analyse').uri, '/analyse');
  assert.equal(run('/api/a/b/c').uri, '/a/b/c');
  assert.equal(run('/api/').uri, '/');
  assert.equal(run('/api').uri, '/');
  assert.equal(run('/api/x', { 'x-viewer-ip': { value: '6.6.6.6' } }).headers['x-viewer-ip'].value, '203.0.113.9', 'a client-supplied viewer IP is overwritten');
});

// ---- 3. End to end: real deploy.mjs -> real Lambda code -> fake AWS ----
const server = spawn(python, [join(here, 'fake_api.py'), join(root, 'lambda'), String(PORT), 'example.edu'], { stdio: 'inherit' });
const state = async () => (await fetch(`http://127.0.0.1:${PORT}/`)).json();
for (let i = 0; i < 50; i++) {
  if (await fetch(`http://127.0.0.1:${PORT}/`).then(() => true, () => false)) break;
  await new Promise((r) => setTimeout(r, 200));
}

const site = mkdtempSync(join(tmpdir(), 'student-site-'));
const deploy = (key = 'alice.s3cret') =>
  spawnSync('node', [join(root, 'student', 'deploy.mjs'), '--skip-build'], {
    cwd: site, encoding: 'utf8', env: { ...process.env, DEPLOY_API_URL: `http://127.0.0.1:${PORT}`, DEPLOY_KEY: key },
  });

try {
  mkdirSync(join(site, 'dist', 'assets'), { recursive: true });
  writeFileSync(join(site, 'dist', 'index.html'), '<script src="/assets/app.js"></script>');
  writeFileSync(join(site, 'dist', 'assets', 'app.js'), 'console.log(1)');
  writeFileSync(join(site, 'dist', 'assets', 'old.css'), 'body{}');

  await step('e2e: first deploy publishes all files with correct headers', async () => {
    const r = deploy();
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.match(r.stdout, /https:\/\/alice\.example\.edu\//);
    const { objects, invalidations } = await state();
    assert.deepEqual(Object.keys(objects).sort(), ['sites/alice/assets/app.js', 'sites/alice/assets/old.css', 'sites/alice/index.html']);
    assert.match(objects['sites/alice/index.html'].CacheControl, /max-age=0/);
    assert.equal(objects['sites/alice/assets/app.js'].ContentType, 'text/javascript; charset=utf-8');
    assert.deepEqual(invalidations.at(-1), ['/sites/alice/*', '/alice/*']);
  });

  await step('e2e: redeploy removes files that disappeared from the build', async () => {
    rmSync(join(site, 'dist', 'assets', 'old.css'));
    const r = deploy();
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.ok(!('sites/alice/assets/old.css' in (await state()).objects));
  });

  await step('e2e: wrong key is rejected and changes nothing', async () => {
    const before = JSON.stringify(await state());
    const r = deploy('alice.wrong');
    assert.notEqual(r.status, 0);
    assert.match(r.stderr, /Invalid or missing x-deploy-key/);
    assert.equal(JSON.stringify(await state()), before);
  });

  await step('e2e: key of an unknown student is rejected', () => {
    const r = deploy('mallory.s3cret');
    assert.notEqual(r.status, 0);
  });
} finally {
  server.kill();
  rmSync(site, { recursive: true, force: true });
}

console.log(failures ? `\n${failures} step(s) FAILED` : '\nAll local tests passed');
process.exit(failures ? 1 : 0);
