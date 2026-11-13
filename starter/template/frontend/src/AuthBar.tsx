import { useUser } from './auth'

/**
 * The sign-in bar: shows who is signed in, or a "Sign in with Google" button. It shows nothing while sign-in is off for the app
 * (turn it on with `npm run signin -- on`). On your own computer a pretend visitor signs in at once, so you can build without Google.
 */
export default function AuthBar() {
  const { user, mode, loading, signIn, signOut } = useUser()
  if (loading || mode === 'off') return null
  if (user) {
    return (
      <p className="muted">
        Signed in as {user.name || user.email}{' '}
        <button className="ghost" onClick={() => void signOut()}>Sign out</button>
      </p>
    )
  }
  return <button onClick={signIn}>Sign in with Google</button>
}
