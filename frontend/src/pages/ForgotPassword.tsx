import { Mail } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Button, Field, Input } from '@/components/ui'
import { api } from '@/lib/api'

export default function ForgotPassword() {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sent, setSent] = useState(false)

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    const form = new FormData(e.currentTarget)
    const email = String(form.get('email') ?? '').trim()
    try {
      await api('/api/auth/forgot', { method: 'POST', json: { email } })
      // The backend never reveals whether the email exists; the UI must not either.
      setSent(true)
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : 'Could not send the reset link. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-8 sm:p-6">
      <div className="w-full max-w-sm">
        {sent ? (
          <div className="space-y-5">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Check your inbox</h1>
              <p className="mt-2 text-sm text-muted">If that email has an account, a reset link is on its way. It can take a minute to arrive.</p>
            </div>
            <Link to="/login" className="text-sm font-semibold text-brand hover:underline">Back to sign in</Link>
          </div>
        ) : (
          <form onSubmit={submit} aria-busy={loading}>
            <fieldset disabled={loading} className="space-y-5">
              <div>
                <h1 className="text-2xl font-semibold tracking-tight">Forgot password?</h1>
                <p className="mt-1 text-sm text-muted">Enter your email and we'll send you a link to reset it.</p>
              </div>
              <Field label="Email">
                <Input name="email" type="text" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus placeholder="you@company.com" />
              </Field>
              {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm break-words text-danger">{error}</p>}
              <Button type="submit" variant="primary" size="lg" className="w-full" loading={loading}><Mail />Send reset link</Button>
              <Link to="/login" className="block text-center text-sm font-semibold text-brand hover:underline">Back to sign in</Link>
            </fieldset>
          </form>
        )}
      </div>
    </div>
  )
}
