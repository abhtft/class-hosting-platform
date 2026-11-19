// The student's sign-in with Google and the home it opens (docs/STUDENT_IDENTITY_PLAN.md). Used by student.html next to the token sign-in.
// The page never sees a Google or Cognito token: it sends the student to the pool's sign-in page with a PKCE challenge, comes back with a
// one-time code, and gives the code and the PKCE verifier to the platform, which checks everything and answers with its own session.
// Everything a person typed (names, notes) is shown as text, never as HTML.
import { api, ApiError, date, fill, h, session, statusPill, toast } from './common.js';
import { studentCards } from './cards.js';

const $ = (id) => document.getElementById(id);
const PKCE_KEY = 'google-pkce';
const bytes = (n) => crypto.getRandomValues(new Uint8Array(n));
const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const DAYS = [[7, '7 days'], [14, '14 days'], [30, '30 days'], [60, '60 days'], [90, '90 days']];
const FEATURES = [['backend', 'A backend (server code)'], ['ai', 'The class AI'], ['storage', 'Storage for my app’s data'], ['knowledge', 'Knowledge (search my own books)'], ['traces', 'Traces'], ['domain', 'My own web address']];
const STATE = { pending: 'waiting for your teacher', approving: 'being set up', approved: 'approved', rejected: 'not approved', withdrawn: 'withdrawn' };

function remember(value) {
  try {
    if (value === null) sessionStorage.removeItem(PKCE_KEY);
    else if (value !== undefined) sessionStorage.setItem(PKCE_KEY, JSON.stringify(value));
    else return JSON.parse(sessionStorage.getItem(PKCE_KEY) || 'null');
  } catch {
    return null; // storage unavailable: the sign-in cannot be finished, and the student is asked to start again
  }
}

export async function start(config) {
  const verifier = b64url(bytes(48)); // 64 characters
  const state = b64url(bytes(16));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  remember({ verifier, state });
  const query = new URLSearchParams({
    response_type: 'code', client_id: config.client_id, redirect_uri: config.redirect_uri, scope: config.scopes.join(' '),
    identity_provider: 'Google', code_challenge: challenge, code_challenge_method: 'S256', state,
  });
  location.assign(`${config.host}/oauth2/authorize?${query}`);
}

/** Back from Google: ?code=…&state=… (or ?error=…). Returns true when a sign-in was finished or refused here. */
async function finish(message) {
  const query = new URLSearchParams(location.search);
  if (!query.has('code') && !query.has('error')) return false;
  const saved = remember();
  remember(null);
  history.replaceState(null, '', location.pathname); // the one-time code never stays in the address bar
  if (query.has('error') || !saved || query.get('state') !== saved.state) {
    message(query.has('error') ? 'Google sign-in was cancelled or refused.' : 'The sign-in could not be finished. Start again.');
    return true;
  }
  try {
    const data = await api('/api/student/google/login', { method: 'POST', body: { code: query.get('code'), verifier: saved.verifier } });
    session('user', data.session);
  } catch (e) {
    message(e.message);
  }
  return true;
}

const go = (path, options) => api(path, { role: 'user', ...options });
const card = (...kids) => h('div', { class: 'card stack' }, ...kids);

/** What the student asked for in the form: only the features they ticked, with the numbers they typed. */
export function requestBody(f) {
  const features = {};
  for (const [name] of FEATURES) if (f.get(`f-${name}`)) features[name] = { enabled: true };
  const num = (key) => (String(f.get(key) || '').trim() === '' ? null : Number(f.get(key)));
  if (features.backend && num('runs')) Object.assign(features.backend, { runs_limit: num('runs'), runs_period: 'day' });
  if (features.ai && num('usd')) Object.assign(features.ai, { limit_usd: num('usd'), period: 'month' });
  if (features.storage && num('mb')) Object.assign(features.storage, { data_mb: num('mb') });
  return { kind: 'new_site', days: Number(f.get('days')), slug: String(f.get('slug') || '').trim().toLowerCase(), note: String(f.get('note') || '').trim(), features };
}

