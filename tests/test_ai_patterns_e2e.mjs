// The AI patterns TOGETHER, in a freshly built student kit running its own dev server (Python and Node): answer from books with the cache, thumbs feedback, the
// tool-using agent with the scripted offline mock and the human approval stop and resume, local class storage and knowledge folders, and `npm run eval`
// (passing, failing, and a backend that is not running). Free: no AI is called.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const { buildKit } = await import(pathToFileURL(join(here, '..', 'starter', 'build-kit.mjs')).href);
const python = spawnSync('python', ['--version']).status === 0 ? 'python' : 'python3';
const work = mkdtempSync(join(tmpdir(), 'patterns-'));
const say = (m) => console.log(`  ok  ${m}`);
const pause = (ms) => new Promise((done) => setTimeout(done, ms));

const kit = join(work, 'kit');
buildKit(kit);

async function scenario(label, dir, script, port) {
  const books = join(work, `${label}-books`);
  mkdirSync(join(books, 'science'), { recursive: true });
  writeFileSync(join(books, 'science', 'meta.json'), JSON.stringify({ title: 'Science' }));
  writeFileSync(join(books, 'science', 'ohm.txt'), "Ohm's law says the current through a conductor is proportional to the voltage across it. The constant is the resistance, measured in ohms.");
  const env = { ...process.env, AI_MOCK: '', OPENAI_API_KEY: '', CLASS_KNOWLEDGE_URL: '', CLASS_KNOWLEDGE_KEY: '', CLASS_STORAGE_URL: '', CLASS_STORAGE_KEY: '',
    CLASS_KNOWLEDGE_DIR: books, CLASS_STORAGE_DIR: join(work, `${label}-storage`) };
  const command = script.endsWith('.py') ? python : process.execPath;
  const child = spawn(command, [join(dir, 'tools', script), String(port)], { cwd: dir, env, stdio: 'ignore' });
  const base = `http://127.0.0.1:${port}/api`;
  const call = async (path, body, method = body === undefined ? 'GET' : 'POST') => {
    const response = await fetch(base + path, { method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  try {
    for (let i = 0; i < 100 && !(await fetch(`${base}/health`).then((r) => r.ok, () => false)); i++) await pause(100);

    // ---- answer from books, with the cache ----
    const question = "What does Ohm's law say about the current and the voltage?";
    const first = await call('/study', { question });
    assert.equal(first.status, 200);
    assert.equal(first.body.confident, true);
    assert.equal(first.body.cached, false);
    assert.ok(first.body.answer.startsWith('[mock AI]') && first.body.sources.length && /^[0-9a-f]{40}$/.test(first.body.answer_id), JSON.stringify(first.body));
    const second = await call('/study', { question: `  ${question.toUpperCase()} ` });
    assert.equal(second.body.cached, true, 'the same question in other letters is served from the cache');
    assert.equal(second.body.answer, first.body.answer);
    const unsure = await call('/study', { question: 'How do I bake sourdough bread with a starter?' });
    assert.deepEqual([unsure.body.answer, unsure.body.confident, unsure.body.sources], ['The books do not say.', false, []]);
    assert.equal((await call('/study', { question: '' })).status, 400);
    say(`${label}: /study answers from the books with sources, caches the repeat, and says "The books do not say." without calling the AI`);

    // ---- feedback ----
    assert.equal((await call('/feedback', { item: first.body.answer_id, rating: 'up' })).body.saved, true);
    await pause(15);
    assert.equal((await call('/feedback', { item: first.body.answer_id, rating: 'down', comment: 'wrong page' })).body.saved, true);
    assert.equal((await call('/feedback', { item: 'x', rating: 'meh' })).status, 400);
    const summary = (await call('/feedback')).body;
    assert.deepEqual([summary.up, summary.down, summary.recent_down[0].comment], [1, 1, 'wrong page']);
    say(`${label}: /feedback records thumbs and the summary shows the newest thumbs-down`);

    // ---- the agent: a free run, then a run that waits for a human ----
    const found = (await call('/agent', { goal: "Find Ohm's law in the books" })).body;
    assert.equal(found.status, 'done');
    assert.deepEqual(found.trace.map((t) => t.kind), ['tool', 'final']);
    assert.equal(found.trace[0].tool, 'search_books');
    assert.match(found.trace[0].result, /Ohm/);
    assert.ok(!('state' in found) && !found.run_id, 'the page never sees the internal state');
    const waiting = (await call('/agent', { goal: 'Please save a note: revise chapter 1' })).body;
    assert.equal(waiting.status, 'needs_approval');
    assert.equal(waiting.pending.tool, 'save_note');
    assert.match(waiting.run_id, /^[0-9a-f]{24}$/);
    assert.ok(!('state' in waiting));
    assert.deepEqual((await call('/notes')).body.notes, [], 'nothing was saved before the human said yes');
    const approved = (await call('/agent/approve', { run_id: waiting.run_id, approve: true })).body;
    assert.equal(approved.status, 'done');
    assert.equal((await call('/notes')).body.notes.length, 1, 'the note is saved only after approval');
    assert.equal((await call('/agent/approve', { run_id: waiting.run_id, approve: true })).status, 404, 'a run is approved once');
    const second_wait = (await call('/agent', { goal: 'save this too' })).body;
    const declined = (await call('/agent/approve', { run_id: second_wait.run_id, approve: false })).body;
    assert.equal(declined.status, 'done');
    assert.equal((await call('/notes')).body.notes.length, 1, 'a declined run saves nothing');
    assert.equal((await call('/agent/approve', { run_id: 'not-a-run', approve: true })).status, 400);
    assert.equal((await call('/agent', { goal: '' })).status, 400);
    say(`${label}: /agent runs a tool, stops for approval before it saves, saves only after yes, nothing after no, and a run is used once`);

    // ---- npm run eval ----
    const runEval = (...args) => spawnSync(process.execPath, [join(dir, 'tools', 'eval.mjs'), ...args], { cwd: dir, encoding: 'utf8' });
    const passing = runEval('--url', base);
    assert.equal(passing.status, 0, passing.stdout + passing.stderr);
    assert.match(passing.stdout, /3 of 3 cases passed/);
    const cases = join(work, `${label}-cases.json`);
    writeFileSync(cases, JSON.stringify({ endpoint: '/study', field: 'question', cases: [
      { id: 'answers', input: question, contains: ['mock AI'], refuses: false },
      { id: 'wrongly-refuses', input: 'How do I bake sourdough bread?', refuses: false },
      { id: 'should-contain', input: question, contains: ['definitely-not-there'] },
    ] }));
    const failing = runEval(cases, '--url', base);
    assert.equal(failing.status, 1);
    assert.match(failing.stdout, /1 of 3 cases passed/);
    assert.match(failing.stdout, /wrongly-refuses[\s\S]*refused a question it should answer/);
    assert.match(failing.stdout, /should-contain[\s\S]*does not contain 'definitely-not-there'/);
    const offline = runEval('--url', 'http://127.0.0.1:9/api');
    assert.equal(offline.status, 2);
    assert.match(offline.stderr, /Could not reach/);
    assert.equal(runEval('evals/missing.json', '--url', base).status, 2);
    say(`${label}: npm run eval passes the starter cases, fails (exit 1) with the reasons, and explains a backend that is not running (exit 2)`);
  } finally {
    child.kill();
  }
}

try {
  const pyKit = join(work, 'py');
  cpSync(kit, pyKit, { recursive: true });
  await scenario('Python', pyKit, 'dev_server.py', 18951);

  const nodeKit = join(work, 'node');
  cpSync(kit, nodeKit, { recursive: true });
  for (const name of readdirSync(join(nodeKit, 'backend')).filter((n) => n.endsWith('.py'))) rmSync(join(nodeKit, 'backend', name));
  cpSync(join(nodeKit, 'variants', 'node-backend'), join(nodeKit, 'backend'), { recursive: true });
  rmSync(join(nodeKit, 'variants'), { recursive: true });
  await scenario('Node', nodeKit, 'dev_server.mjs', 18952);
  console.log('test_ai_patterns_e2e.mjs: all passed');
} finally {
  rmSync(work, { recursive: true, force: true });
}
