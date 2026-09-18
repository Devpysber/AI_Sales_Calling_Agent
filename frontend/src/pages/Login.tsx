import { useQueryClient } from '@tanstack/react-query'
import { AudioWaveform, Lock } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Button, Field, Input } from '@/components/ui'
import { api } from '@/lib/api'

export default function Login() {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const from = (useLocation().state as { from?: string } | null)?.from ?? '/'

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    const form = new FormData(e.currentTarget)
    try {
      const res = await api<{ user: string }>('/api/auth/login', { method: 'POST', json: Object.fromEntries(form) })
      qc.setQueryData(['me'], { user: res.user, auth_enabled: true })
      navigate(from === '/login' ? '/' : from, { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-[#0d0b1f] p-12 text-white lg:flex lg:flex-col">
        <div className="absolute -top-40 -left-40 size-[520px] rounded-full bg-[#5b4bf5] opacity-40 blur-[120px]" />
        <div className="absolute -right-32 -bottom-40 size-[420px] rounded-full bg-[#00c2a8] opacity-20 blur-[120px]" />
        <div className="relative flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-lg bg-white/10 ring-1 ring-white/20"><AudioWaveform className="size-5" /></div>
          <span className="text-lg font-semibold">Samvaad AI</span>
        </div>
        <div className="relative mt-auto max-w-md">
          <h2 className="text-4xl leading-tight font-semibold tracking-tight">Your AI sales team that never stops dialling.</h2>
          <p className="mt-4 text-white/70">Calls leads in Hindi and English, answers from your company knowledge, qualifies intent and books meetings — with every conversation logged to your CRM.</p>
          <div className="mt-8 grid grid-cols-3 gap-4 text-sm">
            {[['Plivo', 'Telephony'], ['Sarvam', 'Indian voices'], ['RAG', 'Grounded answers']].map(([a, b]) => (
              <div key={a} className="rounded-lg bg-white/5 p-3 ring-1 ring-white/10"><div className="font-semibold">{a}</div><div className="text-white/60">{b}</div></div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm space-y-5">
          <div>
            <div className="mb-6 grid size-10 place-items-center rounded-xl bg-brand-soft text-brand lg:hidden"><AudioWaveform className="size-5" /></div>
            <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
            <p className="mt-1 text-sm text-muted">Access your voice agent dashboard.</p>
          </div>
          <Field label="Email" hint="Sign-in email from your Admin profile. First sign-in before an email is set: your admin username.">
            <Input name="email" type="text" inputMode="email" autoComplete="email" required autoFocus placeholder="you@company.com" />
          </Field>
          <Field label="Password"><Input name="password" type="password" autoComplete="current-password" required /></Field>
          {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
          <Button type="submit" variant="primary" size="lg" className="w-full" loading={loading}><Lock />Sign in</Button>
        </form>
      </div>
    </div>
  )
}
