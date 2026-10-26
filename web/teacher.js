import { api, ApiError, ask, copyText, date, duration, expiryBar, h, lifeText, now, session, statusPill, toast, usd } from './common.js';

const $ = (id) => document.getElementById(id);
const DURATIONS = [[7, '7 days'], [14, '14 days'], [30, '30 days'], [60, '60 days'], [90, '90 days'], [180, '180 days']];
const PER = { day: 'per day', month: 'per month', total: 'in total' };
const MB = 1048576;
const mb = (bytes) => (bytes === 0 ? '0 MB' : bytes < MB / 10 ? `${Math.max(Math.round(bytes / 1024), 1)} KB` : `${(bytes / MB).toFixed(bytes < MB * 10 ? 1 : 0)} MB`);

let tokens = [];
let requests = [];
let overview = null; // storage across the class (null until loaded, {unavailable: true} when the server has no storage)
let tab = 'students';
let query = '';
let openId = null; // the student whose detail panel is open

function show(signedIn) {
  $('login').classList.toggle('hidden', signedIn);
  $('app').classList.toggle('hidden', !signedIn);
}

/** Run an API call for the teacher; a 401 means the session ended, so go back to the sign-in form. */
async function call(path, options) {
  try {
    return await api(path, { role: 'teacher', ...options });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && session('teacher')) {
      session('teacher', null);
      closeDrawer();
      show(false);
    }
    throw e;
  }
}

async function loadOverview() {
  try {
    overview = await call('/api/admin/storage');
  } catch (e) {
    if (e instanceof ApiError && e.status === 503) overview = { unavailable: true };
    else $('error').textContent = e.message;
  }
}

async function load() {
  $('error').textContent = '';
  try {
    [{ tokens }, { requests }] = await Promise.all([call('/api/admin/tokens'), call('/api/admin/requests')]);
    if (tab === 'storage' || (openId && tokens.find((t) => t.id === openId)?.storage?.enabled)) await loadOverview();
  } catch (e) {
    $('error').textContent = e.message;
    return;
  }
  render();
}

// ---------- small building blocks ----------
function avatar(t, size = '') {
  let hash = 0;
  for (const ch of t.slug) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
  const label = (t.student_name || t.slug).trim().charAt(0).toUpperCase();
  return h('span', { class: `avatar ${size}`, style: `--hue:${hash}`, 'aria-hidden': 'true' }, label);
}

const chip = (label, title) => h('span', { class: 'chip', title }, label);

function meter(label, used, limit, text) {
  const percent = limit > 0 ? Math.min(100, (used / limit) * 100) : 100;
  const cls = percent >= 100 ? 'bad' : percent >= 80 ? 'warn' : '';
  return h('div', { class: 'meter' },
    h('div', { class: 'meter-head' }, h('span', {}, label), h('span', { class: 'muted' }, text)),
    h('div', { class: `bar ${cls}`, role: 'progressbar', 'aria-label': label, 'aria-valuenow': Math.round(percent), 'aria-valuemin': 0, 'aria-valuemax': 100 },
      h('span', { style: `width:${Math.max(percent, used ? 2 : 0)}%` })));
}

function tile(label, value, sub) {
  return h('div', { class: 'tile' }, h('div', { class: 'tile-value' }, value), h('div', { class: 'tile-label' }, label), sub ? h('div', { class: 'small muted' }, sub) : null);
}

function switchControl(title, on, disabled, onToggle) {
  return h('button', { class: 'switch', role: 'switch', 'aria-checked': String(on), 'aria-label': title, disabled: disabled ? true : null, onclick: onToggle },
    h('span', { class: 'knob' }));
}

