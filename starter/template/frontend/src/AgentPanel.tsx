import { useState } from 'react'
import { ApiError } from './api'
import { answerRun, runAgent, type AgentResult, type Pending, type TraceStep } from './patterns'

/**
 * An agent that works in steps and asks YOU before it changes anything. Backend: POST /agent and POST /agent/approve (backend/study.py).
 *
 * The important part is the approval card: it shows the EXACT text the agent wants to use, word for word, next to what the agent read to decide, with
 * Approve and Decline buttons. A model can pick the wrong line of a page, so a person reads it before anything is saved. Keep that whenever you copy this.
 */

const LABEL: Record<TraceStep['kind'], string> = {
  tool: 'Used a tool',
  refused: 'Refused a step',
  invalid: 'Reply it could not use',
  final: 'Gave the answer',
  approval: 'Asked for approval',
  declined: 'You said no',
  budget: 'Ran out of steps or time',
  error: 'Stopped by a problem',
  retry: 'Tried again',
}

function Trace({ steps }: { steps: TraceStep[] }) {
  return (
    <ol className="trace" aria-label="What the agent did">
      {steps.map((s, i) => (
        <li key={i} className={s.failed || s.kind === 'refused' || s.kind === 'error' ? 'bad' : ''}>
          <strong>{LABEL[s.kind] ?? s.kind}</strong>
          {s.tool && <> · <code>{s.tool}</code></>}
          {s.approved && <> · approved</>}
          {s.args && Object.keys(s.args).length > 0 && <div className="muted small">{Object.entries(s.args).map(([k, v]) => `${k}: ${String(v)}`).join(' · ')}</div>}
          {s.reason && <div className="muted small">{s.reason}</div>}
          {s.result && (
            <details>
              <summary className="small">What it got back</summary>
              <pre className="small">{s.result}</pre>
            </details>
          )}
        </li>
      ))}
    </ol>
  )
}

function ApprovalCard({ pending, steps, busy, onAnswer }: { pending: Pending; steps: TraceStep[]; busy: boolean; onAnswer: (approve: boolean) => void }) {
  const evidence = steps.filter((s) => s.kind === 'tool' && s.result)
  return (
    <div className="approval" role="group" aria-labelledby="approval-title">
      <h3 id="approval-title">The agent wants to use <code>{pending.tool}</code></h3>
      <p className="muted">Nothing has been saved yet. This is exactly what it will use:</p>
      <dl>
        {Object.entries(pending.args).map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd><blockquote>{String(v)}</blockquote></dd>
          </div>
        ))}
      </dl>
      {evidence.length > 0 && (
        <details>
          <summary>What it read to decide (check it against this)</summary>
          {evidence.map((s, i) => <pre key={i} className="small">{s.result}</pre>)}
        </details>
      )}
      <div className="row">
        <button onClick={() => onAnswer(true)} disabled={busy}>Approve and save</button>
        <button className="ghost" onClick={() => onAnswer(false)} disabled={busy}>Decline</button>
      </div>
    </div>
  )
}

export default function AgentPanel() {
  const [goal, setGoal] = useState('')
  const [result, setResult] = useState<AgentResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function start() {
    setBusy(true)
    setError('')
    setResult(null)
    try {
      setResult(await runAgent(goal.trim()))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  async function answer(approve: boolean) {
    if (!result?.run_id) return
    setBusy(true)
    setError('')
    try {
      setResult(await answerRun(result.run_id, approve)) // the answer carries the whole trace, including what happened before the pause
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="agent-title">
      <h2 id="agent-title">4. An agent that asks first</h2>
      <p className="muted">It looks things up in the books on its own. Before it saves anything, it asks you. Try: “Find who the chief advisor is, then save a note with their name.”</p>
      <textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={2} maxLength={300} placeholder="What should the agent do?" aria-label="The agent's goal" />
      <button onClick={start} disabled={busy || !goal.trim()}>{busy && !result ? 'Working…' : 'Start'}</button>
      {error && <p className="error" role="alert">{error}</p>}
      {result && (
        <div className="stack" aria-live="polite">
          {result.status === 'needs_approval' && result.pending
            ? <ApprovalCard pending={result.pending} steps={result.trace} busy={busy} onAnswer={answer} />
            : <p className={`answer ${result.status === 'done' ? '' : 'unsure'}`}>{result.answer}</p>}
          <details open={result.status !== 'needs_approval'}>
            <summary>What the agent did ({result.trace.length} steps)</summary>
            <Trace steps={result.trace} />
          </details>
        </div>
      )}
    </section>
  )
}
