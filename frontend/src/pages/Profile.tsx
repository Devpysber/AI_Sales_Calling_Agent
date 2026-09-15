import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, KeyRound, LogOut, Mail, Phone, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, CardHeader, Field, Input, PageHeader, Select, Skeleton } from '@/components/ui'
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

export default function ProfilePage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data: p, isLoading } = useQuery({ queryKey: ['auth', 'profile'], queryFn: () => api<Profile>('/api/auth/profile') })
  const [form, setForm] = useState<Partial<Profile>>({})
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [emailPw, setEmailPw] = useState('')
  useEffect(() => { if (p) setForm(p) }, [p])

  const save = useMutation({
    mutationFn: (body: Partial<Profile>) => api<Profile>('/api/auth/profile', { method: 'PUT', json: body }),
    onSuccess: (data) => {
      qc.setQueryData(['auth', 'profile'], data); qc.invalidateQueries({ queryKey: ['me'] }); setEmailPw('')
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
  const logout = async () => { await api('/api/auth/logout', { method: 'POST' }).catch(() => undefined); qc.clear(); navigate('/login') }

  const set = (k: keyof Profile) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const dirty = p && (['display_name', 'email', 'phone', 'role', 'company', 'timezone'] as const).some((k) => (form[k] ?? '') !== (p[k] ?? ''))
  const submitProfile = (e: FormEvent) => {
    e.preventDefault()
    const { display_name, email, phone, role, company, timezone } = form
    save.mutate({ display_name, email, phone, role, company, timezone, ...(emailChanged ? { current_password: emailPw } : {}) } as Partial<Profile>)
  }
  const emailChanged = Boolean(p) && (form.email ?? '').trim().toLowerCase() !== (p?.login_email ?? '')
  const score = strength(pw.next)
  const pwError = pw.confirm && pw.next !== pw.confirm ? 'Passwords do not match' : undefined
  const name = form.display_name || p?.username || 'Admin'

  return (
    <>
      <PageHeader eyebrow={<><UserRound className="size-3.5" />Account</>} title="Admin profile"
        description="Your identity in the dashboard, sign-in security and session details."
        actions={<Button onClick={logout}><LogOut />Sign out</Button>} />

      {isLoading || !p ? <div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-64 lg:col-span-2" /><Skeleton className="h-64" /></div> : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <Card>
              <div className="flex flex-wrap items-center gap-4 border-b border-border px-5 py-5">
                <span className="grid size-16 place-items-center rounded-2xl bg-fg text-2xl font-extrabold text-bg uppercase">{name.slice(0, 1)}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xl font-extrabold tracking-tight">{name}</div>
                  <div className="truncate text-sm text-muted">{form.role || 'Administrator'}{form.company ? ` · ${form.company}` : ''}</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <Badge tone="success" dot>Full access</Badge>
                    {p.login_email && <Badge>{p.login_email}</Badge>}
                  </div>
                </div>
              </div>
              <form onSubmit={submitProfile} className="space-y-4 px-5 py-5">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Display name"><Input value={form.display_name ?? ''} onChange={set('display_name')} placeholder="Ashish Sharma" maxLength={80} /></Field>
                  <Field label="Role / title"><Input value={form.role ?? ''} onChange={set('role')} placeholder="Sales operations lead" maxLength={60} /></Field>
                  <Field label="Sign-in email *" hint={p.login_email ? 'You sign in with this email and your password.' : 'Set this now: you will sign in with email instead of a username.'}>
                    <div className="relative"><Mail className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" /><Input type="email" required className="pl-9" value={form.email ?? ''} onChange={set('email')} placeholder="you@company.com" maxLength={160} /></div>
                  </Field>
                  {emailChanged && (
                    <Field label="Current password" hint="Required to set or change the sign-in email.">
                      <Input type="password" autoComplete="current-password" required value={emailPw} onChange={(e) => setEmailPw(e.target.value)} />
                    </Field>
                  )}
                  <Field label="Phone">
                    <div className="relative"><Phone className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" /><Input type="tel" className="pl-9" value={form.phone ?? ''} onChange={set('phone')} placeholder="+91 98765 43210" maxLength={32} /></div>
                  </Field>
                  <Field label="Organisation"><Input value={form.company ?? ''} onChange={set('company')} placeholder="Psyber Technologies" maxLength={120} /></Field>
                  <Field label="Time zone" hint="Calling hours and schedules run in IST.">
                    <Select value={form.timezone ?? 'Asia/Kolkata'} onChange={set('timezone')}>{TIMEZONES.map((t) => <option key={t}>{t}</option>)}</Select>
                  </Field>
                </div>
                <div className="flex justify-end gap-2 border-t border-border pt-4">
                  <Button type="button" disabled={!dirty} onClick={() => setForm(p)}>Reset</Button>
                  <Button type="submit" variant="primary" disabled={!dirty} loading={save.isPending}>Save profile</Button>
                </div>
              </form>
            </Card>

            <Card>
              <CardHeader title="Password" description={p.password_source === 'dashboard' ? `Last changed ${fromEpoch(p.password_changed_at)}` : 'Currently the ADMIN_PASSWORD from the server environment. Changing it here stores a secure hash that replaces it.'} />
              <form onSubmit={(e) => { e.preventDefault(); if (!pwError) changePw.mutate() }} className="grid gap-4 px-5 pb-5 sm:grid-cols-3">
                <Field label="Current password"><Input type="password" autoComplete="current-password" required value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
                <Field label="New password" hint={pw.next ? <span className={score >= 3 ? 'text-success' : 'text-warning'}>{STRENGTH[score]}</span> : 'At least 10 characters, mixed case and a number or symbol.'}>
                  <Input type="password" autoComplete="new-password" required minLength={10} value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
                </Field>
                <Field label="Confirm new password" error={pwError}><Input type="password" autoComplete="new-password" required value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} /></Field>
                {pw.next && <div className="flex gap-1 sm:col-span-3">{[0, 1, 2, 3].map((i) => <span key={i} className={`h-1 flex-1 rounded-full ${i < score ? (score >= 3 ? 'bg-success' : 'bg-warning') : 'bg-surface-2'}`} />)}</div>}
                <div className="flex justify-end sm:col-span-3">
                  <Button type="submit" variant="primary" loading={changePw.isPending} disabled={!pw.current || score < 2 || !!pwError || pw.next !== pw.confirm}><KeyRound />Change password</Button>
                </div>
              </form>
            </Card>
          </div>

          <div className="space-y-4">
            <Card>
              <CardHeader title="Security" />
              <dl className="divide-y divide-border text-sm">
                {[
                  [<ShieldCheck key="i" className="size-4 text-success" />, 'Sign-in', `${p.login_email || 'Email not set yet'} · password`],
                  [<KeyRound key="i" className="size-4 text-muted" />, 'Password source', p.password_source === 'dashboard' ? 'Set in dashboard (hashed)' : 'Server environment'],
                  [<Clock key="i" className="size-4 text-muted" />, 'Session length', `${p.session_hours} hours, then sign in again`],
                  [<Clock key="i" className="size-4 text-muted" />, 'Last sign-in', `${fromEpoch(p.last_login_at)}${p.last_login_ip ? ` · ${p.last_login_ip}` : ''}`],
                  [<ShieldCheck key="i" className={`size-4 ${p.api_token_enabled ? 'text-success' : 'text-muted'}`} />, 'API token', p.api_token_enabled ? 'Enabled for server-to-server calls' : 'Not configured'],
                ].map(([icon, label, value]) => (
                  <div key={label as string} className="flex items-start gap-3 px-5 py-3">
                    <span className="mt-0.5">{icon}</span>
                    <div className="min-w-0"><dt className="text-xs text-muted">{label}</dt><dd className="font-medium [overflow-wrap:anywhere]">{value}</dd></div>
                  </div>
                ))}
              </dl>
            </Card>
            <Card className="p-5 text-sm text-muted">
              Sign in with <b className="text-fg">{p.login_email || 'your email'}</b> and your password. Sessions use an HttpOnly signed cookie; the server can also preset the email with <code>ADMIN_EMAIL</code>.
            </Card>
          </div>
        </div>
      )}
    </>
  )
}
