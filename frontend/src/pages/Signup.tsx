import { Mail, UserPlus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { Button, Field, Input } from '@/components/ui'
import { api } from '@/lib/api'

export default function Signup() {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [email, setEmail] = useState('')
  const [done, setDone] = useState(false)
  const [resent, setResent] = useState(false)
  const [resending, setResending] = useState(false)

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    const form = new FormData(e.currentTarget)
    const body = {
      email: String(form.get('email') ?? '').trim(),
      password: String(form.get('password') ?? ''),
      name: String(form.get('name') ?? '').trim(),
      organization: String(form.get('organization') ?? '').trim(),
      business_type: String(form.get('business_type') ?? '').trim(),
    }
    try {
      await api('/api/auth/signup', { method: 'POST', json: body })
      setEmail(body.email)
      setDone(true)
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : 'Could not create your account. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const resend = async () => {
    setResending(true)
    try {
      await api('/api/auth/resend', { method: 'POST', json: { email } })
      setResent(true)
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : 'Could not resend the email. Please try again.')
    } finally {
      setResending(false)
    }
  }

  if (done) {
    return (
      <div className="flex min-h-dvh items-center justify-center px-4 py-8 sm:p-6">
        <div className="w-full max-w-sm space-y-5">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Check your email</h1>
            <p className="mt-2 text-sm text-muted">
              We sent a verification link to <span className="font-semibold text-fg">{email}</span>. After you verify, an
              administrator still needs to approve your account before you can sign in.
            </p>
          </div>
          {resent && <p className="rounded-lg bg-success-soft px-3 py-2 text-sm text-success">Verification email resent.</p>}
          {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm break-words text-danger">{error}</p>}
          <Button type="button" variant="secondary" size="lg" className="w-full" loading={resending} onClick={resend}><Mail />Resend verification email</Button>
          <Link to="/login" className="block text-center text-sm font-semibold text-brand hover:underline">Back to sign in</Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-8 sm:p-6">
      <div className="w-full max-w-sm">
        <form onSubmit={submit} aria-busy={loading}>
          <fieldset disabled={loading} className="space-y-5">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Create an account</h1>
              <p className="mt-1 text-sm text-muted">Sign up to get started. New accounts need email verification and admin approval first.</p>
            </div>
            <Field label="Name"><Input name="name" type="text" autoComplete="name" required autoFocus /></Field>
            <Field label="Email">
              <Input name="email" type="text" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required placeholder="you@company.com" />
            </Field>
            <Field label="Password" hint="At least 8 characters.">
              <Input name="password" type="password" autoComplete="new-password" required minLength={8} />
            </Field>
            <Field label="Organization" hint="Optional.">
              <Input name="organization" type="text" autoComplete="organization" />
            </Field>
            <Field label="Business type" hint="Optional.">
              <Input name="business_type" type="text" />
            </Field>
            {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm break-words text-danger">{error}</p>}
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={loading}><UserPlus />Create account</Button>
            <p className="text-center text-sm text-muted">Already have an account? <Link to="/login" className="font-semibold text-brand hover:underline">Sign in</Link></p>
          </fieldset>
        </form>
      </div>
    </div>
  )
}
