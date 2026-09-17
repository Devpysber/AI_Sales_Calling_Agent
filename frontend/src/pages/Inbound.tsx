import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Clock, MoonStar, PhoneForwarded, PhoneIncoming, PhoneMissed, Save, UserRound, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import CallSheet from '@/components/CallSheet'
import InboundSetup from '@/components/InboundSetup'
import { CallStatusBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Skeleton, Switch, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { AgentProfile, AutomationSettings, Call, Page } from '@/lib/types'
import { cn, formatDuration, timeAgo } from '@/lib/utils'

type ProfileResponse = { profile: AgentProfile }
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hourLabel = (h: number) => `${((h + 11) % 12) + 1}:00 ${h < 12 ? 'AM' : 'PM'}`
type Routing = Pick<AgentProfile, 'transfer_number' | 'team_members' | 'inbound_mode' | 'transfer_on_request' | 'after_hours_mode' | 'after_hours_message' | 'forward_fallback' | 'notify_missed_calls' | 'inbound_collect'>
const KEYS: (keyof Routing)[] = ['transfer_number', 'team_members', 'inbound_mode', 'transfer_on_request', 'after_hours_mode', 'after_hours_message', 'forward_fallback', 'notify_missed_calls', 'inbound_collect']

export default function Inbound() {
  const { agent, base, path } = useAgent()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['agent'], queryFn: () => api<ProfileResponse>(`${base}/profile`) })
  const automation = useQuery({ queryKey: ['automation'], queryFn: () => api<{ settings: AutomationSettings; within_calling_hours: boolean }>(`${base}/automation`) })
  const calls = useQuery({
    queryKey: ['calls', 'inbound'],
    queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { direction: 'inbound', page_size: 25 } }),
    refetchInterval: (q) => (q.state.data?.items.some((c) => ['Ringing', 'In Progress'].includes(c.status)) ? 2000 : 6000),
  })
  // Forwarded calls hunt the agent's number first, then the team's own lines, so show who is in that
  // queue here. The endpoint is admin-only: a team member sees nothing rather than an error.
  const team = useQuery({
    queryKey: ['team-members'],
    queryFn: () => api<{ members: { id: string; name: string; role?: string; phone?: string }[] }>('/api/system/team-members'),
    retry: false,
  })
  const [form, setForm] = useState<Routing | null>(null)
  const [callId, setCallId] = useState<number | null>(null)
  useEffect(() => { if (data && !form) setForm(Object.fromEntries(KEYS.map((k) => [k, data.profile[k]])) as Routing) }, [data, form])

  const save = useMutation({
    mutationFn: (values: Routing) => api<AgentProfile>(`${base}/profile`, { method: 'PUT', json: values }),
    onSuccess: (profile) => {
      qc.setQueryData<ProfileResponse>(['agent'], (old) => (old ? { ...old, profile } : old))
      setForm(Object.fromEntries(KEYS.map((k) => [k, profile[k]])) as Routing)
      toast.success('Call routing saved', { description: 'Applies to the next incoming call.' })
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  })

  const saveHours = useMutation({
    mutationFn: (values: { calling_hours_start: number, calling_hours_end: number }) => api(`${base}/automation`, { method: 'PUT', json: { ...automation.data!.settings, ...values } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['automation'] })
      toast.success('Calling hours updated')
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  })

  const items = useMemo(() => calls.data?.items ?? [], [calls.data])
  const stats = useMemo(() => {
    const answered = items.filter((c) => c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0)).length
    const forwarded = items.filter((c) => c.trigger === 'forwarded').length
    return { total: calls.data?.total ?? 0, answered, forwarded, missed: items.filter((c) => ['No Answer', 'Busy'].includes(c.status) || (c.status === 'Failed' && c.duration === 0)).length }
  }, [items, calls.data])

  if (!form || !data) return <><PageHeader title="Inbound & transfer" /><div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-80" /><Skeleton className="h-80" /></div></>

  const set = <K extends keyof Routing>(k: K, v: Routing[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))
  const tNums = form.team_members && form.team_members.length > 0
    ? form.team_members.map(m => m.phone).filter(n => n)
    : (form.transfer_number || '').split(',').map(n => n.trim()).filter(n => n)
  const hasNumber = tNums.length > 0 && tNums.every(n => { const d = n.replace(/\D/g, ''); return d.length >= 11 && d.length <= 15 })
  const numberError = tNums.length > 0 && !hasNumber
    ? 'All numbers must include the country code and be valid length, e.g. +91 98765 43210.'
    : undefined
  // Only a full international number can be dialled, so only those count as a backup line.
  const backups = (team.data?.members ?? []).filter((m) => {
    const d = (m.phone ?? '').replace(/\D/g, '')
    return d.length >= 11 && d.length <= 15
  })
  const dirty = KEYS.some((k) => JSON.stringify(form[k]) !== JSON.stringify(data.profile[k]))
  const cfg = automation.data?.settings
  const hours = cfg ? `${cfg.calling_hours_start}:00 – ${cfg.calling_hours_end}:00 IST` : '…'

  const routeNow = automation.data?.within_calling_hours === false ? form.after_hours_mode : form.inbound_mode
  const flow: Record<string, { icon: ReactNode; label: string; detail: string }> = {
    ai: { icon: <Bot />, label: `${data.profile.agent_name} (AI) answers`, detail: form.transfer_on_request && hasNumber ? `Hands over to ${form.transfer_number} when asked` : 'Answers from the knowledge base' },
    forward: { icon: <PhoneForwarded />, label: 'Your team answers', detail: hasNumber ? `Rings ${form.transfer_number}` : 'Needs a transfer number' },
    message: { icon: <MoonStar />, label: 'Closed message', detail: 'Plays a message, then hangs up' },
  }
  const option = (group: 'inbound_mode' | 'after_hours_mode', value: string, icon: ReactNode, title: string, detail: string, disabled = false) => {
    const active = form[group] === value
    return (
      <button key={value} type="button" disabled={disabled} onClick={() => set(group, value as never)}
        className={cn('flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition disabled:cursor-not-allowed disabled:opacity-45',
          active ? 'border-fg bg-surface-2' : 'border-border hover:border-border-strong')}>
        <span className={cn('grid size-4 shrink-0 place-items-center rounded-full border-2', active ? 'border-fg' : 'border-border-strong')}>{active && <span className="size-2 rounded-full bg-fg" />}</span>
        <span className="text-fg-2 [&_svg]:size-4">{icon}</span>
        <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{title}</span><span className="block truncate text-xs text-muted">{detail}</span></span>
      </button>
    )
  }
  const teamDetail = hasNumber ? `Rings ${form.transfer_number}` : 'Set a transfer number first'

  return (
    <>
      <PageHeader eyebrow={<><PhoneIncoming className="size-3.5" />{agent?.name} · Call routing</>} title="Inbound & transfer"
        description="Choose who answers when customers call, and where the AI sends callers who need a person." />

      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-4 px-5 py-4">
          <div className="flex items-center gap-2.5 text-sm">
            <span className="grid size-9 place-items-center rounded-xl bg-surface-2"><PhoneIncoming className="size-4" /></span>
            <div><div className="font-bold">A customer calls now</div><div className="text-xs text-muted">{automation.data ? `${automation.data.within_calling_hours ? 'Open' : 'Closed'} · ${hours}` : '…'}</div></div>
          </div>
          <span className="hidden h-px w-10 bg-border-strong sm:block" />
          <div className="flex min-w-0 items-center gap-2.5 text-sm">
            <span className="grid size-9 place-items-center rounded-xl bg-fg text-bg [&_svg]:size-4">{flow[routeNow]?.icon}</span>
            <div className="min-w-0"><div className="font-bold">{flow[routeNow]?.label}</div><div className="truncate text-xs text-muted">{flow[routeNow]?.detail}</div></div>
          </div>
          <div className="ml-auto grid grid-cols-4 gap-5 text-center">
            {([['Inbound', stats.total], ['Answered', stats.answered], ['Forwarded', stats.forwarded], ['Missed', stats.missed]] as const).map(([l, v]) => (
              <div key={l}><div className="text-lg font-extrabold tabular-nums">{v}</div><div className="text-[11px] text-muted">{l}</div></div>
            ))}
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader title="1 · Phone number" description="Plivo must send calls on this number to the app." />
            <div className="px-5 pb-5 text-sm"><InboundSetup /></div>
          </Card>

          <Card>
            <CardHeader title="2 · Your team's number" description="Where calls go when a person should take over."
              action={<Badge tone={hasNumber ? 'success' : 'warning'} dot>{hasNumber ? 'Set' : 'Not set'}</Badge>} />
            <div className="space-y-3 px-5 pb-5">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="font-semibold">Team members</div>
                  <button type="button" onClick={() => set('team_members', [...(form.team_members || []), { name: '', phone: '', email: '' }])} className="text-xs font-semibold text-fg hover:underline">+ Add team member</button>
                </div>
                <div className="text-[13px] text-muted mb-2">Team members who should receive urgent alerts and fallback calls.</div>
                {numberError && <div className="text-xs font-medium text-destructive">{numberError}</div>}
                {((form.team_members && form.team_members.length > 0) ? form.team_members : (form.transfer_number || '').split(',').map(n => ({ name: '', phone: n.trim(), email: '' }))).filter(m => form.team_members?.length || m.phone).map((member, i, arr) => (
                   <div key={i} className="flex items-start gap-2 rounded-xl border border-border p-3 bg-surface-2">
                     <div className="flex-1 space-y-2">
                       <Input type="text" value={member.name} onChange={e => { const updated = [...arr]; updated[i].name = e.target.value; set('team_members', updated) }} placeholder="Name (e.g. Alice)" className="h-8 text-sm" />
                       <Input type="tel" value={member.phone} onChange={e => { const updated = [...arr]; updated[i].phone = e.target.value; set('team_members', updated) }} placeholder="Phone (e.g. +91 98765 43210)" maxLength={20} className="h-8 text-sm" />
                       <Input type="email" value={member.email} onChange={e => { const updated = [...arr]; updated[i].email = e.target.value; set('team_members', updated) }} placeholder="Email (e.g. alice@example.com)" className="h-8 text-sm" />
                     </div>
                     <button type="button" onClick={() => { const updated = [...arr]; updated.splice(i, 1); set('team_members', updated) }} className="text-muted hover:text-fg p-1 mt-1"><X className="size-4" /></button>
                   </div>
                ))}
              </div>
              {backups.length > 0 && (
                <div className="rounded-xl border border-border px-3 py-2.5 text-sm">
                  <div className="font-semibold">If that line is busy, these ring next</div>
                  <ul className="mt-1.5 space-y-1 text-xs text-fg-2">
                    {backups.map((m) => (
                      <li key={m.id} className="flex items-center justify-between gap-2">
                        <span>{m.name}{m.role ? ` · ${m.role}` : ''}</span>
                        <span className="font-mono text-muted">{m.phone}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1.5 text-[11.5px] text-muted">From Sales Team Accounts, in the order they were added.</p>
                </div>
              )}
              <label className={cn('flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm', !hasNumber && 'opacity-50')}>
                <UserRound className="size-4 text-fg-2" />
                <span className="flex-1"><span className="block font-semibold">AI transfers when a caller asks for a person</span><span className="text-xs text-muted">The agent says it is connecting them, then your number rings.</span></span>
                <Switch checked={form.transfer_on_request && hasNumber} onChange={(v) => set('transfer_on_request', v)} disabled={!hasNumber} label="Transfer on request" />
              </label>
              <label className={cn('flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm', !hasNumber && 'opacity-50')}>
                <Bot className="size-4 text-fg-2" />
                <span className="flex-1"><span className="block font-semibold">If your team doesn't pick up, the AI takes the call</span><span className="text-xs text-muted">Off: the caller hears “our team will call you back” and the call ends.</span></span>
                <Switch checked={form.forward_fallback === 'ai'} onChange={(v) => set('forward_fallback', v ? 'ai' : 'message')} disabled={!hasNumber} label="AI fallback" />
              </label>
              <label className={cn('flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm', !hasNumber && 'opacity-50')}>
                <PhoneMissed className="size-4 text-fg-2" />
                <span className="flex-1"><span className="block font-semibold">Email me about missed forwarded calls</span><span className="text-xs text-muted">Sent to your Admin profile email with the caller's CRM details.</span></span>
                <Switch checked={form.notify_missed_calls} onChange={(v) => set('notify_missed_calls', v)} disabled={!hasNumber} label="Missed call email" />
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader title="3 · New callers" description="When an unknown number calls, the agent saves them as a lead, asks for these details one at a time (in this order), then helps. Names are saved the moment they're said." />
            <div className="space-y-3 px-5 pb-5">
              <div className="flex flex-wrap gap-2">
                {([['name', 'Name'], ['requirement', 'What they need'], ['city', 'City'], ['company', 'Company'], ['email', 'Email'], ['budget', 'Budget'], ['timeline', 'Timeline'], ['callback_time', 'Best time to call back'], ['source', 'How they heard about us']] as const).map(([k, l]) => {
                  const list = form.inbound_collect ?? []
                  const idx = list.indexOf(k)
                  return (
                    <button key={k} type="button" onClick={() => set('inbound_collect', idx >= 0 ? list.filter((x) => x !== k) : [...list, k])}
                      className={cn('inline-flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-[13px] font-semibold transition',
                        idx >= 0 ? 'border-fg bg-fg text-bg' : 'border-border text-fg-2 hover:border-border-strong')}>
                      {idx >= 0 && <span className="grid size-4 place-items-center rounded-full bg-bg/20 text-[10px]">{idx + 1}</span>}{l}
                    </button>
                  )
                })}
              </div>
              <p className="text-xs text-muted">Keep it to 2–3 details: every question adds time to the call. Known callers skip details they already gave, and callers known to another agent are recognised.</p>
            </div>
          </Card>

          <Card>
            <CardHeader title="4 · Who answers" description={<>Open hours come from the <Link to={`${base}/automation`} className="text-primary hover:underline">Automation page</Link>.</>} />
            <div className="grid gap-5 px-5 pb-5 md:grid-cols-2">
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-bold tracking-wide text-muted uppercase">
                  <Clock className="size-3.5" />Open
                  {cfg && (
                    <div className="ml-2 flex items-center gap-1 font-normal normal-case">
                      <select value={cfg.calling_hours_start} onChange={(e) => saveHours.mutate({ calling_hours_start: +e.target.value, calling_hours_end: cfg.calling_hours_end })} className="rounded-md border border-border bg-bg px-1 py-0.5 text-xs text-fg">
                        {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                      </select>
                      <span>to</span>
                      <select value={cfg.calling_hours_end} onChange={(e) => saveHours.mutate({ calling_hours_start: cfg.calling_hours_start, calling_hours_end: +e.target.value })} className="rounded-md border border-border bg-bg px-1 py-0.5 text-xs text-fg">
                        {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                      </select>
                    </div>
                  )}
                </div>
                {option('inbound_mode', 'ai', <Bot />, 'AI agent', 'Answers, qualifies and logs to the CRM')}
                {option('inbound_mode', 'forward', <PhoneForwarded />, 'Your team', teamDetail, !hasNumber)}
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-bold tracking-wide text-muted uppercase"><MoonStar className="size-3.5" />After hours</div>
                {option('after_hours_mode', 'ai', <Bot />, 'AI agent', 'Around the clock')}
                {option('after_hours_mode', 'forward', <PhoneForwarded />, 'Your team', teamDetail, !hasNumber)}
                {option('after_hours_mode', 'message', <MoonStar />, 'Closed message', 'Message, then hang up')}
              </div>
              {form.after_hours_mode === 'message' && (
                <div className="md:col-span-2">
                  <Field label="Closed message" hint="Leave empty for a default message in the caller's language.">
                    <Textarea rows={2} maxLength={300} value={form.after_hours_message} onChange={(e) => set('after_hours_message', e.target.value)}
                      placeholder="Thanks for calling. We're closed right now: please call again between 9 AM and 9 PM." />
                  </Field>
                </div>
              )}
            </div>
          </Card>
        </div>

        <Card className="h-fit">
          <CardHeader title="Recent inbound calls" description="Updates live."
            action={<Link to={path('/calls')} className="text-xs font-semibold text-brand hover:underline">All calls</Link>} />
          {calls.isLoading ? <div className="space-y-2 px-5 pb-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div>
            : items.length ? (
              <ul className="divide-y divide-border">
                {items.map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => setCallId(c.id)} className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-surface-2">
                      <span className={cn('grid size-8 shrink-0 place-items-center rounded-full', c.trigger === 'forwarded' ? 'bg-surface-2 text-fg-2' : 'bg-brand-soft text-brand')}>
                        {c.trigger === 'forwarded' ? <PhoneForwarded className="size-4" /> : <PhoneIncoming className="size-4" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">{c.lead_name || c.from_number}</span>
                        <span className="block truncate text-xs text-muted">{c.trigger === 'forwarded' ? 'Forwarded to team' : 'Answered by AI'} · {timeAgo(c.created_at)}{c.duration ? ` · ${formatDuration(c.duration)}` : ''}</span>
                      </span>
                      <CallStatusBadge status={c.status} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : <EmptyState icon={<PhoneIncoming />} title="No inbound calls yet" description="Call your Plivo number from another phone to test." />}
        </Card>
      </div>

      {dirty && (
        <div className="sticky bottom-4 z-20 mt-4 flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-elevated px-4 py-3 shadow-pop">
          <span className="flex-1 text-sm font-semibold">Unsaved routing changes</span>
          <Button onClick={() => setForm(Object.fromEntries(KEYS.map((k) => [k, data.profile[k]])) as Routing)}>Discard</Button>
          <Button variant="primary" loading={save.isPending} disabled={!!form.transfer_number && !hasNumber} onClick={() => save.mutate(form)}><Save />Save routing</Button>
        </div>
      )}

      <CallSheet callId={callId} onClose={() => setCallId(null)} />
    </>
  )
}