function sections(home, reload) {
  const say = (e) => toast(e instanceof ApiError ? e.message : 'Something went wrong. Try again.');
  const run = (fn, ok) => async (...args) => {
    try {
      await fn(...args);
      if (ok) toast(ok);
      await reload();
    } catch (e) {
      say(e);
    }
  };
  const member = home.level === 'member';
  const out = [];

  out.push(card(
    h('div', {}, h('strong', {}, home.user.name || home.user.email), ' ', h('span', { class: 'muted' }, home.user.email)),
    member
      ? h('div', { class: 'small muted' }, 'Your teacher has let this account in.')
      : h('div', { class: 'notice warn' }, 'You are signed in, but your teacher has not let this account in yet. Request a project above; your teacher will answer.')));

  if (member) {
    if (home.projects.length) {
      out.push(card(h('h2', {}, 'Class projects'),
        h('div', { class: 'small muted' }, 'Green: you are in the project. Red: you are not; ask to join.'),
        home.projects.map((p) => h('div', { class: 'btns', style: 'align-items:center' },
          h('span', { style: `display:inline-block;width:10px;height:10px;border-radius:50%;background:${p.state === 'member' ? '#2a9d4b' : p.state === 'pending' ? '#d9a21b' : '#d64545'}`, 'aria-hidden': 'true' }),
          h('strong', {}, p.title), h('span', { class: 'small muted' }, p.state === 'member' ? 'you are in' : p.state === 'pending' ? 'waiting for your teacher' : 'not a member'),
          p.state === 'none' && !p.frozen && home.sites.length ? h('button', { class: 'small', onclick: run(async () => {
            const site = home.sites.length === 1 ? home.sites[0].id : (await pickSite(home.sites));
            if (site) await go('/api/student/google/requests', { method: 'POST', body: { kind: 'join_project', project: p.id, site } });
          }, 'Request sent') }, 'Request access') : null))));
    }
  }

  // the project cards: first "request a new project", then what is still waiting, then the projects the student has
  const full = home.sites.length >= home.limits.sites;
  const form = h('form', { class: 'stack', onsubmit: async (e) => {
    e.preventDefault();
    await run(() => go('/api/student/google/requests', { method: 'POST', body: requestBody(new FormData(e.target)) }), 'Request sent')();
  } },
  h('label', {}, 'How long do you need it? ', h('select', { name: 'days' }, DAYS.map(([v, l]) => h('option', { value: v, selected: v === 30 }, l)))),
  h('label', {}, 'A name for your project (optional) ', h('input', { name: 'slug', maxlength: 30, placeholder: 'lowercase letters, digits and -', autocomplete: 'off' })),
  h('fieldset', {}, h('legend', { class: 'small muted' }, 'What would you like to use? (your teacher decides)'),
    FEATURES.map(([name, label]) => h('label', { class: 'small' }, h('input', { type: 'checkbox', name: `f-${name}` }), ` ${label}`)),
    h('label', { class: 'small' }, 'Backend runs per day ', h('input', { name: 'runs', type: 'number', min: 1, placeholder: 'optional' })),
    h('label', { class: 'small' }, 'AI budget in dollars per month ', h('input', { name: 'usd', type: 'number', min: 0.01, step: 0.01, placeholder: 'optional' })),
    h('label', { class: 'small' }, 'Storage in MB ', h('input', { name: 'mb', type: 'number', min: 1, placeholder: 'optional' }))),
  h('label', {}, 'Tell your teacher what it is for (optional) ', h('input', { name: 'note', maxlength: 500, autocomplete: 'off' })),
  h('button', { class: 'primary', type: 'submit', disabled: full }, 'Send request'));
  const { waiting, projects } = studentCards(member ? home.sites : [], home.requests);
  const grid = h('div', { class: 'cards', id: 'project-cards' },
    h('div', { class: 'card pcard new' }, h('details', { open: !projects.length && !waiting.length },
      h('summary', {}, '＋ Request a new project'),
      h('div', { class: 'stack', style: 'margin-top:12px' }, full ? h('div', { class: 'muted' }, `You already have the most projects one account can have (${home.limits.sites}).`) : form))),
    waiting.map((r) => h('div', { class: 'card pcard stack waiting' },
      h('div', {}, h('strong', {}, r.kind === 'new_site' ? (r.requested.slug || 'New project') : `Join ${r.requested.project}`), ' ', h('span', { class: 'pill warn' }, STATE[r.state] || r.state)),
      h('div', { class: 'small muted' }, r.kind === 'new_site' ? `${r.requested.days} days · asked ${date(r.created_at)}` : `asked ${date(r.created_at)}`),
      r.state === 'pending' && r.error ? h('div', { class: 'small muted' }, 'Your teacher is still setting this up.') : null,
      r.state === 'pending' ? h('div', { class: 'btns' }, h('button', { class: 'small', onclick: run(() => go(`/api/student/google/requests/${r.id}/withdraw`, { method: 'POST', body: {} }), 'Withdrawn') }, 'Withdraw')) : null)),
    projects.map((s) => h('div', { class: 'card pcard stack' },
      h('div', {}, h('strong', {}, s.slug), ' ', statusPill(s)),
      h('div', { class: 'small' }, h('a', { href: s.url, target: '_blank', rel: 'noopener' }, 'open the project')),
      s.people > 1 ? h('div', { class: 'small muted' }, `${s.people} people work on this project`) : null,
      s.kind === 'personal' ? h('div', { class: 'small muted' }, 'works on a computer you pair below') : null)));
  out.splice(1, 0, grid);

  const earlier = home.requests.filter((r) => r.state !== 'pending' && r.state !== 'approving');
  if (earlier.length) {
    out.push(card(h('h2', {}, 'Earlier requests'), earlier.map((r) => h('div', { class: 'stack', style: 'border-top:1px solid var(--line,#ddd);padding-top:8px' },
      h('div', {}, h('strong', {}, r.kind === 'new_site' ? `New project (${r.requested.days} days)` : `Join ${r.requested.project}`), ' ', h('span', { class: 'small muted' }, `${STATE[r.state] || r.state} · ${date(r.created_at)}`)),
      r.state === 'approved' && r.granted && r.kind === 'new_site' && r.granted.days !== r.requested.days ? h('div', { class: 'small' }, `Your teacher gave you ${r.granted.days} days.`) : null,
      r.teacher_note ? h('div', { class: 'small' }, `Your teacher says: ${r.teacher_note}`) : null))));
  }

  if (member && home.sites.length) {
    const pairForm = h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      await run(async () => {
        const done = await go('/api/student/google/pair', { method: 'POST', body: { code: String(f.get('code')).trim(), site: f.get('site') || undefined } });
        toast(`Paired “${done.device}”. Go back to your terminal.`);
        e.target.reset();
      })();
    } },
    h('div', { class: 'small muted' }, 'In the terminal of your project run  npm run login -- --google  and type the code it shows. Only do this for a computer you started it on yourself, just now.'),
    h('label', {}, 'Code from the terminal ', h('input', { name: 'code', required: true, maxlength: 12, placeholder: 'ABCD-EFGH', autocomplete: 'off', spellcheck: 'false' })),
    home.sites.length > 1 ? h('label', {}, 'For which site? ', h('select', { name: 'site' }, home.sites.map((s) => h('option', { value: s.id }, s.slug)))) : null,
    h('button', { type: 'submit' }, 'Pair this computer'));
    out.push(card(h('h2', {}, 'Pair a computer'), pairForm));
    out.push(card(h('h2', {}, 'Your computers'), h('div', { id: 'computers', class: 'stack' }, h('div', { class: 'muted' }, 'Loading…'))));
    queueMicrotask(async () => {
      try {
        const { devices } = await go('/api/student/google/devices');
        fill($('computers'), devices.length ? devices.map((d) => h('div', { class: 'btns', style: 'align-items:center' },
          h('strong', {}, d.device), h('span', { class: 'small muted' }, `${d.site} · ${d.expired ? 'expired' : `works until ${date(d.expires_at)}`}${d.last_used ? ` · used ${date(d.last_used)}` : ''}`),
          h('button', { class: 'small', onclick: run(() => go(`/api/student/google/devices/${d.id}/revoke`, { method: 'POST', body: {} }), 'Signed out') }, 'Sign out')))
          .concat(h('button', { class: 'small', onclick: run(() => go('/api/student/google/sign-out-everywhere', { method: 'POST', body: {} }), 'Every computer signed out') }, 'Sign out everywhere'))
          : h('div', { class: 'muted' }, 'No computer is paired yet.'));
      } catch {
        // the list is a convenience; the rest of the page works without it
      }
    });
  }

  if (member) {
    const linkForm = h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      const token = new FormData(e.target).get('token');
      await run(async () => { await go('/api/student/google/link', { method: 'POST', body: { token } }); e.target.reset(); }, 'Linked')();
    } },
    h('div', { class: 'small muted' }, 'Already have a token from your teacher? Link it to this account. After that it belongs to you and nobody else can link it.'),
    h('label', {}, 'Token ', h('input', { name: 'token', placeholder: 'mds_…', autocomplete: 'off', spellcheck: 'false', required: true })),
    h('button', { type: 'submit' }, 'Link token'));
    out.push(card(h('h2', {}, 'Link a token you already have'), linkForm));
  }
  return out;
}