// ---------- main page ----------
function render() {
  const pending = requests.length;
  $('badge').textContent = pending;
  $('badge').classList.toggle('hidden', pending === 0);
  for (const name of ['students', 'requests', 'storage']) $(`tab-${name}`).setAttribute('aria-selected', tab === name);
  $('toolbar').classList.toggle('hidden', tab !== 'students');
  $('stats').replaceChildren(...stats());
  let rows;
  if (tab === 'students') {
    const q = query.trim().toLowerCase();
    const shown = tokens.filter((t) => !q || `${t.student_name} ${t.slug}`.toLowerCase().includes(q));
    rows = shown.length ? shown.map(studentRow) : [h('div', { class: 'empty' }, tokens.length ? 'No student matches your search.' : 'No tokens yet. Click “New token” to create one.')];
  } else if (tab === 'requests') {
    rows = requests.length ? requests.map(requestRow) : [h('div', { class: 'empty' }, 'No pending requests.')];
  } else {
    rows = storageTab();
  }
  $('panel').replaceChildren(...rows);
  refreshDrawer();
}

function stats() {
  const active = tokens.filter((t) => t.status === 'active');
  const soon = tokens.filter((t) => t.status === 'active' && now() >= t.warn_at);
  const stored = tokens.reduce((sum, t) => sum + (t.storage?.usage?.data_bytes || 0), 0);
  const users = tokens.filter((t) => t.storage?.enabled).length;
  return [
    tile('Students', tokens.length, `${active.length} active`),
    tile('Expiring soon', soon.length, soon.length ? 'within the warning window' : 'none right now'),
    tile('Requests', requests.length, requests.length ? 'waiting for you' : 'all clear'),
    tile('Class storage', users ? mb(stored) : '—', users ? `${users} student${users === 1 ? '' : 's'} using it` : 'switched on for no one'),
  ];
}

function studentRow(t) {
  const enabled = [
    t.backend.available && t.backend.enabled ? chip('Backend', 'Backend is on') : null,
    t.ai.available && t.ai.enabled ? chip('AI', 'Class AI is on') : null,
    t.storage?.available && t.storage.enabled ? chip('Storage', 'Class storage is on') : null,
    t.custom_domain.available && t.custom_domain.enabled ? chip('Domain', 'Custom domain allowed') : null,
  ];
  return h('button', { class: 'student', onclick: () => openDrawer(t.id), 'aria-label': `Open details for ${t.student_name || t.slug}` },
    avatar(t),
    h('span', { class: 'student-main' },
      h('span', { class: 'title' }, t.student_name || t.slug),
      h('span', { class: 'small muted' }, `${t.slug}${t.last_deploy_at ? ` · last deploy ${date(t.last_deploy_at)}` : ' · never deployed'}`)),
    h('span', { class: 'student-chips' }, enabled),
    h('span', { class: 'student-state' }, statusPill(t), h('span', { class: 'small muted' }, lifeText(t)),
      t.regen.state === 'pending' ? h('span', { class: 'pill warn' }, 'Regeneration requested') : null),
    h('span', { class: 'chevron', 'aria-hidden': 'true' }, '›'));
}

function requestRow(t) {
  const original = Math.max(Math.round((t.expires_at - t.created_at) / 86400), 1);
  return h('div', { class: 'request' },
    avatar(t),
    h('div', { class: 'student-main' }, h('span', { class: 'title' }, t.student_name || t.slug),
      h('span', { class: 'small muted' }, `${t.slug} · requested ${date(t.regen.requested_at)}`),
      t.regen.note ? h('span', { class: 'quote' }, `“${t.regen.note}”`) : null),
    h('div', { class: 'student-state' }, statusPill(t), h('span', { class: 'small muted' }, t.archived ? 'Site is offline; approving brings it back' : expiryText(t))),
    h('div', { class: 'btns' },
      h('button', { class: 'small primary', onclick: () => approve(t, original) }, 'Approve'),
      h('button', { class: 'small danger', onclick: () => deny(t) }, 'Deny')));
}

const expiryText = (t) => (t.status === 'active' ? `Expires in ${duration(t.expires_at - now())}` : `Expired ${duration(now() - t.expires_at)} ago`);

