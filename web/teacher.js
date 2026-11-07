import { capsText, classUsage } from './usage.js';
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
  for (const name of ['students', 'requests', 'storage', 'library', 'traces', 'worker', 'usage']) $(`tab-${name}`).setAttribute('aria-selected', tab === name);
  $('toolbar').classList.toggle('hidden', tab !== 'students');
  $('stats').replaceChildren(...stats());
  let rows;
  if (tab === 'students') {
    const q = query.trim().toLowerCase();
    const shown = tokens.filter((t) => !q || `${t.student_name} ${t.slug}`.toLowerCase().includes(q));
    rows = shown.length ? shown.map(studentRow) : [h('div', { class: 'empty' }, tokens.length ? 'No student matches your search.' : 'No tokens yet. Click “New token” to create one.')];
  } else if (tab === 'requests') {
    rows = requests.length ? requests.map(requestRow) : [h('div', { class: 'empty' }, 'No pending requests.')];
  } else if (tab === 'usage') {
    rows = usageTab();
  } else if (tab === 'library') {
    rows = libraryTab();
  } else if (tab === 'traces') {
    rows = tracesTab();
  } else if (tab === 'worker') {
    rows = workerTab();
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
    t.knowledge?.available && t.knowledge.enabled ? chip('Books', 'Class knowledge is on') : null,
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
    h('div', { class: 'student-state' }, statusPill(t), h('span', { class: 'small muted' }, t.archived ? 'Site is offline; open it and choose Bring online (or extend)' : expiryText(t))),
    h('div', { class: 'btns' },
      h('button', { class: 'small primary', onclick: () => approve(t, original) }, 'Approve'),
      h('button', { class: 'small danger', onclick: () => deny(t) }, 'Deny')));
}

const expiryText = (t) => (t.status === 'active' ? `Expires in ${duration(t.expires_at - now())}` : `Expired ${duration(now() - t.expires_at)} ago`);

// ---------- the usage tab: what the class is using and the most it could cost, from the list already loaded ----------
function usageTab() {
  const { rows, totals } = classUsage(tokens);
  const head = h('div', { class: 'tiles inner' },
    tile('Backend on', totals.backendOn, `of ${totals.students} students`),
    tile('Runs used', totals.runsUsed.toLocaleString(), `of ${totals.runsAllowed.toLocaleString()} allowed`),
    tile('AI spent', usd(totals.aiSpent), `${totals.aiOn} with the AI on · caps add up to ${capsText(totals.aiCaps, usd)}`),
    tile('Need attention', totals.attention, totals.attention ? '80% or more of an allowance used' : 'nobody is near a limit'));
  const note = h('p', { class: 'privacy small muted' }, 'The most the class AI can cost is the sum of the caps above (each student stops at their own cap). Backend runs cost AWS pennies; the run allowance keeps them bounded. Lower a cap or switch a feature off from the details of each student.');
  if (!rows.length) return [head, note, h('div', { class: 'empty' }, 'No student has the backend or the class AI switched on yet.')];
  return [head, note, h('div', { class: 'stable' }, ...rows.map((r) => {
    const t = tokens.find((x) => x.id === r.id);
    return h('div', { class: `urow ${r.worst >= 80 ? 'flag' : ''}` },
      h('button', { class: 'link-button', onclick: () => openDrawer(r.id), title: 'Open details' }, t ? avatar(t, 'sm') : null, h('span', {}, r.name)),
      r.runs ? meter('Runs', r.runs.used, r.runs.limit, `${r.runs.used.toLocaleString()} / ${r.runs.limit.toLocaleString()} ${PER[r.runs.period] || ''}`) : h('span', { class: 'small muted' }, 'Backend off'),
      r.ai ? meter('AI spend', r.ai.spent, r.ai.cap, `${usd(r.ai.spent)} / ${usd(r.ai.cap)} ${PER[r.ai.period] || ''}`) : h('span', { class: 'small muted' }, 'AI off'));
  }))];
}

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

// ---------- the library tab: the class library every student with class knowledge can search (read-only for them) ----------
const LIB = '/api/admin/knowledge/library/collections';
const BOOK_STATES = { uploading: ['Uploading', 'warn'], queued: ['Waiting', 'warn'], processing: ['Reading…', 'warn'], ready: ['Ready', 'active'], failed: ['Failed', 'expired'] };
let library = null; // { available, spent_micro, ceiling_micro, library: { collections, limits, usage } } or { unavailable: true }
let libraryBooks = {}; // collection name -> its documents
let libraryTimer = null;
let libraryBusy = ''; // a short line while a file is being sent

async function loadLibrary() {
  try {
    library = await call('/api/admin/knowledge');
    if (!library.available) library = { unavailable: true };
    else {
      const names = library.library.collections.map((c) => c.name);
      const lists = await Promise.all(names.map((name) => call(`${LIB}/${name}/documents`).then((r) => r.documents)));
      libraryBooks = Object.fromEntries(names.map((name, i) => [name, lists[i]]));
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 503) library = { unavailable: true };
    else $('error').textContent = e.message;
  }
  clearTimeout(libraryTimer);
  const reading = Object.values(libraryBooks).some((docs) => docs.some((d) => ['uploading', 'queued', 'processing'].includes(d.state)));
  if (tab === 'library' && reading) libraryTimer = setTimeout(async () => { if (tab === 'library') { await loadLibrary(); render(); } }, 4000);
}

const spent = (micro) => usd(micro / 1e6);
const passages = (n) => `${n.toLocaleString()} passage${n === 1 ? '' : 's'}`;
const shelves = (n) => `${n} ${n === 1 ? 'shelf' : 'shelves'}`;