function pickSite(sites) {
  return new Promise((resolve) => {
    const dialog = h('dialog', {}, h('form', { method: 'dialog' }, h('h2', {}, 'Which project should join?'),
      h('select', { name: 'site' }, sites.map((s) => h('option', { value: s.id }, s.slug))),
      h('div', { class: 'btns' }, h('button', { type: 'button', onclick: () => dialog.close() }, 'Cancel'), h('button', { class: 'primary', type: 'submit' }, 'Ask'))));
    let chosen = null;
    dialog.querySelector('form').addEventListener('submit', () => { chosen = dialog.querySelector('select').value; });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(chosen); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

/** Called once by student.js. Adds the Google button when the teacher has turned Google sign-in on, and shows the home after a sign-in. */
export async function initGoogle(tokenSignedIn) {
  const box = $('google-signin');
  const account = $('google-account');
  if (!box || !account) return;
  const note = (text) => { $('google-error').textContent = text || ''; };
  const signedInNow = await finish(note);
  if (session('user') && !tokenSignedIn) {
    const show = async () => {
      const home = await go('/api/student/google/home');
      $('login').style.display = 'none';
      account.classList.remove('hidden');
      const signOut = () => { session('user', null); location.reload(); };
      fill(account, h('div', { class: 'topbar' }, h('h1', {}, 'Your projects'), h('div', { class: 'actions' }, h('button', { onclick: signOut }, 'Sign out'))), h('div', { class: 'stack' }, sections(home, show)));
    };
    try {
      await show();
      return;
    } catch (e) {
      session('user', null);
      if (e instanceof ApiError) note(e.message);
    }
  }
  if (signedInNow && !session('user')) box.classList.remove('hidden'); // keep the refusal visible
  try {
    const config = await api('/api/student/google/config');
    if (config.enabled) {
      box.classList.remove('hidden');
      $('google-button').onclick = () => start(config).catch(() => note('Could not start the sign-in. Try again.'));
    }
  } catch {
    // Google sign-in is optional: a failure here leaves the token sign-in exactly as it was
  }
}