// ---------- the storage tab: sizes and counts for the whole class, never the content ----------
function storageTab() {
  if (!overview) return [h('div', { class: 'empty' }, 'Loading storage…')];
  if (overview.unavailable) return [h('div', { class: 'empty' }, 'Class storage is not set up on this server.')];
  const { students, totals } = overview;
  const head = h('div', { class: 'tiles inner' },
    tile('Using storage', totals.enabled, `${totals.students} with any data`),
    tile('Records', totals.items.toLocaleString(), mb(totals.data_bytes)),
    tile('Files', totals.files.toLocaleString(), mb(totals.files_bytes)),
    tile('Needs cleanup', totals.inactive_with_data, totals.inactive_with_data ? 'no longer using it, still holding data' : 'nothing left behind'));
  const note = h('p', { class: 'privacy small muted' }, 'You see sizes and counts only. What students store is private to them; clearing empties it without showing it to you.');
  if (!students.length) return [head, note, h('div', { class: 'empty' }, 'No student has used storage yet. Switch it on for a student from their details.')];
  const rows = students.map((r) => {
    const t = tokens.find((x) => x.id === r.id);
    const l = r.limits;
    return h('div', { class: `srow ${r.inactive ? 'flag' : ''}` },
      h('button', { class: 'link-button', onclick: () => openDrawer(r.id), title: 'Open details' }, t ? avatar(t, 'sm') : null, h('span', {}, r.student_name || r.slug)),
      h('span', {}, r.inactive ? h('span', { class: 'pill warn' }, 'Needs cleanup') : r.enabled ? h('span', { class: 'pill active' }, 'On') : h('span', { class: 'pill offline' }, 'Off')),
      meter('Records', r.usage.data_bytes, l.data_mb * MB, `${mb(r.usage.data_bytes)} / ${l.data_mb} MB`),
      meter('Files', r.files.bytes, l.files_mb * MB, `${mb(r.files.bytes)} / ${l.files_mb} MB`),
      h('span', { class: 'small muted' }, `${r.usage.ops_used.toLocaleString()} / ${l.ops_limit.toLocaleString()} ops`),
      r.holds_data && t ? h('button', { class: 'small danger', onclick: () => clearStorage(t) }, 'Clear…') : h('span'));
  });
  return [head, note, h('div', { class: 'stable' }, ...rows)];
}

// ---------- the detail panel (opens when you click a student) ----------
const drawer = h('dialog', { class: 'drawer', 'aria-label': 'Student details' });
document.body.append(drawer);
drawer.addEventListener('click', (e) => { if (e.target === drawer) closeDrawer(); });
drawer.addEventListener('close', () => { openId = null; });

function openDrawer(id) {
  openId = id;
  fillDrawer();
  if (!drawer.open) drawer.showModal();
  const t = tokens.find((x) => x.id === id);
  if (t?.storage?.enabled) loadOverview().then(() => { if (openId === id) fillDrawer(); });
}

function closeDrawer() {
  if (drawer.open) drawer.close();
  openId = null;
}

function refreshDrawer() {
  if (openId && drawer.open) fillDrawer();
}

function fillDrawer() {
  const t = tokens.find((x) => x.id === openId);
  if (!t) { closeDrawer(); return; }
  const top = drawer.scrollTop;
  drawer.replaceChildren(drawerContent(t));
  drawer.scrollTop = top;
}

function feature({ title, hint, on, disabled, onToggle, body }) {
  return h('section', { class: `feature ${on ? 'on' : ''}` },
    h('div', { class: 'feature-head' }, h('div', {}, h('h3', {}, title), h('div', { class: 'small muted' }, hint)), onToggle ? switchControl(title, on, disabled, onToggle) : null),
    body && on ? h('div', { class: 'feature-body' }, body) : null);
}

