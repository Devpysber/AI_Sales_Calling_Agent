import { UserPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Field, Input } from '@/components/ui'
import { api } from '@/lib/api'

export default function AcceptInvite() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setError('')
    const form = new FormData(e.currentTarget)
    const name = String(form.get('name') ?? '').trim()
    const password = String(form.get('password') ?? '')
    const confirm = String(form.get('confirm') ?? '')
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setLoading(true)
    try {
      await api('/api/auth/accept-invite', { method: 'POST', json: { token, password, name } })
      setDone(true)
      setTimeout(() => navigate('/login', { replace: true }), 1500)
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : 'Could not accept this invitation. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    return (
      <div className="flex min-h-dvh items-center justify-center px-4 py-8 sm:p-6">
        <div className="w-full max-w-sm space-y-5">
          <h1 className="text-2xl font-semibold tracking-tight">Invalid invitation</h1>
          <p className="text-sm text-muted">This invitation link is missing its token. Ask your administrator for a new one.</p>
          <Link to="/login" className="text-sm font-semibold text-brand hover:underline">Back to sign in</Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-8 sm:p-6">
      <div className="w-full max-w-sm">
        {done ? (
          <div className="space-y-5">
            <h1 className="text-2xl font-semibold tracking-tight">You're in</h1>
            <p className="text-sm text-muted">Taking you to sign in…</p>
          </div>
        ) : (
          <form onSubmit={submit} aria-busy={loading}>
            <fieldset disabled={loading} className="space-y-5">
              <div>
                <h1 className="text-2xl font-semibold tracking-tight">Accept your invitation</h1>
                <p className="mt-1 text-sm text-muted">Set your name and a password to join the organization.</p>
              </div>
              <Field label="Name"><Input name="name" type="text" autoComplete="name" required autoFocus /></Field>
              <Field label="Password"><Input name="password" type="password" autoComplete="new-password" required minLength={8} /></Field>
              <Field label="Confirm password"><Input name="confirm" type="password" autoComplete="new-password" required minLength={8} /></Field>
              {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm break-words text-danger">{error}</p>}
              <Button type="submit" variant="primary" size="lg" className="w-full" loading={loading}><UserPlus />Join</Button>
            </fieldset>
          </form>
        )}
      </div>
    </div>
  )
}
