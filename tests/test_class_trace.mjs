// Kit helpers and traces (Node): same checks as test_class_trace.py. A fake local server plays the platform.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'student');
const seen = [];
const server = createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => { raw += c; });
  req.on('end', () => {
    seen.push([req.url, req.headers['x-trace-id'], JSON.parse(raw || '{}')]);
    const out = req.url === '/v1/search'
      ? { hits: [{ text: "Ohm's law: V = IR.", title: 'Physics', page: 1, page_end: 1, section: 'Ohm', collection: 'mine', score: 0.9 }], confident: true }
      : req.url === '/ai/chat/completions' ? { choices: [{ message: { content: 'V equals I times R [1].' } }] } : { ok: true };
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify(out));
  });
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;
Object.assign(process.env, { CLASS_KNOWLEDGE_URL: base, CLASS_KNOWLEDGE_KEY: 'ks_x.secretsecretsecretsecret', OPENAI_API_KEY: 'k', OPENAI_BASE_URL: `${base}/ai`, AI_MODEL: 'm', AI_MOCK: '0' });
const kb = await import(pathToFileURL(join(root, 'class_knowledge.mjs')).href);
const ai = await import(pathToFileURL(join(root, 'class_ai.mjs')).href);
const agent = await import(pathToFileURL(join(root, 'class_agent.mjs')).href);

const out = await kb.answer("What is Ohm's law?");
const tid = out.trace_id;
assert.match(tid, /^tr_[0-9a-f]{16}$/);
assert.ok(seen.some(([p, t]) => p === '/v1/search' && t === tid) && seen.some(([p, t]) => p === '/ai/chat/completions' && t === tid), 'the id rides on the search and the AI call');
let posts = seen.filter(([p]) => p === '/v1/trace').map((s) => s[2]);
assert.equal(posts.length, 1);
assert.deepEqual(posts[0].spans.map((s) => s.name), ['answer']);
assert.deepEqual([posts[0].spans[0].attrs.confident, posts[0].spans[0].attrs.refused, posts[0].spans[0].attrs.sources, posts[0].spans[0].attrs.cited], [true, false, 1, 1]);
assert.equal(ai.traceId(), null, 'the trace ends with the answer');
console.log('  ok  answer(): one trace id on the search, the AI call and the answer step, sent once, then ended');

const saved = [];
const tools = [agent.tool('look', 'Look something up', { q: 'text' }, () => 'found it'), agent.tool('save', 'Save a note', { text: 'text' }, (a) => { saved.push(a); return 'saved'; }, { needsApproval: true })];
const replies = [{ tool: 'look', args: { q: 'x' } }, { tool: 'save', args: { text: 'hello' } }, { final: 'all done' }];
const model = async () => JSON.stringify(replies.shift());
seen.length = 0;
const first = await agent.run('find and save', tools, { model });
assert.equal(first.status, 'needs_approval');
posts = seen.filter(([p]) => p === '/v1/trace').map((s) => s[2]);
assert.equal(posts.length, 1);
assert.equal(posts[0].trace_id, first.trace_id);
assert.deepEqual(posts[0].spans.map((s) => s.name), ['tool:look', 'tool:save', 'agent']);
seen.length = 0;
const done = await agent.resume(first.state, true, tools, { model });
assert.equal(done.status, 'done');
assert.equal(done.trace_id, first.trace_id);
assert.deepEqual(saved, [{ text: 'hello' }]);
posts = seen.filter(([p]) => p === '/v1/trace').map((s) => s[2]);
assert.equal(posts.length, 1);
assert.deepEqual(posts[0].spans.map((s) => s.name), ['tool:save', 'agent:final', 'agent']);
console.log('  ok  agent: a step per tool call, one agent summary, the same trace across approval and resume, nothing sent twice');

server.close();
process.env.CLASS_KNOWLEDGE_URL = 'http://127.0.0.1:9';
ai.traceBegin();
ai.traceSpan('x');
assert.match(await ai.traceFlush(), /^tr_/);
console.log('  ok  an unreachable trace service changes nothing');
process.exitCode = 0;