function drawerContent(t) {
  const live = t.status !== 'revoked';
  const btn = (label, fn, cls = '') => h('button', { class: `small ${cls}`, onclick: fn }, label);
  const features = [];

  if (t.custom_domain.available) {
    const d = t.custom_domain;
    features.push(feature({ title: 'Custom domain', hint: d.domain ? `${d.domain} · ${d.state.replace('_', ' ')}` : 'Lets the student point their own domain at the site', on: d.enabled, disabled: !live, onToggle: () => toggleDomain(t) }));
  }
  if (t.backend.available) {
    const b = t.backend, u = b.usage;
    features.push(feature({ title: 'Backend', hint: b.enabled ? `One function per student · ${b.state === 'none' ? 'no function yet' : b.state}` : 'A small server-side function for the student’s site', on: b.enabled, disabled: !live, onToggle: () => toggleBackend(t),
      body: [u ? meter('Runs', u.used, u.limit, `${u.used.toLocaleString()} / ${u.limit.toLocaleString()} ${PER[u.period]}`) : null,
        h('div', { class: 'btns' }, btn('Edit allowance…', () => editRuns(t)), btn('+1,000 runs', () => addRuns(t)))] }));
    if (t.ai.available) {
      const a = t.ai;
      features.push(feature({ title: 'Class AI', hint: a.enabled ? `${a.model}${a.key_active ? '' : ' · paused while the token is not active'}` : 'A capped AI key, no key of their own needed', on: a.enabled, disabled: !live, onToggle: () => toggleAi(t),
        body: [meter('Spend', a.used_usd, a.limit_usd, `${usd(a.used_usd)} / ${usd(a.limit_usd)} ${PER[a.period]}${a.approx_calls ? ` · about ${a.approx_calls.toLocaleString()} answers` : ''}`),
          h('div', { class: 'btns' }, btn('Edit cap…', () => editAi(t)))] }));
    }
    if (t.storage?.available) features.push(storageFeature(t, live, btn));
  }

  return h('div', { class: 'drawer-body' },
    h('header', { class: 'drawer-head' },
      avatar(t, 'lg'),
      h('div', { class: 'drawer-title' }, h('h2', {}, t.student_name || t.slug),
        h('div', { class: 'small muted' }, t.slug, ' · ', h('a', { href: t.url, target: '_blank', rel: 'noopener' }, t.url.replace('https://', '')))),
      h('button', { class: 'icon-button', onclick: closeDrawer, 'aria-label': 'Close' }, '×')),
    h('div', { class: 'drawer-status' }, statusPill(t), t.regen.state === 'pending' ? h('span', { class: 'pill warn' }, 'Regeneration requested') : null),
    h('section', { class: 'feature on' },
      h('h3', {}, 'Token'),
      expiryBar(t),
      h('dl', { class: 'facts' },
        h('dt', {}, 'Token'), h('dd', {}, `…${t.hint}`),
        h('dt', {}, 'Created'), h('dd', {}, date(t.created_at)),
        h('dt', {}, 'Expires'), h('dd', {}, date(t.expires_at)),
        h('dt', {}, 'Goes offline'), h('dd', {}, date(t.offline_at)),
        h('dt', {}, 'Last deploy'), h('dd', {}, t.last_deploy_at ? date(t.last_deploy_at) : 'never')),
      live ? h('div', { class: 'btns' }, btn('Extend…', () => extend(t)), !t.archived ? btn('Take offline…', () => offline(t)) : null, btn('Revoke…', () => revoke(t), 'danger')) : h('p', { class: 'small muted', style: 'margin:0' }, 'This token is revoked. Features are locked.')),
    h('h3', { class: 'section-title' }, 'Features'),
    ...features);
}

function storageFeature(t, live, btn) {
  const s = t.storage;
  const u = s.usage || { data_bytes: 0, items: 0, ops_used: 0 };
  const l = s.limits;
  const row = overview && !overview.unavailable ? overview.students.find((r) => r.id === t.id) : null;
  const files = row ? row.files : null;
  return feature({ title: 'Class storage', hint: s.enabled ? 'A private database and file area' : 'A small private database and file area, with hard caps', on: s.enabled, disabled: !live, onToggle: () => toggleStorage(t),
    body: [meter('Records', u.data_bytes, l.data_mb * MB, `${mb(u.data_bytes)} / ${l.data_mb} MB · ${u.items.toLocaleString()} items`),
      files ? meter('Files', files.bytes, l.files_mb * MB, `${mb(files.bytes)} / ${l.files_mb} MB · ${files.count} files`) : h('div', { class: 'small muted' }, `Files: up to ${l.files_mb} MB (loading usage…)`),
      meter('Operations', u.ops_used, l.ops_limit, `${u.ops_used.toLocaleString()} / ${l.ops_limit.toLocaleString()} ${PER[l.ops_period]}`),
      h('div', { class: 'btns' }, btn('Edit caps…', () => editStorage(t)), btn('Clear storage…', () => clearStorage(t), 'danger')),
      h('p', { class: 'privacy small muted' }, 'You see sizes and counts only. What a student stores is private to them.')] });
}

