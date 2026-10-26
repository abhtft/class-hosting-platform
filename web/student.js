import { analyze, LIMITS, skippedFolders } from './check.js';
import { api, ApiError, ask, date, duration, expiryBar, fill, h, now, session, statusPill, toast } from './common.js';
import { renderBackend, stopBackend } from './backend.js';
import { renderGithub, stopGithub } from './github.js';
import { buildZip } from './zip.js';

const $ = (id) => document.getElementById(id);
let token = null; // the latest token view from the server
let picked = null; // {mode: 'folder', result} or {mode: 'zip', file}
let busy = false;
let domainSig = null; // what the domain card shows, so a refresh doesn't wipe a half-typed domain
let domainTimer = null;
let deployMode = null; // what the deploy card currently shows, so a refresh doesn't rebuild it and lose the result message

function show(signedIn) {
  $('login').classList.toggle('hidden', signedIn);
  $('app').classList.toggle('hidden', !signedIn);
}

function signOut(message = '') {
  session('student', null);
  token = picked = deployMode = domainSig = null;
  clearTimeout(domainTimer);
  stopBackend();
  stopGithub();
  show(false);
  $('login-error').textContent = message;
}

/** Student API call. A 401 means the session or token is gone, so return to the sign-in form. */
async function call(path, options) {
  try {
    return await api(path, { role: 'student', ...options });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) signOut(e.message);
    throw e;
  }
}

async function refresh() {
  try {
    ({ token } = await call('/api/student/me'));
    render();
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 401)) fill($('status'), h('div', { class: 'error' }, e.message));
  }
}

// ---------- status card ----------
function render() {
  $('title').textContent = token.student_name || token.slug;
  renderStatus();
  renderDeploy();
  renderDomain();
  renderBackend($('backend'), token, { call, setToken: (next) => { token = next; render(); } });
  renderGithub($('github'), token);
}

function banner() {
  const t = token;
  const regen = t.regen;
  if (regen.state === 'pending') return h('div', { class: 'notice' }, `Regeneration requested on ${date(regen.requested_at)}. Waiting for your teacher to answer.`);
  const notices = [];
  if (t.status === 'offline') notices.push(h('div', { class: 'notice bad' }, 'Your site is offline. Request regeneration to bring it back.'));
  else if (t.status === 'expired') notices.push(h('div', { class: 'notice bad' }, `Your token has expired, so you can't upload. Your site goes offline in ${duration(t.offline_at - now())}. Request regeneration to keep it.`));
  else if (t.status === 'active' && t.can_request_regen) notices.push(h('div', { class: 'notice warn' }, `⚠ Your token expires in ${duration(t.expires_at - now())}. Request regeneration now if you need more time.`));
  if (regen.state === 'denied') notices.push(h('div', { class: 'notice' }, `Your teacher declined your last request${regen.reason ? `: “${regen.reason}”` : '.'}`));
  if (regen.state === 'approved' && now() - regen.resolved_at < 7 * 86400) notices.push(h('div', { class: 'notice ok' }, `Your last request was approved on ${date(regen.resolved_at)}.`));
  return notices;
}

function renderStatus() {
  const t = token;
  fill($('status'),
    h('div', {}, h('div', { class: 'small muted' }, 'Your site'),
      h('a', { class: 'big-url', href: t.url, target: '_blank', rel: 'noopener' }, t.url.replace('https://', '').replace(/\/$/, ''))),
    h('div', {}, statusPill(t), ' ', h('span', { class: 'small muted' }, t.last_deploy_at ? `Last upload ${date(t.last_deploy_at)}` : 'Nothing uploaded yet')),
    expiryBar(t),
    banner(),
    t.can_request_regen ? h('div', {}, h('button', { class: 'primary', onclick: requestRegen }, 'Request regeneration')) : null,
  );
}

async function requestRegen() {
  const r = await ask({ title: 'Request regeneration', text: 'Your teacher will approve or decline. You can add a short note.',
    fields: [{ name: 'note', label: 'Note (optional)', maxlength: 300, placeholder: 'e.g. I need two more weeks for my project' }], confirm: 'Send request' });
  if (!r) return;
  try {
    ({ token } = await call('/api/student/regen-request', { method: 'POST', body: { note: r.note } }));
    toast('Request sent');
    render();
  } catch (e) {
    toast(e.message);
  }
}

