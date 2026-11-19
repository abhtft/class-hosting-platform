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
// the project cards (web/cards.js): filter, order, and the student's order (waiting first)
const { filterProjects, sortProjects, studentCards } = await import('../web/cards.js');
const site = (slug, extra = {}) => ({ slug, student_name: '', status: 'active', created_at: 100, expires_at: 9000, warn_at: 8000, regen: { state: 'none' }, ...extra });
const list = [site('b-lab', { last_deploy_at: 500 }), site('a-lab', { last_deploy_at: 900 }), site('c-lab', { status: 'expired', expires_at: 50 }), site('d-lab', { regen: { state: 'pending' }, expires_at: 7000, warn_at: 6000 })];
assert.deepEqual(sortProjects(list, 'recent').map((t) => t.slug), ['a-lab', 'b-lab', 'c-lab', 'd-lab'], 'recent first, a tie falls back to the name');
assert.deepEqual(sortProjects(list, 'name').map((t) => t.slug), ['a-lab', 'b-lab', 'c-lab', 'd-lab']);
assert.deepEqual(sortProjects(list, 'expiry').map((t) => t.slug), ['c-lab', 'd-lab', 'a-lab', 'b-lab']);
assert.deepEqual(filterProjects(list, { filter: 'active' }).map((t) => t.slug), ['b-lab', 'a-lab', 'd-lab']);
assert.deepEqual(filterProjects(list, { filter: 'ended' }).map((t) => t.slug), ['c-lab']);
assert.deepEqual(filterProjects(list, { filter: 'waiting' }).map((t) => t.slug), ['d-lab']);
assert.deepEqual(filterProjects(list, { filter: 'soon', now: 6500 }).map((t) => t.slug), ['d-lab']);
assert.deepEqual(filterProjects(list, { q: ' A-LAB ' }).map((t) => t.slug), ['a-lab']);
assert.equal(list[0].slug, 'b-lab', 'sorting never changes the list it was given');
const cards = studentCards([{ slug: 'old', created_at: 1 }, { slug: 'new', created_at: 5 }],
  [{ id: 'r1', state: 'rejected', created_at: 9 }, { id: 'r2', state: 'pending', created_at: 3 }, { id: 'r3', state: 'approving', created_at: 7 }]);
assert.deepEqual(cards.waiting.map((r) => r.id), ['r3', 'r2'], 'waiting requests first, newest first; decided ones are not cards');
assert.deepEqual(cards.projects.map((s) => s.slug), ['new', 'old']);
say('the project cards: filter, order, and waiting requests on top');

for (const id of ['filter', 'sort', 'search', 'new-token']) assert.ok(teacher.includes(`id="${id}"`), `teacher.html needs #${id}`);
assert.ok(read('web/teacher.js').includes("class: 'pcard new'"), 'the first teacher card makes a new project');
assert.ok(read('web/google.js').includes("'＋ Request a new project'"), 'the first student card asks for a new project');
assert.ok(read('web/index.html').includes('Log in as a student') && read('web/index.html').includes('Log in as a teacher'), 'the first page offers both roles');
say('both homes start with the new-project card, and the first page offers the two roles');
// the People section (web/people.js): what the teacher reads after adding addresses, and text only
const { addedText } = await import('../web/people.js');
assert.equal(addedText({ added: ['a'], invited: ['b', 'c'], already: [], refused: [] }), '1 added · 2 invited (they join when they first sign in)');
assert.equal(addedText({ added: [], invited: [], already: ['a'], refused: [{ email: 'x', reason: 'The project is full (5 people).' }] }), '1 already there · 1 not added: The project is full (5 people).');
assert.equal(addedText({ added: [], invited: [], already: [], refused: [] }), 'Nothing to add');
assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|eval\(/.test(read('web/people.js')), 'people.js builds every element as text');
assert.ok(read('web/teacher.js').includes('peopleSection('), 'the project panel shows the people');
say('the People section: the teacher sees what happened to each address, and the names are shown as text');
console.log('test_google_pages.mjs: passed');