// ---------- actions ----------
async function act(path, body, message) {
  try {
    await call(path, { method: 'POST', body });
    toast(message);
    await load();
  } catch (e) {
    $('error').textContent = e.message;
    toast(e.message);
  }
}

async function extend(t) {
  const r = await ask({ title: `Extend ${t.slug}`, text: 'Days are added to the current expiry (or counted from today if it has already expired).',
    fields: [{ name: 'days', label: 'Days to add', type: 'number', value: 7, min: 1, max: 365, required: true }], confirm: 'Extend' });
  if (r) act(`/api/admin/tokens/${t.id}/extend`, { days: Number(r.days) }, 'Extended');
}

async function toggleStorage(t) {
  const enabled = !t.storage.enabled;
  act(`/api/admin/tokens/${t.id}/storage`, { enabled },
    enabled ? `Storage switched on for ${t.slug}: ${t.storage.limits.data_mb} MB of data and ${t.storage.limits.files_mb} MB of files` : `Storage switched off for ${t.slug}: their data is kept but not served`);
}

async function editStorage(t) {
  const l = t.storage.limits;
  const r = await ask({ title: `Storage caps for ${t.slug}`,
    text: 'Hard limits, enforced by the platform on every call. Data is small JSON records (up to 64 KB each); files are up to 5 MB each.',
    fields: [{ name: 'data_mb', label: 'Database size (MB)', type: 'number', value: l.data_mb, min: 1, max: 100, required: true },
      { name: 'files_mb', label: 'File space (MB)', type: 'number', value: l.files_mb, min: 1, max: 500, required: true },
      { name: 'ops_limit', label: 'Operations allowed (each read, write or file call is one)', type: 'number', value: l.ops_limit, min: 0, max: 1000000, required: true },
      { name: 'ops_period', label: 'Per', options: [['day', 'day'], ['month', 'month'], ['total', 'total (whole token life)']], value: l.ops_period }], confirm: 'Save' });
  if (r) act(`/api/admin/tokens/${t.id}/storage`, { data_mb: Number(r.data_mb), files_mb: Number(r.files_mb), ops_limit: Number(r.ops_limit), ops_period: r.ops_period }, 'Storage caps saved');
}

/** Empties what a student stored. The teacher never sees it; the student's key, switch and caps stay. Typing the name guards against a slip. */
async function clearStorage(t) {
  const r = await ask({ title: `Clear storage for ${t.slug}`,
    text: 'This deletes what the student saved. You will not see it, and it cannot be undone from here (deleted files can still be restored by the owner for 30 days). Their access and caps stay as they are.',
    fields: [{ name: 'scope', label: 'What to clear', options: [['all', 'Records and files'], ['data', 'Records only'], ['files', 'Files only']], value: 'all' },
      { name: 'confirm', label: `Type “${t.slug}” to confirm`, required: true, placeholder: t.slug, maxlength: 40 }], confirm: 'Clear', danger: true });
  if (!r) return;
  if (r.confirm.trim() !== t.slug) { toast('The name did not match, so nothing was cleared'); return; }
  try {
    const { cleared } = await call(`/api/admin/tokens/${t.id}/storage/clear`, { method: 'POST', body: { scope: r.scope } });
    toast(`Cleared ${cleared.items.toLocaleString()} record${cleared.items === 1 ? '' : 's'} and ${cleared.files.toLocaleString()} file${cleared.files === 1 ? '' : 's'}`);
    await load();
    if (tab === 'storage') { await loadOverview(); render(); }
  } catch (e) {
    toast(e.message);
    $('error').textContent = e.message;
  }
}

async function toggleAi(t) {
  const enabled = !t.ai.enabled;
  act(`/api/admin/tokens/${t.id}/ai`, { enabled },
    enabled ? `AI switched on for ${t.slug}: a ${usd(t.ai.limit_usd)} ${PER[t.ai.period]} cap` : `AI switched off for ${t.slug}: the key is disabled now`);
}