// ---------- deploy card ----------
function renderDeploy() {
  const mode = token.can_deploy ? 'form' : token.status;
  if (busy || mode === deployMode) return;
  deployMode = mode;
  const box = $('deploy');
  if (!token.can_deploy) {
    fill(box, h('h2', {}, 'Upload'), h('div', { class: 'notice' }, token.status === 'revoked' ? 'This token was revoked.' : 'Uploads are disabled until your token is renewed.'));
    return;
  }
  const folderInput = h('input', { type: 'file', webkitdirectory: true, multiple: true, class: 'hidden', onchange: (e) => pickFromInput(e.target) });
  const zipInput = h('input', { type: 'file', accept: '.zip,application/zip', class: 'hidden', onchange: (e) => pickFromInput(e.target) });
  const drop = h('div', { class: 'drop', id: 'drop' },
    h('div', { style: 'font-weight:600' }, 'Drag your build folder here'),
    h('div', { class: 'small muted' }, 'or a .zip file'),
    h('div', { class: 'btns' },
      h('button', { onclick: () => folderInput.click() }, 'Choose folder'),
      h('button', { onclick: () => zipInput.click() }, 'Choose .zip')),
    folderInput, zipInput);
  for (const type of ['dragenter', 'dragover']) drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add('over'); });
  for (const type of ['dragleave', 'drop']) drop.addEventListener(type, () => drop.classList.remove('over'));
  drop.addEventListener('dragover', (e) => e.preventDefault());
  drop.addEventListener('drop', onDrop);
  fill(box, h('h2', {}, 'Upload your site'), drop, h('div', { id: 'picked' }), h('div', { id: 'result' }));
  showPicked();
}

function showPicked() {
  const area = $('picked');
  if (!area) return;
  if (!picked) { fill(area); return; }
  if (picked.mode === 'zip') {
    const tooBig = picked.file.size > LIMITS.zipBytes;
    fill(area, h('ul', { class: 'checklist' }, checkItem(tooBig ? 'error' : 'ok', `${picked.file.name} (${(picked.file.size / 1048576).toFixed(1)} MB). The server checks the contents when you upload.${tooBig ? ' It is over the 50 MB limit.' : ''}`)),
      uploadButton(!tooBig));
    return;
  }
  const { result } = picked;
  fill(area, 
    h('ul', { class: 'checklist' }, result.checks.map((c) => checkItem(c.level, c.text))),
    result.ok ? h('div', { class: 'small muted', style: 'margin-top:8px' }, `Will upload ${result.count} files.`) : null,
    uploadButton(result.ok));
}

const ICON = { ok: ['✔', 'Passed'], warn: ['⚠', 'Warning'], error: ['✖', 'Problem'] };
function checkItem(level, text) {
  return h('li', { class: level }, h('span', { class: 'icon', 'aria-label': ICON[level][1] }, ICON[level][0]), h('span', {}, text));
}

function uploadButton(enabled) {
  return h('div', { style: 'margin-top:12px' }, h('button', { class: 'primary', id: 'upload', disabled: !enabled, onclick: upload }, 'Upload and publish'));
}

// ---------- custom domain ----------
const DOMAIN_STEPS = { waiting_dns: 1, issuing_certificate: 2, finishing: 3, live: 4, removing: 0 };

function renderDomain() {
  const box = $('domain');
  const c = token.custom_domain;
  if (!c || !c.available) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const sig = JSON.stringify([c.enabled, c.domain, c.state, token.can_deploy]);
  if (sig === domainSig) return;
  domainSig = sig;
  clearTimeout(domainTimer);

  if (c.domain) return renderDomainProgress(box, c);

  if (!c.enabled) {
    fill(box, h('h2', {}, 'Custom domain'),
      h('label', {}, 'Your domain', h('input', { disabled: true, placeholder: 'www.example.com' })),
      h('div', { class: 'notice warn', role: 'status' }, 'Owner not enabled for you. Please contact site owner.'));
    return;
  }
  const input = h('input', { name: 'domain', placeholder: 'www.example.com', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Your domain' });
  const error = h('div', { class: 'error', role: 'alert' });
  const save = h('button', { class: 'primary', onclick: async () => {
    save.disabled = true;
    error.textContent = '';
    try {
      ({ token } = await call('/api/student/domain', { method: 'PUT', body: { domain: input.value } }));
      renderDomain();
    } catch (e) {
      error.textContent = e.message;
    }
    save.disabled = false;
  } }, 'Save');
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save.click(); });
  fill(box, h('h2', {}, 'Custom domain'),
    h('p', { class: 'muted small', style: 'margin:0' }, 'Use your own address, for example www.yourname.com. Use a subdomain such as www or app: a bare domain cannot use a CNAME at most DNS providers. You will add one DNS record, and HTTPS is set up automatically.'),
    h('label', {}, 'Your domain', input), error, h('div', {}, save));
}

