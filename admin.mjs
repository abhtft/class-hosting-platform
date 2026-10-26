#!/usr/bin/env node
// Teacher helper - manage student deploy keys (uses your local AWS CLI credentials).
//
//   node admin.mjs add alice bob     create keys and print what to send each student
//   node admin.mjs remove bob        revoke a key (site stays online)
//   node admin.mjs list              show students and their site URLs
//   node admin.mjs teacher-setup <username>   set (or reset) the web-portal teacher login
//   node admin.mjs portal-publish    upload web/ (the portal pages) and clear the CDN cache
//
// STACK_NAME (default "student-hosting") selects the deployed stack.
import { execFileSync } from 'node:child_process';
import { randomBytes, scryptSync } from 'node:crypto';
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createInterface } from 'node:readline';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const STACK = process.env.STACK_NAME || 'student-hosting';
const NAME_RE = /^[a-z0-9][a-z0-9-]{0,29}$/;
// Names that would clash with internal folders or subdomains you are likely to use yourself.
// Add your own (e.g. "bs") here; an existing explicit DNS record always wins over the wildcard.
const RESERVED = new Set(['sites', 'uploads', 'www', 'api', 'admin', 'mail', 'ftp', 'app', 'apps', 'static', 'assets', 'archive', 'deploy', 'bs', 'lenden']);
const [command, ...names] = process.argv.slice(2);

const aws = (...args) => execFileSync('aws', [...args, '--output', 'json'], { encoding: 'utf8' });

const outputs = Object.fromEntries(
  JSON.parse(aws('cloudformation', 'describe-stacks', '--stack-name', STACK)).Stacks[0].Outputs.map((o) => [
    o.OutputKey,
    o.OutputValue,
  ]),
);
const param = outputs.KeysParameterName;
const siteUrl = (name) => outputs.SiteUrlPattern.replace('<student>', name);

const keys = JSON.parse(JSON.parse(aws('ssm', 'get-parameter', '--name', param)).Parameter.Value || '{}');
const save = () => aws('ssm', 'put-parameter', '--name', param, '--type', 'String', '--overwrite', '--value', JSON.stringify(keys));

// Site names already taken by web-portal tokens (their folders share sites/<name>/ with CLI students).
function portalSlugs() {
  if (!outputs.TokensTableName) return new Set();
  const scan = JSON.parse(aws('dynamodb', 'scan', '--table-name', outputs.TokensTableName, '--filter-expression', 'begins_with(pk, :p)',
    '--expression-attribute-values', '{":p":{"S":"slug#"}}', '--projection-expression', 'pk'));
  return new Set(scan.Items.map((item) => item.pk.S.slice('slug#'.length)));
}