function libraryTab() {
  if (!library) return [h('div', { class: 'empty' }, 'Loading the library…')];
  if (library.unavailable) return [h('div', { class: 'empty' }, 'Class knowledge is not set up on this server. Install it from your terminal with:  plan --enable knowledge  and then  apply <id>.')];
  const { collections, limits, usage } = library.library;
  const head = h('div', { class: 'tiles inner' },
    tile('Books', usage.documents.toLocaleString(), `of ${limits.documents} · ${shelves(collections.length)}`),
    tile('Passages', usage.passages.toLocaleString(), `of ${limits.passages.toLocaleString()}`),
    tile('Space', mb(usage.stored_bytes), `of ${limits.stored_mb} MB`),
    tile('Adding books this month', spent(library.spent_micro), `ceiling ${spent(library.ceiling_micro)} for the whole class`));
  const note = h('p', { class: 'privacy small muted' },
    'Students with class knowledge switched on can search these books (as “library/<shelf>”) but cannot change them. Only add books you may share with the class. PDFs with text work; scanned pages are skipped. When the class reaches the monthly ceiling no new book is read until next month; searching keeps working.');
  const bar = h('div', { class: 'btns', style: 'padding:0 16px 12px' }, h('button', { class: 'primary small', onclick: addShelf }, 'New shelf…'),
    libraryBusy ? h('span', { class: 'small muted' }, libraryBusy) : null);
  if (!collections.length) return [head, note, bar, h('div', { class: 'empty' }, 'The library is empty. Create a shelf (for example “science-9”) and add the school’s books to it.')];
  return [head, note, bar, ...collections.map(shelfSection)];
}

function shelfSection(c) {
  const books = libraryBooks[c.name] || [];
  const picker = h('input', { type: 'file', accept: '.pdf,.txt,.md,.markdown,.html,.htm', multiple: true, class: 'hidden', 'aria-label': `Add books to ${c.name}`,
    onchange: (e) => { const files = [...e.target.files]; e.target.value = ''; if (files.length) uploadBooks(c.name, files); } });
  const rows = books.length ? books.map((d) => bookRow(c.name, d)) : [h('div', { class: 'small muted', style: 'padding:8px 16px' }, 'No books on this shelf yet.')];
  return h('section', { class: 'feature on', style: 'margin:0 16px 12px' },
    h('div', { class: 'feature-head' }, h('div', {}, h('h3', {}, c.title && c.title !== c.name ? `${c.title} (${c.name})` : c.name),
      h('div', { class: 'small muted' }, `${c.documents} book${c.documents === 1 ? '' : 's'} · ${passages(c.passages)}`)),
    h('div', { class: 'btns' }, picker, h('button', { class: 'small', onclick: () => picker.click() }, 'Add books…'),
      h('button', { class: 'small danger', onclick: () => removeShelf(c) }, 'Remove shelf…'))),
    h('div', { class: 'feature-body' }, rows));
}

function bookRow(shelf, d) {
  const [label, cls] = BOOK_STATES[d.state] || [d.state, 'offline'];
  const detail = d.state === 'ready' ? `${d.pages ? `${d.pages} page${d.pages === 1 ? '' : 's'} · ` : ''}${passages(d.passages || 0)}${d.message ? ` · ${d.message}` : ''}` : d.message || '';
  return h('div', { class: 'srow', style: 'grid-template-columns: minmax(140px, 1.4fr) 100px 1fr 80px' },
    h('span', { title: d.filename }, d.title || d.filename),
    h('span', {}, h('span', { class: `pill ${cls}` }, label)),
    h('span', { class: 'small muted' }, detail),
    h('button', { class: 'small danger', onclick: () => removeBook(shelf, d) }, 'Remove…'));
}

async function addShelf() {
  const r = await ask({ title: 'New library shelf', text: 'A shelf groups books students search together, for example one subject or class. Students refer to it as library/<name>.',
    fields: [{ name: 'name', label: 'Name (lowercase letters, digits, - and _)', required: true, placeholder: 'science-9', maxlength: 40 },
      { name: 'title', label: 'Title shown to you (optional)', placeholder: 'Science, class 9', maxlength: 100 }], confirm: 'Create' });
  if (!r) return;
  try {
    await call(LIB, { method: 'POST', body: { name: r.name.trim(), title: r.title.trim() } });
    toast('Shelf created');
    await loadLibrary();
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function removeShelf(c) {
  const r = await ask({ title: `Remove the shelf “${c.name}”`, text: `This deletes ${c.documents} book${c.documents === 1 ? '' : 's'} and their search data for every student. Students whose apps search library/${c.name} will get “no such collection”.`,
    fields: [{ name: 'confirm', label: `Type “${c.name}” to confirm`, required: true, placeholder: c.name, maxlength: 40 }], confirm: 'Remove', danger: true });
  if (!r) return;
  if (r.confirm.trim() !== c.name) { toast('The name did not match, so nothing was removed'); return; }
  try {
    await call(`${LIB}/${c.name}`, { method: 'DELETE' });
    toast('Shelf removed');
    await loadLibrary();
    render();
  } catch (e) {
    toast(e.message);
  }
}

async function removeBook(shelf, d) {
  const r = await ask({ title: `Remove “${d.title || d.filename}”`, text: 'The book and its search data are deleted. Adding the same file again costs almost nothing, because its embeddings are remembered for 90 days.', confirm: 'Remove', danger: true });
  if (!r) return;
  try {
    await call(`${LIB}/${shelf}/documents/${d.id}`, { method: 'DELETE' });
    toast('Book removed');
    await loadLibrary();
    render();
  } catch (e) {
    toast(e.message);
  }
}

/** The browser sends the file straight to S3 with the one-time form the platform gives, then tells the platform to read it. Files go one after another. */
async function uploadBooks(shelf, files) {
  for (const file of files) {
    libraryBusy = `Sending ${file.name}…`;
    render();
    try {
      const target = await call(`${LIB}/${shelf}/documents`, { method: 'POST', body: { filename: file.name, size: file.size } });
      const form = new FormData();
      for (const [field, value] of Object.entries(target.fields)) form.append(field, value);
      form.append('file', file); // S3 needs the file as the LAST field
      const sent = await fetch(target.url, { method: 'POST', body: form });
      if (!sent.ok) throw new Error(`The upload was refused (${sent.status}). The file may be larger than allowed.`);
      await call(`${LIB}/${shelf}/documents/${target.doc_id}/start`, { method: 'POST', body: {} });
      toast(`${file.name} is being read`);
    } catch (e) {
      toast(`${file.name}: ${e.message}`);
      $('error').textContent = `${file.name}: ${e.message}`;
    }
  }
  libraryBusy = '';
  await loadLibrary();
  render();
}

// ---------- the worker tab: one small machine, off unless started (worker.py) ----------
let worker = null; // the answer of GET /api/admin/worker, or { unavailable: true }
let workerTimer = null;
let workerBusy = '';
const WORKER_STATES = { running: ['Running', 'active'], pending: ['Starting…', 'warn'], stopping: ['Stopping…', 'warn'], 'shutting-down': ['Stopping…', 'warn'], stopped: ['Off', 'offline'] };

async function loadWorker() {
  try {
    const out = await call('/api/admin/worker');
    worker = out.available === false ? { unavailable: true } : out;
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 409)) worker = { unavailable: true };
    else $('error').textContent = e.message;
  }
  clearTimeout(workerTimer);
  if (tab === 'worker' && worker && ['pending', 'stopping', 'shutting-down'].includes(worker.state)) workerTimer = setTimeout(async () => { if (tab === 'worker') { await loadWorker(); render(); } }, 5000);
}