async function editAi(t) {
  const a = t.ai;
  const gemini = (a.providers || []).includes('gemini');
  const fields = [{ name: 'limit_usd', label: 'Spending cap (USD)', type: 'number', value: a.limit_usd, min: 0.01, max: 20, step: 0.01, required: true },
    { name: 'period', label: 'Per', options: [['day', 'day'], ['month', 'month'], ['total', 'total (whole token life)']], value: a.period }];
  if (gemini) {
    fields.push({ name: 'provider', label: 'AI service', value: a.provider, options: [['gemini', 'Google Gemini'], ['openrouter', 'OpenRouter']] });
  }
  fields.push({ name: 'model', label: 'Model (you can type another)', value: a.model, maxlength: 80, required: true,
    follows: { field: gemini ? 'provider' : null, by: {
      gemini: { value: a.gemini_models[0], suggestions: a.gemini_models },
      openrouter: { value: a.default_model, suggestions: [a.default_model] } } } });
  if (!gemini) delete fields[fields.length - 1].follows;
  const r = await ask({ title: `AI allowance for ${t.slug}`,
    text: gemini
      ? `Used so far: ${usd(a.used_usd)}. Gemini: the platform counts the cost of every answer itself (an answer already under way can overshoot the cap by one answer). OpenRouter enforces its own cap. Changing the service gives the student a new key setup and stops the old one.`
      : `Used so far: ${usd(a.used_usd)}. OpenRouter enforces the cap itself, so it holds even if the student's code misbehaves. Usage is counted a few seconds late.`,
    fields, confirm: 'Save' });
  if (!r) return;
  const body = { limit_usd: Number(r.limit_usd), period: r.period, model: r.model.trim() };
  if (gemini) body.provider = r.provider;
  act(`/api/admin/tokens/${t.id}/ai`, body, 'AI allowance saved');
}

async function toggleBackend(t) {
  const enabled = !t.backend.enabled;
  act(`/api/admin/tokens/${t.id}/backend`, { enabled }, enabled ? `Backend turned on for ${t.slug}` : `Backend turned off for ${t.slug}: it stops answering requests`);
}

async function editRuns(t) {
  const u = t.backend.usage || { limit: 5000, period: 'month', used: 0 };
  const r = await ask({ title: `Run allowance for ${t.slug}`, text: `Used so far: ${u.used.toLocaleString()}. A “run” is one call to the student's function.`,
    fields: [{ name: 'runs_limit', label: 'Runs allowed', type: 'number', value: u.limit, min: 0, max: 1000000, required: true },
      { name: 'runs_period', label: 'Per', options: [['day', 'day'], ['month', 'month'], ['total', 'total (whole token life)']], value: u.period }], confirm: 'Save' });
  if (r) act(`/api/admin/tokens/${t.id}/backend`, { runs_limit: Number(r.runs_limit), runs_period: r.runs_period }, 'Allowance saved');
}

async function addRuns(t) {
  const limit = (t.backend.usage?.limit ?? 5000) + 1000;
  act(`/api/admin/tokens/${t.id}/backend`, { runs_limit: Math.min(limit, 1000000) }, `Allowance for ${t.slug} is now ${Math.min(limit, 1000000).toLocaleString()}`);
}

async function toggleDomain(t) {
  const enabled = !t.custom_domain.enabled;
  act(`/api/admin/tokens/${t.id}/domain-access`, { enabled }, enabled ? `Custom domain allowed for ${t.slug}` : `Custom domain switched off for ${t.slug}`);
}

async function offline(t) {
  const r = await ask({ title: `Take ${t.slug} offline?`, text: 'The site stops being served now. Files are archived, not deleted, and come back if you extend the token or approve a request.', confirm: 'Take offline', danger: true });
  if (r) act(`/api/admin/tokens/${t.id}/offline`, {}, 'Site taken offline');
}

async function revoke(t) {
  const r = await ask({ title: `Revoke ${t.slug}?`, text: 'The student can no longer sign in or deploy. The live site is not changed.', confirm: 'Revoke', danger: true });
  if (r) act(`/api/admin/tokens/${t.id}/revoke`, {}, 'Revoked');
}

