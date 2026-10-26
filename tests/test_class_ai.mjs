// student/class_ai.mjs against a local OpenAI-compatible server: the same cases as test_class_ai.py.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const { ask, chat, available, AiError, MESSAGES } = await import(pathToFileURL(join(here, '..', 'student', 'class_ai.mjs')).href);

const KEY = 'sk-class-SECRET-0123456789';
const PROMPT = 'my private prompt about alice';
const OK = JSON.stringify({ choices: [{ message: { role: 'assistant', content: 'Hello from the model' } }] });
const script = [];
const seen = [];

const server = createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => (raw += c));
  req.on('end', () => {
    seen.push({ path: req.url, auth: req.headers.authorization, body: JSON.parse(raw) });
    const [status, text, delay] = script.shift() ?? [200, OK, 0];
    setTimeout(() => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(text); }, delay * 1000);
  });
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const BASE = `http://127.0.0.1:${server.address().port}/v1`;

const NAMES = ['OPENAI_API_KEY', 'OPENAI_BASE_URL', 'AI_MODEL', 'AI_MOCK', 'AI_TIMEOUT', 'AWS_LAMBDA_FUNCTION_NAME'];
const env = (values = {}) => { for (const n of NAMES) delete process.env[n]; Object.assign(process.env, values); };
const platform = (extra = {}) => { env({ OPENAI_API_KEY: KEY, OPENAI_BASE_URL: BASE, AI_MODEL: 'the-model', ...extra }); script.length = 0; seen.length = 0; };
const logged = [];
const realLog = console.log;
console.log = (...a) => logged.push(a.join(' '));
const attempt = async (fn) => { try { return [await fn(), null]; } catch (e) { return [null, e]; } };
const say = (m) => realLog(`  ok  ${m}`);
const everything = [];

// 1. success
platform();
assert.equal(await ask(PROMPT, { system: 'be brief', maxTokens: 50 }), 'Hello from the model');
assert.equal(seen[0].path, '/v1/chat/completions');
assert.equal(seen[0].auth, `Bearer ${KEY}`);
assert.deepEqual(seen[0].body, { model: 'the-model', messages: [{ role: 'system', content: 'be brief' }, { role: 'user', content: PROMPT }], max_tokens: 50 });
assert.equal(logged.length, 0, 'a successful call logs nothing');
say('success: path, bearer key, model, system prompt, max_tokens');

// 2. overrides and URL shapes
platform();
process.env.OPENAI_BASE_URL = `${BASE}/`;
await ask('hi', { model: 'other-model', temperature: 0.2 });
assert.equal(seen[0].body.model, 'other-model');
assert.equal(seen[0].body.temperature, 0.2);
assert.equal(seen[0].path, '/v1/chat/completions');
say('model override, temperature, trailing slash');

// 3. status -> kind
for (const [status, kind] of [[401, 'disabled'], [402, 'cap_reached'], [403, 'cap_reached'], [400, 'provider_error'], [404, 'provider_error']]) {
  platform();
  script.push([status, JSON.stringify({ error: { message: `provider says ${KEY}` } }), 0]);
  const [, error] = await attempt(() => ask(PROMPT));
  assert.ok(error instanceof AiError, String(error));
  assert.equal(error.kind, kind);
  assert.equal(error.status, status);
  assert.equal(seen.length, 1, `${status}: no retry`);
  assert.equal(error.message, MESSAGES[kind]);
  everything.push(String(error), error.message, JSON.stringify(error), error.stack ?? '');
}
say('401 disabled, 402/403 cap_reached, 400/404 provider_error (no retry)');

// 4. busy: one retry
platform();
script.push([429, '{}', 0], [200, OK, 0]);
assert.equal(await ask('hi'), 'Hello from the model');
assert.equal(seen.length, 2);
platform();
script.push([500, 'oops', 0], [503, 'oops', 0]);
{
  const [, error] = await attempt(() => ask(PROMPT));
  assert.equal(error.kind, 'busy');
  assert.equal(seen.length, 2);
  everything.push(String(error), error.stack ?? '');
}
say('429/5xx are retried once, then busy');

