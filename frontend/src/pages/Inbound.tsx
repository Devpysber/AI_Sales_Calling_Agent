import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Clock, MoonStar, PhoneForwarded, PhoneIncoming, Save, UserRound } from 'lucide-react'
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
type Routing = Pick<AgentProfile, 'transfer_number' | 'inbound_mode' | 'transfer_on_request' | 'after_hours_mode' | 'after_hours_message'>
const KEYS: (keyof Routing)[] = ['transfer_number', 'inbound_mode', 'transfer_on_request', 'after_hours_mode', 'after_hours_message']

function Choice({ active, onClick, icon, title, detail, disabled }: { active: boolean; onClick: () => void; icon: ReactNode; title: string; detail: string; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className={cn('flex w-full items-start gap-3 rounded-2xl border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-50',
        active ? 'border-fg bg-surface-2 ring-1 ring-fg' : 'border-border hover:border-border-strong')}>
      <span className={cn('grid size-9 shrink-0 place-items-center rounded-xl [&_svg]:size-4', active ? 'bg-fg text-bg' : 'bg-surface-2 text-fg-2')}>{icon}</span>
      <span className="min-w-0">
        <span className="block text-sm font-bold">{title}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-muted">{detail}</span>
      </span>
    </button>
  )
}

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

  const items = useMemo(() => calls.data?.items ?? [], [calls.data])
  const stats = useMemo(() => {
    const answered = items.filter((c) => c.status === 'Completed').length
    const forwarded = items.filter((c) => c.trigger === 'forwarded').length
    return { total: calls.data?.total ?? 0, answered, forwarded, missed: items.filter((c) => ['No Answer', 'Busy', 'Failed'].includes(c.status)).length }
  }, [items, calls.data])

  if (!form || !data) return <><PageHeader title="Inbound & transfer" /><div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-80" /><Skeleton className="h-80" /></div></>

  const set = <K extends keyof Routing>(k: K, v: Routing[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))
  const hasNumber = form.transfer_number.replace(/\D/g, '').length >= 10
  const dirty = KEYS.some((k) => form[k] !== data.profile[k])
  const cfg = automation.data?.settings
  const hours = cfg ? `${cfg.calling_hours_start}:00 – ${cfg.calling_hours_end}:00 IST` : '…'

  return (
    <>
      <PageHeader eyebrow={<><PhoneIncoming className="size-3.5" />{agent?.name} · Call routing</>} title="Inbound & transfer"
        description="Decide who answers when customers call, and when the AI hands a caller to a person on your team."
        actions={<Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(form)}><Save />Save routing</Button>} />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[['Inbound calls', stats.total, 'All time'], ['Answered', stats.answered, 'In the last 25'], ['Forwarded to team', stats.forwarded, 'In the last 25'], ['Missed', stats.missed, 'In the last 25']].map(([l, v, s]) => (
          <Card key={l as string} className="p-4"><div className="text-xs text-muted">{l}</div><div className="mt-1 text-2xl font-semibold tabular-nums">{v}</div><div className="text-xs text-muted">{s}</div></Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader title="Phone number" description="Incoming calls reach this app only when the Plivo number points to it." />
            <div className="px-5 pb-5 text-sm"><InboundSetup /></div>
          </Card>

          <Card>
            <CardHeader title="Your team's number" description="Where callers are sent when they need a person. Use a mobile or office line with country code." />
            <div className="grid gap-4 px-5 pb-5 sm:grid-cols-[1fr_auto] sm:items-end">
              <Field label="Transfer number" hint={hasNumber ? 'Callers hear “connecting you to our team”, then this number rings.' : 'Required for forwarding and transfers.'}>
                <Input type="tel" inputMode="tel" value={form.transfer_number} onChange={(e) => set('transfer_number', e.target.value)} placeholder="+91 98765 43210" maxLength={20} />
              </Field>
              <label className="flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm">
                <span><span className="block font-semibold">AI transfers on request</span><span className="text-xs text-muted">“Can I talk to a person?”</span></span>
                <Switch checked={form.transfer_on_request} onChange={(v) => set('transfer_on_request', v)} disabled={!hasNumber} label="Transfer on request" />
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader title={<span className="inline-flex items-center gap-2"><Clock className="size-4" />During calling hours</span>} description={`Inside the window set on Automation: ${hours}.`}
              action={automation.data && <Badge tone={automation.data.within_calling_hours ? 'success' : 'neutral'} dot>{automation.data.within_calling_hours ? 'Open now' : 'Closed now'}</Badge>} />
            <div className="grid gap-3 px-5 pb-5 md:grid-cols-2">
              <Choice active={form.inbound_mode === 'ai'} onClick={() => set('inbound_mode', 'ai')} icon={<Bot />} title="AI agent answers"
                detail={`${data.profile.agent_name} greets the caller, answers from the knowledge base and logs everything to the CRM.${form.transfer_on_request && hasNumber ? ' Hands over to your team when asked.' : ''}`} />
              <Choice active={form.inbound_mode === 'forward'} onClick={() => set('inbound_mode', 'forward')} disabled={!hasNumber} icon={<PhoneForwarded />} title="Forward to my team"
                detail={hasNumber ? `Every call rings ${form.transfer_number} directly. The call is still logged.` : 'Add a transfer number first.'} />
            </div>
          </Card>

          <Card>
            <CardHeader title={<span className="inline-flex items-center gap-2"><MoonStar className="size-4" />After hours</span>} description="Outside calling hours and on days you don't call." />
            <div className="space-y-3 px-5 pb-5">
              <div className="grid gap-3 md:grid-cols-3">
                <Choice active={form.after_hours_mode === 'ai'} onClick={() => set('after_hours_mode', 'ai')} icon={<Bot />} title="AI agent answers" detail="Around the clock." />
                <Choice active={form.after_hours_mode === 'forward'} onClick={() => set('after_hours_mode', 'forward')} disabled={!hasNumber} icon={<UserRound />} title="Forward to team" detail={hasNumber ? 'Rings your number.' : 'Add a transfer number first.'} />
                <Choice active={form.after_hours_mode === 'message'} onClick={() => set('after_hours_mode', 'message')} icon={<MoonStar />} title="Closed message" detail="Plays a message, then hangs up." />
              </div>
              {form.after_hours_mode === 'message' && (
                <Field label="Closed message" hint="Leave empty for a default message in the caller's language.">
                  <Textarea rows={2} maxLength={300} value={form.after_hours_message} onChange={(e) => set('after_hours_message', e.target.value)}
                    placeholder="Thanks for calling. We're closed right now: please call again between 9 AM and 9 PM." />
                </Field>
              )}
            </div>
          </Card>
        </div>

        <Card className="h-fit">
          <CardHeader title="Recent inbound calls" description="Live: updates while calls ring and connect."
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
            ) : <EmptyState icon={<PhoneIncoming />} title="No inbound calls yet" description="Connect the number above, then call it to test." />}
        </Card>
      </div>

      <CallSheet callId={callId} onClose={() => setCallId(null)} />
    </>
  )
}
