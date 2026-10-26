// The student's "Backend" card: upload, state, usage meter, environment variables, logs, removal.
import { analyzeBackend, BACKEND_LIMITS } from './backend-check.js';
import { ask, date, fill, h, toast, usd } from './common.js';
import { buildZip } from './zip.js';

let sig = null; // structural state; the card is rebuilt only when it changes, so typing is never wiped
let timer = null;
let meterBox = null;
let aiMeterBox = null;
let aiTimer = null;
let picked = null; // {mode: 'zip', file} | {mode: 'folder', result}

export function stopBackend() {
  clearTimeout(timer);
  clearTimeout(aiTimer);
  sig = meterBox = aiMeterBox = picked = null;
}

export function renderBackend(box, token, { call, setToken }) {
  const b = token.backend;
  if (!b || !b.available) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const a = token.ai || { available: false };
  const st = token.storage || { available: false };
  const state = JSON.stringify([b.enabled, b.state, b.error, b.env_names, token.can_deploy, a.available, a.enabled, a.provider, a.model, a.base_url, a.limit_usd, a.period, a.key_active, st.available, st.enabled, st.limits]);
  if (state !== sig) {
    sig = state;
    clearTimeout(timer);
    clearTimeout(aiTimer);
    build(box, token, { call, setToken });
  }
  if (meterBox) fill(meterBox, meter(b));
  if (aiMeterBox) fill(aiMeterBox, aiMeterContent(a));
}

// ---------- usage meter ----------
function meter(b) {
  const u = b.usage;
  if (!u) return null;
  const percent = u.limit > 0 ? Math.min(100, (u.used / u.limit) * 100) : 100;
  const cls = percent >= 100 ? 'bad' : percent >= 80 ? 'warn' : '';
  const period = { day: 'today', month: 'this month', total: 'in total' }[u.period];
  const reset = u.resets_at ? ` · resets ${date(u.resets_at)}` : '';
  return h('div', {},
    h('div', { class: `bar ${cls}`, role: 'progressbar', 'aria-label': 'Runs used', 'aria-valuenow': Math.round(percent), 'aria-valuemin': 0, 'aria-valuemax': 100 },
      h('span', { style: `width:${Math.max(percent, u.used ? 2 : 0)}%` })),
    h('div', { class: 'small muted', style: 'margin-top:4px' }, `${u.used.toLocaleString()} / ${u.limit.toLocaleString()} runs ${period}${reset}`),
    percent >= 100 ? h('div', { class: 'notice bad', style: 'margin-top:8px' }, 'The allowance is used up, so visitors get a “used its allowed runs” message. Ask your teacher for more.')
      : percent >= 80 ? h('div', { class: 'notice warn', style: 'margin-top:8px' }, 'You have used most of your run allowance. Ask your teacher if you need more.') : null);
}

// ---------- the card ----------
function build(box, token, ctx) {
  const b = token.backend;
  meterBox = h('div', {});
  const site = token.url.replace(/\/$/, '');

  if (!b.enabled && b.state === 'none') {
    fill(box, h('h2', {}, 'Backend'),
      h('label', {}, 'Backend', h('input', { disabled: true, placeholder: 'index.mjs or app.py' })),
      h('div', { class: 'notice warn', role: 'status' }, 'Owner not enabled for you. Please contact site owner.'));
    meterBox = null;
    return;
  }
  const off = !b.enabled
    ? h('div', { class: 'notice bad', role: 'status' }, 'Your teacher turned your backend off, so it is not answering requests. Deploys are paused until it is turned on again.') : null;
  const error = h('div', { class: 'error', role: 'alert' });
  const body = [h('h2', {}, 'Backend'), off, meterBox];
  aiMeterBox = null;

  if (b.enabled) {
    body.push(h('p', { class: 'muted small', style: 'margin:0' },
      `Add a function that visitors reach at ${site}/api/… Upload a folder or zip with index.mjs (Node 22) or app.py (Python 3.12) exporting a handler. Limits: ${b.limits.timeout_seconds} s per request, ${b.limits.memory_mb} MB, ${b.limits.zip_mb} MB zip.`));
    if (b.state === 'creating') body.push(h('div', { class: 'notice', role: 'status' }, 'Deploying your function… this usually takes a few seconds.'));
    if (b.state === 'failed') body.push(h('div', { class: 'notice bad', role: 'alert' }, `Deployment failed: ${b.error}`));
    if (b.state === 'ready') {
      body.push(h('div', {}, h('span', { class: 'pill active' }, 'Ready'), ' ',
        h('span', { class: 'small muted' }, `${b.runtime === 'nodejs22.x' ? 'Node 22' : 'Python 3.12'}${b.deployed_at ? ` · deployed ${date(b.deployed_at)}` : ''}`)),
      h('div', { class: 'small' }, 'Try it: ', h('a', { href: `${site}/api/health`, target: '_blank', rel: 'noopener' }, `${site}/api/health`), ' (counts as one run)'));
    }
    if (b.state !== 'creating') body.push(uploader(b, ctx, error));
    if (b.state === 'ready') body.push(envSection(b, ctx, error));
    const ai = aiSection(token, ctx);
    if (ai) body.push(ai);
    const storage = storageSection(token, ctx);
    if (storage) body.push(storage);
    if (b.state === 'ready') body.push(logsSection(ctx));
  }
  if (b.state !== 'none') body.push(removeButton(ctx, error));
  body.push(error);
  fill(box, ...body);
  if (b.state === 'creating') timer = setTimeout(() => poll(ctx), 3000);
}