// 5. timeout
platform({ AI_TIMEOUT: '1' });
script.push([200, OK, 3]);
{
  const started = Date.now();
  const [, error] = await attempt(() => ask(PROMPT));
  assert.equal(error.kind, 'timeout');
  assert.ok(Date.now() - started < 3000, 'gave up at the limit');
  everything.push(String(error), error.stack ?? '');
}
say('a slow answer ends in a timeout error within the limit');

// 6. unreadable replies
for (const body of ['not json', JSON.stringify({ choices: [] }), JSON.stringify({ nothing: 1 }), JSON.stringify({ choices: [{ message: null }] })]) {
  platform();
  script.push([200, body, 0]);
  const [, error] = await attempt(() => ask('hi'));
  assert.equal(error?.kind, 'provider_error', body);
}
platform();
script.push([200, JSON.stringify({ choices: [{ message: { content: null } }] }), 0]);
assert.equal(await ask('hi'), '');
platform();
script.push([200, `\n\n  ${OK}  \n`, 0]);
assert.equal(await ask('hi'), 'Hello from the model');
say('bad JSON or shape is provider_error; null content is empty; whitespace is tolerated');

// 7. unreachable
platform();
process.env.OPENAI_BASE_URL = 'http://127.0.0.1:1/v1';
{
  const [, error] = await attempt(() => ask(PROMPT));
  assert.ok(['provider_error', 'timeout'].includes(error.kind), error.kind);
  everything.push(String(error), error.stack ?? '');
}
say('an unreachable provider is a friendly error');

// 8. default
platform();
script.push([403, '{}', 0]);
logged.length = 0;
assert.equal(await ask(PROMPT, { default: 'AI is resting' }), 'AI is resting');
assert.deepEqual(logged, ['class_ai: cap_reached (status 403)']);
everything.push(...logged);
platform();
script.push([200, OK, 0]);
assert.equal(await ask('hi', { default: 'AI is resting' }), 'Hello from the model');
say('default returns the fallback text on error; the log line has only kind and status');

// 9. not enabled, mock, AI_MOCK
env({ AWS_LAMBDA_FUNCTION_NAME: 'sd-alice' });
{
  const [, error] = await attempt(() => ask(PROMPT));
  assert.equal(error.kind, 'not_enabled');
  assert.equal(available(), false);
}
env();
assert.equal(await ask('hello there'), '[mock AI] You asked: hello there');
assert.equal(available(), true);
env({ AI_MOCK: '0' });
assert.equal((await attempt(() => ask('x')))[1].kind, 'not_enabled');
platform({ AI_MOCK: '1' });
assert.ok((await ask('x')).startsWith('[mock AI]'));
assert.equal(seen.length, 0, 'the mock makes no request');
env({ OPENAI_API_KEY: KEY, AWS_LAMBDA_FUNCTION_NAME: 'x' });
assert.equal((await attempt(() => ask('x')))[1].kind, 'not_enabled');
assert.equal(available(), false);
say('not_enabled on the platform, offline mock on a laptop, AI_MOCK overrides, available()');

// 10. chat
platform();
await chat([{ role: 'user', content: 'a' }, { role: 'assistant', content: 'b' }, { role: 'user', content: 'c' }]);
assert.deepEqual(seen[0].body.messages.map((m) => m.content), ['a', 'b', 'c']);
say('chat() sends the whole conversation');

// 10b. mock is what the offline mock answers (so a structured reply can be tried offline), and is ignored by the real AI
env();
assert.equal(await ask('q', { mock: '{"questions": []}' }), '{"questions": []}');
assert.equal(await chat([{ role: 'user', content: 'q' }], { mock: 'canned' }), 'canned');
assert.equal(await ask('q', { mock: '' }), '', 'an empty mock is still a mock');
platform();
assert.equal(await ask('q', { mock: 'canned' }), 'Hello from the model');
assert.equal(seen.length, 1, 'the real AI answers; the mock text is ignored');
say('mock gives the offline mock a chosen reply and is ignored by the real AI');

// 11. nothing secret in anything printed or raised
const blob = [...everything, ...logged].join('\n');
assert.ok(!blob.includes(KEY) && !blob.includes('SECRET'), 'the key leaked into an error or a log');
assert.ok(!blob.includes(PROMPT), 'the prompt leaked into an error or a log');
server.close();
realLog('test_class_ai.mjs: all passed');
process.exit(0);
