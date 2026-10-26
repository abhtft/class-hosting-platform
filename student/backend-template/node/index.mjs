// Starter backend (Node 22). Deploy this folder from the portal's "Backend" card, then call it from your site:
//   fetch('/api/hello')        -> this function, at the path /hello
//
// The contract: export `handler(event)`. `event` is an API Gateway HTTP API (payload v2) event:
//   event.rawPath, event.rawQueryString, event.requestContext.http.method, event.headers, event.body, event.isBase64Encoded
// Return { statusCode, headers, body } (or just a value: a plain object becomes a 200 JSON response).
// Secrets (database URLs, API keys) go in "Environment variables" in the portal: read them from process.env.

const json = (statusCode, data) => ({ statusCode, headers: { 'content-type': 'application/json' }, body: JSON.stringify(data) });

export const handler = async (event) => {
  const { method } = event.requestContext.http;
  const path = event.rawPath;

  if (path === '/health') return json(200, { ok: true });

  if (path === '/hello' && method === 'GET') {
    const name = new URLSearchParams(event.rawQueryString).get('name') || 'world';
    console.log('hello requested for', name); // shows up under "Show recent logs" in the portal
    return json(200, { message: `Hello, ${name}!` });
  }

  if (path === '/echo' && method === 'POST') {
    let data;
    try {
      data = JSON.parse(event.body || '{}');
    } catch {
      return json(400, { error: 'Send JSON.' });
    }
    return json(200, { youSent: data });
  }

  return json(404, { error: 'Not found' });
};
