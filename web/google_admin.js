// The teacher's "Google" tab: who may sign in with Google, the students who did, and the requests they sent (identity.py and access.py).
// Everything a student typed (names, notes, e-mail addresses) is shown as text, never as HTML.
import { ApiError, ask, date, h, toast } from './common.js';

const FEATURE_LABELS = { backend: 'Backend', ai: 'AI', storage: 'Storage', knowledge: 'Knowledge', traces: 'Traces', domain: 'Custom domain' };
const MODES = [
  ['token-only', 'Off: students use their token only'],
  ['invite', 'Invite: only the listed e-mail addresses'],
  ['domain', 'Domain: the listed addresses and everyone at a listed domain'],
  ['request', 'Request: listed people are in; anyone else with Google may ask'],
];
const list = (text) => String(text || '').split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);

export function featuresText(features) {
  const names = Object.entries(features || {}).map(([name, block]) => {
    const parts = Object.entries(block).map(([k, v]) => (typeof v === 'boolean' ? (v ? 'on' : 'off') : `${k.replace(/_/g, ' ')} ${v}`));
    return `${FEATURE_LABELS[name] || name}: ${parts.join(', ')}`;
  });
  return names.length ? names.join(' · ') : 'no extra features';
}

export function createGoogleTab({ call, render }) {
  let rules = null;
  let users = [];
  let inbox = { requests: [], open: 0 };
  let busy = '';
  let error = '';

  async function load() {
    try {
      const [r, u, i] = await Promise.all([call('/api/admin/identity'), call('/api/admin/identity/users'), call('/api/admin/access-requests')]);
      rules = r;
      users = u.users;
      inbox = i;
      error = '';
    } catch (e) {
      error = e instanceof ApiError ? e.message : 'Could not load.';
    }
  }

  const openRequests = () => inbox.open || 0;

  /** Only the number of waiting requests, for the badge on the tab. Quiet: a failure just leaves the old number. */
  async function count() {
    try {
      inbox = await call('/api/admin/access-requests');
    } catch {
      // the badge is a convenience
    }
  }

  async function act(path, body, message) {
    busy = message;
    render();
    try {
      await call(path, { method: 'POST', body });
      toast(message);
    } catch (e) {
      toast(e.message);
    }
    busy = '';
    await load();
    render();
  }

  async function editRules() {
    const r = rules.rules;
    const answer = await ask({
      title: 'Who may sign in with Google', text: 'Students keep using their token whatever you choose here. Turning this on sets up the portal’s sign-in with Google; the Google client must be stored first (see docs/SIGN_IN.md).',
      fields: [
        { name: 'mode', label: 'Mode', options: MODES, value: r.mode },
        { name: 'portal_url', label: 'Your portal address (https)', value: r.portal_url || location.origin, placeholder: 'https://deploy.your-domain', maxlength: 200 },
        { name: 'domains', label: `E-mail domains, for example your school (up to ${rules.limits.domains})`, value: r.domains.join(', '), maxlength: 500 },
        { name: 'emails', label: `Individual e-mail addresses (up to ${rules.limits.emails})`, value: r.emails.join(', '), maxlength: 20000 },
      ], confirm: 'Save',
    });
    if (!answer) return;
    await act('/api/admin/identity', { mode: answer.mode, portal_url: answer.mode === 'token-only' ? undefined : answer.portal_url.trim(), domains: list(answer.domains), emails: list(answer.emails) }, 'Saved');
  }

  async function decide(request, action) {
    const asked = request.requested;
    if (action === 'reject') {
      const a = await ask({ title: 'Reject this request', text: 'The student sees your note.', fields: [{ name: 'note', label: 'Note (optional)', maxlength: 500 }], confirm: 'Reject', danger: true });
      if (a) await act(`/api/admin/access-requests/${request.id}/decide`, { action: 'reject', note: a.note }, 'Rejected');
      return;
    }
    if (action === 'modify' && request.kind === 'new_site') {
      const a = await ask({
        title: 'Accept with changes', text: `Asked for: ${asked.days} days. ${featuresText(asked.features)}. Change the numbers; to give no extra feature, leave the box empty.`,
        fields: [
          { name: 'days', label: 'Days', type: 'number', min: 1, value: asked.days, required: true },
          { name: 'slug', label: 'Site name (empty = from the student’s name)', value: asked.slug || '', maxlength: 30 },
          { name: 'features', label: 'Features as JSON, for example {"backend":{"enabled":true,"runs_limit":500}}', value: JSON.stringify(asked.features || {}), maxlength: 2000 },
          { name: 'note', label: 'Note for the student (optional)', maxlength: 500 },
        ], confirm: 'Accept with these',
      });
      if (!a) return;
      let features;
      try {
        features = a.features.trim() ? JSON.parse(a.features) : {};
      } catch {
        toast('The features are not valid JSON, so nothing was changed');
        return;
      }
      await act(`/api/admin/access-requests/${request.id}/decide`, { action: 'modify', granted: { days: Number(a.days), slug: a.slug.trim(), features }, note: a.note }, 'Accepted with changes');
      return;
    }
    await act(`/api/admin/access-requests/${request.id}/decide`, { action: 'accept' }, 'Accepted');
  }

  async function userAct(user, what) {
    if (what === 'erase') {
      const a = await ask({ title: `Erase ${user.email}`, text: 'This removes the account, its requests and its paired computers. Its sites are not deleted: they are unlinked (sites made for the account are switched off). It cannot be undone.',
        fields: [{ name: 'confirm', label: `Type the account id to confirm: ${user.sub}`, required: true, maxlength: 128 }], confirm: 'Erase', danger: true });
      if (a) await act(`/api/admin/identity/users/${encodeURIComponent(user.sub)}/erase`, { confirm: a.confirm.trim() }, 'Erased');
      return;
    }
    await act(`/api/admin/identity/users/${encodeURIComponent(user.sub)}/${what}`, {}, what === 'disable' ? 'Turned off' : 'Turned on');
  }

  function requestRow(r) {
    const open = r.state === 'pending';
    const what = r.kind === 'new_site'
      ? `New site, ${r.requested.days} days${r.requested.slug ? `, named ${r.requested.slug}` : ''}. ${featuresText(r.requested.features)}`
      : `Join the project “${r.requested.project}” with the site ${r.requested.slug}`;
    return h('div', { class: 'student', style: 'display:block' },
      h('div', { class: 'title' }, `${r.requester.name || r.requester.email}`, h('span', { class: 'muted small' }, ` ${r.requester.email}`)),
      h('div', { class: 'small' }, what),
      r.note ? h('div', { class: 'small muted' }, `“${r.note}”`) : null,
      r.error ? h('div', { class: 'notice warn small' }, `Last try failed: ${r.error}`) : null,
      h('div', { class: 'small muted' }, `${date(r.created_at)} · ${r.state}${r.teacher_note ? ` · your note: ${r.teacher_note}` : ''}`),
      open ? h('div', { class: 'btns' },
        h('button', { class: 'primary small', onclick: () => decide(r, 'accept') }, 'Accept'),
        r.kind === 'new_site' ? h('button', { class: 'small', onclick: () => decide(r, 'modify') }, 'Accept with changes…') : null,
        h('button', { class: 'small', onclick: () => decide(r, 'reject') }, 'Reject…')) : null);
  }

  function userRow(u) {
    return h('div', { class: 'student', style: 'display:block' },
      h('div', { class: 'title' }, u.name || u.email, h('span', { class: 'muted small' }, ` ${u.email}`)),
      h('div', { class: 'small muted' }, `${u.status === 'disabled' ? 'turned off' : u.approved ? 'approved by you' : 'on your list'} · last seen ${u.last_seen ? date(u.last_seen) : 'never'}`),
      h('div', { class: 'btns' },
        h('button', { class: 'small', onclick: () => userAct(u, u.status === 'disabled' ? 'enable' : 'disable') }, u.status === 'disabled' ? 'Turn on' : 'Turn off'),
        h('button', { class: 'small', onclick: () => userAct(u, 'erase') }, 'Erase…')));
  }

  function view() {
    if (error) return [h('div', { class: 'empty' }, error)];
    if (!rules) return [h('div', { class: 'empty' }, 'Loading…')];
    const r = rules.rules;
    const modeText = MODES.find(([m]) => m === r.mode)[1];
    const waiting = inbox.requests.filter((x) => x.state === 'pending');
    const done = inbox.requests.filter((x) => x.state !== 'pending');
    return [
      h('p', { class: 'privacy small muted' }, 'Students can sign in with their Google account, see their sites, ask for a new site with the settings they expect, and ask to join a class project. You answer each request: accept, change it, or reject. Their e-mail address and name are stored on your platform only. A site made this way has no token to hand out: the student pairs each computer with a code (npm run login -- --google).'),
      h('div', { class: 'btns', style: 'padding:0 16px 12px' }, h('button', { class: 'primary small', onclick: editRules }, 'Who may sign in…'), busy ? h('span', { class: 'small muted' }, busy) : null),
      h('div', { class: 'student', style: 'display:block' }, h('div', { class: 'title' }, 'Right now'),
        h('div', { class: 'small' }, modeText), r.domains.length ? h('div', { class: 'small muted' }, `Domains: ${r.domains.join(', ')}`) : null,
        r.emails.length ? h('div', { class: 'small muted' }, `${r.emails.length} listed address${r.emails.length === 1 ? '' : 'es'}`) : null),
      h('h2', { style: 'padding:12px 16px 0' }, `Requests waiting (${waiting.length})`),
      ...(waiting.length ? waiting.map(requestRow) : [h('div', { class: 'empty' }, 'Nothing is waiting for you.')]),
      h('h2', { style: 'padding:12px 16px 0' }, `Students who signed in (${users.length})`),
      ...(users.length ? users.map(userRow) : [h('div', { class: 'empty' }, 'Nobody has signed in with Google yet.')]),
      done.length ? h('h2', { style: 'padding:12px 16px 0' }, 'Answered requests') : null,
      ...done.slice(0, 30).map(requestRow),
    ].filter(Boolean);
  }

  return { load, view, openRequests, count };
}
