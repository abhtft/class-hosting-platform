// The terminal prompts (installer/prompts.mjs) on a fake terminal. The bug that reached a real Ubuntu terminal: a plain question after a hidden answer
// never received its answer (input stayed paused) and the program exited with "unsettled top-level await". These tests run exactly that sequence,
// check that a password is never shown (not even its first letters) or echoed twice, that a key shows only its prefix, and that a new password
// must be typed twice.
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import { createPrompter, editLine, mask } from '../installer/prompts.mjs';

function terminal() {
  const input = new PassThrough();
  input.isTTY = true;
  input.setRawMode = (on) => { input.raw = on; };
  const output = new PassThrough();
  let shown = '';
  output.on('data', (d) => { shown += d; });
  return { input, output, shown: () => shown, type: (text) => input.write(text) };
}
const until = async (check) => { for (let i = 0; i < 200 && !check(); i += 1) await new Promise((r) => setTimeout(r, 10)); assert.ok(check(), 'the prompt did not appear'); };
/** Type each character separately, like a person at a keyboard. */
const keys = (t, text) => { for (const ch of text) t.type(ch); t.type('\r'); };

// ---------- editing and masking ----------
assert.deepEqual(editLine('ab', 'c'), { buffer: 'abc', done: false, abort: false });
assert.deepEqual(editLine('abc', '\u007f'), { buffer: 'ab', done: false, abort: false });
assert.equal(editLine('abc', '\r').done, true);
assert.equal(editLine('abc', '\u0003').abort, true);
assert.equal(editLine('abc', '\u001b[A').buffer, 'abc', 'arrow keys are ignored');
assert.equal(mask('secret-password'), '***************', 'a password shows no letters');
assert.equal(mask('sk-or-abcdef', 4), 'sk-o********', 'a key shows the first four letters');
console.log('  ok  editing keys; a password is fully masked, a key shows only its first four letters');

// ---------- the sequence that failed: question, hidden answer, question ----------
{
  const t = terminal();
  const p = createPrompter({ input: t.input, output: t.output });
  const first = p.ask('Teacher login name?', 'teacher');
  await until(() => t.shown().includes('Teacher login name?'));
  t.type('anivesh\n');
  assert.equal(await first, 'anivesh');

  const secret = p.hidden('Password: ');
  await until(() => t.shown().includes('Password:'));
  keys(t, 'Star-Wars-2026');
  assert.equal(await secret, 'Star-Wars-2026');
  assert.equal(t.input.raw, false, 'the terminal is back to normal after a hidden answer');
  assert.ok(!/Star/.test(t.shown()), 'not even the first letters of a password were shown');
  assert.ok(!t.shown().includes('Wars'), 'the password never appears');

  const after = p.yes('Continue?', 'n');
  await until(() => t.shown().includes('Continue?'));
  t.type('y\n');
  assert.equal(await after, true, 'the question after a hidden answer gets its answer');

  const another = p.ask('Domain?');
  await until(() => t.shown().includes('Domain?'));
  t.type('\n');
  assert.equal(await another, '', 'and so does the one after that');
  console.log('  ok  question, hidden answer, question, question: every question is answered; the password is never shown');
}

// ---------- hidden answers back to back, a key with its prefix, Ctrl+C ----------
{
  const t = terminal();
  const p = createPrompter({ input: t.input, output: t.output });
  const one = p.hidden('Key 1: ', { prefix: 4 });
  await until(() => t.shown().includes('Key 1:'));
  keys(t, 'sk-or-v1-abcdef');
  assert.equal(await one, 'sk-or-v1-abcdef');
  assert.ok(t.shown().includes('sk-o***'), 'the prefix of a key is shown');
  assert.ok(!t.shown().includes('v1-abcdef'), 'the rest of the key is not');
  const two = p.hidden('Key 2: ', { prefix: 4 });
  await until(() => t.shown().includes('Key 2:'));
  keys(t, 'AQ.Axxxxxxxxxx');
  assert.equal(await two, 'AQ.Axxxxxxxxxx');
  const three = p.hidden('Key 3: ');
  await until(() => t.shown().includes('Key 3:'));
  t.type('abc\u0003');
  await assert.rejects(three, /Cancelled/);
  assert.equal(t.input.raw, false, 'Ctrl+C also puts the terminal back');
  console.log('  ok  back-to-back hidden answers, key prefix only, Ctrl+C cancels and restores the terminal');
}

// ---------- a new password: twice, long enough, retries ----------
{
  const t = terminal();
  const p = createPrompter({ input: t.input, output: t.output });
  const result = p.newPassword('New password', { tries: 3 });
  await until(() => t.shown().includes('New password'));
  keys(t, 'short');                                         // too short
  await until(() => t.shown().includes('Too short'));
  keys(t, 'long-enough-1');                                 // first of a pair
  await until(() => t.shown().includes('Type it again'));
  keys(t, 'long-enough-2');                                 // does not match
  await until(() => t.shown().includes('did not match'));
  keys(t, 'long-enough-3');
  await until(() => (t.shown().match(/Type it again/g) || []).length >= 2);
  keys(t, 'long-enough-3');                                 // matches
  assert.equal(await result, 'long-enough-3');
  assert.ok(!t.shown().includes('long-enough'), 'no password letters were shown');
  const gives = createPrompter({ input: terminal().input, output: new PassThrough() });
  assert.equal(typeof gives.newPassword, 'function');
  console.log('  ok  a new password must be long enough and typed twice; a mismatch asks again');
}

// ---------- piped input (scripts) ----------
{
  const input = new PassThrough();
  const output = new PassThrough();
  const p = createPrompter({ input, output });
  const answer = p.hidden('Token: ');
  input.write('piped-value\n');
  assert.equal(await answer, 'piped-value');
  console.log('  ok  piped input is read as one line');
}
console.log('test_prompts.mjs: all passed');