async function workerAct(path, body, message) {
  workerBusy = message;
  render();
  try {
    const out = await call(path, { method: 'POST', body });
    worker = out.available === false ? { unavailable: true } : out;
    toast(message);
  } catch (e) { toast(e.message); }
  workerBusy = '';
  clearTimeout(workerTimer);
  await loadWorker();
  render();
}

function workerTab() {
  if (!worker) return [h('div', { class: 'empty' }, 'Loading the worker…')];
  if (worker.unavailable) return [h('div', { class: 'empty' }, 'The compute worker is not installed on this server. It is a small machine that is off until you start it. Install it from your terminal with:  plan --enable worker  and then  apply <id>.')];
  const [label, cls] = WORKER_STATES[worker.state] || [worker.state, 'offline'];
  const on = ['running', 'pending'].includes(worker.state);
  const s = worker.settings;
  const m = worker.month;
  const lease = worker.lease;
  const head = h('div', { class: 'tiles inner' },
    tile('Machine', label, `${worker.instance_type} · ${worker.disk_gb} GB disk`),
    tile('Hours this month', `${(m.minutes / 60).toFixed(1)} of ${(m.cap_minutes / 60).toFixed(0)}`, m.compute_usd === null ? 'price unknown' : `about $${m.compute_usd.toFixed(2)} compute + $${m.disk_usd.toFixed(2)} disk`),
    tile('Time limit', `${s.max_minutes} min`, 'a watchdog stops it after this'));
  const note = h('p', { class: 'privacy small muted' }, 'A small machine for heavy checks that do not fit in a web request. It is off until you start it, nothing can connect to it from the internet, it can only use its own folder in the bucket, and today it runs no student code. The cost shown is an estimate from list prices; the AWS bill is the truth.');
  const buttons = h('div', { class: 'btns', style: 'padding:0 16px 12px' },
    h('button', { class: 'primary small', disabled: on || Boolean(workerBusy), onclick: () => workerAct('/api/admin/worker/start', { mode: 'run' }, 'Starting the machine: it runs the checks, writes a report and shuts itself down') }, 'Start now'),
    h('button', { class: 'small', disabled: on || Boolean(workerBusy), onclick: () => workerAct('/api/admin/worker/start', { mode: 'keep' }, 'Starting the machine and keeping it on until the time limit') }, 'Start and keep on'),
    h('button', { class: 'small danger', disabled: !on || Boolean(workerBusy), onclick: () => workerAct('/api/admin/worker/stop', {}, 'Stopping the machine') }, 'Stop now'),
    workerBusy ? h('span', { class: 'small muted' }, workerBusy) : null);
  const state = h('div', { class: 'small', style: 'padding:0 16px 12px' }, on && lease ? `On since ${when(lease.started * 1000)} (${lease.mode === 'keep' ? 'kept on' : 'runs, then shuts down'}), it will be stopped by ${when(lease.until * 1000)} at the latest.` : 'The machine is off. A stopped machine costs only its disk.');
  const hours = Array.from({ length: 24 }, (_, i) => [i, `${String(i).padStart(2, '0')}:00 UTC`]);
  const schedule = h('section', { class: `feature ${s.daily_enabled ? 'on' : ''}`, style: 'margin:0 16px 12px' },
    h('div', { class: 'feature-head' }, h('div', {}, h('h3', {}, 'Run every day'), h('div', { class: 'small muted' }, s.daily_enabled ? `Starts every day at ${String(s.hour_utc).padStart(2, '0')}:00 UTC and shuts itself down when done` : 'Off: the machine only runs when you start it')),
      switchControl('Run every day', s.daily_enabled, false, () => workerAct('/api/admin/worker/settings', { daily_enabled: !s.daily_enabled }, s.daily_enabled ? 'The daily start is off' : 'The daily start is on'))),
    h('div', { class: 'feature-body' }, h('div', { class: 'btns' },
      h('label', { class: 'small' }, 'Hour ', h('select', { 'aria-label': 'Hour (UTC)', onchange: (e) => workerAct('/api/admin/worker/settings', { hour_utc: Number(e.target.value) }, 'The daily hour is saved') },
        ...hours.map(([v, text]) => h('option', { value: v, selected: v === s.hour_utc }, text)))),
      h('label', { class: 'small' }, 'Time limit ', h('select', { 'aria-label': 'Time limit', onchange: (e) => workerAct('/api/admin/worker/settings', { max_minutes: Number(e.target.value) }, 'The time limit is saved') },
        ...[30, 60, 120, 180, 360].map((v) => h('option', { value: v, selected: v === s.max_minutes }, `${v} minutes`)))),
      h('label', { class: 'small' }, 'Monthly cap ', h('select', { 'aria-label': 'Monthly cap on machine hours', onchange: (e) => workerAct('/api/admin/worker/settings', { monthly_cap_minutes: Number(e.target.value) * 60 }, 'The monthly cap is saved') },
        ...[10, 20, 40, 80, 160].map((v) => h('option', { value: v, selected: v * 60 === s.monthly_cap_minutes }, `${v} hours`)))))));
  const last = worker.last
    ? h('section', { class: 'feature on', style: 'margin:0 16px 12px' }, h('div', { class: 'feature-head' }, h('div', {}, h('h3', {}, 'Last run'),
      h('div', { class: 'small muted' }, `${worker.last.started} · ${worker.last.seconds} s · ${worker.last.exit === 0 ? 'finished' : `ended with code ${worker.last.exit}`}`))),
      h('div', { class: 'feature-body' }, h('pre', { class: 'small', style: 'white-space:pre-wrap;overflow-wrap:anywhere;margin:0' }, worker.last.output || '')))
    : h('div', { class: 'small muted', style: 'padding:0 16px 12px' }, 'No run yet.');
  return [head, note, buttons, state, schedule, last];
}

