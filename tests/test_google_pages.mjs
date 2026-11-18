// The portal pages for Google sign-in (web/google.js on the student page, web/google_admin.js on the teacher page): the pieces that can be
// tested without a browser. What a student typed is shown as text only (no innerHTML anywhere), every element the scripts look for exists,
// and the request the form builds holds only what was asked for.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(join(here, '..', p), 'utf8');
const say = (m) => console.log(`  ok  ${m}`);

for (const file of ['web/google.js', 'web/google_admin.js']) {
  const text = read(file);
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/.test(text), `${file} must never build HTML from text`);
}
say('the Google pages build every element as text: no innerHTML, no eval');

const student = read('web/student.html');
for (const id of ['google-signin', 'google-button', 'google-error', 'google-account', 'login']) assert.ok(student.includes(`id="${id}"`), `student.html needs #${id}`);
const teacher = read('web/teacher.html');
for (const id of ['tab-google', 'google-badge']) assert.ok(teacher.includes(`id="${id}"`), `teacher.html needs #${id}`);
assert.ok(read('web/teacher.js').includes("$('tab-google').onclick"), 'the Google tab is wired');
assert.ok(read('web/student.js').includes('initGoogle('), 'the student page starts the Google sign-in');
say('every element the scripts look for exists, and both pages start the new code');

const { requestBody } = await import('../web/google.js');
const { featuresText } = await import('../web/google_admin.js');
const form = (values) => { const f = new FormData(); for (const [k, v] of Object.entries(values)) f.set(k, v); return f; };
assert.deepEqual(requestBody(form({ days: '30', slug: ' Robot-Lab ', note: ' for the fair ' })),
  { kind: 'new_site', days: 30, slug: 'robot-lab', note: 'for the fair', features: {} });
assert.deepEqual(requestBody(form({ days: '14', slug: '', note: '', 'f-backend': 'on', 'f-ai': 'on', runs: '500', usd: '1.5' })).features,
  { backend: { enabled: true, runs_limit: 500, runs_period: 'day' }, ai: { enabled: true, limit_usd: 1.5, period: 'month' } });
// a number typed for a feature that was not ticked is ignored; an empty number is ignored
assert.deepEqual(requestBody(form({ days: '7', mb: '50', usd: '' , 'f-storage': 'on' })).features, { storage: { enabled: true, data_mb: 50 } });
assert.deepEqual(requestBody(form({ days: '7', mb: '50' })).features, {});
say('the request form builds exactly what was asked: only ticked features, numbers only for those');

assert.equal(featuresText({}), 'no extra features');
assert.equal(featuresText({ backend: { enabled: true, runs_limit: 500 }, ai: { enabled: false } }), 'Backend: on, runs limit 500 · AI: off');
say('the teacher sees a request in plain words');
console.log('test_google_pages.mjs: passed');
