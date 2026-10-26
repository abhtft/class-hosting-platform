/**
 * The ONLY place where the browser talks to the backend.
 *
 * Always use a RELATIVE address (`/api/...`). On the platform the backend is a function behind the same address as the site, and
 * during development `vite.config.ts` forwards /api to the local backend, so there is no CORS and nothing to configure.
 *
 * Never put a secret (an AI key, a token) in this app: everything in the browser is public. The backend holds secrets and calls the
 * AI for you.
 */

/** A failed call. `status` is 0 when the server could not be reached. `message` is safe to show to the user. */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

// What the platform's gateway and the starter backend answer, in plain words.
const FRIENDLY: Record<string, string> = {
  not_enabled: 'The class AI is not switched on for you yet. Ask your teacher.',
  cap_reached: 'Your AI allowance is used up. Ask your teacher for more.',
  disabled: 'Your AI key is switched off.',
  busy: 'It is busy right now. Please try again in a moment.',
  backend_off: 'Your backend is switched off by your teacher.',
  allowance_exhausted: 'Your backend has used all of its allowed runs.',
  too_many_requests: 'Too many requests. Please slow down.',
  token_expired: 'Your token has expired, so the backend is paused.',
  site_offline: 'This site is offline.',
  no_backend: 'There is no backend deployed yet.',
  function_timeout: 'The backend took too long to answer.',
  function_error: 'The backend had a problem.',
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, 'network', 'Could not reach the server. Check your connection.')
  }
  const text = await response.text()
  let body: any = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (!response.ok) {
    const code = String(body?.error ?? 'error')
    throw new ApiError(response.status, code, FRIENDLY[code] ?? String(body?.message ?? `Something went wrong (${response.status}).`))
  }
  return body as T
}
