# API reference

> **Companion document.** The source of truth for this release is [TECHNICAL_SPECIFICATION.md](TECHNICAL_SPECIFICATION.md); if the two disagree, the specification wins. This document goes deeper on every HTTP route and its request and response shapes.

Every HTTP route of the portal, the legacy CLI, and the student-backend gateway. Companion to [END_TO_END.md](END_TO_END.md).

Conventions:
- All bodies are JSON unless stated. Errors are `{"error": "<message or code>"}`.
- Times are Unix epoch **seconds** (UTC).
- Portal routes are served at **`https://deploy.example.edu`** (CloudFront forwards `/api/*` to the portal API). They need a session in the header `Authorization: Bearer <session>`.
- **Teacher** and **student** sessions are different: a student session never works on `/api/admin/...`, and vice versa. Teacher sessions last 8 hours, student sessions 12 hours.

---

## 1. Teacher API

### `POST /api/teacher/login`
Body `{ "username": "...", "password": "..." }` → `200 { "session": "...", "expires_at": 1792600000 }`.
Errors: `401` wrong credentials; `429` five failures from one address in 15 minutes; `503` teacher account not set up.

### `GET /api/admin/tokens`
→ `200 { "tokens": [ <token view>, ... ] }` (newest first). Token view: see section 4.

### `POST /api/admin/tokens`
Create a token. Body:

| Field | Type | Notes |
|---|---|---|
| `days` | integer 1 to 365 | required |
| `student_name` | string, ≤ 60 | optional |
| `slug` | string | optional site name: 1 to 30 of `a-z 0-9 -`, not reserved, not used |

→ `201 { "token": "mds_…", "item": <token view> }`. **The plaintext token is returned only here.**
Errors: `400` invalid field or reserved name; `409` the explicit slug is taken.

### `POST /api/admin/tokens/{id}/revoke`
→ `200 { "token": <view> }`. The student can no longer sign in or deploy; the live site is unchanged.

### `POST /api/admin/tokens/{id}/extend`
Body `{ "days": 1..365 }`. Adds to the current expiry (or counts from now if already lapsed) and restores an archived site. → `200 { "token": <view> }`. `409` if the token is revoked.

### `POST /api/admin/tokens/{id}/offline`
Archives the site now (moved to `archive/<slug>/`). → `200 { "token": <view> }`.

### `GET /api/admin/requests`
Pending regeneration requests, oldest first → `200 { "requests": [ <view>, ... ] }`.

### `POST /api/admin/tokens/{id}/approve`
Body `{ "days": 1..365 }`: new duration counted from now; restores an archived site. → `200 { "token": <view> }`. `409` if nothing is pending.

### `POST /api/admin/tokens/{id}/deny`
Body `{ "reason": "..." }` (optional, ≤ 300). → `200 { "token": <view> }`. `409` if nothing is pending.

### `POST /api/admin/tokens/{id}/domain-access`
Body `{ "enabled": true|false }`: allow or stop a student *adding* a custom domain (a live domain keeps working). → `200 { "token": <view> }`; `400` if `enabled` is not a boolean.

### `POST /api/admin/tokens/{id}/backend`
Body, any of:

| Field | Type | Notes |
|---|---|---|
| `enabled` | boolean | ON serves requests and allows deploys; OFF answers `503 backend_off` within about 10 s and pauses deploys. The first time it is switched on, the allowance defaults to 5,000 per month |
| `runs_limit` | integer 0 to 1,000,000 | runs allowed per period; 0 means none |
| `runs_period` | `"day"` \| `"month"` \| `"total"` | UTC periods; `total` never resets |

→ `200 { "token": <view> }`. `400` invalid value; `503` backends are not set up on this server.

### `POST /api/admin/tokens/{id}/ai`
Class AI connector (needs `AiEnabled`; see END_TO_END.md section 3.3a). Body, any of:

