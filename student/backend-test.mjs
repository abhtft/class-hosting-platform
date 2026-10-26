#!/usr/bin/env node
// Run your backend locally before uploading it. Works for both Node (index.mjs / index.js) and Python (app.py).
//
//   node backend-test.mjs <folder> [METHOD] [/path?query] [json-body]
//   node backend-test.mjs backend-template/node GET "/hello?name=Sam"
//   node backend-test.mjs backend-template/python POST /echo '{"a":1}'
//
// It builds the same event the platform sends and prints the response. Set secrets for the run with environment variables,
// for example:  API_KEY=abc node backend-test.mjs ./backend GET /hello
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [folder = '.', method = 'GET', target = '/', bodyText] = process.argv.slice(2);
const dir = resolve(folder);
const [rawPath, ...rest] = target.split('?');
const event = {
  version: '2.0', routeKey: '$default', rawPath, rawQueryString: rest.join('?'),
  headers: { 'content-type': 'application/json', host: 'localhost' },
  requestContext: { http: { method: method.toUpperCase(), path: rawPath, sourceIp: '127.0.0.1' } },
  body: bodyText ?? null, isBase64Encoded: false,
};

let result;
const nodeEntry = ['index.mjs', 'index.js'].find((f) => existsSync(join(dir, f)));
if (nodeEntry) {
  const module = await import(pathToFileURL(join(dir, nodeEntry)).href);
  result = await (module.handler ?? module.default?.handler)(event, {});
} else if (existsSync(join(dir, 'app.py'))) {
  const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
  const code = 'import json,sys; sys.path.insert(0, sys.argv[1]); import app; print("__RESULT__" + json.dumps(app.handler(json.loads(sys.argv[2]), None)))';
  const run = spawnSync(python, ['-c', code, dir, JSON.stringify(event)], { encoding: 'utf8' });
  if (run.status !== 0) { console.error(run.stderr || run.stdout); process.exit(1); }
  const lines = run.stdout.split('\n');
  process.stdout.write(lines.filter((l) => !l.startsWith('__RESULT__')).join('\n'));
  result = JSON.parse(lines.find((l) => l.startsWith('__RESULT__')).slice('__RESULT__'.length));
} else {
  console.error(`No index.mjs, index.js or app.py found in ${dir}`);
  process.exit(1);
}

console.log(typeof result === 'object' && result && 'statusCode' in result ? `${result.statusCode}\n${result.body ?? ''}` : `200 (plain value)\n${JSON.stringify(result)}`);