/** The server advances one step per call, so poll until the function is ready or failed. */
async function poll(ctx) {
  try {
    const before = sig;
    const { token } = await ctx.call('/api/student/backend');
    ctx.setToken(token); // a changed state rebuilds the card, which schedules its own next poll
    if (sig === before && token.backend.state === 'creating') timer = setTimeout(() => poll(ctx), 3000);
  } catch {
    timer = setTimeout(() => poll(ctx), 8000);
  }
}

// ---------- upload ----------
function uploader(b, ctx, errorBox) {
  const checks = h('div', {});
  const status = h('div', { class: 'small muted', role: 'status' });
  const upload = h('button', { class: 'primary', disabled: true }, b.state === 'ready' ? 'Upload new version' : 'Upload and deploy');

  const refresh = () => {
    if (!picked) { fill(checks); upload.disabled = true; return; }
    const list = picked.mode === 'zip'
      ? [{ level: picked.file.size > BACKEND_LIMITS.zipBytes ? 'error' : 'ok', text: `${picked.file.name} (${(picked.file.size / 1048576).toFixed(1)} MB). The server checks the contents when you deploy.` }]
      : picked.result.checks;
    const icon = { ok: '✔', warn: '⚠', error: '✖' };
    fill(checks, h('ul', { class: 'checklist' }, list.map((c) => h('li', { class: c.level }, h('span', { class: 'icon' }, icon[c.level]), h('span', {}, c.text)))));
    upload.disabled = !(picked.mode === 'zip' ? picked.file.size <= BACKEND_LIMITS.zipBytes : picked.result.ok);
  };
  refresh();

  const folder = h('input', { type: 'file', webkitdirectory: true, multiple: true, class: 'hidden', onchange: (e) => pick(e.target, refresh) });
  const zip = h('input', { type: 'file', accept: '.zip,application/zip', class: 'hidden', onchange: (e) => pick(e.target, refresh) });
  upload.addEventListener('click', async () => {
    upload.disabled = true;
    errorBox.textContent = '';
    try {
      status.textContent = 'Preparing…';
      const blob = picked.mode === 'zip' ? picked.file : await buildZip(picked.result.files);
      if (blob.size > BACKEND_LIMITS.zipBytes) throw new Error(`The zipped backend is ${(blob.size / 1048576).toFixed(1)} MB; the limit is ${BACKEND_LIMITS.zipBytes / 1048576} MB. Bundle your dependencies or remove unused files.`);
      const { uploadUrl } = await ctx.call('/api/student/backend/upload-url', { method: 'POST' });
      status.textContent = 'Uploading…';
      const put = await fetch(uploadUrl, { method: 'PUT', body: blob });
      if (!put.ok) throw new Error(`The upload was rejected (${put.status}).`);
      status.textContent = 'Deploying…';
      const { token } = await ctx.call('/api/student/backend/publish', { method: 'POST' });
      picked = null;
      ctx.setToken(token);
    } catch (e) {
      errorBox.textContent = e.message;
      status.textContent = '';
      refresh();
    }
  });
  return h('div', { class: 'stack' },
    h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap' },
      h('button', { onclick: () => folder.click() }, 'Choose backend folder'), h('button', { onclick: () => zip.click() }, 'Choose .zip'), folder, zip),
    checks, h('div', {}, upload), status);
}