function renderDomainProgress(box, c) {
  const current = DOMAIN_STEPS[c.state] ?? 0;
  const labels = ['Add the DNS record', 'HTTPS certificate', 'Finishing setup', 'Live'];
  const steps = h('ol', { class: 'steps' }, labels.map((label, i) => {
    const n = i + 1;
    const cls = c.state === 'live' || n < current ? 'done' : n === current ? 'current' : '';
    return h('li', { class: cls }, h('span', { class: 'dot' }, cls === 'done' ? '✓' : String(n)), label);
  }));
  const parts = c.domain.split('.');
  const host = parts.length > 2 ? parts.slice(0, -2).join('.') : c.domain;
  const dns = ['waiting_dns', 'issuing_certificate'].includes(c.state) ? h('div', { class: 'stack' },
    h('p', { style: 'margin:0' }, 'At your domain’s DNS provider, add this record:'),
    h('table', { class: 'dns' },
      h('tr', {}, h('th', {}, 'Type'), h('th', {}, 'Name'), h('th', {}, 'Target')),
      h('tr', {}, h('td', {}, 'CNAME'), h('td', {}, host), h('td', {}, c.cname_target))),
    h('p', { class: 'muted small', style: 'margin:0' }, `Some providers want the full name (${c.domain}) instead of just “${host}”. On Cloudflare, set the record to DNS only (grey cloud). This page updates by itself.`)) : null;
  const error = h('div', { class: 'error', role: 'alert' });
  const remove = h('button', { class: 'small danger', onclick: async () => {
    const r = await ask({ title: 'Remove your custom domain?', text: `${c.domain} will stop showing your site. Your ${token.url.replace('https://', '').replace(/\/$/, '')} address keeps working.`, confirm: 'Remove', danger: true });
    if (!r) return;
    try {
      ({ token } = await call('/api/student/domain', { method: 'DELETE' }));
      renderDomain();
    } catch (e) {
      error.textContent = e.message;
    }
  } }, 'Remove domain');
  fill(box, h('h2', {}, 'Custom domain'), h('div', {}, h('div', { class: 'small muted' }, 'Domain'), h('div', { style: 'font-weight:600;word-break:break-all' }, c.domain)),
    steps, h('p', { class: 'muted small', style: 'margin:0', role: 'status' }, c.message), dns, error,
    h('div', { style: 'display:flex;gap:8px;align-items:center;flex-wrap:wrap' },
      c.state === 'live' ? h('a', { href: c.site_url, target: '_blank', rel: 'noopener' }, `Open ${c.site_url}`) : null, remove));
  if (c.state !== 'live') domainTimer = setTimeout(() => pollDomain(error), 8000);
}

/** Setup advances one step per call, so poll until live (or removed). */
async function pollDomain(errorBox) {
  try {
    const before = domainSig;
    ({ token } = await call('/api/student/domain/status'));
    renderDomain(); // a changed state rebuilds the card, which schedules its own next poll
    const c = token.custom_domain;
    if (domainSig === before && c.domain && c.state !== 'live') domainTimer = setTimeout(() => pollDomain(errorBox), 8000);
  } catch (e) {
    if (errorBox?.isConnected) errorBox.textContent = e.message;
    domainTimer = setTimeout(() => pollDomain(errorBox), 15000);
  }
}

// ---------- reading what the student picked ----------
async function pickFromInput(input) {
  const files = [...input.files];
  input.value = '';
  if (files.length === 1 && /\.zip$/i.test(files[0].name)) return setPicked({ mode: 'zip', file: files[0] });
  const items = [];
  for (const file of files) {
    const parts = (file.webkitRelativePath || file.name).split('/');
    const skipAt = parts.findIndex((part) => skippedFolders.includes(part));
    if (skipAt === -1) items.push({ path: parts.join('/'), file });
    else items.push({ path: `${parts.slice(0, skipAt + 1).join('/')}/`, skipped: true });
  }
  await setPicked({ mode: 'folder', result: await analyze(dedupeMarkers(items)) });
}

