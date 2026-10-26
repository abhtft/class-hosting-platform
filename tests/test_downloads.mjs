// The student downloads (portal-downloads.json) and the card that offers them (web/github.js) must stay in step.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(root, p), 'utf8').replace(/\r\n/g, '\n');
const downloads = JSON.parse(read('portal-downloads.json'));
const card = read('web/github.js');

// every published file has a real source, and the card links only to published files
for (const [name, source] of Object.entries(downloads)) assert.ok(existsSync(join(root, source)), `${name}: source ${source} is missing`);
const list = card.slice(card.indexOf('export const DOWNLOAD_FILES'), card.indexOf('};', card.indexOf('export const DOWNLOAD_FILES')));
const offered = [...list.matchAll(/'([\w.-]+\.(?:mjs|yml|md))'/g)].map((m) => m[1]);
assert.deepEqual([...new Set(offered)].sort(), Object.keys(downloads).sort(), 'the card must offer exactly the published downloads');
console.log('  ok  published list, sources and the card agree');

// the two workflows: same secret, right folder handling, never a token in the file
const frontendFolder = read(downloads['deploy-frontend-folder.yml']);
const rootApp = read(downloads['deploy-root-app.yml']);
for (const [name, text] of [['frontend-folder', frontendFolder], ['root-app', rootApp]]) {
  assert.match(text, /PORTAL_TOKEN: \$\{\{ secrets\.PORTAL_TOKEN \}\}/, `${name}: token comes from the secret`);
  assert.match(text, /node portal-deploy\.mjs/, `${name}: runs the CLI`);
  assert.doesNotMatch(text, /mds_[A-Za-z0-9_-]{8,}/, `${name}: no token value in the file`);
  assert.match(text, /branches: \[main\]/, `${name}: deploys on push to main`);
}
assert.match(frontendFolder, /FRONTEND: frontend/, 'frontend-folder variant installs inside frontend/');
assert.doesNotMatch(rootApp, /working-directory/, 'root-app variant installs at the repo root');
assert.match(rootApp, /node portal-deploy\.mjs --frontend \./, 'root-app variant passes --frontend .');
console.log('  ok  both workflow variants');

// the CLI download is the CLI the tests exercise, and carries nothing secret
assert.equal(downloads['portal-deploy.mjs'], 'student/portal-deploy.mjs');
assert.ok(!/mds_(?!x+\b)[A-Za-z0-9_-]{8,}/.test(read('student/portal-deploy.mjs')), 'no token in the CLI (the placeholder mds_xxxxxxxx is fine)');
console.log('downloads: all passed');