async function pick(input, refresh) {
  const files = [...input.files];
  input.value = '';
  if (files.length === 1 && /\.zip$/i.test(files[0].name)) picked = { mode: 'zip', file: files[0] };
  else {
    const items = files.map((file) => ({ path: file.webkitRelativePath || file.name, file }));
    picked = { mode: 'folder', result: analyzeBackend(items) };
  }
  refresh();
}

// ---------- environment variables (write-only) ----------
function envSection(b, ctx, errorBox) {
  const name = h('input', { placeholder: 'NAME', 'aria-label': 'Variable name', autocomplete: 'off', spellcheck: 'false' });
  const value = h('input', { type: 'password', placeholder: 'value (hidden, never shown again)', 'aria-label': 'Variable value', autocomplete: 'off' });
  const save = h('button', { class: 'small primary', onclick: async () => {
    errorBox.textContent = '';
    try {
      const { token } = await ctx.call('/api/student/backend/env', { method: 'PUT', body: { name: name.value, value: value.value } });
      toast('Saved');
      ctx.setToken(token);
    } catch (e) {
      errorBox.textContent = e.message;
    }
  } }, 'Save variable');
  const rows = b.env_names.map((n) => h('div', { style: 'display:flex;gap:8px;align-items:center' }, h('code', {}, n), h('span', { class: 'small muted' }, '••••••'),
    h('button', { class: 'small danger', onclick: async () => {
      try { ctx.setToken((await ctx.call(`/api/student/backend/env/${n}`, { method: 'DELETE' })).token); } catch (e) { errorBox.textContent = e.message; }
    } }, 'Delete')));
  return h('div', { class: 'stack' }, h('h2', { style: 'margin:8px 0 0' }, 'Environment variables'),
    h('p', { class: 'muted small', style: 'margin:0' }, 'Use these for secrets such as database URLs or API keys. Values are write-only: the portal can never show them again.'),
    rows.length ? rows : h('div', { class: 'small muted' }, 'None yet.'),
    h('div', { class: 'form-grid' }, name, value), h('div', {}, save));
}

// ---------- class AI connector ----------
const PER = { day: 'per day', month: 'per month', total: 'in total' };

function aiMeterContent(a) {
  const percent = a.limit_usd > 0 ? Math.min(100, (a.used_usd / a.limit_usd) * 100) : 100;
  const cls = percent >= 100 ? 'bad' : percent >= 80 ? 'warn' : '';
  return h('div', {},
    h('div', { class: `bar ${cls}`, role: 'progressbar', 'aria-label': 'AI allowance used', 'aria-valuenow': Math.round(percent), 'aria-valuemin': 0, 'aria-valuemax': 100 },
      h('span', { style: `width:${Math.max(percent, a.used_usd ? 2 : 0)}%` })),
    h('div', { class: 'small muted', style: 'margin-top:4px' }, `${usd(a.used_usd)} of ${usd(a.limit_usd)} ${PER[a.period]}`,
      a.approx_calls ? ` (about ${a.approx_calls.toLocaleString()} ordinary answers)` : ''),
    percent >= 100 ? h('div', { class: 'notice bad', style: 'margin-top:8px' }, 'Your AI allowance is used up, so AI calls fail until it resets. Catch the error in your code and show a friendly message. Ask your teacher for more.') : null,
    !a.key_active ? h('div', { class: 'notice warn', style: 'margin-top:8px' }, 'Your AI key is paused while your token is not active.') : null);
}

const AI_PYTHON = `import json, os, urllib.request

def ask_ai(prompt):
    request = urllib.request.Request(
        os.environ["OPENAI_BASE_URL"] + "/chat/completions",
        data=json.dumps({"model": os.environ["AI_MODEL"], "max_tokens": 300,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["choices"][0]["message"]["content"]`;

const AI_NODE = `export async function askAi(prompt) {
  const response = await fetch(\`\${process.env.OPENAI_BASE_URL}/chat/completions\`, {
    method: 'POST',
    headers: { Authorization: \`Bearer \${process.env.OPENAI_API_KEY}\`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: process.env.AI_MODEL, max_tokens: 300, messages: [{ role: 'user', content: prompt }] }),
  });
  if (!response.ok) throw new Error(\`AI error \${response.status}\`);
  return (await response.json()).choices[0].message.content;
}`;