| Field | Type | Notes |
|---|---|---|
| `enabled` | boolean | ON creates (or re-enables) the student's capped OpenRouter key and sets `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_MODEL` on the function with a 25 s timeout; OFF disables the key first, then removes them |
| `limit_usd` | number 0.01 to 20 | spending cap; the first time on it defaults to `AiDefaultLimitUsd` |
| `period` | `"day"`, `"month"` or `"total"` | when the cap resets |
| `model` | string | an OpenRouter model id; empty means the class default |

An empty body refreshes the usage numbers. → `200 { "token": <view> }`. `400` invalid value; `409` the student's function is updating (repeat the same request); `502` OpenRouter refused; `503` the connector is not set up.

### `POST /api/admin/tokens/{id}/storage`
Class storage connector (needs `StorageEnabled`; see [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md)). Body, any of:

| Field | Type | Notes |
|---|---|---|
| `enabled` | boolean | ON makes the student's storage key once (kept afterwards) and sets `CLASS_STORAGE_URL` and `CLASS_STORAGE_KEY` on the function; OFF removes them and stops all storage access at once (the data is kept) |
| `data_mb` | integer 1 to 100 | database size cap; default 5 |
| `files_mb` | integer 1 to 500 | file space cap; default 50 |
| `ops_limit` | integer 0 to 1,000,000 | operations allowed per `ops_period`; default 10,000 |
| `ops_period` | `"day"`, `"month"` or `"total"` | when the allowance resets; default `"day"` |

→ `200 { "token": <view> }`. `400` invalid value; `409` the student's function is updating (repeat the same request); `503` the connector is not set up. An empty body changes nothing.

### `POST /api/admin/tokens/{id}/storage/clear`
Deletes what a student stored. Body: `{ "scope": "data" | "files" | "all" }`. Data means every record of every collection the student ever used; files means everything in their file folder. The byte and item counters go back to zero; the student's key, the switch and the caps stay. Works for a revoked student too (that is the cleanup case). The teacher sees no content: the answer carries only counts.
→ `200 { "cleared": { "items": 40, "files": 3 }, "token": <view> }`. `400` bad scope; `404` unknown token; `503` the connector is not set up. Files are removed from a versioned bucket, so the owner can still restore one for 30 days.

### `GET /api/admin/storage`
Class storage at a glance. → `200 { "students": [...], "totals": {...} }` where each student is `{ id, slug, student_name, status, enabled, limits, usage: {data_bytes, items, ops_used, resets_at}, files: {count, bytes}, holds_data, inactive }` and `totals` is `{ students, enabled, inactive_with_data, data_bytes, items, files, files_bytes }`. Only students who have the switch on, ever had a key, or still hold data are listed. `inactive` is true when a student is revoked, offline or switched off but still holds data. No value, file name or key is ever returned. `503` when the connector is not set up.

`{id}` is the token's `id` (a 64-character hex string) from the token view.

---

## 2. Student API

### `POST /api/student/login`
Body `{ "token": "mds_…" }` → `200 { "session": "...", "token": <view> }`. A token in its read-only or offline period still signs in (so the student can request regeneration); a revoked or unknown token gets `401`.

### `GET /api/student/me`
→ `200 { "token": <view> }`.

### `POST /api/student/regen-request`
Body `{ "note": "..." }` (optional, ≤ 300). Allowed in the last 3 days before expiry and after it. → `200 { "token": <view> }`. `403` too early; `409` a request is already pending.

### Publishing the frontend

| Route | Body | Response |
|---|---|---|
| `POST /api/student/upload-url` | none | `200 { "uploadUrl": "<presigned S3 PUT URL>", "maxBytes": 52428800 }` |
| `POST /api/student/publish` | none (reads the uploaded zip) | `200 { "url": "https://<slug>.example.edu/", "files": 12, "removed": 1, "warning"?: "..." }` |

Flow: `upload-url`, then `PUT` the zip bytes to `uploadUrl` (no extra headers needed; the browser may send `Content-Type`), then `publish`. Requires an **active** token (`403` otherwise). `400` for a bad zip: unsafe paths, more than 2,000 files, more than 200 MB unzipped, or no `index.html` at the top.