/** What this student may do with the compute worker (a per-student switch; off by default). Setting it never starts the machine: only you do that. */
const WORKER_LEVELS = [[0, 'Off'], [1, 'Reports: see checks of their own app'], [2, 'Platform tasks: ask for a named check of their own app'], [3, 'Own code: run their own scripts in a locked-down container']];
function workerAccessFeature(t, live, btn) {
  const a = t.worker_access;
  const on = a.level > 0;
  const hint = !on ? 'Off: the student cannot use the worker' : `Level ${a.level}: ${WORKER_LEVELS[a.level][1]}`;
  return feature({ title: 'Compute worker', hint, on, disabled: !live, onToggle: () => act(`/api/admin/tokens/${t.id}/worker-access`, { level: on ? 0 : 1 }, on ? `The compute worker is off for ${t.slug}` : `${t.slug} may now see worker reports (level 1)`),
    body: [h('div', { class: 'small' }, `Level: ${a.level} (${a.level_name})`),
      a.level >= 2 ? h('div', { class: 'small' }, `At most ${a.requests_per_day} request${a.requests_per_day === 1 ? '' : 's'} a day and ${a.minutes_per_month} machine minutes a month`) : null,
      a.level === 3 ? h('div', { class: 'small' }, `Each job may run ${a.job_minutes} minutes. Internet: ${a.network ? 'ALLOWED' : 'not allowed'}`) : null,
      h('div', { class: 'btns' }, btn('Change…', () => editWorkerAccess(t))),
      h('p', { class: 'privacy small muted' }, 'This only stores what the student will be allowed to do: reports, platform tasks and running their own code are not available yet, so no level has an effect today. You start and stop the machine yourself (the Worker tab); a student’s request will never start it.')] });
}

async function editWorkerAccess(t) {
  const a = t.worker_access;
  const b = a.bounds;
  const num = (name, label) => ({ name, label, type: 'number', value: a[name], min: b[name][0], max: b[name][1], required: true });
  const r = await ask({ title: `Compute worker for ${t.slug}`, text: 'What this student may do. Level 3 runs their own code on the machine, so switch it on only for students you trust with that, and keep the internet off unless the project needs it.',
    fields: [{ name: 'level', label: 'Level', options: WORKER_LEVELS, value: a.level }, num('requests_per_day', 'Requests a day (levels 2 and 3)'), num('minutes_per_month', 'Machine minutes a month'), num('job_minutes', 'Longest single job (minutes, level 3)'),
      { name: 'network', label: 'Internet for their jobs (level 3 only)', options: [['false', 'Not allowed'], ['true', 'Allowed']], value: String(a.network) }], confirm: 'Save' });
  if (!r) return;
  const level = Number(r.level);
  act(`/api/admin/tokens/${t.id}/worker-access`, { level, requests_per_day: Number(r.requests_per_day), minutes_per_month: Number(r.minutes_per_month), job_minutes: Number(r.job_minutes), network: level === 3 && r.network === 'true' }, 'Compute worker access saved');
}

// ---------- the traces tab: what happened in each answer, step by step (traces.py) ----------
const LEVEL_NAMES = { metadata: 'Numbers only (no text)', masked: 'Text with personal details hidden', full: 'Full text' };
const VIEW_NAMES = { off: 'Nothing', metadata: 'Numbers only', stored: 'Whatever is stored' };
let traceList = null; // { available, traces } or { unavailable: true }
let traceFilter = { slug: '', day: '' };
let traceOpen = null; // { slug, trace_id, data } the trace being read
let traceBusy = false;
let traceStatus = null; // { available, oldest, days_left, remind } for the expiry banner
let traceDaily = null; // { days: [{ day, answers, refused, errors, tokens, cost_micro, llm_ms }] } the last days, newest first

async function loadTraces(more = false) {
  traceBusy = true;
  try {
    const q = new URLSearchParams();
    if (traceFilter.slug) q.set('slug', traceFilter.slug);
    if (traceFilter.day) q.set('day', traceFilter.day);
    if (more && traceList?.traces?.length) q.set('before', String(traceList.traces.at(-1).started));
    const out = await call(`/api/admin/traces?${q}`);
    traceList = out.available === false ? { unavailable: true } : more ? { ...out, traces: [...traceList.traces, ...out.traces] } : out;
    if (!more) {
      traceStatus = await call('/api/admin/traces/status').catch(() => null);
      traceDaily = await call('/api/admin/traces/summary?days=14').catch(() => null);
    }
  } catch (e) {
    $('error').textContent = e.message;
  }
  traceBusy = false;
}

