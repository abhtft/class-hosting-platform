/**
 * The page's side of the AI patterns (see PATTERNS.md): answer from books, thumbs feedback, and an agent that waits for a person's approval.
 * The backend routes are in backend/study.py (or study.mjs). These are your files: copy what you need, delete the rest.
 */
import { api } from './api'

export type Source = { n: number; cited: boolean; title: string; page: number | null; section: string; collection: string }
export type StudyAnswer = { answer: string; sources: Source[]; confident: boolean; cached: boolean; answer_id?: string }

export type TraceStep = {
  step: number
  kind: 'tool' | 'refused' | 'invalid' | 'final' | 'approval' | 'declined' | 'budget' | 'error' | 'retry'
  ms: number
  tool?: string
  args?: Record<string, unknown> | null
  result?: string
  reason?: string
  failed?: boolean
  approved?: boolean
}
export type Pending = { tool: string; args: Record<string, unknown> }
export type AgentResult = {
  status: 'done' | 'needs_approval' | 'budget' | 'error'
  answer: string
  trace: TraceStep[]
  steps: number
  pending?: Pending | null
  run_id?: string
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

/** Ask the books. `confident: false` means they do not seem to answer: show the answer ("The books do not say.") as it is, never fill the gap yourself. */
export const askBooks = (question: string) => api<StudyAnswer>('/study', post({ question }))

/** Thumbs from a visitor. `item` is the `answer_id` of what they saw. */
export const sendFeedback = (item: string, rating: 'up' | 'down', comment = '') => api<{ saved: boolean }>('/feedback', post({ item, rating, comment }))

/** Start an agent run. A result with status "needs_approval" has `pending` (what it wants to do) and a `run_id` to answer with `answerRun`. */
export const runAgent = (goal: string) => api<AgentResult>('/agent', post({ goal }))

/** The person's answer to a waiting run. A run can be answered once. */
export const answerRun = (run_id: string, approve: boolean) => api<AgentResult>('/agent/approve', post({ run_id, approve }))
