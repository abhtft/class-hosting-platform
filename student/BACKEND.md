# Backend for your site (student guide)

If your teacher turned **Backend** on for you, you can add a small server-side function to your site without running a server. Your frontend calls it on **your own address**, so there is no CORS to configure:

```js
const res = await fetch('/api/hello?name=Sam');   // https://<your-site>/api/hello?name=Sam
const data = await res.json();
```

If you do not see a **Backend** card on your portal page, ask your teacher to enable it. (You can also use a backend you host elsewhere; see the last section.)

## 1. Start from the template

Copy one of the starter folders, `backend-template/node` (Node 22) or `backend-template/python` (Python 3.12). Each has one file with three example routes: `/health`, `/hello` and `/echo`.

## 2. The contract

You write **one function**. Visitors' requests to `/api/...` arrive as an event, and you return a response.

| | Node 22 | Python 3.12 |
|---|---|---|
| File at the top of your folder | `index.mjs` (or `index.js`) | `app.py` |
| What to define | `export const handler = async (event) => {...}` | `def handler(event, context): ...` |

The `event` is an API Gateway HTTP API (payload v2) event. The parts you will use:

| Field | Meaning |
|---|---|
| `event.rawPath` | The path after `/api`, for example `/hello` |
| `event.rawQueryString` | The part after `?`, for example `name=Sam` |
| `event.requestContext.http.method` | `GET`, `POST`, ... |
| `event.headers` | Request headers (lower-case names) |
| `event.body`, `event.isBase64Encoded` | The request body |
| `event.requestContext.http.sourceIp` | The visitor's IP address |

Return `{ statusCode, headers, body }`, with `body` as a string. Returning a plain object or string is allowed too and becomes a `200` response.

## 3. Test locally

```bash
node backend-test.mjs backend-template/node GET "/hello?name=Sam"
node backend-test.mjs backend-template/python POST /echo '{"a":1}'
```

It builds the same event the platform sends and prints the status and body. Secrets can be set for a run with environment variables: `API_KEY=abc node backend-test.mjs ./backend GET /hello`.

## 4. Deploy

1. Open your portal page, find the **Backend** card.
2. **Choose backend folder** (or **Choose .zip**). A checklist shows whether it is valid.
3. **Upload and deploy**. After a few seconds it shows **Ready**.
4. Call `/api/...` from your site.

Uploading again replaces your function (the runtime cannot change: to switch between Node and Python, **Remove backend** first).

## 5. Limits

| Limit | Value |
|---|---|
| Time per request | 10 seconds |
| Memory | 256 MB |
| Upload | 5 MB zipped (50 MB unzipped, 500 files) |
| Request and response body | 1 MB each |
| Environment variables | 20 variables, 4 KB in total |
| Dependencies | Bundle your own: run `npm install --omit=dev` (or use esbuild) before zipping. Nothing is installed for you |

Not supported: WebSockets, background or scheduled jobs, files that persist between requests (each request may run on a fresh copy), and a database (use an external one such as Supabase and keep its URL in an environment variable).

## 6. Secrets: environment variables

Never put passwords or API keys in your code or in a `.env` file (uploads containing `.env` are refused). In the Backend card, **Environment variables**, add a name and a value. Your function reads it with `process.env.NAME` (Node) or `os.environ["NAME"]` (Python). Values are **write-only**: the portal can never show them again, so keep a copy yourself. Names cannot start with `AWS_` or `LAMBDA_`.

## 7. Run allowance

Your teacher gives you a number of **runs** (one run = one call to your function) per day, month or in total. The card shows a meter. When it is used up, visitors get a clear message instead of your function's answer:

| What visitors get | Meaning |
|---|---|
| `429 allowance_exhausted` | Your allowance is used up. Ask your teacher for more |
| `503 backend_off` | Your teacher turned the backend off |
| `429 busy` | Too many requests at once. Wait a second and retry |
| `429 too_many_requests` | One visitor is sending too many requests per minute |
| `403 token_expired`, `503 site_offline` | Your token expired or your site is offline |
| `502 function_error` | Your function crashed (details are in your logs, never shown to visitors) |
| `504 function_timeout` | Your function took longer than 10 seconds |
| `502 bad_response` | Your function returned something that is not a valid response |

Anyone on the internet can call your `/api`, so strangers can use up your runs. Keep your function cheap and do not expose anything sensitive without checking who is calling.

A small helper that retries when the backend is momentarily busy:

```js
async function api(path, options) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(`/api${path}`, options);
    if (res.status !== 429 || (await res.clone().json().catch(() => ({}))).error !== 'busy') return res;
    await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
  }
  return fetch(`/api${path}`, options);
}
```

## 8. Logs

**Show recent logs** in the card lists what your function printed (`console.log` or `print`) in the last hour. Logs are kept for 7 days. Do not print secrets.

## 9. Troubleshooting

| Symptom | What to check |
|---|---|
| "No entry file" | `index.mjs` or `app.py` must be at the top of the folder (a single folder inside the one you choose is fine) |
| "Remove .env" | Delete `.env` files and use environment variables |
| Stuck on "Deploying" | Wait up to a minute; if it fails, the card shows the reason. Upload again |
| Works locally, fails online | Run `backend-test.mjs` again; check the logs for the exact error; remember the 10 second limit |
| `/api/...` returns your site's page | Your teacher has not enabled the backend yet, or it is not deployed |
| Cannot upload | The token must be active, and the Backend must be on |

## Using the class AI (when your teacher switches it on)

You do not need an AI key. When your teacher turns **AI** on for you, your function gets three environment variables automatically: `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `AI_MODEL`. They work with any OpenAI-compatible library, or with a plain request:

```python
import json, os, urllib.request

def ask_ai(prompt):
    request = urllib.request.Request(
        os.environ["OPENAI_BASE_URL"] + "/chat/completions",
        data=json.dumps({"model": os.environ["AI_MODEL"], "max_tokens": 300,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)["choices"][0]["message"]["content"]
```

Rules that keep it working (and safe):
- **Never send the key to the browser or print it in logs.** Your page calls your own `/api/...` route; that route calls the AI.
- You have a **spending cap** (shown on the Backend card). When it is used up the call fails with HTTP 403 "Key limit exceeded": catch it and show a friendly message. The number on the card can be up to half a minute behind.
- Answers take a few seconds. While AI is on, your function may run up to **25 s**. Keep `max_tokens` small.
- Your prompts go to the AI provider: do not send personal or private data.
- While AI is on you cannot overwrite the three variables. If you would rather use your own key, ask your teacher to switch it off and set your own.

## Using a backend you host elsewhere

You can instead call a backend on another service (Vercel, Railway, Supabase, ...). Read its address from your build settings (for example `import.meta.env.VITE_API_URL` in Vite), make sure it is served over `https://`, and allow your site's address (`https://<your-site>`) in its CORS settings.