function aiSection(token, ctx) {
  const a = token.ai;
  if (!a || !a.available) return null;
  if (!a.enabled) {
    return h('p', { class: 'small muted', style: 'margin:8px 0 0' }, 'Class AI is not switched on for you. If your project needs an AI model, ask your teacher: it comes with a spending cap and needs no key of your own.');
  }
  aiMeterBox = h('div', {});
  fill(aiMeterBox, aiMeterContent(a));
  scheduleAiRefresh(ctx);
  const runtime = token.backend.runtime === 'nodejs22.x' ? 'node' : 'python';
  const example = runtime === 'node' ? AI_NODE : AI_PYTHON;
  return h('div', { class: 'stack' },
    h('h2', { style: 'margin:8px 0 0' }, 'Class AI'),
    h('p', { class: 'muted small', style: 'margin:0' },
      'Your teacher switched on AI for your backend, with a spending cap and no key of your own. These variables are set for your function automatically: ',
      ...a.env_names.flatMap((n, i) => [i ? ', ' : '', h('code', {}, n)]),
      `. The model is ${a.model}${a.provider === 'gemini' ? ' (Google Gemini)' : ''}. They work with any OpenAI-compatible library.`),
    aiMeterBox,
    h('details', {}, h('summary', {}, `Example (${runtime === 'node' ? 'Node' : 'Python'})`), h('pre', { class: 'logs' }, example)),
    h('ul', { class: 'small muted', style: 'margin:0;padding-left:20px;display:grid;gap:2px' },
      h('li', {}, 'Never send the key to the browser or print it in logs. Your page calls your own ', h('code', {}, '/api/…'), ' route, and that route calls the AI.'),
      h('li', {}, `Answers take a few seconds. While AI is on your function may run up to ${a.timeout_seconds} s. Keep max_tokens small.`),
      a.provider === 'gemini'
        ? h('li', {}, 'When the allowance is used up, the call returns an error (HTTP 429, “insufficient_quota”). Answers are not streamed: leave stream out.')
        : h('li', {}, 'When the allowance is used up, the call returns an error (HTTP 403, “Key limit exceeded”). Handle it with a friendly message.'),
      h('li', {}, 'Your prompts are sent to the AI provider. Do not send personal or private data.')));
}

/** Usage is read from OpenRouter by the server (cached 20 s). Ask once a minute while the card is on screen. */
function scheduleAiRefresh(ctx) {
  clearTimeout(aiTimer);
  aiTimer = setTimeout(async () => {
    try {
      const { token } = await ctx.call('/api/student/ai');
      ctx.setToken(token);
    } catch {
      // a failed refresh just keeps the numbers on screen; the next tick tries again
    }
    if (aiMeterBox) scheduleAiRefresh(ctx);
  }, 60000);
}

// ---------- class storage connector ----------
const MB = 1048576;
const STORAGE_PYTHON = `import class_storage as storage     # class_storage.py is in your backend folder (kit)

storage.put("scores", "alice", {"points": 3})            # save a record (any JSON up to 64 KB)
record = storage.get("scores", "alice", default=None)    # read it back
rows = storage.items("scores")                           # [{"id": ..., "value": ...}, ...]
storage.delete("scores", "alice")

link = storage.files.upload_url("photo.png", 20000, "image/png")   # the browser posts the file straight to it
storage.files.list(); storage.files.url("photo.png"); storage.files.delete("photo.png")`;

const STORAGE_NODE = `import * as storage from './class_storage.mjs';   // class_storage.mjs is in your backend folder (kit)

await storage.put('scores', 'alice', { points: 3 });
const record = await storage.get('scores', 'alice', null);
const rows = await storage.items('scores');
await storage.del('scores', 'alice');

const link = await storage.files.uploadUrl('photo.png', 20000, 'image/png');
await storage.files.list(); await storage.files.url('photo.png'); await storage.files.delete('photo.png');`;

function barRow(label, used, limit, text) {
  const percent = limit > 0 ? Math.min(100, (used / limit) * 100) : 100;
  const cls = percent >= 100 ? 'bad' : percent >= 80 ? 'warn' : '';
  return h('div', {},
    h('div', { class: `bar ${cls}`, role: 'progressbar', 'aria-label': label, 'aria-valuenow': Math.round(percent), 'aria-valuemin': 0, 'aria-valuemax': 100 },
      h('span', { style: `width:${Math.max(percent, used ? 2 : 0)}%` })),
    h('div', { class: 'small muted', style: 'margin-top:4px' }, text));
}

