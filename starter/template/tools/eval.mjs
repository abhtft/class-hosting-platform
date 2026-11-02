// npm run eval: test your AI app with a list of cases, before you deploy. It calls your backend (so it works for Python and Node backends alike) and checks each answer.
//
//   npm run dev -- --backend-only                      (in one terminal: starts your backend on http://127.0.0.1:8788)
//   npm run eval                                       (in another: runs evals/study.json against it)
//   npm run eval -- evals/my-cases.json                (another cases file)
//   npm run eval -- --url https://<your-site>/api      (against your deployed site instead; it uses AI allowance and books, so keep the list short)
//
// A cases file is JSON: { "endpoint": "/study", "field": "question", "cases": [ { "id", "input", "contains": [], "not_contains": [], "refuses": false, "max_seconds": 8 } ] }.
// "endpoint" is the route that answers, "field" the name of the request field that carries the input. The route must answer { "answer": "..." } (or any JSON: it is
// checked as text). A case with "refuses": true passes only when the app says it does not know: write some, they matter most. The checks are the kit's
// class_quality.evaluate (see PATTERNS.md); the exit code is 1 when any case fails, so it can also run in CI.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const flag = (name) => { const i = args.indexOf(name); return i === -1 ? null : args.splice(i, 2)[1]; };
const base = (flag('--url') || process.env.EVAL_URL || 'http://127.0.0.1:8788/api').replace(/\/$/, '');
const file = resolve(root, args[0] || 'evals/study.json');

const helper = ['backend/class_quality.mjs', 'variants/node-backend/class_quality.mjs'].map((p) => join(root, p)).find(existsSync);
if (!helper) { console.error('class_quality.mjs is missing: run `npm run platform -- update --apply` to restore the kit files.'); process.exit(2); }
if (!existsSync(file)) { console.error(`No cases file at ${file}. Create one (see the top of tools/eval.mjs for the shape).`); process.exit(2); }
const spec = JSON.parse(readFileSync(file, 'utf8'));
if (!Array.isArray(spec.cases) || !spec.cases.length || typeof spec.endpoint !== 'string' || typeof spec.field !== 'string') {
  console.error('The cases file needs "endpoint", "field" and a non-empty "cases" list.');
  process.exit(2);
}

const { evaluate } = await import(pathToFileURL(helper).href);
let reachable = true;
const report = await evaluate(spec.cases, async (input) => {
  let response;
  try {
    response = await fetch(base + spec.endpoint, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ [spec.field]: input }), signal: AbortSignal.timeout(30000) });
  } catch {
    reachable = false;
    throw new Error('the backend did not answer');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
  return data;
});

// no process.exit() after fetch: on Windows it can trip a libuv assertion while a request handle is still closing. Setting the exit code lets Node end by itself.
if (!reachable) {
  console.error(`Could not reach ${base}${spec.endpoint}. Start your backend first: npm run dev -- --backend-only (or pass --url).`);
  process.exitCode = 2;
} else {
  console.log(`${report.passed} of ${report.total} cases passed (score ${report.score}) in ${report.seconds}s against ${base}${spec.endpoint}`);
  for (const failure of report.failed) {
    console.log(`
✖ ${failure.id}: ${JSON.stringify(failure.input)}`);
    for (const reason of failure.reasons) console.log(`    ${reason}`);
    if (failure.answer) console.log(`    answer: ${failure.answer.replace(/\s+/g, ' ').slice(0, 160)}`);
  }
  process.exitCode = report.failed.length ? 1 : 0;
}
