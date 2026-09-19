import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, KeyRound, LogOut, Mail, Phone, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Skeleton } from '@/components/ui'
import { Stagger } from '@/lib/motion'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'

type Profile = {
  username: string; login_email: string; display_name: string; email: string; phone: string; role: string; company: string; timezone: string
  password_source: 'dashboard' | 'environment'; password_changed_at: number | null
  last_login_at: number | null; last_login_ip: string | null; session_hours: number; api_token_enabled: boolean
}

const TIMEZONES = ['Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore', 'Europe/London', 'America/New_York', 'UTC']
const fromEpoch = (s: number | null) => (s ? formatDate(new Date(s * 1000).toISOString()) : '—')

function strength(pw: string) {
  let score = 0
  if (pw.length >= 10) score++
  if (pw.length >= 14) score++
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++
  if (/\d/.test(pw)) score++
  if (/[^A-Za-z0-9]/.test(pw)) score++
  return Math.min(4, score)
}
const STRENGTH = ['Too weak', 'Weak', 'Fair', 'Good', 'Strong']
// The server derives login_email from ADMIN_EMAIL when the stored email is empty; edit that address rather than a blank field.
const withLoginEmail = (p: Profile): Profile => ({ ...p, email: p.email || p.login_email || '' })
const strongEnough = (pw: string) => pw.length >= 10 && /[a-z]/.test(pw) && /[A-Z]/.test(pw) && (/\d/.test(pw) || /[^A-Za-z0-9]/.test(pw))

