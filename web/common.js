// Helpers shared by the teacher and student pages. No dependencies, no build step.
const DAY = 86400;

/** Build a DOM element safely (text is never parsed as HTML). h('div', {class: 'x'}, 'text', childEl) */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) if (child != null && child !== false) el.append(child);
  return el;
}

/** Dollars for small AI amounts: $0.25, $0.0123, $12.50 (never rounds a tiny amount to $0.00). */
export function usd(n) {
  const value = Number(n) || 0;
  if (value === 0) return '$0.00';
  const text = value >= 1 ? value.toFixed(2) : value.toFixed(4).replace(/0+$/, '').replace(/(\.\d)$/, '$10');
  return `$${text === '0.' || text === '0.0' ? '0.0001' : text}`;
}

/** Replace an element's children, skipping null/false so conditional children don't render as text. */
export const fill = (el, ...kids) => el.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));

export function session(role, value) {
  const key = `session-${role}`;
  try {
    if (value === null) sessionStorage.removeItem(key);
    else if (value !== undefined) sessionStorage.setItem(key, value);
    else return sessionStorage.getItem(key);
  } catch {
    // storage unavailable (private window): the session just lasts until reload
    session.memory ??= {};
    if (value === null) delete session.memory[key];
    else if (value !== undefined) session.memory[key] = value;
    else return session.memory[key] ?? null;
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Call the portal API. role picks which stored session is sent as the Bearer token. */
export async function api(path, { method = 'GET', body, role } = {}) {
  const headers = { 'content-type': 'application/json' };
  const token = role && session(role);
  if (token) headers.authorization = `Bearer ${token}`;
  let response;
  try {
    response = await fetch((window.PORTAL_API || '') + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, data.error || `Request failed (${response.status})`);
  return data;
}

export function duration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / DAY), hr = Math.floor((s % DAY) / 3600), m = Math.floor((s % 3600) / 60);
  if (d >= 1) return hr ? `${d}d ${hr}h` : `${d}d`;
  if (hr >= 1) return `${hr}h ${m}m`;
  return `${Math.max(m, 1)}m`;
}

export const date = (epoch) => new Date(epoch * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
export const now = () => Math.floor(Date.now() / 1000);

/** active tokens inside the warning window show as "warn"; everything else keeps the server status. */
export function displayState(token) {
  return token.status === 'active' && now() >= token.warn_at ? 'warn' : token.status;
}

export function statusPill(token) {
  const state = displayState(token);
  const label = { active: 'Active', warn: 'Expiring soon', expired: 'Expired (read-only)', offline: 'Offline', revoked: 'Revoked' }[state];
  return h('span', { class: `pill ${state}` }, token.archived && state !== 'revoked' ? 'Offline (archived)' : label);
}

/** One-line description of where the token is in its life. */
export function lifeText(token) {
  const t = now();
  if (token.status === 'revoked') return 'Revoked';
  if (token.status === 'active') return `Expires in ${duration(token.expires_at - t)}`;
  if (token.status === 'expired') return `Site goes offline in ${duration(token.offline_at - t)}`;
  return `Offline since ${date(token.archived_at || token.offline_at)}`;
}

export function expiryBar(token) {
  const t = now();
  const total = Math.max(token.expires_at - token.created_at, 1);
  const left = Math.max(token.expires_at - t, 0);
  const state = displayState(token);
  const cls = { active: '', warn: 'warn', expired: 'bad', revoked: 'bad', offline: 'off' }[state];
  const graceLeft = Math.max(token.offline_at - t, 0) / Math.max(token.offline_at - token.expires_at, 1);
  const percent = token.status === 'active' ? (left / total) * 100 : token.status === 'expired' ? graceLeft * 100 : 0;
  const bar = h('div', { class: `bar ${cls}`, role: 'progressbar', 'aria-label': lifeText(token), 'aria-valuenow': Math.round(percent), 'aria-valuemin': 0, 'aria-valuemax': 100 },
    h('span', { style: `width:${Math.min(100, Math.max(percent, token.status === 'active' ? 2 : 0))}%` }));
  return h('div', {}, bar, h('div', { class: 'small muted', style: 'margin-top:4px' }, lifeText(token)));
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = h('textarea', { style: 'position:fixed;opacity:0' });
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  toast('Copied');
}

export function toast(message) {
  const el = h('div', { class: 'toast', role: 'status' }, message);
  // inside the open dialog (if any): a dialog is shown above everything else on the page, so a toast outside it would be hidden
  const host = [...document.querySelectorAll('dialog[open]')].pop() || document.body;
  host.append(el);
  setTimeout(() => el.remove(), 2500);
}

/**
 * Modal prompt. fields: [{name, label, type, value, min, max, options, required, placeholder}].
 * A text field may follow a select: `follows: {field, by: {<select value>: {value, suggestions}}}` fills in that choice's default (while the box still
 * holds the default of the previous choice) and offers its suggestions as a list, so the box stays writable.
 * Resolves to {name: value} on confirm, or null on cancel. With no fields it is a plain confirm.
 */
export function ask({ title, text, fields = [], confirm = 'OK', danger = false }) {
  return new Promise((resolve) => {
    const inputs = fields.map((f) => {
      const control = f.options
        ? h('select', { name: f.name }, f.options.map(([value, label]) => h('option', { value, selected: String(value) === String(f.value) }, label)))
        : h('input', { name: f.name, type: f.type || 'text', value: f.value ?? '', min: f.min, max: f.max, step: f.step, required: f.required, placeholder: f.placeholder, maxlength: f.maxlength });
      return h('label', {}, f.label, control);
    });
    const follow = (f) => {
      const source = () => dialog.querySelector(`[name="${f.follows.field}"]`);
      const box = () => dialog.querySelector(`[name="${f.name}"]`);
      const list = h('datalist', { id: `list-${f.name}` });
      const apply = (previous) => {
        const now = f.follows.by[source().value] || { value: '', suggestions: [] };
        const before = f.follows.by[previous];
        if (!box().value || (before && box().value === before.value)) box().value = now.value;
        list.replaceChildren(...now.suggestions.map((v) => h('option', { value: v })));
      };
      box().setAttribute('list', list.id);
      box().after(list);
      let last = source().value;
      source().addEventListener('change', () => { apply(last); last = source().value; });
      apply(last);
    };
    const submit = h('button', { class: danger ? 'danger' : 'primary', type: 'submit' }, confirm);
    const dialog = h('dialog', {}, h('form', { method: 'dialog' },
      h('h2', {}, title), text && h('p', { class: 'muted', style: 'margin:0' }, text), inputs,
      h('div', { class: 'btns' }, h('button', { type: 'button', onclick: () => dialog.close() }, 'Cancel'), submit)));
    let result = null;
    dialog.querySelector('form').addEventListener('submit', () => {
      result = Object.fromEntries(fields.map((f) => [f.name, dialog.querySelector(`[name="${f.name}"]`).value]));
    });
    dialog.addEventListener('close', () => { dialog.remove(); resolve(result); });
    document.body.append(dialog);
    fields.filter((f) => f.follows).forEach(follow);
    dialog.showModal();
  });
}