function storageSection(token, ctx) {
  const s = token.storage;
  if (!s || !s.available) return null;
  if (!s.enabled) {
    return h('p', { class: 'small muted', style: 'margin:8px 0 0' }, 'Class storage is not switched on for you. If your project needs to save data or files (scores, notes, uploads), ask your teacher: it comes with size limits and needs no database of your own.');
  }
  const l = s.limits;
  const u = s.usage || { data_bytes: 0, items: 0, ops_used: 0 };
  const filesBox = h('div', { class: 'small muted' }, 'Checking your files…');
  ctx.call('/api/student/storage').then((r) => {
    const f = r.files || { count: 0, bytes: 0 };
    fill(filesBox, barRow('File space used', f.bytes, l.files_mb * MB, `Files: ${f.count} of ${l.max_files}, ${(f.bytes / MB).toFixed(1)} of ${l.files_mb} MB`));
  }).catch(() => fill(filesBox, ''));
  const runtime = token.backend.runtime === 'nodejs22.x' ? 'node' : 'python';
  return h('div', { class: 'stack' },
    h('h2', { style: 'margin:8px 0 0' }, 'Class storage'),
    h('p', { class: 'muted small', style: 'margin:0' },
      'Your teacher switched on storage for your backend: a small database and a file area, with no setup. These variables are set for your function automatically: ',
      ...s.env_names.flatMap((n, i) => [i ? ', ' : '', h('code', {}, n)]),
      '. Use the helper in your backend folder; do not call them yourself.'),
    barRow('Database size used', u.data_bytes, l.data_mb * MB, `Data: ${(u.data_bytes / MB).toFixed(2)} of ${l.data_mb} MB (${u.items.toLocaleString()} of ${l.max_items.toLocaleString()} items)`),
    filesBox,
    barRow('Operations used', u.ops_used, l.ops_limit, `${u.ops_used.toLocaleString()} of ${l.ops_limit.toLocaleString()} operations ${PER[l.ops_period]} (every read, write or file call counts)`),
    u.ops_used >= l.ops_limit ? h('div', { class: 'notice bad' }, 'Your storage allowance is used up for now, so storage calls fail until it resets. Handle the error in your code. Ask your teacher for more.') : null,
    h('details', {}, h('summary', {}, `Example (${runtime === 'node' ? 'Node' : 'Python'})`), h('pre', { class: 'logs' }, runtime === 'node' ? STORAGE_NODE : STORAGE_PYTHON)),
    h('ul', { class: 'small muted', style: 'margin:0;padding-left:20px;display:grid;gap:2px' },
      h('li', {}, `A value is up to ${l.max_value_kb} KB; a file is up to ${l.max_file_mb} MB. Everything is private to you.`),
      h('li', {}, 'Do not store secrets or other people’s personal data in records or files.'),
      h('li', {}, 'Anyone who can reach your page can call your routes, so check the input before you save it.'),
      h('li', {}, 'When something is full or the allowance is used up, the helper raises an error with a clear message. Show a friendly one.')));
}

// ---------- logs ----------
function logsSection(ctx) {
  const output = h('pre', { class: 'logs hidden' });
  const button = h('button', { class: 'small', onclick: async () => {
    try {
      const { lines } = await ctx.call('/api/student/backend/logs');
      output.textContent = lines.length ? lines.map((l) => `${new Date(l.t * 1000).toLocaleTimeString()}  ${l.message}`).join('\n') : 'No log lines in the last hour.';
      output.classList.remove('hidden');
    } catch (e) {
      output.textContent = e.message;
      output.classList.remove('hidden');
    }
  } }, 'Show recent logs');
  return h('div', { class: 'stack' }, h('div', {}, button), output);
}

function removeButton(ctx, errorBox) {
  return h('div', {}, h('button', { class: 'small danger', onclick: async () => {
    const r = await ask({ title: 'Remove your backend?', text: 'Your function and its logs are deleted. The run allowance and your teacher’s settings stay.', confirm: 'Remove', danger: true });
    if (!r) return;
    try { ctx.setToken((await ctx.call('/api/student/backend', { method: 'DELETE' })).token); } catch (e) { errorBox.textContent = e.message; }
  } }, 'Remove backend'));
}