async function openTrace(slug, id) {
  if (traceOpen?.trace_id === id) { traceOpen = null; render(); return; }
  traceOpen = { slug, trace_id: id, data: null };
  render();
  try { traceOpen.data = await call(`/api/admin/traces/${slug}/${id}`); } catch (e) { traceOpen = null; toast(e.message); }
  render();
}

async function deleteTrace(slug, id) {
  const r = await ask({ title: 'Delete this trace', text: 'The steps of this answer are removed now (they would expire by themselves anyway). This cannot be undone.', confirm: 'Delete', danger: true });
  if (!r) return;
  try { await call(`/api/admin/traces/${slug}/${id}`, { method: 'DELETE' }); traceOpen = null; toast('Trace deleted'); await loadTraces(); } catch (e) { toast(e.message); }
  render();
}

const when = (ms) => new Date(ms).toLocaleString();
const micro = (n) => (n ? `$${(n / 1e6).toFixed(n < 1e4 ? 4 : 3)}` : '$0');

function tracesTab() {
  if (!traceList) return [h('div', { class: 'empty' }, 'Loading traces…')];
  if (traceList.unavailable) return [h('div', { class: 'empty' }, 'Traces are not set up on this server. Install them from your terminal with:  plan --enable traces  and then  apply <id>.')];
  const slugs = h('select', { 'aria-label': 'Student', onchange: (e) => { traceFilter.slug = e.target.value; reloadTraces(); } },
    h('option', { value: '' }, 'All students'), ...tokens.filter((t) => t.traces?.available).map((t) => h('option', { value: t.slug, selected: t.slug === traceFilter.slug }, `${t.student_name} (${t.slug})`)));
  const day = h('input', { type: 'date', value: traceFilter.day, 'aria-label': 'Day', onchange: (e) => { traceFilter.day = e.target.value; reloadTraces(); } });
  const bar = h('div', { class: 'btns', style: 'padding:12px 16px' }, slugs, day, h('button', { class: 'small', onclick: reloadTraces }, 'Refresh'), h('button', { class: 'small', onclick: downloadArchive }, 'Download archive…'), traceBusy ? h('span', { class: 'small muted' }, 'Loading…') : null);
  const banner = traceStatus?.remind ? h('div', { class: 'privacy small', role: 'status', style: 'margin:12px 16px;padding:10px 12px;border:1px solid var(--warn, #c90);border-radius:8px' },
    `Traces from ${traceStatus.oldest.day} will be deleted automatically in ${traceStatus.days_left} day${traceStatus.days_left === 1 ? '' : 's'}. To keep them, use “Download archive…”. The file may contain personal text: keep it safe.`) : null;
  const note = h('p', { class: 'privacy small muted' }, 'One row is one answer. Open it to see each step: the search, the AI call and the app’s own steps. By default no text is stored, only sizes, scores and costs; set how much text is kept for each student on their card. Every trace is deleted automatically after the student’s retention (30, 60 or 90 days).');
  const daily = dailyTable();
  if (!traceList.traces.length) return [banner, daily, bar, note, h('div', { class: 'empty' }, 'No traces for this filter yet. They appear when a student’s app uses the class AI or the books.')];
  const rows = traceList.traces.flatMap((t) => {
    const opened = traceOpen?.trace_id === t.trace_id;
    const row = h('div', { class: 'srow', style: 'grid-template-columns: 150px minmax(110px,1fr) 70px 80px 90px 60px; cursor:pointer', role: 'button', tabindex: 0,
      onclick: () => openTrace(t.slug, t.trace_id), onkeydown: (e) => { if (e.key === 'Enter') openTrace(t.slug, t.trace_id); } },
      h('span', { class: 'small' }, when(t.started)), h('span', { title: t.trace_id }, `${t.slug} · ${t.trace_id.slice(3, 9)}`),
      h('span', { class: 'small muted' }, `${t.spans} step${t.spans === 1 ? '' : 's'}`), h('span', { class: 'small muted' }, `${(t.tokens || 0).toLocaleString()} tok`),
      h('span', { class: 'small muted' }, micro(t.cost_micro)), t.errors ? h('span', { class: 'pill expired' }, 'error') : h('span', { class: 'small muted' }, t.level));
    return opened ? [row, traceDetail()] : [row];
  });
  const more = traceList.traces.length >= 50 ? [h('div', { class: 'btns', style: 'padding:12px 16px' }, h('button', { class: 'small', onclick: async () => { await loadTraces(true); render(); } }, 'Older…'))] : [];
  return [banner, daily, bar, note, ...rows, ...more];
}

const isoDay = (d) => d.toISOString().slice(0, 10);

/** One file for everything: pages are fetched until the range is done, joined as JSON lines and compressed in the browser. */
async function downloadArchive() {
  const today = new Date();
  const r = await ask({ title: 'Download an archive of traces', text: 'One file (JSON lines, compressed) with whole traces for the dates you choose. It holds what was stored: text appears only for students whose setting stores text. Once the file leaves the platform it is yours to protect.',
    fields: [{ name: 'slug', label: 'Student', options: [['', 'All students'], ...tokens.filter((t) => t.traces?.available).map((t) => [t.slug, `${t.student_name} (${t.slug})`])], value: traceFilter.slug },
      { name: 'shelf', label: 'Only answers that searched this shelf (optional, for example science or library/science)', value: '' },
      { name: 'from', label: 'From', type: 'date', value: isoDay(new Date(today - 90 * 86400000)), required: true },
      { name: 'to', label: 'To', type: 'date', value: isoDay(today), required: true }], confirm: 'Download' });
  if (!r) return;
  const lines = [];
  let cursor = null;
  try {
    do {
      const q = new URLSearchParams({ from: r.from, to: r.to });
      if (r.slug) q.set('slug', r.slug);
      if (r.shelf.trim()) q.set('shelf', r.shelf.trim());
      if (cursor) q.set('cursor', JSON.stringify(cursor));
      const page = await call(`/api/admin/traces/export?${q}`);
      for (const rec of page.records) lines.push(JSON.stringify(rec));
      cursor = page.next;
      toast(`Collected ${lines.length} trace${lines.length === 1 ? '' : 's'}…`);
    } while (cursor);
  } catch (e) { toast(e.message); return; }
  if (!lines.length) { toast('No traces in that range.'); return; }
  const raw = new Blob([`${lines.join('\n')}\n`]);
  const zipped = typeof CompressionStream === 'function';
  const blob = zipped ? await new Response(raw.stream().pipeThrough(new CompressionStream('gzip'))).blob() : raw;
  const link = h('a', { href: URL.createObjectURL(blob), download: `traces-${r.from}-to-${r.to}${r.slug ? `-${r.slug}` : ''}.jsonl${zipped ? '.gz' : ''}` });
  document.body.append(link);
  link.click();
  link.remove();
  toast(`Archive downloaded: ${lines.length} trace${lines.length === 1 ? '' : 's'}`);
}

