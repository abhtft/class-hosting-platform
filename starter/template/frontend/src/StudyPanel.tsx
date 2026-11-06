import { useState } from 'react'
import { ApiError } from './api'
import { askBooks, sendFeedback, type StudyAnswer } from './patterns'

/** Answer from the books, with where the answer came from and a thumbs up or down. Backend: POST /study and POST /feedback (backend/study.py). */
export default function StudyPanel() {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState<StudyAnswer | null>(null)
  const [rated, setRated] = useState<'up' | 'down' | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function ask() {
    setBusy(true)
    setError('')
    setResult(null)
    setRated(null)
    try {
      setResult(await askBooks(question.trim()))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  async function rate(rating: 'up' | 'down') {
    if (!result?.answer_id) return
    setRated(rating)
    try {
      await sendFeedback(result.answer_id, rating)
    } catch {
      setRated(null) // the thumbs did not arrive: let the person try again
    }
  }

  return (
    <section aria-labelledby="study-title">
      <h2 id="study-title">3. Ask the books</h2>
      <p className="muted">Answers come only from the books your teacher added. When they do not say, you are told so. Answers are recorded so your teacher can check their quality.</p>
      <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} maxLength={500} placeholder="What does the book say about…?" aria-label="Your question" />
      <button onClick={ask} disabled={busy || !question.trim()}>{busy ? 'Looking in the books…' : 'Ask the books'}</button>
      {error && <p className="error" role="alert">{error}</p>}
      {result && (
        <div className="stack">
          <p className={`answer ${result.confident ? '' : 'unsure'}`}>{result.answer}</p>
          {result.sources.length > 0 && (
            <ul className="sources" aria-label="Where this came from">
              {result.sources.map((s) => (
                <li key={s.n} className={s.cited ? '' : 'muted'}><strong>[{s.n}]</strong> {s.title}{s.page ? `, page ${s.page}` : ''}{s.section ? ` (${s.section})` : ''}{s.cited ? '' : ' · looked at, not used'}</li>
              ))}
            </ul>
          )}
          {result.trace_id && <p className="muted" title="Give this reference to your teacher if the answer looks wrong">Reference: {result.trace_id}</p>}
          {result.answer_id && result.confident && (
            <div className="row" role="group" aria-label="Was this helpful?">
              <span className="muted">Was this helpful?</span>
              <button className="ghost" onClick={() => rate('up')} disabled={rated !== null} aria-pressed={rated === 'up'}>👍</button>
              <button className="ghost" onClick={() => rate('down')} disabled={rated !== null} aria-pressed={rated === 'down'}>👎</button>
              {rated && <span className="ok">Thank you.</span>}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
