import { useState } from 'react'
import { api, ApiError } from './api'

/**
 * Starter page: replace this with your own app. It shows the two things every project needs:
 *  - calling your backend:  api('/health')          -> GET  /api/health
 *  - calling the class AI:  api('/ask', {...})      -> POST /api/ask   (the BACKEND talks to the AI; the key never reaches the browser)
 */
export default function App() {
  const [status, setStatus] = useState('')
  const [prompt, setPrompt] = useState('Give me one idea for a school project.')
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function checkBackend() {
    setError('')
    try {
      const result = await api<{ ok: boolean }>('/health')
      setStatus(result.ok ? 'The backend is working.' : 'The backend answered, but not as expected.')
    } catch (e) {
      setStatus('')
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    }
  }

  async function askAi() {
    setBusy(true)
    setError('')
    setAnswer('')
    try {
      const result = await api<{ answer: string }>('/ask', { method: 'POST', body: JSON.stringify({ prompt }) })
      setAnswer(result.answer)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main>
      <h1>My class project</h1>
      <p className="muted">Edit <code>frontend/src/App.tsx</code> and <code>backend/app.py</code>, then push to GitHub: it deploys by itself.</p>

      <section>
        <h2>1. Is the backend alive?</h2>
        <button onClick={checkBackend}>Check backend</button>
        {status && <p className="ok">{status}</p>}
      </section>

      <section>
        <h2>2. Ask the class AI</h2>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} maxLength={500} />
        <button onClick={askAi} disabled={busy || !prompt.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
        {answer && <p className="answer">{answer}</p>}
      </section>

      {error && <p className="error" role="alert">{error}</p>}
    </main>
  )
}