/** The last days at a glance: a rising “books do not say” share or a rising error share is the first sign that something changed. */
function dailyTable() {
  const days = traceDaily?.days || [];
  if (!days.length) return null;
  const pct = (n, of) => (of ? `${Math.round((n / of) * 100)}%` : '–');
  const head = h('div', { class: 'srow small muted', style: 'grid-template-columns: 110px 70px 110px 70px 90px 80px' }, h('span', {}, 'Day'), h('span', {}, 'Answers'), h('span', {}, '“Books do not say”'), h('span', {}, 'Errors'), h('span', {}, 'Tokens'), h('span', {}, 'Cost'));
  const rows = days.map((d) => h('div', { class: 'srow small', style: 'grid-template-columns: 110px 70px 110px 70px 90px 80px' }, h('span', {}, d.day), h('span', {}, d.answers.toLocaleString()), h('span', {}, pct(d.refused, d.answers)),
    h('span', {}, pct(d.errors, d.answers)), h('span', {}, (d.tokens || 0).toLocaleString()), h('span', {}, micro(d.cost_micro))));
  return h('details', { style: 'margin:8px 16px' }, h('summary', { class: 'small' }, `Last ${days.length} day${days.length === 1 ? '' : 's'}: answers, refusals, errors and cost`), head, ...rows,
    h('p', { class: 'small muted' }, 'You get an email (when alert email is set up) if the share of “books do not say” jumps by 15 points over the week before, or 20% of answers have an error. Today’s row is live; earlier days are written each night.'));
}

async function reloadTraces() { traceOpen = null; render(); await loadTraces(); render(); }

function attrLines(attrs) {
  return Object.entries(attrs || {}).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) =>
    h('div', { class: 'small', style: 'display:grid;grid-template-columns:150px 1fr;gap:8px' }, h('span', { class: 'muted' }, k),
      h('span', { style: 'white-space:pre-wrap;overflow-wrap:anywhere' }, typeof v === 'object' ? JSON.stringify(v, null, 1) : String(v))));
}

function traceDetail() {
  const box = h('div', { style: 'padding:8px 16px 16px;background:rgba(127,127,127,.06)' });
  if (!traceOpen.data) { box.append(h('div', { class: 'small muted' }, 'Loading…')); return box; }
  const { trace, spans } = traceOpen.data;
  box.append(h('div', { class: 'small muted' }, `${trace.trace_id} · stored as: ${LEVEL_NAMES[trace.level] || trace.level}`));
  for (const s of spans) {
    box.append(h('section', { style: 'margin:10px 0;border-left:3px solid #888;padding-left:10px' },
      h('div', {}, h('strong', {}, s.name), ' ', h('span', { class: `pill ${s.status === 'error' ? 'expired' : s.status === 'skipped' ? 'warn' : 'active'}` }, s.status),
        h('span', { class: 'small muted' }, ` ${s.source} · ${s.dur_ms} ms · ${new Date(s.ts).toLocaleTimeString()}`)), ...attrLines(s.attrs)));
  }
  box.append(h('div', { class: 'btns' }, h('button', { class: 'small', onclick: () => copyText(JSON.stringify(traceOpen.data, null, 2)).then(() => toast('Copied')) }, 'Copy as JSON'),
    h('button', { class: 'small danger', onclick: () => deleteTrace(trace.slug, trace.trace_id) }, 'Delete…')));
  return box;
}

/** Per-student trace settings (card on the student's panel). */
function tracesFeature(t, live, btn) {
  const x = t.traces;
  return feature({ title: 'Traces', hint: x.enabled ? `${LEVEL_NAMES[x.level]} · kept ${x.retention_days} days` : 'Not recording this student’s answers', on: x.enabled, disabled: !live,
    onToggle: () => act(`/api/admin/tokens/${t.id}/traces`, { enabled: !x.enabled }, x.enabled ? `Traces switched off for ${t.slug}` : `Traces switched on for ${t.slug}`),
    body: [h('div', { class: 'small' }, `Stored: ${LEVEL_NAMES[x.level]}`), h('div', { class: 'small' }, `Kept: ${x.retention_days} days`),
      h('div', { class: 'small' }, `The student sees: ${VIEW_NAMES[x.student_view]} (in their project: npm run traces)`),
      h('div', { class: 'small' }, `At most ${x.daily_limit.toLocaleString()} new traces a day`),
      h('div', { class: 'btns' }, btn('Change…', () => editTraces(t)), btn('See their traces', () => { closeDrawer(); traceFilter = { slug: t.slug, day: '' }; tab = 'traces'; reloadTraces(); }),
        btn('Delete all their traces…', () => clearTraces(t), 'danger')),
      h('p', { class: 'privacy small muted' }, 'Numbers only is the default: what visitors typed is never stored. Choose more text only for projects whose users know their questions are recorded.')] });
}