if (command === 'add') {
  const taken = portalSlugs();
  for (const name of names) {
    if (taken.has(name)) {
      console.error(`Skipping "${name}": a web-portal token already uses this site name.`);
      continue;
    }
    if (!NAME_RE.test(name) || RESERVED.has(name)) {
      console.error(`Skipping "${name}": use 1-30 lowercase letters, digits or "-" and not a reserved name.`);
      continue;
    }
    if (keys[name]) {
      console.log(`${name} already has a key (remove it first to rotate).`);
      continue;
    }
    keys[name] = randomBytes(12).toString('base64url');
    console.log(`\n--- send to ${name} ---\nDEPLOY_KEY=${name}.${keys[name]}\nSite: ${siteUrl(name)}`);
  }
  save();
} else if (command === 'remove') {
  for (const name of names) delete keys[name];
  save();
  console.log(`Removed: ${names.join(', ')} (takes effect within a minute)`);
} else if (command === 'list') {
  for (const name of Object.keys(keys).sort()) console.log(`${name.padEnd(20)} ${siteUrl(name)}`);
  console.log(`\n${Object.keys(keys).length} students, parameter uses ${JSON.stringify(keys).length}/4096 bytes`);
} else if (command === 'portal-publish') {
  const root = dirname(fileURLToPath(import.meta.url));
  const web = join(root, 'web');
  const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.yml': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.json': 'application/json', '.zip': 'application/zip', '.tgz': 'application/gzip' };
  // Pages from web/, plus the student downloads (portal-downloads.json maps published name -> source file, so each has one source of truth).
  const downloads = JSON.parse(readFileSync(join(root, 'portal-downloads.json'), 'utf8'));
  const uploads = [
    ...readdirSync(web).filter((f) => statSync(join(web, f)).isFile()).map((f) => [f, join(web, f)]),
    ...Object.entries(downloads).map(([name, source]) => [name, join(root, source)]),
  ];
  // The student kit (zip + version file) and the MCP package (one tarball per version, the newest alias, mcp-version.json), all built fresh from
  // starter/ by the same code the local dev portal uses.
  const { buildPublishFiles } = await import('./starter/publish-files.mjs');
  const kitTemp = mkdtempSync(join(tmpdir(), 'kit-publish-'));
  const built = buildPublishFiles(kitTemp, outputs.PortalUrl);
  for (const name of built.files) uploads.push([name, join(kitTemp, name)]);
  const kit = { version: built.kitVersion };
  const files = uploads.map(([name]) => name);
  for (const [file, path] of uploads) {
    // Explicit content types: the AWS CLI guesses from the Windows registry, which can label .js as text/plain.
    aws('s3', 'cp', path, `s3://${outputs.BucketName}/portal/${file}`, '--content-type', TYPES[extname(file)] ?? 'application/octet-stream', '--cache-control', 'no-cache');
  }
  const listed = JSON.parse(aws('s3api', 'list-objects-v2', '--bucket', outputs.BucketName, '--prefix', 'portal/', '--query', 'Contents[].Key') || 'null') ?? [];
  // every older class-platform-mcp-<version>.tgz stays: editors' settings point at the exact version they were set up with
  for (const key of listed.filter((k) => !files.includes(k.slice('portal/'.length)) && !/^portal\/class-platform-mcp-[\w.-]+\.tgz$/.test(k))) aws('s3', 'rm', `s3://${outputs.BucketName}/${key}`);
  aws('cloudfront', 'create-invalidation', '--distribution-id', outputs.PortalDistributionId, '--paths', '/*');
  rmSync(kitTemp, { recursive: true, force: true });
  console.log(`Published ${files.length} files (including the student kit ${kit.version} and class-platform-mcp ${built.mcpVersion}). Portal: ${outputs.PortalUrl}`);
} else if (command === 'teacher-setup') {
  const username = names[0];
  if (!username || !/^[a-zA-Z0-9._-]{3,40}$/.test(username)) {
    console.error('Usage: node admin.mjs teacher-setup <username>   (3-40 letters, digits, . _ -)');
    process.exit(1);
  }
  const password = process.env.TEACHER_PASSWORD || (await askHidden('New teacher password (min 10 chars): '));
  if (password.length < 10) {
    console.error('Password must be at least 10 characters.');
    process.exit(1);
  }
  // Must match scrypt_hash() in lambda/portal.py. Re-running keeps the signing secret unless the parameter is empty,
  // so existing sessions survive a password change only if you want them to; delete the parameter value to log everyone out.
  const salt = randomBytes(16);
  const existing = JSON.parse(JSON.parse(aws('ssm', 'get-parameter', '--name', outputs.TeacherParameterName)).Parameter.Value || '{}');
  const account = {
    username,
    salt: salt.toString('base64url'),
    hash: scryptSync(password, salt, 32, { N: 16384, r: 8, p: 1 }).toString('base64url'),
    secret: existing.secret || randomBytes(32).toString('base64url'),
  };
  aws('ssm', 'put-parameter', '--name', outputs.TeacherParameterName, '--type', 'String', '--overwrite', '--value', JSON.stringify(account));
  console.log(`Teacher login saved for "${username}" (takes effect within a minute).`);
} else {
  console.log('Usage: node admin.mjs add <name...> | remove <name...> | list | teacher-setup <username> | portal-publish');
}

function askHidden(prompt) {
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
    rl._writeToOutput = (text) => rl.output.write(text.startsWith(prompt) ? text : '');
    rl.question(prompt, (answer) => {
      rl.close();
      console.log();
      resolve(answer);
    });
  });
}