async function approve(t, original) {
  const options = [...DURATIONS];
  if (!options.some(([d]) => d === original)) options.unshift([original, `${original} days (original)`]);
  const r = await ask({ title: `Approve ${t.slug}`, text: 'New duration, counted from today.', fields: [{ name: 'days', label: 'New duration', options, value: original }], confirm: 'Approve' });
  if (r) act(`/api/admin/tokens/${t.id}/approve`, { days: Number(r.days) }, 'Approved');
}

async function deny(t) {
  const r = await ask({ title: `Deny ${t.slug}`, fields: [{ name: 'reason', label: 'Reason (shown to the student, optional)', maxlength: 300 }], confirm: 'Deny', danger: true });
  if (r) act(`/api/admin/tokens/${t.id}/deny`, { reason: r.reason }, 'Denied');
}

async function newToken() {
  const options = [...DURATIONS, ['custom', 'Custom…']];
  const r = await ask({ title: 'New token', fields: [
    { name: 'days', label: 'Valid for', options, value: 30 },
    { name: 'student_name', label: 'Student name (optional)', maxlength: 60 },
    { name: 'slug', label: 'Site name (optional)', placeholder: 'defaults to the student name', maxlength: 30 },
  ], confirm: 'Create' });
  if (!r) return;
  let days = r.days;
  if (days === 'custom') {
    const c = await ask({ title: 'Custom duration', fields: [{ name: 'days', label: 'Days (1–365)', type: 'number', value: 45, min: 1, max: 365, required: true }], confirm: 'Create' });
    if (!c) return;
    days = c.days;
  }
  try {
    const body = { days: Number(days), student_name: r.student_name, slug: r.slug || undefined };
    const { token, item } = await call('/api/admin/tokens', { method: 'POST', body });
    showCreated(token, item);
    await load();
  } catch (e) {
    $('error').textContent = e.message;
  }
}

function showCreated(token, item) {
  const portal = `${location.origin}${location.pathname.replace(/[^/]*$/, '')}student.html`;
  const message = `Your deploy token: ${token}\nSign in at ${portal}\nYour site will be ${item.url}\nValid until ${date(item.expires_at)}.`;
  $('created').classList.remove('hidden');
  $('created').replaceChildren(
    h('h2', {}, `Token for ${item.student_name || item.slug}`),
    h('p', { class: 'notice warn', style: 'margin-top:0' }, 'Copy it now. For security it is shown only once.'),
    h('div', { class: 'secret' }, token),
    h('div', { style: 'display:flex;gap:8px;margin-top:12px;flex-wrap:wrap' },
      h('button', { class: 'primary small', onclick: () => copyText(token) }, 'Copy token'),
      h('button', { class: 'small', onclick: () => copyText(message) }, 'Copy message for the student'),
      h('button', { class: 'small', onclick: () => $('created').classList.add('hidden') }, 'Done')));
  $('created').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('login-error').textContent = '';
  const form = new FormData(e.target);
  try {
    const { session: s } = await api('/api/teacher/login', { method: 'POST', body: { username: form.get('username'), password: form.get('password') } });
    session('teacher', s);
    e.target.reset();
    show(true);
    load();
  } catch (err) {
    $('login-error').textContent = err.message;
  }
});
$('logout').onclick = () => { session('teacher', null); tokens = requests = []; overview = null; closeDrawer(); $('created').classList.add('hidden'); show(false); };
$('refresh').onclick = load;
$('new-token').onclick = newToken;
$('search').addEventListener('input', (e) => { query = e.target.value; render(); });
$('tab-students').onclick = () => { tab = 'students'; render(); };
$('tab-requests').onclick = () => { tab = 'requests'; render(); };
$('tab-storage').onclick = async () => { tab = 'storage'; render(); await loadOverview(); render(); };

setInterval(() => { if (session('teacher') && !document.hidden && !document.querySelector('dialog[open]:not(.drawer)')) load(); }, 60000);
if (session('teacher')) { show(true); load(); } else show(false);