async function clearTraces(t) {
  const r = await ask({ title: `Delete all traces of ${t.slug}`, text: 'Every recorded answer of this student is removed now (they would expire by themselves anyway). Download an archive first if you want to keep them. This cannot be undone.', confirm: 'Delete all', danger: true });
  if (!r) return;
  let total = 0;
  try {
    for (let more = true; more;) {
      const out = await call(`/api/admin/tokens/${t.id}/traces/clear`, { method: 'POST', body: {} });
      total += out.removed;
      more = out.more;
      toast(`Deleted ${total} trace${total === 1 ? '' : 's'}…`);
    }
  } catch (e) { toast(e.message); return; }
  toast(`Deleted ${total} trace${total === 1 ? '' : 's'} of ${t.slug}`);
  traceList = null;
}

async function editTraces(t) {
  const x = t.traces;
  const r = await ask({ title: `Traces for ${t.slug}`, text: 'How much of each answer is stored. It applies to answers from now on; older traces stay as they were until they expire or you delete them.',
    fields: [{ name: 'level', label: 'Stored', options: Object.entries(LEVEL_NAMES), value: x.level },
      { name: 'retention_days', label: 'Kept for', options: [[30, '30 days'], [60, '60 days'], [90, '90 days']], value: x.retention_days },
      { name: 'student_view', label: 'The student can see', options: Object.entries(VIEW_NAMES), value: x.student_view },
      { name: 'daily_limit', label: 'New traces a day (0 stops recording)', type: 'number', value: x.daily_limit, min: 0, max: 100000, required: true }], confirm: 'Save' });
  if (r) act(`/api/admin/tokens/${t.id}/traces`, { level: r.level, retention_days: Number(r.retention_days), student_view: r.student_view, daily_limit: Number(r.daily_limit) }, 'Trace settings saved');
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

/** Background jobs: work that takes longer than a web request (a chain of AI calls, a report). Off by default; the limits keep the cost bounded. */
function jobsFeature(t, live, btn) {
  const j = t.backend.jobs;
  const hint = !t.backend.enabled ? 'Switch the backend on first'
    : j.enabled ? `Up to ${j.minutes} minute${j.minutes === 1 ? '' : 's'} each · ${j.ready ? 'ready' : 'the student publishes the backend again to set it up'}`
      : 'Lets the student run work that takes longer than a request (a chain of AI calls, a report)';
  return feature({ title: 'Background jobs', hint, on: j.enabled, disabled: !live || !t.backend.enabled, onToggle: () => toggleJobs(t),
    body: [meter('Jobs today', j.used_today, j.per_day, `${j.used_today.toLocaleString()} / ${j.per_day.toLocaleString()} today · ${j.concurrent} at a time`),
      h('div', { class: 'btns' }, btn('Edit limits…', () => editJobs(t)))] });
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
    if (b.jobs) features.push(jobsFeature(t, live, btn));
    if (t.ai.available) {
      const a = t.ai;
      features.push(feature({ title: 'Class AI', hint: a.enabled ? `${a.model}${a.key_active ? '' : ' · paused while the token is not active'}` : 'A capped AI key, no key of their own needed', on: a.enabled, disabled: !live, onToggle: () => toggleAi(t),
        body: [meter('Spend', a.used_usd, a.limit_usd, `${usd(a.used_usd)} / ${usd(a.limit_usd)} ${PER[a.period]}${a.approx_calls ? ` · about ${a.approx_calls.toLocaleString()} answers` : ''}`),
          h('div', { class: 'btns' }, btn('Edit cap…', () => editAi(t)))] }));
    }
    if (t.storage?.available) features.push(storageFeature(t, live, btn));
    if (t.knowledge?.available) features.push(knowledgeFeature(t, live, btn));
    if (t.traces?.available) features.push(tracesFeature(t, live, btn));
    if (t.worker_access?.available) features.push(workerAccessFeature(t, live, btn));
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
      live ? h('div', { class: 'btns' }, btn('Extend…', () => extend(t)), !t.archived ? btn('Take offline…', () => offline(t)) : (t.expires_at > now() ? btn('Bring online', () => online(t)) : null), btn('Revoke…', () => revoke(t), 'danger')) : h('p', { class: 'small muted', style: 'margin:0' }, 'This token is revoked. Features are locked.')),
    h('h3', { class: 'section-title' }, 'Features'),
    ...(features.length ? features : [h('p', { class: 'small muted', style: 'margin:0' }, 'No optional features are switched on for this platform yet. Switch them on in your terminal with:  plan --enable backend,ai,gemini,storage  and then  apply <id>  (see the teacher guide). Each one stays off for every student until you switch it on here.')]));
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

const tokenText = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));

/** Class knowledge: a book shelf the student's app answers from. The teacher sees counts and sizes, never the books. */
function knowledgeFeature(t, live, btn) {
  const k = t.knowledge;
  const u = k.usage || { documents: 0, passages: 0, stored_bytes: 0, searches_used: 0, embed_tokens_month: 0 };
  const l = k.limits;
  return feature({ title: 'Class knowledge', hint: k.enabled ? 'A book shelf the student’s app answers from, plus the class library' : 'Lets the student’s app answer from books (PDF and text), with sources', on: k.enabled, disabled: !live, onToggle: () => toggleKnowledge(t),
    body: [meter('Books', u.documents, l.documents, `${u.documents.toLocaleString()} / ${l.documents.toLocaleString()} books · up to ${l.collections} shelves`),
      meter('Passages', u.passages, l.passages, `${u.passages.toLocaleString()} / ${l.passages.toLocaleString()}`),
      meter('Space', u.stored_bytes, l.stored_mb * MB, `${mb(u.stored_bytes)} / ${l.stored_mb} MB`),
      meter('Searches', u.searches_used, l.searches_limit, `${u.searches_used.toLocaleString()} / ${l.searches_limit.toLocaleString()} ${PER[l.searches_period]}`),
      meter('Adding books this month', u.embed_tokens_month, l.embed_tokens, `${tokenText(u.embed_tokens_month)} / ${tokenText(l.embed_tokens)} tokens`),
      h('div', { class: 'btns' }, btn('Edit limits…', () => editKnowledge(t)), btn('Remove all books…', () => clearKnowledge(t), 'danger')),
      h('p', { class: 'privacy small muted' }, 'You see counts and sizes only. What a student adds to their shelf is private to them. The class library is under the Library tab.')] });
}

