// The teacher's "Usage" tab: what the class is using and what it could cost, from the student list the page already has (no extra request, no student content).
// Pure functions, so they are tested without a browser.

const PERIODS = ['day', 'month', 'total'];

/** How full a meter is, 0 to 100 (a limit of 0 means nothing is allowed, so it is full). */
export const percentOf = (used, limit) => (limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 100);

/**
 * One row per student who has the backend or the class AI switched on, the fullest first, and the class totals.
 * `tokens` is the list from /api/admin/tokens (each has .backend and .ai with .available/.enabled/.usage).
 */
export function classUsage(tokens) {
  const rows = [];
  const totals = { students: tokens.length, backendOn: 0, runsUsed: 0, runsAllowed: 0, aiOn: 0, aiSpent: 0, aiCaps: Object.fromEntries(PERIODS.map((p) => [p, 0])), attention: 0 };
  for (const t of tokens) {
    const b = t.backend?.available && t.backend.enabled ? t.backend : null;
    const a = t.ai?.available && t.ai.enabled ? t.ai : null;
    if (!b && !a) continue;
    const runs = b?.usage ? { used: b.usage.used, limit: b.usage.limit, period: b.usage.period, percent: percentOf(b.usage.used, b.usage.limit) } : null;
    const ai = a ? { spent: a.used_usd || 0, cap: a.limit_usd || 0, period: a.period, percent: percentOf(a.used_usd || 0, a.limit_usd || 0) } : null;
    if (b) { totals.backendOn += 1; if (runs) { totals.runsUsed += runs.used; totals.runsAllowed += runs.limit; } }
    if (ai) { totals.aiOn += 1; totals.aiSpent += ai.spent; totals.aiCaps[PERIODS.includes(ai.period) ? ai.period : 'month'] += ai.cap; }
    const worst = Math.max(runs?.percent ?? 0, ai?.percent ?? 0);
    if (worst >= 80) totals.attention += 1;
    rows.push({ id: t.id, slug: t.slug, name: t.student_name || t.slug, status: t.status, runs, ai, worst });
  }
  rows.sort((x, y) => y.worst - x.worst || x.name.localeCompare(y.name));
  return { rows, totals };
}

/** "$4.00 per month, $1.00 per day" for the caps that add up to the most the AI could cost. */
export function capsText(caps, usd) {
  const label = { day: 'per day', month: 'per month', total: 'in total' };
  const parts = PERIODS.filter((p) => caps[p] > 0).map((p) => `${usd(caps[p])} ${label[p]}`);
  return parts.length ? parts.join(', ') : 'no AI caps yet';
}
