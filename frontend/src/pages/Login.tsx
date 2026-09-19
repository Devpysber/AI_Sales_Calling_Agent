import { useQueryClient } from '@tanstack/react-query'
import { AudioWaveform, Lock } from 'lucide-react'
import { Orb3D, Waveform } from '@/components/VoiceViz'
import { useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Button, Field, Input } from '@/components/ui'
import { api } from '@/lib/api'

export default function Login() {
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const rawFrom = (useLocation().state as { from?: string } | null)?.from
  // Only return to an in-app path: never to /login itself or to anything that looks like an external URL.
  const from = rawFrom && rawFrom.startsWith('/') && !rawFrom.startsWith('//') && rawFrom !== '/login' ? rawFrom : '/'

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    const form = new FormData(e.currentTarget)
    const email = String(form.get('email') ?? '').trim()
    const password = String(form.get('password') ?? '')
    try {
      const res = await api<{ user: string }>('/api/auth/login', { method: 'POST', json: { email, password } })
      // Seed the session so the guarded routes render at once, then refetch: /me also carries the
      // display name, role and agent limits that the login response does not.
      qc.setQueryData(['me'], (prev: object | undefined) => ({ ...(prev ?? {}), user: res.user, auth_enabled: true }))
      void qc.invalidateQueries({ queryKey: ['me'] })
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : 'Could not sign in. Please try again.')
      setLoading(false)
    }
  }

  return (
    // One screen, never a scrollbar: on desktop the page is exactly the viewport and the orb
    // shrinks to the height the copy leaves it; on phones only the form shows.
    <div className="grid min-h-dvh lg:h-dvh lg:grid-cols-2 lg:overflow-hidden">
      <div className="relative hidden min-h-0 overflow-hidden bg-[#0d0b1f] px-10 py-8 text-white lg:flex lg:flex-col xl:px-12 xl:py-10">
        <div className="absolute -top-40 -left-40 size-[520px] rounded-full bg-[#5b4bf5] opacity-40 blur-[120px] [animation:aurora-a_18s_ease-in-out_infinite]" />
        <div className="absolute -right-32 -bottom-40 size-[420px] rounded-full bg-[#00c2a8] opacity-20 blur-[120px] [animation:aurora-b_22s_ease-in-out_infinite]" />
        <div className="relative flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-lg bg-white/10 ring-1 ring-white/20"><Waveform bars={4} className="h-4" /></div>
          <span className="text-lg font-semibold">Samvaad AI</span>
        </div>
        {/* The product in one image: the agent's voice, alive and waiting for the next call. */}
        <FittedOrb />
        <div className="relative max-w-md shrink-0">
          <p className="text-3xl leading-tight font-semibold tracking-tight xl:text-4xl">Your AI sales team that never stops dialling.</p>
          <p className="mt-3 text-[15px] text-white/70">Calls leads in Hindi and English, answers from your company knowledge, qualifies intent and books meetings — with every conversation logged to your CRM.</p>
          <div className="mt-6 grid grid-cols-3 gap-3 text-sm [&>div]:min-w-0 [&>div]:break-words">
            {[['Plivo', 'Telephony'], ['Sarvam', 'Indian voices'], ['RAG', 'Grounded answers']].map(([a, b]) => (
              <div key={a} className="rounded-lg bg-white/5 p-3 ring-1 ring-white/10 transition hover:-translate-y-0.5 hover:bg-white/10"><div className="font-semibold">{a}</div><div className="text-white/60">{b}</div></div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex min-h-0 min-w-0 items-center justify-center overflow-y-auto px-4 py-8 sm:p-6">
        <form onSubmit={submit} className="w-full min-w-0 max-w-sm" aria-busy={loading}>
          <fieldset disabled={loading} className="min-w-0 space-y-5">
            <div>
              <div className="mb-6 grid size-10 place-items-center rounded-xl bg-brand-soft text-brand lg:hidden"><AudioWaveform className="size-5" /></div>
              <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
              <p className="mt-1 text-sm text-muted">Access your voice agent dashboard.</p>
            </div>
            <Field label="Email" hint="Sign-in email from your Admin profile. First sign-in before an email is set: your admin username.">
              <Input name="email" type="text" inputMode="email" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus placeholder="you@company.com" />
            </Field>
            <Field label="Password"><Input name="password" type="password" autoComplete="current-password" required /></Field>
            {error && <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-sm break-words text-danger">{error}</p>}
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={loading}><Lock />Sign in</Button>
          </fieldset>
        </form>
      </div>
    </div>
  )
}

/** The hero orb sized to the space left between the logo and the copy, so the panel never overflows. */
function FittedOrb() {
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    // Snap to a 40px step: VoiceOrb3D rebuilds its whole WebGL scene whenever `size` changes, so a
    // per-pixel value would churn contexts on every resize tick. It follows its own box for the rest.
    const measure = () => setSize(Math.max(0, Math.floor(Math.min(el.clientHeight - 16, el.clientWidth * 0.8, 380) / 40) * 40))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return (
    <div ref={ref} className="relative grid min-h-0 flex-1 place-items-center">
      {size >= 120 && <Orb3D state="listening" size={size} />}
    </div>
  )
}
