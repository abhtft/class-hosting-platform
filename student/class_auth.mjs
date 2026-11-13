// class_auth: "Sign in with Google" for the visitors of your app. No packages needed.
//
//   import * as classAuth from './class_auth.mjs';
//
//   export async function handler(event) {
//     const answer = await classAuth.handle(event);      // serves /auth/login, /auth/callback, /auth/logout and /auth/me for you
//     if (answer) return answer;
//     const user = classAuth.currentUser(event);         // { id, email, name } or null
//     ...
//     try { const user = classAuth.requireUser(event); } // the same, or it throws SignInRequired
//     catch (stop) { if (stop instanceof classAuth.SignInRequired) return stop.response; throw stop; }
//   }
//
// The page sends people to /api/auth/login (the kit's frontend/src/auth.ts does it) and asks /api/auth/me who is signed in.
//
// Rules that keep it safe (see AGENTS.md, "Sign-in"):
//   * never write your own login form, password check or token: this helper is the only sign-in;
//   * the user comes from currentUser(event), never from something the page sent;
//   * save each visitor's data under user.id, and check it on every read and write.
//
// How it works: the platform runs the Google sign-in (Amazon Cognito). This helper sends the visitor there, receives them back with a one-time
// code, exchanges the code with the platform over HTTPS, checks the answer (who issued it, for which app, not expired) and then sets ITS OWN
// session cookie: signed, not readable by page scripts, sent only over HTTPS. The page never sees a token.
//
// Three modes: on the platform with sign-in ON (the real Google sign-in); on your own computer (npm run dev: a pretend visitor is signed in at
// once); on the platform with sign-in OFF (/auth/login answers 503 and currentUser() is null). Turn it on with `npm run signin -- on`.
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

export const SESSION_COOKIE = 'class_session';
export const PENDING_COOKIE = 'class_auth_tx';
const PENDING_SECONDS = 600;
const LOCAL_KEY = 'local-development-only';

/** Thrown by requireUser() when nobody is signed in. `.response` is a ready 401 answer. */
export class SignInRequired extends Error {
  constructor() {
    super('Please sign in first.');
    this.response = json(401, { error: 'sign_in_required', message: 'Please sign in first.' });
  }
}

/** Tests replace these two. */
export const hooks = {
  now: () => Math.floor(Date.now() / 1000),
  post: async (url, fields) => {
    const response = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(fields).toString(), signal: AbortSignal.timeout(8000) });
    return response.json();
  },
};

// ---------- the mode ----------
const env = (name) => process.env[name] || '';

/** "on" (the platform's Google sign-in), "local" (your computer: a pretend visitor) or "off". */
export function mode() {
  if (env('CLASS_AUTH_HOST') && env('CLASS_AUTH_CLIENT_ID') && env('CLASS_AUTH_KEY')) return 'on';
  return env('AWS_LAMBDA_FUNCTION_NAME') ? 'off' : 'local';
}
const key = () => (mode() === 'on' ? env('CLASS_AUTH_KEY') : LOCAL_KEY);
function days() {
  const value = Number.parseInt(env('CLASS_AUTH_DAYS') || '7', 10);
  return Number.isInteger(value) ? Math.max(1, Math.min(30, value)) : 7;
}

// ---------- signed values (cookies) ----------
const b64 = (data) => Buffer.from(data).toString('base64url');
const mac = (body) => createHmac('sha256', key()).update(body).digest('base64url');
const same = (a, b) => { const x = Buffer.from(String(a)); const y = Buffer.from(String(b)); return x.length === y.length && timingSafeEqual(x, y); };

function sign(payload) {
  const body = b64(JSON.stringify(payload));
  return `${body}.${mac(body)}`;
}

/** The payload of a value we signed, or null when it is missing, changed or expired. */
function unsign(value) {
  const parts = String(value ?? '').split('.');
  if (parts.length !== 2 || !same(mac(parts[0]), parts[1])) return null;
  let payload;
  try { payload = JSON.parse(Buffer.from(parts[0], 'base64url').toString('utf8')); } catch { return null; }
  if (!payload || typeof payload !== 'object' || !Number.isInteger(payload.exp) || payload.exp <= hooks.now()) return null;
  return payload;
}

function cookies(event) {
  const found = {};
  const parts = [...(event.cookies ?? [])];
  const header = event.headers?.cookie;
  if (header) parts.push(...header.split(';'));
  for (const part of parts) {
    const text = String(part).trim();
    const at = text.indexOf('=');
    const name = at < 0 ? text : text.slice(0, at);
    if (name && !(name in found)) found[name] = at < 0 ? '' : text.slice(at + 1);
  }
  return found;
}

const cookie = (name, value, seconds, path = '/') => `${name}=${value}; Path=${path}; Max-Age=${seconds}; HttpOnly; SameSite=Lax${mode() === 'local' ? '' : '; Secure'}`;

// ---------- answers ----------
function json(status, data, list) {
  return { statusCode: status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }, body: JSON.stringify(data), ...(list ? { cookies: list } : {}) };
}
function redirect(location, list) {
  return { statusCode: 302, headers: { location, 'cache-control': 'no-store' }, body: '', ...(list ? { cookies: list } : {}) };
}