const dedupeMarkers = (items) => items.filter((item, i) => !item.skipped || items.findIndex((o) => o.path === item.path) === i);

async function onDrop(e) {
  e.preventDefault();
  // Entries must be read synchronously inside the event; reading them is async afterwards.
  const entries = [...e.dataTransfer.items].filter((i) => i.kind === 'file').map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
  const dropped = [...e.dataTransfer.files];
  if (dropped.length === 1 && /\.zip$/i.test(dropped[0].name) && !entries[0]?.isDirectory) return setPicked({ mode: 'zip', file: dropped[0] });
  const items = [];
  for (const entry of entries) await walk(entry, '', items);
  await setPicked({ mode: 'folder', result: await analyze(items) });
}

async function walk(entry, parent, items) {
  const path = parent + entry.name;
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    items.push({ path, file });
    return;
  }
  if (skippedFolders.includes(entry.name)) {
    items.push({ path: `${path}/`, skipped: true });
    return;
  }
  const reader = entry.createReader();
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    for (const child of batch) await walk(child, `${path}/`, items);
  }
}

async function setPicked(value) {
  picked = value;
  if ($('result')) fill($('result'));
  showPicked();
}

// ---------- upload ----------
function put(url, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Upload was rejected (${xhr.status}). If your token just expired, refresh the page.`)));
    xhr.onerror = () => reject(new Error('The upload failed. Check your connection and try again.'));
    xhr.send(blob);
  });
}

async function upload() {
  if (!picked || busy) return;
  busy = true;
  const button = $('upload');
  button.disabled = true;
  const bar = h('span');
  const label = h('div', { class: 'small muted', style: 'margin-top:6px', role: 'status' }, 'Preparing…');
  fill($('result'), h('div', { class: 'progress' }, bar), label);
  const step = (text, fraction) => { label.textContent = text; bar.style.width = `${Math.round(fraction * 100)}%`; };

  try {
    let blob;
    if (picked.mode === 'zip') {
      blob = picked.file;
    } else {
      step('Zipping…', 0);
      blob = await buildZip(picked.result.files, (done, total) => step(`Zipping… ${done}/${total} files`, (done / total) * 0.2));
    }
    if (blob.size > LIMITS.zipBytes) throw new Error(`The zipped site is ${(blob.size / 1048576).toFixed(0)} MB; the limit is 50 MB. Compress large images or remove unused files.`);

    const { uploadUrl } = await call('/api/student/upload-url', { method: 'POST' });
    await put(uploadUrl, blob, (f) => step(`Uploading… ${Math.round(f * 100)}%`, 0.2 + f * 0.7));
    step('Publishing…', 0.95);
    const result = await call('/api/student/publish', { method: 'POST' });
    step('Done', 1);

    picked = null;
    fill($('picked'));
    fill($('result'), 
      h('div', { class: 'notice ok' }, `✔ Published ${result.files} files. `, h('a', { href: result.url, target: '_blank', rel: 'noopener' }, 'Open your site')),
      result.warning ? h('div', { class: 'notice warn', style: 'margin-top:8px' }, result.warning) : null,
      h('div', { class: 'small muted', style: 'margin-top:8px' }, 'Changes can take up to a minute to appear. Hard-refresh (Ctrl+Shift+R) if you still see the old version.'));
    busy = false;
    await refresh();
  } catch (e) {
    busy = false;
    if ($('result')) fill($('result'), h('div', { class: 'notice bad', role: 'alert' }, e.message));
    if ($('upload')) $('upload').disabled = false;
  }
}

// ---------- boot ----------
$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  $('login-error').textContent = '';
  try {
    const data = await api('/api/student/login', { method: 'POST', body: { token: new FormData(e.target).get('token') } });
    session('student', data.session);
    e.target.reset();
    token = data.token;
    show(true);
    render();
  } catch (err) {
    $('login-error').textContent = err.message;
  }
});
$('logout').onclick = () => signOut();
setInterval(() => { if (session('student') && !busy && !document.hidden) refresh(); }, 60000);
if (session('student')) { show(true); refresh(); } else show(false);
