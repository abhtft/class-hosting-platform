// Run backend/index.mjs (or index.js) on your computer, the way the platform calls it.
//
//   node tools/dev_server.mjs [port]        (normally started for you by `npm run dev`)
//
// It listens on http://127.0.0.1:8788 and answers /api/... by building the same "event" the platform sends and calling
// handler(event, context). Changed backend files are picked up on the next call. Secrets for local runs come from .env.local
// (git-ignored). Without an AI key the class AI helper answers from an offline mock, so nothing is spent.
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

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const send = (status, headers, body) => {
    const data = Buffer.from(body);
    res.writeHead(status, { ...headers, 'content-length': data.length });
    res.end(data);
  };
  if (url.pathname !== '/api' && !url.pathname.startsWith('/api/')) {
    return send(404, { 'content-type': 'application/json' }, JSON.stringify({ error: 'not_found', message: `Only /api/... is served here. Try /api${url.pathname}` }));
  }
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
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
    if (Date.now() - started > 10000) console.log('  (!) this request took more than 10 s: the platform would stop it');
    console.log(`  ${req.method} ${req.url} -> ${result.statusCode}`);
    send(Number(result.statusCode), result.headers ?? {}, data);
  } catch (error) { // the platform would answer 502 function_error; here you see why
    console.error(error);
    send(502, { 'content-type': 'application/json' }, JSON.stringify({ error: 'function_error', message: `${error?.name}: ${error?.message}` }));
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`Backend (Node) on http://127.0.0.1:${port}/api/...   AI: ${process.env.OPENAI_API_KEY ? 'your own key' : 'offline mock'}`);
});