/** Only a path inside this app may be the place to return to (never another site). */
export function safeNext(value) {
  const text = String(value || '/');
  // eslint-disable-next-line no-control-regex
  if (!text.startsWith('/') || text.startsWith('//') || text.includes('\\') || /[\u0000-\u001f]/.test(text) || text.length > 500) return '/';
  return text;
}
function homePath(nextPath) {
  let prefix = '';
  if (mode() === 'on') { try { prefix = new URL(env('CLASS_AUTH_HOME')).pathname.replace(/\/+$/, ''); } catch { prefix = ''; } }
  return prefix + nextPath;
}
const query = (event) => Object.fromEntries(new URLSearchParams(event.rawQueryString || ''));

// ---------- the user ----------
/** The signed-in visitor as { id, email, name }, or null. Always use this: never trust a user id the page sends. */
export function currentUser(event) {
  if (mode() === 'off') return null;
  const session = unsign(cookies(event)[SESSION_COOKIE] ?? '');
  if (!session || !session.id) return null;
  return { id: String(session.id), email: String(session.email ?? ''), name: String(session.name ?? '') };
}

/** The signed-in visitor, or throw SignInRequired (return its .response). */
export function requireUser(event) {
  const user = currentUser(event);
  if (!user) throw new SignInRequired();
  return user;
}

function sessionCookie(user) {
  const seconds = days() * 86400;
  return cookie(SESSION_COOKIE, sign({ id: user.id, email: user.email, name: user.name, exp: hooks.now() + seconds }), seconds);
}

// ---------- the Google sign-in (through the platform) ----------
function login(event) {
  const nextPath = safeNext(query(event).next);
  if (mode() === 'off') return json(503, { error: 'sign_in_off', message: 'Sign-in is not switched on for this app. Its owner can turn it on with: npm run signin -- on' });
  if (mode() === 'local') { // your own computer: a pretend visitor, so you can build without Google
    return redirect(nextPath, [sessionCookie({ id: 'local-visitor', email: ['local.visitor', 'example.test'].join('@'), name: 'Local Visitor' })]);
  }
  const state = randomBytes(18).toString('base64url');
  const verifier = randomBytes(36).toString('base64url');
  const pending = sign({ s: state, v: verifier, n: nextPath, exp: hooks.now() + PENDING_SECONDS });
  const params = new URLSearchParams({
    response_type: 'code', client_id: env('CLASS_AUTH_CLIENT_ID'), redirect_uri: env('CLASS_AUTH_REDIRECT'), scope: 'openid email profile',
    state, code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256', identity_provider: 'Google',
  });
  return redirect(`${env('CLASS_AUTH_HOST')}/oauth2/authorize?${params}`, [cookie(PENDING_COOKIE, pending, PENDING_SECONDS)]);
}

async function callback(event) {
  const clear = cookie(PENDING_COOKIE, '', 0);
  const failed = redirect(`${homePath('/')}?signin=failed`, [clear]);
  if (mode() !== 'on') return failed;
  const q = query(event);
  const pending = unsign(cookies(event)[PENDING_COOKIE] ?? '');
  if (!pending || !q.code || !same(pending.s ?? '', q.state ?? '')) return failed;
  let claims;
  try {
    const answer = await hooks.post(`${env('CLASS_AUTH_HOST')}/oauth2/token`, {
      grant_type: 'authorization_code', client_id: env('CLASS_AUTH_CLIENT_ID'), code: q.code, redirect_uri: env('CLASS_AUTH_REDIRECT'), code_verifier: pending.v,
    });
    // The token comes straight from the platform's sign-in service over HTTPS, in answer to our one-time code and proof key, so the
    // connection itself proves who sent it (OpenID Connect allows this instead of checking the token's signature). Its claims are checked.
    claims = JSON.parse(Buffer.from(String(answer.id_token).split('.')[1], 'base64url').toString('utf8'));
  } catch {
    return failed; // any failure signs nobody in; nothing about it is shown or logged
  }
  if (!claims || typeof claims !== 'object' || claims.iss !== env('CLASS_AUTH_ISSUER') || claims.aud !== env('CLASS_AUTH_CLIENT_ID') || claims.token_use !== 'id'
    || typeof claims.exp !== 'number' || claims.exp <= hooks.now() || !claims.sub) return failed;
  const email = String(claims.email ?? '');
  const user = { id: String(claims.sub), email, name: String(claims.name || email.split('@')[0]) };
  return redirect(homePath(safeNext(pending.n)), [sessionCookie(user), clear]);
}

/** Serve the sign-in routes. Resolves to an answer for /auth/..., or null for every other path (then your own routes run). */
export async function handle(event) {
  const path = event.rawPath || '';
  if (!path.startsWith('/auth/')) return null;
  const method = event.requestContext?.http?.method ?? 'GET';
  if (path === '/auth/me' && method === 'GET') return json(200, { user: currentUser(event), sign_in: mode() });
  if (path === '/auth/login' && method === 'GET') return login(event);
  if (path === '/auth/callback' && method === 'GET') return callback(event);
  if (path === '/auth/logout' && method === 'POST') return json(200, { ok: true }, [cookie(SESSION_COOKIE, '', 0)]);
  return json(404, { error: 'not_found', message: 'No such sign-in route.' });
}