### Custom domain

| Route | Body | Notes |
|---|---|---|
| `PUT /api/student/domain` | `{ "domain": "www.example.com" }` | requires **Domain: on**, an active token, no existing domain. `403` with `Owner not enabled for you. Please contact site owner.`; `409` already have one / domain used by another site; `400` invalid (bare domain, `example.edu` name, AWS address). Returns `200 { "token": <view> }` |
| `GET /api/student/domain/status` | none | advances setup by one step and returns `{ "token": <view> }`; poll about every 8 seconds |
| `DELETE /api/student/domain` | none | starts removal; returns the view (state `removing`, then none) |

`custom_domain.state` in the view: `none` → `waiting_dns` → `issuing_certificate` → `finishing` → `live` (and `removing`). `cname_target` is the record to create.

### Class AI usage

`GET /api/student/ai` returns `200 { "token": <view> }` with fresh usage (read from OpenRouter, cached 20 s). The student page asks once a minute.

### Class storage usage

`GET /api/student/storage` returns `200 { "token": <view>, "files": { "count": 3, "bytes": 123456 } | null }`. `files` is counted by listing the student's folder (so it is not part of every view) and is `null` when storage is off for the student.

### Backend

| Route | Body | Response |
|---|---|---|
| `GET /api/student/backend` | none | advances `creating` → `ready`/`failed`; `200 { "token": <view> }` |
| `POST /api/student/backend/upload-url` | none | `200 { "uploadUrl": "...", "maxBytes": 5242880 }` |
| `POST /api/student/backend/publish` | none | validates the uploaded zip and creates or updates `sd-<slug>`; `200 { "token": <view> }` (state `creating`) |
| `PUT /api/student/backend/env` | `{ "name": "API_KEY", "value": "..." }` | `200 { "token": <view> }`; the view lists **names only** |
| `DELETE /api/student/backend/env/{NAME}` | none | `200 { "token": <view> }` |
| `GET /api/student/backend/logs` | none | `200 { "lines": [ { "t": 1792600000, "message": "..." } ] }` (last hour, up to 50) |
| `DELETE /api/student/backend` | none | removes the function and its log group; the teacher's settings stay |

Errors: `403` backend not enabled (`Owner not enabled for you…`) or token not active; `400` invalid zip (no entry file, both `index.mjs` and `app.py`, `.env` file, unsafe path or symlink, too many or too large files, or a switch of runtime: remove the backend first) or invalid env name/value (not `AWS_*`, `LAMBDA_*`, `_*`, `TZ`; value ≤ 1,024 characters; at most 20 variables and 4,096 bytes); `409` function not ready, or still updating; `502` AWS refused the deployment (message shown); `503` backends not set up.

---

## 3. Legacy CLI API (permanent keys)

Served directly by the Deploy API (not through the portal). Auth header `x-deploy-key: <name>.<secret>`.

| Route | Response |
|---|---|
| `POST /upload-url` | `200 { "uploadUrl": "...", "maxBytes": ... }` |
| `POST /publish` | `200 { "url": "...", "files": n, "removed": n }` |

`401` for a missing or wrong key. Keys are managed with `node admin.mjs add|remove|list`.

---

## 4. The token view

Returned by almost every route above (`token`, `item`, or inside lists).