export default function ProfilePage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data: p, isLoading, isError, error, refetch, isFetching } = useQuery({ queryKey: ['auth', 'profile'], queryFn: () => api<Profile>('/api/auth/profile') })
  const [signingOut, setSigningOut] = useState(false)
  const [form, setForm] = useState<Partial<Profile>>({})
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [emailPw, setEmailPw] = useState('')
  const [synced, setSynced] = useState(false)
  const base = useMemo(() => (p ? withLoginEmail(p) : undefined), [p])
  useEffect(() => { if (base && !synced) { setForm(base); setSynced(true) } }, [base, synced])

  const save = useMutation({
    mutationFn: (body: Partial<Profile>) => api<Profile>('/api/auth/profile', { method: 'PUT', json: body }),
    onSuccess: (data) => {
      qc.setQueryData(['auth', 'profile'], data); qc.invalidateQueries({ queryKey: ['me'] }); setForm(withLoginEmail(data)); setEmailPw('')
      toast.success('Profile saved', { description: data.login_email ? `Sign in with ${data.login_email}` : undefined })
    },
    onError: (e) => toast.error('Could not save profile', { description: e.message }),
  })
  const changePw = useMutation({
    mutationFn: () => api('/api/auth/password', { method: 'POST', json: { current_password: pw.current, new_password: pw.next } }),
    onSuccess: () => {
      setPw({ current: '', next: '', confirm: '' })
      qc.invalidateQueries({ queryKey: ['auth', 'profile'] })
      toast.success('Password changed', { description: 'Use the new password next time you sign in.' })
    },
    onError: (e) => toast.error('Password not changed', { description: e.message }),
  })
  const logout = async () => {
    setSigningOut(true)
    try { await api('/api/auth/logout', { method: 'POST' }).catch(() => undefined); qc.clear(); navigate('/login') } finally { setSigningOut(false) }
  }

  const set = (k: keyof Profile) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const dirty = Boolean(base) && (['display_name', 'email', 'phone', 'role', 'company', 'timezone'] as const).some((k) => (form[k] ?? '') !== (base?.[k] ?? ''))
  const emailChanged = Boolean(base) && (form.email ?? '').trim().toLowerCase() !== (base?.email ?? '').toLowerCase()
  const submitProfile = (e: FormEvent) => {
    e.preventDefault()
    if (save.isPending) return
    const { display_name, email, phone, role, company, timezone } = form
    save.mutate({ display_name, email, phone, role, company, timezone, ...(emailChanged ? { current_password: emailPw } : {}) } as Partial<Profile>)
  }
  const score = strength(pw.next)
  const pwError = pw.confirm && pw.next !== pw.confirm ? 'Passwords do not match' : undefined
  const name = form.display_name || p?.username || 'Admin'

  return (
    <>
      <PageHeader eyebrow={<><UserRound className="size-3.5" />Account</>} title={!p ? 'Profile' : p.role === 'Team Member' ? 'My profile' : 'Admin profile'}
        description="Your identity in the dashboard, sign-in security and session details."
        actions={<Button onClick={logout} loading={signingOut}><LogOut />Sign out</Button>} />

      {isError ? (
        <Card><EmptyState title="Could not load your profile" description={(error as Error)?.message || 'The server did not respond.'}
          action={<Button onClick={() => refetch()} loading={isFetching}>Try again</Button>} /></Card>
      ) : isLoading || !p ? <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]"><Skeleton className="h-64" /><Skeleton className="h-64" /></div> : (
        <Stagger className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <Card className="reveal reveal-in reveal-up">
              <div className="flex flex-wrap items-center gap-4 border-b border-border px-4 py-4 sm:px-5 sm:py-5">
                <span className="grid size-16 shrink-0 place-items-center rounded-2xl bg-fg text-2xl font-extrabold text-bg uppercase">{name.slice(0, 1)}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xl font-extrabold tracking-tight">{name}</div>
                  <div className="truncate text-sm text-muted">{form.role || 'Administrator'}{form.company ? ` · ${form.company}` : ''}</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <Badge tone="success" dot>{p.role === 'Team Member' ? 'Limited access' : 'Full access'}</Badge>
                    {p.login_email && <Badge className="max-w-full whitespace-normal [overflow-wrap:anywhere]">{p.login_email}</Badge>}
                  </div>
                </div>
              </div>
              <form onSubmit={submitProfile} className="space-y-4 px-4 py-4 sm:px-5 sm:py-5">
                <fieldset disabled={save.isPending} className="grid min-w-0 gap-4 sm:grid-cols-2">
                  <Field label="Display name"><Input className="min-w-0" value={form.display_name ?? ''} onChange={set('display_name')} placeholder="Ashish Sharma" maxLength={80} /></Field>
                  <Field label="Role / title"><Input className="min-w-0" value={form.role ?? ''} onChange={set('role')} disabled={p.role === 'Team Member'} placeholder="Sales operations lead" maxLength={60} /></Field>
                  <Field label="Sign-in email *" hint={p.login_email ? 'You sign in with this email and your password.' : 'Set this now: you will sign in with email instead of a username.'}>
                    <div className="relative min-w-0"><Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" /><Input type="email" required className="min-w-0 pl-9" value={form.email ?? ''} onChange={set('email')} disabled={p.role === 'Team Member'} placeholder="you@company.com" maxLength={160} /></div>
                  </Field>
                  {emailChanged && (
                    <Field label="Current password" hint="Required to set or change the sign-in email.">
                      <Input type="password" autoComplete="current-password" required value={emailPw} onChange={(e) => setEmailPw(e.target.value)} />
                    </Field>
                  )}
                  <Field label="Phone">
                    <div className="relative min-w-0"><Phone className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" /><Input type="tel" className="min-w-0 pl-9" value={form.phone ?? ''} onChange={set('phone')} disabled={p.role === 'Team Member'} placeholder="+91 98765 43210" maxLength={32} /></div>
                  </Field>
                  <Field label="Organisation"><Input className="min-w-0" value={form.company ?? ''} onChange={set('company')} disabled={p.role === 'Team Member'} placeholder="Psyber Technologies" maxLength={120} /></Field>
                  <Field label="Time zone" hint="Calling hours and schedules run in IST.">
                    <Select className="min-w-0" value={form.timezone ?? 'Asia/Kolkata'} onChange={set('timezone')} disabled={p.role === 'Team Member'}>{(form.timezone && !TIMEZONES.includes(form.timezone) ? [form.timezone, ...TIMEZONES] : TIMEZONES).map((t) => <option key={t} value={t}>{t}</option>)}</Select>
                  </Field>
                </fieldset>
                <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
                  <Button type="button" disabled={!dirty || save.isPending} onClick={() => { setForm(base ?? p); setEmailPw('') }}>Reset</Button>
                  <Button type="submit" variant="primary" disabled={!dirty} loading={save.isPending}>Save profile</Button>
                </div>
              </form>
            </Card>

            <Card className="reveal reveal-in reveal-up" style={{ animationDelay: '55ms' }}>
              <CardHeader title="Password" description={p.password_source === 'dashboard' ? `Last changed ${fromEpoch(p.password_changed_at)}` : 'Currently the ADMIN_PASSWORD from the server environment. Changing it here stores a secure hash that replaces it.'} />
              <form onSubmit={(e) => { e.preventDefault(); if (!pwError && !changePw.isPending) changePw.mutate() }} className="grid gap-4 px-4 pb-4 sm:px-5 sm:pb-5 sm:grid-cols-2 xl:grid-cols-3">
                <Field label="Current password"><Input type="password" autoComplete="current-password" required disabled={changePw.isPending} value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
                <Field label="New password" hint={pw.next ? <span className={score >= 3 ? 'text-success' : 'text-warning'}>{STRENGTH[score]}</span> : 'At least 10 characters, mixed case and a number or symbol.'}>
                  <Input type="password" autoComplete="new-password" required minLength={10} disabled={changePw.isPending} value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
                </Field>
                <Field label="Confirm new password" error={pwError}><Input type="password" autoComplete="new-password" required disabled={changePw.isPending} value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} /></Field>
                {pw.next && <div className="flex gap-1 sm:col-span-2 xl:col-span-3">{[0, 1, 2, 3].map((i) => <span key={i} className={`h-1 flex-1 rounded-full ${i < score ? (score >= 3 ? 'bg-success' : 'bg-warning') : 'bg-surface-2'}`} />)}</div>}
                <div className="flex justify-end sm:col-span-2 xl:col-span-3">
                  <Button type="submit" variant="primary" loading={changePw.isPending} disabled={!pw.current || !strongEnough(pw.next) || !!pwError || pw.next !== pw.confirm}><KeyRound />Change password</Button>
                </div>
              </form>
            </Card>
          </div>

          <div className="min-w-0 space-y-4">
            <Card className="reveal reveal-in reveal-up" style={{ animationDelay: '110ms' }}>
              <CardHeader title="Security" />
              <dl className="divide-y divide-border text-sm">
                {[
                  [<ShieldCheck key="i" className="size-4 text-success" />, 'Sign-in', `${p.login_email || 'Email not set yet'} · password`],
                  [<KeyRound key="i" className="size-4 text-muted" />, 'Password source', p.password_source === 'dashboard' ? 'Set in dashboard (hashed)' : 'Server environment'],
                  [<Clock key="i" className="size-4 text-muted" />, 'Session length', p.session_hours ? `${p.session_hours} hours, then sign in again` : 'Until you sign out'],
                  [<Clock key="i" className="size-4 text-muted" />, 'Last sign-in', `${fromEpoch(p.last_login_at)}${p.last_login_ip ? ` · ${p.last_login_ip}` : ''}`],
                  [<ShieldCheck key="i" className={`size-4 ${p.api_token_enabled ? 'text-success' : 'text-muted'}`} />, 'API token', p.api_token_enabled ? 'Enabled for server-to-server calls' : 'Not configured'],
                ].map(([icon, label, value]) => (
                  <div key={label as string} className="flex items-start gap-3 px-4 py-3 sm:px-5">
                    <span className="mt-0.5 shrink-0">{icon}</span>
                    <div className="min-w-0 flex-1"><dt className="text-xs text-muted">{label}</dt><dd className="font-medium [overflow-wrap:anywhere]">{value}</dd></div>
                  </div>
                ))}
              </dl>
            </Card>
            <Card className="reveal reveal-in reveal-up p-4 text-sm break-words text-muted sm:p-5" style={{ animationDelay: '165ms' }}>
              Sign in with <b className="text-fg">{p.login_email || 'your email'}</b> and your password. Sessions use an HttpOnly signed cookie; the server can also preset the email with <code>ADMIN_EMAIL</code>.
            </Card>
          </div>
        </Stagger>
      )}
    </>
  )
}
