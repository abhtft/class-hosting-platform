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
  jobs_off: 'Background jobs are not switched on for you yet. Ask your teacher.',
  jobs_not_ready: 'Background jobs are being set up. Publish your backend again, then retry.',
  jobs_busy: 'A job is already running. Wait until it finishes.',
  jobs_allowance_exhausted: 'You have used your background jobs for today.',
  bad_job_name: 'A job name uses lowercase letters, digits, - and _.',
  payload_too_large: 'The input is too big for a job. Save big inputs with class storage.',
  no_such_job: 'That job was not found (finished jobs are kept for one day).',
}

// "busy" and "too_many_requests" are answered by the platform BEFORE your backend runs, so asking again is always safe. A class using the AI at the same
// moment can hit them; waiting a moment and asking again (up to three times, longer each time, with a little randomness) is what a polite client does.
const RETRY_CODES = new Set(['busy', 'too_many_requests'])
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  for (let attempt = 0; ; attempt++) {
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
      if (response.status === 429 && RETRY_CODES.has(code) && attempt < 3) {
        const asked = Number(response.headers.get('retry-after')) * 1000
        await sleep(Math.min(Number.isFinite(asked) && asked > 0 ? asked : 400 * 2 ** attempt, 4000) + Math.random() * 300)
        continue
      }
      throw new ApiError(response.status, code, FRIENDLY[code] ?? String(body?.message ?? `Something went wrong (${response.status}).`))
    }
    return body as T
  }
}

/** A background job as the platform reports it. */
export type Job<R = unknown> = { id: string; name: string; status: 'queued' | 'running' | 'done' | 'failed'; result?: R; error?: string }

/**
 * Start a BACKGROUND JOB and wait for it: work that takes longer than a request (a chain of AI calls, a report). The job's code is the `run_job`
 * function in backend/app.py (or `runJob` in backend/index.mjs); what it returns is the result here. The teacher must switch jobs on for you.
 * Resolves with the result; throws an ApiError (with a message safe to show) when the job is refused, fails, or takes longer than `timeoutMs`.
 * `onStatus` is called on every poll, for a progress message ("queued", "running").
 */
export async function runJob<R = unknown>(
  name: string,
  input?: unknown,
  options: { everyMs?: number; timeoutMs?: number; onStatus?: (job: Job<R>) => void } = {},
): Promise<R> {
  const { id } = await api<{ id: string }>(`/_jobs/${name}`, { method: 'POST', body: JSON.stringify(input ?? {}) })
  const started = Date.now()
  for (;;) {
    const job = await api<Job<R>>(`/_jobs/${id}`)
    options.onStatus?.(job)
    if (job.status === 'done') return job.result as R
    if (job.status === 'failed') throw new ApiError(0, 'job_failed', job.error ?? 'The job failed.')
    if (Date.now() - started > (options.timeoutMs ?? 15 * 60 * 1000)) throw new ApiError(0, 'job_timeout', 'The job is taking very long. Try again later.')
    await new Promise((resolve) => setTimeout(resolve, options.everyMs ?? 2000))
  }
}