// ---------- actions ----------
async function toggleKnowledge(t) {
  const enabled = !t.knowledge.enabled;
  act(`/api/admin/tokens/${t.id}/knowledge`, { enabled },
    enabled ? `Class knowledge switched on for ${t.slug}: up to ${t.knowledge.limits.documents} books. They publish their backend again to receive the key` : `Class knowledge switched off for ${t.slug}: their books are kept but not served`);
}

async function editKnowledge(t) {
  const l = t.knowledge.limits, b = l.bounds;
  const num = (name, label, extra = {}) => ({ name, label, type: 'number', value: l[name], min: b[name][0], max: b[name][1], required: true, ...extra });
  const r = await ask({ title: `Class knowledge limits for ${t.slug}`,
    text: 'Hard limits, checked by the platform before any money is spent. Reading a book costs a few US cents once; searching costs almost nothing.',
    fields: [num('collections', 'Shelves (collections)'), num('documents', 'Books in all'), num('passages', 'Passages in all (a 300-page book is about 500 to 900)'),
      num('stored_mb', 'Space for books and their search data (MB)'), num('upload_mb', 'Largest file (MB)'), num('searches_limit', 'Searches allowed'),
      { name: 'searches_period', label: 'Per', options: [['day', 'day'], ['month', 'month'], ['total', 'total (whole token life)']], value: l.searches_period },
      num('embed_tokens', 'Adding books: embedding tokens per month (1 page is about 500)')], confirm: 'Save' });
  if (!r) return;
  const body = { searches_period: r.searches_period };
  for (const name of ['collections', 'documents', 'passages', 'stored_mb', 'upload_mb', 'searches_limit', 'embed_tokens']) body[name] = Number(r[name]);
  act(`/api/admin/tokens/${t.id}/knowledge`, body, 'Class knowledge limits saved');
}

/** Removes every book the student added. The teacher never sees them; the key, switch and limits stay. Typing the name guards against a slip. */
async function clearKnowledge(t) {
  const r = await ask({ title: `Remove all books of ${t.slug}`,
    text: 'This deletes every shelf and book the student added, with its search data. You will not see them, and it cannot be undone. Their access and limits stay as they are. The class library is not touched.',
    fields: [{ name: 'confirm', label: `Type “${t.slug}” to confirm`, required: true, placeholder: t.slug, maxlength: 40 }], confirm: 'Remove', danger: true });
  if (!r) return;
  if (r.confirm.trim() !== t.slug) { toast('The name did not match, so nothing was removed'); return; }
  try {
    const { cleared } = await call(`/api/admin/tokens/${t.id}/knowledge/clear`, { method: 'POST', body: {} });
    toast(`Removed ${shelves(cleared.removed.length)}`);
    await load();
  } catch (e) {
    toast(e.message);
    $('error').textContent = e.message;
  }
}

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

async function toggleJobs(t) {
  const jobs_enabled = !t.backend.jobs.enabled;
  act(`/api/admin/tokens/${t.id}/backend`, { jobs_enabled }, jobs_enabled ? `Background jobs turned on for ${t.slug}: they start working after the student publishes the backend` : `Background jobs turned off for ${t.slug}`);
}

async function editJobs(t) {
  const j = t.backend.jobs;
  const r = await ask({ title: `Background jobs for ${t.slug}`, text: `Used today: ${j.used_today}. Each job runs the student's own code, so the limits are what keep the cost small: a 5-minute job costs a fraction of a cent.`,
    fields: [{ name: 'jobs_per_day', label: 'Jobs a day', type: 'number', value: j.per_day, min: 0, max: j.max.per_day, required: true },
      { name: 'jobs_concurrent', label: 'At the same time', type: 'number', value: j.concurrent, min: 1, max: j.max.concurrent, required: true },
      { name: 'jobs_minutes', label: `Minutes per job (up to ${j.max.minutes})`, type: 'number', value: j.minutes, min: 1, max: j.max.minutes, required: true }], confirm: 'Save' });
  if (r) act(`/api/admin/tokens/${t.id}/backend`, { jobs_per_day: Number(r.jobs_per_day), jobs_concurrent: Number(r.jobs_concurrent), jobs_minutes: Number(r.jobs_minutes) }, 'Job limits saved');
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

async function online(t) {
  const r = await ask({ title: `Bring ${t.slug} back online?`, text: 'The archived files are served again now. The token keeps its expiry date; extend it as well if you want more time.', confirm: 'Bring online' });
  if (r) act(`/api/admin/tokens/${t.id}/online`, {}, 'Site is back online');
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
$('logout').onclick = () => { session('teacher', null); tokens = requests = []; overview = null; library = null; libraryBooks = {}; traceList = null; traceOpen = null; closeDrawer(); $('created').classList.add('hidden'); show(false); };
$('refresh').onclick = load;
$('new-token').onclick = newToken;
$('search').addEventListener('input', (e) => { query = e.target.value; render(); });
$('tab-students').onclick = () => { tab = 'students'; render(); };
$('tab-requests').onclick = () => { tab = 'requests'; render(); };
$('tab-usage').onclick = () => { tab = 'usage'; render(); };
$('tab-storage').onclick = async () => { tab = 'storage'; render(); await loadOverview(); render(); };
$('tab-library').onclick = async () => { tab = 'library'; render(); await loadLibrary(); render(); };
$('tab-traces').onclick = async () => { tab = 'traces'; render(); await loadTraces(); render(); };
$('tab-worker').onclick = async () => { tab = 'worker'; render(); await loadWorker(); render(); };

setInterval(() => { if (session('teacher') && !document.hidden && !document.querySelector('dialog[open]:not(.drawer)')) load(); }, 60000);
if (session('teacher')) { show(true); load(); } else show(false);
