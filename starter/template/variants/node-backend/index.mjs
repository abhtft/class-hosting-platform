// Starter backend (Node 22) for the class platform. Replace the routes with your own.
//
// The contract: export `handler(event, context)`. `event` is an API Gateway HTTP API (payload v2) event:
//   event.rawPath                  the path AFTER /api, e.g. "/health" (the site calls /api/health)
//   event.rawQueryString           text after "?"
//   event.requestContext.http.method
//   event.body, event.isBase64Encoded
// Return { statusCode, headers, body } where body is a string.
//
// Rules (see AGENTS.md): Node 22 built-ins only (nothing is npm-installed on the platform; bundle a package into one file if you
// really need it), at most 10 s per request (25 s while the class AI is on), secrets only in the portal's "Environment variables"
// (never in code or a .env file inside backend/).
//
// Run it on your computer with `npm run dev` (or `npm run dev -- --backend-only`).
import { AiError, ask } from './class_ai.mjs'; // the platform's helper for the class AI: two lines are all you need
import * as storage from './class_storage.mjs'; // the platform's helper for saving data (needs the teacher to switch class storage on)

const json = (statusCode, data) => ({ statusCode, headers: { 'content-type': 'application/json' }, body: JSON.stringify(data) });

function readJson(event) {
  let raw = event.body || '';
  if (event.isBase64Encoded) raw = Buffer.from(raw, 'base64').toString('utf8');
  try {
    const data = raw ? JSON.parse(raw) : {};
    return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
  } catch {
    return {};
  }
}

export const handler = async (event) => {
  const { method } = event.requestContext.http;
  const path = event.rawPath;

  if (method === 'GET' && path === '/health') return json(200, { ok: true });

  if (method === 'GET' && path === '/hello') {
    const name = new URLSearchParams(event.rawQueryString || '').get('name') || 'world';
    return json(200, { message: `Hello, ${name}!` });
  }

  if (method === 'POST' && path === '/echo') return json(200, { youSent: readJson(event) });

  if (method === 'POST' && path === '/ask') {
    const prompt = String(readJson(event).prompt ?? '').trim();
    if (!prompt || prompt.length > 500) return json(400, { error: 'bad_request', message: 'Send a prompt of 1 to 500 characters.' });
    try {
      // THE class AI call. `ask` returns the answer text, or throws AiError with a friendly message.
      const answer = await ask(prompt, { system: 'You are a friendly helper for a school project. Answer in at most three sentences.', maxTokens: 200 });
      return json(200, { answer });
    } catch (error) {
      if (error instanceof AiError) return json(503, { error: error.kind, message: error.message });
      throw error;
    }
  }

  if (path === '/notes' && (method === 'GET' || method === 'POST')) {
    try {
      if (method === 'GET') return json(200, { notes: (await storage.items('notes')).map((row) => row.value).slice(-20) }); // ids start with the time: oldest first
      const text = String(readJson(event).text ?? '').trim();
      if (!text || text.length > 200) return json(400, { error: 'bad_request', message: 'Send a note of 1 to 200 characters.' });
      const note = { id: String(Date.now()).padStart(13, '0'), text };
      await storage.put('notes', note.id, note); // THE storage call: collection, id, any JSON
      return json(201, note);
    } catch (error) {
      if (error instanceof storage.StorageError) return json(503, { error: error.kind, message: error.message }); // not switched on, full, allowance used...
      throw error;
    }
  }

  return json(404, { error: 'not_found', message: 'No such route.' });
};
