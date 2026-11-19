// Ordering and filtering of the project cards on the teacher's and the student's home (docs/STUDENT_IDENTITY_PLAN.md, phase 1 of the project home).
// Pure functions, so they can be tested without a browser. Nothing here touches the page.

export const FILTERS = [['all', 'All'], ['active', 'Active'], ['soon', 'Expiring soon'], ['waiting', 'Waiting for you'], ['ended', 'Ended']];
export const SORTS = [['recent', 'Recent activity'], ['name', 'Name'], ['expiry', 'Expiring first']];

/** When a project was last worked on: its last deploy, else when it was made. */
export const activity = (t) => t.last_deploy_at || t.created_at || 0;

const label = (t) => String(t.student_name || t.slug || '').toLowerCase();

/** The cards that match the search text and the filter. `now` is in seconds, like the platform's times. */
export function filterProjects(list, { q = '', filter = 'all', now = 0 } = {}) {
  const text = q.trim().toLowerCase();
  return list.filter((t) => {
    if (text && !`${t.student_name || ''} ${t.slug || ''}`.toLowerCase().includes(text)) return false;
    if (filter === 'active') return t.status === 'active';
    if (filter === 'soon') return t.status === 'active' && now >= (t.warn_at || Infinity);
    if (filter === 'waiting') return Boolean(t.regen && t.regen.state === 'pending');
    if (filter === 'ended') return t.status !== 'active';
    return true;
  });
}

/** A new array in the order the teacher asked for; a tie falls back to the name, so the order never jumps around. */
export function sortProjects(list, sort = 'recent') {
  const byName = (a, b) => label(a).localeCompare(label(b));
  const copy = [...list];
  if (sort === 'name') return copy.sort(byName);
  if (sort === 'expiry') return copy.sort((a, b) => (a.expires_at || Infinity) - (b.expires_at || Infinity) || byName(a, b));
  return copy.sort((a, b) => activity(b) - activity(a) || byName(a, b));
}

/** The student's cards: requests still waiting first (newest first), then the projects, most recently worked on first. */
export function studentCards(sites, requests) {
  const waiting = requests.filter((r) => r.state === 'pending' || r.state === 'approving').sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const projects = [...sites].sort((a, b) => activity(b) - activity(a) || label(a).localeCompare(label(b)));
  return { waiting, projects };
}
