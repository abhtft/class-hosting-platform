/**
 * Sign in with Google, for the people who use YOUR app (not the platform's own login).
 *
 * The page never sees a token: the backend (class_auth) keeps the session in a cookie the page cannot read. The page only asks "who am I" and
 * sends people to the sign-in address.
 *
 *   const { user, signIn, signOut } = useUser()      // user is { id, email, name } or null while nobody is signed in
 *
 * Rules (see AGENTS.md, "Sign-in"): do not write your own login form or token handling. Show data that belongs to a visitor only after the
 * backend says who they are; the backend must check it again on every request.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

export type User = { id: string; email: string; name: string }
type Me = { user: User | null; sign_in: 'on' | 'local' | 'off' }

/** The place to come back to after signing in: the page you are on now. */
function here(): string {
  return `${window.location.pathname}${window.location.search}`
}

export function useUser() {
  const [me, setMe] = useState<Me | null>(null)

  const refresh = useCallback(async () => {
    try {
      setMe(await api<Me>('/auth/me'))
    } catch {
      setMe({ user: null, sign_in: 'off' })
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** Go to the Google sign-in page. The backend sends the visitor back here afterwards. */
  const signIn = useCallback(() => {
    window.location.href = `/api/auth/login?next=${encodeURIComponent(here())}`
  }, [])

  const signOut = useCallback(async () => {
    await api('/auth/logout', { method: 'POST' })
    await refresh()
  }, [refresh])

  return { user: me?.user ?? null, mode: me?.sign_in ?? null, loading: me === null, signIn, signOut }
}
