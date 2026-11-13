// Run backend/index.mjs (or index.js) on your computer, the way the platform calls it.
//
//   node tools/dev_server.mjs [port]        (normally started for you by `npm run dev`)
//
// It listens on http://127.0.0.1:8788 and answers /api/... by building the same "event" the platform sends and calling
// handler(event, context). Changed backend files are picked up on the next call. Secrets for local runs come from .env.local
// (git-ignored). Without an AI key the class AI helper answers from an offline mock, so nothing is spent.
import { randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const backend = join(root, 'backend');
const port = Number(process.argv[2] || 8788);

// .env.local: NAME=value lines (existing variables win). Nothing from the VITE_* family: that is for the browser.
const envFile = join(root, '.env.local');
if (existsSync(envFile)) {
  for (const line of readFileSync(envFile, 'utf8').split(/\r?\n/)) {
    const match = line.trim().match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (match && !match[1].startsWith('VITE_') && !(match[1] in process.env)) process.env[match[1]] = match[2].replace(/^["']|["']$/g, '');
  }
}

const entry = ['index.mjs', 'index.js'].map((n) => join(backend, n)).find(existsSync);
if (!entry) {
  console.error('No backend/index.mjs found. Add one (see AGENTS.md).');
  process.exit(1);
}

// Re-import the entry whenever any backend file changed (a changing query string defeats Node's module cache).
async function currentHandler() {
  const { readdirSync } = await import('node:fs');
  const stamp = Math.max(...readdirSync(backend).filter((f) => /\.(mjs|js)$/.test(f)).map((f) => statSync(join(backend, f)).mtimeMs));
  const module = await import(`${pathToFileURL(entry).href}?t=${stamp}`);
  return module.handler;
}

// ---- background jobs, the way the platform runs them: POST /api/_jobs/<name> starts one, GET /api/_jobs/<id> reads it (in memory only) ----
const jobs = new Map();
const JOB_NAME = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const JOB_ID = /^[0-9a-f]{32}$/;
const JOB_INPUT_MAX = 64 * 1024;
const JOB_RESULT_MAX = 100 * 1024;
const JOB_MINUTES = 5;
const now = () => Math.floor(Date.now() / 1000);

async function runJob(job) {
  job.status = 'running';
  job.started = now();
  try {
    const result = await (await currentHandler())({ job: { id: job.id, name: job.name, payload: job.payload } }, {});
    if (Buffer.byteLength(JSON.stringify(result ?? null)) > JOB_RESULT_MAX) {
      Object.assign(job, { status: 'failed', error: "The job's result is larger than 100 KB. Save big results with class storage and return a reference." });
    } else Object.assign(job, { status: 'done', result: result ?? null });
  } catch (error) { // the platform shows the page a plain message and keeps the details in your backend logs; here they are in this terminal
    console.error(error);
    Object.assign(job, { status: 'failed', error: 'The job raised an error. The details are printed in this terminal.' });
  }
  job.finished = now();
  if (job.finished - job.started > JOB_MINUTES * 60) console.log(`  (!) job ${job.name} ran longer than ${JOB_MINUTES} minutes: the platform would have stopped it (the teacher sets the limit, up to 15)`);
}

const publicJob = (job, withResult = true) => {
  const out = Object.fromEntries(['id', 'name', 'status', 'created', 'started', 'finished'].filter((k) => job[k] != null).map((k) => [k, job[k]]));
  if (job.status === 'failed') out.error = job.error;
  if (withResult && job.status === 'done') out.result = job.result;
  return out;
};

/** The reserved /api/_jobs routes, with the platform's answers (no per-day limit locally; one job at a time, like the default). */
function jobsRoute(req, url, raw, send) {
  const json = (status, body) => send(status, { 'content-type': 'application/json' }, JSON.stringify(body));
  const rest = url.pathname.slice('/api/_jobs'.length).split('/').filter(Boolean);
  if (req.method === 'GET' && rest.length === 0) return json(200, { jobs: [...jobs.values()].sort((a, b) => b.created - a.created).slice(0, 20).map((j) => publicJob(j, false)) });
  if (req.method === 'GET' && rest.length === 1) {
    if (!JOB_ID.test(rest[0])) return json(400, { error: 'bad_job_id', message: 'That is not a job id.' });
    return jobs.has(rest[0]) ? json(200, publicJob(jobs.get(rest[0]))) : json(404, { error: 'no_such_job', message: 'No such job (restarting npm run dev forgets local jobs).' });
  }
  if (req.method !== 'POST' || rest.length !== 1) return json(405, { error: 'method_not_allowed', message: 'Use POST /api/_jobs/<name> to start a job and GET /api/_jobs/<id> to read it.' });
  if (!JOB_NAME.test(rest[0])) return json(400, { error: 'bad_job_name', message: 'A job name uses lowercase letters, digits, - and _ (up to 40 characters).' });
  if (Buffer.byteLength(raw) > JOB_INPUT_MAX) return json(413, { error: 'payload_too_large', message: "The job's input is limited to 64 KB. Save big inputs with class storage and send a reference." });
  let payload = null;
  try { payload = raw.trim() ? JSON.parse(raw) : null; } catch { return json(400, { error: 'bad_payload', message: "Send the job's input as JSON (or nothing)." }); }
  if ([...jobs.values()].some((j) => j.status === 'queued' || j.status === 'running')) return json(429, { error: 'jobs_busy', message: 'A job is already running. Wait until it finishes, then start the next one.' });
  const job = { id: randomBytes(16).toString('hex'), name: rest[0], payload, status: 'queued', created: now() };
  jobs.set(job.id, job);
  runJob(job);
  return json(202, { id: job.id, name: job.name, status: 'queued' });
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const send = (status, headers, body, cookies = []) => {
    const data = Buffer.from(body);
    res.writeHead(status, { ...headers, ...(cookies.length ? { 'set-cookie': cookies } : {}), 'content-length': data.length }); // like the platform: every entry of "cookies" becomes a Set-Cookie header
    res.end(data);
  };
  if (url.pathname !== '/api' && !url.pathname.startsWith('/api/')) {
    return send(404, { 'content-type': 'application/json' }, JSON.stringify({ error: 'not_found', message: `Only /api/... is served here. Try /api${url.pathname}` }));
  }
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (url.pathname === '/api/_jobs' || url.pathname.startsWith('/api/_jobs/')) return jobsRoute(req, url, chunks.length ? Buffer.concat(chunks).toString('utf8') : '', send);
  const event = {
    rawPath: url.pathname.slice('/api'.length) || '/', rawQueryString: url.search.slice(1),
    headers: Object.fromEntries(Object.entries(req.headers).map(([k, v]) => [k.toLowerCase(), String(v)])),
    body: chunks.length ? Buffer.concat(chunks).toString('utf8') : null, isBase64Encoded: false,
    requestContext: { http: { method: req.method, path: url.pathname, sourceIp: req.socket.remoteAddress } },
  };
  const started = Date.now();
  try {
    let result = await (await currentHandler())(event, {});
    if (result === null || typeof result !== 'object' || !('statusCode' in result)) { // like API Gateway: a plain value becomes a 200 response
      result = { statusCode: 200, headers: { 'content-type': 'application/json' }, body: JSON.stringify(result) };
    }
    const body = typeof result.body === 'string' ? result.body : JSON.stringify(result.body ?? '');
    const data = result.isBase64Encoded ? Buffer.from(body, 'base64') : body;
    if (Date.now() - started > 27000) console.log('  (!) this request took more than 27 s: the platform would stop it');
    console.log(`  ${req.method} ${req.url} -> ${result.statusCode}`);
    send(Number(result.statusCode), result.headers ?? {}, data, Array.isArray(result.cookies) ? result.cookies.map(String) : []);
  } catch (error) { // the platform would answer 502 function_error; here you see why
    console.error(error);
    send(502, { 'content-type': 'application/json' }, JSON.stringify({ error: 'function_error', message: `${error?.name}: ${error?.message}` }));
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`Backend (Node) on http://127.0.0.1:${port}/api/...   AI: ${process.env.OPENAI_API_KEY ? 'your own key' : 'offline mock'}`);
});
