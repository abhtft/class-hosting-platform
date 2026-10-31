// The teacher's Usage tab: the class totals and the per-student rows come from the student list already loaded (web/usage.js, pure functions).
import assert from 'node:assert/strict';
import { capsText, classUsage, percentOf } from '../web/usage.js';

const usd = (n) => `$${n.toFixed(2)}`;
const student = (name, backend, ai) => ({
  id: name, slug: name, student_name: name, status: 'active',
  backend: backend ? { available: true, enabled: backend.enabled ?? true, usage: backend.usage } : { available: true, enabled: false },
  ai: ai ? { available: true, enabled: ai.enabled ?? true, used_usd: ai.spent, limit_usd: ai.cap, period: ai.period } : { available: true, enabled: false },
});

assert.equal(percentOf(0, 100), 0);
assert.equal(percentOf(50, 100), 50);
assert.equal(percentOf(500, 100), 100, 'never above 100');
assert.equal(percentOf(0, 0), 100, 'an allowance of 0 is full: nothing is allowed');

const tokens = [
  student('amy', { usage: { used: 4500, limit: 5000, period: 'month' } }, { spent: 0.2, cap: 1, period: 'month' }), // 90% of runs: needs attention
  student('ben', { usage: { used: 100, limit: 5000, period: 'month' } }, null),
  student('cleo', null, { spent: 1, cap: 1, period: 'day' }),                                                     // AI exhausted
  student('dan', null, null),                                                                                      // nothing on: not listed
  student('eve', { enabled: false, usage: { used: 9, limit: 10, period: 'month' } }, null),                      // backend off: not counted
  { id: 'old', slug: 'old', status: 'active' },                                                                    // a token from before the features existed
];
const { rows, totals } = classUsage(tokens);
assert.deepEqual(rows.map((r) => r.name), ['cleo', 'amy', 'ben'], 'fullest first; students with nothing switched on are left out');
assert.deepEqual(rows.map((r) => r.worst), [100, 90, 2]);
assert.equal(totals.students, 6);
assert.equal(totals.backendOn, 2);
assert.equal(totals.runsUsed, 4600);
assert.equal(totals.runsAllowed, 10000);
assert.equal(totals.aiOn, 2);
assert.equal(Math.round(totals.aiSpent * 100) / 100, 1.2);
assert.deepEqual(totals.aiCaps, { day: 1, month: 1, total: 0 }, 'caps are added up per period');
assert.equal(totals.attention, 2, 'cleo (AI 100%) and amy (runs 90%)');
assert.equal(capsText(totals.aiCaps, usd), '$1.00 per day, $1.00 per month');
assert.equal(capsText({ day: 0, month: 0, total: 0 }, usd), 'no AI caps yet');
assert.deepEqual(classUsage([]), { rows: [], totals: { students: 0, backendOn: 0, runsUsed: 0, runsAllowed: 0, aiOn: 0, aiSpent: 0, aiCaps: { day: 0, month: 0, total: 0 }, attention: 0 } });
console.log('  ok  usage tab: totals, caps per period, the fullest students first, nothing listed for features that are off');
console.log('test_usage.mjs: all passed');