```json
{
  "id": "<64 hex chars>",           "slug": "alice",            "student_name": "Alice",   "hint": "NdgQ",
  "url": "https://alice.example.edu/",
  "status": "active",               // active | expired | offline | revoked
  "created_at": 1792000000,         "expires_at": 1794592000,   "offline_at": 1794851200,  "warn_at": 1794332800,
  "last_deploy_at": 1792500000,     "can_deploy": true,
  "archived": false,                "archived_at": null,
  "regen": { "state": "none" },     // none | pending | approved | denied (+ requested_at, note, resolved_at, days, reason)
  "can_request_regen": false,
  "custom_domain": {
    "available": true, "enabled": false, "domain": "", "state": "none",
    "message": "", "cname_target": "<id>.cloudfront.net", "site_url": ""
  },
  "backend": {
    "available": true, "enabled": true,
    "state": "ready",               // none | creating | ready | failed
    "runtime": "python3.12", "deployed_at": 1792500100, "env_names": ["OPENROUTER_API_KEY"], "error": "",
    "limits": { "memory_mb": 256, "timeout_seconds": 10, "zip_mb": 5 },
    "usage": { "period": "month", "used": 12, "limit": 5000, "resets_at": 1793491200 }
  }
}
```
`ai` (always present; `{ "available": false }` when the connector is not deployed):
```json
"ai": { "available": true, "enabled": true, "model": "anthropic/claude-haiku-5.5", "base_url": "https://openrouter.ai/api/v1",
        "limit_usd": 0.25, "period": "month", "used_usd": 0.0123, "remaining_usd": 0.2377, "approx_calls": 714,
        "checked_at": 1792600000, "env_names": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "AI_MODEL"], "timeout_seconds": 25, "key_active": true }
```
The key itself is never returned. `approx_calls` is shown only for the default model. While the connector is on, the three `env_names` are reserved: `PUT`/`DELETE` of them on the backend env routes answer `400`.

`storage` (always present; `{ "available": false }` when the connector is not deployed):
```json
"storage": { "available": true, "enabled": true,
             "limits": { "data_mb": 5, "files_mb": 50, "ops_limit": 10000, "ops_period": "day",
                         "max_value_kb": 64, "max_items": 5000, "max_file_mb": 5, "max_files": 100 },
             "env_names": ["CLASS_STORAGE_URL", "CLASS_STORAGE_KEY"],
             "usage": { "data_bytes": 1234, "items": 12, "ops_used": 87, "resets_at": 1792684800 } }
```
The storage key is never returned. While the connector is on, the two `env_names` are reserved like the AI names.

`status` meaning: `active` until `expires_at`; `expired` (read-only) until `offline_at`; then `offline`. An archived site counts as `offline`. When the backend feature is not deployed, `backend` is just `{ "available": false }`.

---

## 5. Student backend (gateway)

The student's function is reached at `https://<site>/api/<route>` (the site is `<slug>.example.edu` or the student's custom domain). The `/api` prefix is removed before the function sees the request.

### What the function receives
An API Gateway HTTP API **payload v2** event:

| Field | Meaning |
|---|---|
| `rawPath` | the path after `/api`, e.g. `/analyse` (`/` for `/api/`) |
| `rawQueryString` | text after `?` |
| `requestContext.http.method` | `GET`, `POST`, … |
| `requestContext.http.sourceIp` | the visitor's real IP |
| `headers` | lower-case request headers (the platform's own `x-origin-secret` and `x-viewer-ip` are removed) |
| `body`, `isBase64Encoded` | the request body as a string (≤ 1 MB) |

### What the function returns
`{ "statusCode": 200, "headers": { ... }, "body": "<string>", "isBase64Encoded": false }`. The body must be a **string**; a response of at most 1 MB is passed through with `x-content-type-options: nosniff` and `cache-control: no-store` added if missing. As with API Gateway, returning a plain value (object or string) without `statusCode` becomes a `200` response, and a non-string `body` is serialized as JSON. Hop-by-hop headers (`content-length`, `transfer-encoding`, …) are dropped.

### Responses produced by the gateway
(Always JSON `{ "error": "<code>", "message": "..." }`.)

| Status | `error` | When | Extra |
|---|---|---|---|
| 403 | `forbidden` | the request did not come through CloudFront (bad origin secret), or the token is revoked | |
| 404 | `not_found` | the path is not `/b/<slug>/...` | |
| 404 | `no_backend` | no backend deployed, still creating, or removed | |
| 413 | `payload_too_large` | request body over 1 MB | |
| 403 | `token_expired` | the student's token has expired | |
| 503 | `site_offline` | the site is offline/archived | |
| 503 | `backend_off` | the teacher turned the backend off | |
| 429 | `too_many_requests` | one visitor over 120 requests per minute | `retry-after: 30` |
| 429 | `busy` | in-flight cap reached (3 in total, 2 per student) or AWS throttled | `retry-after: 1` |
| 429 | `allowance_exhausted` | the run allowance is used up | `resets_at` (epoch or `null`), `retry-after` |
| 504 | `function_timeout` | the function ran longer than 10 s | |
| 502 | `function_error` | the function crashed (details stay in the student's own logs) | |
| 502 | `bad_response` | invalid status code, headers, or not JSON | |
| 502 | `response_too_large` | response over 1 MB | |
| 502 | `backend_unavailable` | AWS could not invoke the function | |

Anything else (status codes, bodies, headers) comes from the student's own function, for example `400` validation messages.

### Order of checks (a run is counted only at step 9)
1 origin secret → 2 path and slug → 3 body size → 4 backend exists and is ready → 5 token lifecycle → 6 teacher toggle → 7 per-visitor limit → 8 in-flight slot → 9 **run allowance (atomic, exact)** → invoke → release the slot.

### Settings cache
The gateway re-reads a student's settings at most every 10 seconds, and a site **without** a ready backend for at most 2 seconds, so a freshly deployed backend is served within about 2 seconds and a toggle change takes effect within about 10 seconds.

---

## 6. Storage API (called by student functions, not by browsers)

Base address: `CLASS_STORAGE_URL` (an API Gateway address of its own). Every request carries `Authorization: Bearer <CLASS_STORAGE_KEY>` (`cs_<slug>.<secret>`). The student helpers `class_storage.py` and `class_storage.mjs` make these calls; the rules and limits are in [STORAGE_CONNECTOR.md](STORAGE_CONNECTOR.md) and section 4.14 of the specification. Every call, successful or not, counts as one operation.

| Route | Body or query | Answer |
|---|---|---|
| `PUT /v1/data/{collection}/{id}` | `{"value": <any JSON, up to 64 KB>}` | `200 {"id", "bytes"}` |
| `GET /v1/data/{collection}/{id}` | | `200 {"id", "value", "updated_at"}`; `404 not_found` |
| `DELETE /v1/data/{collection}/{id}` | | `200 {"deleted": true\|false}` |
| `GET /v1/data/{collection}` | `?limit=1..100&after=<id>` | `200 {"items": [{"id", "value", "updated_at"}], "next": "<id>"\|null}`, ordered by id, at most about 400 KB per answer |
| `POST /v1/files/upload-url` | `{"name", "size", "content_type"?}` | `200 {"url", "fields", "max_bytes", "expires_in"}`: a presigned S3 POST (send every field, then the file as the **last** field, as `multipart/form-data`) |
| `GET /v1/files` | | `200 {"files": [{"name", "size", "updated_at"}]}` |
| `GET /v1/files/{name}` | | `200 {"url", "expires_in"}` (a 5-minute download link); `404 not_found` |
| `DELETE /v1/files/{name}` | | `200 {"deleted": true}` |

Names: collection `[a-z0-9_-]{1,40}`; id `[A-Za-z0-9._-]{1,128}`; file name `[A-Za-z0-9._-]{1,100}` (no folders).

Errors are JSON `{"error": "<code>", "message": "..."}`:

| HTTP | `error` | Meaning |
|---|---|---|
| 401 | `bad_key` | missing, malformed or wrong key |
| 403 | `token_inactive` | the student's token is expired, revoked or offline |
| 403 | `not_enabled` | the teacher has switched storage off for this student |
| 429 | `allowance_used` | the operations allowance is used up until it resets |
| 413 | `quota_exceeded` | the database size, the item count, the file space or the file count is full |
| 413 | `too_large` | a value, a request body or a file is over its size limit |
| 400 | `bad_request` | a name, id, size or content type is not allowed, or the body is not valid |
| 404 | `not_found` | no such item or file, or an unknown route |
