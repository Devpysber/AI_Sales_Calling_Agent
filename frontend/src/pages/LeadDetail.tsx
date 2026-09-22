import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Ban, Bot, Building2, CalendarClock, Check, ChevronRight, Clock, Copy, Gauge, Lightbulb, ListPlus, Mail, MapPin,
  Globe, MessageSquareQuote, Pencil, Phone, PhoneCall, PhoneIncoming, PhoneOutgoing, Play, RefreshCw, ShieldAlert, Sparkles, Target, Trash2, User,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import ActivityFeed from '@/components/ActivityFeed'
import CallSheet from '@/components/CallSheet'
import { LeadFormSheet, useStartCall } from '@/components/LeadSheets'
import { CallStatusBadge, QualificationBadge, SentimentDot } from '@/components/status'
import { Avatar, Badge, Button, Card, CardHeader, Dialog, EmptyState, Input, PageHeader, Ring, Select, ShowMore, Skeleton, Switch, Tabs, Textarea, useConfirm } from '@/components/ui'
import { api, ApiError } from '@/lib/api'
import { Stagger } from '@/lib/motion'
import { useAgent } from '@/lib/agent'
import type { ActivityEvent, Call, Lead, Page } from '@/lib/types'
import { cn, formatDate, formatDuration, LANGUAGES, LEAD_STATUSES, timeAgo, titleCase } from '@/lib/utils'

const JOURNEY = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won']
const LIVE_STATUSES = ['Queued', 'Ringing', 'In Progress']
const TEMP_SCORE: Record<string, number> = { Cold: 1, Warm: 2, Hot: 3 }
/** How long after a call ends we keep polling it for the AI summary before giving up on the fast poll. */
const SUMMARY_WAIT_MS = 3 * 60_000
const isGone = (e: unknown) => e instanceof ApiError && e.status === 404
/** Short stage names for the journey bar on narrow screens. */
const JOURNEY_SHORT: Record<string, string> = { 'New': 'New', 'Contacted': 'Contact', 'Interested': 'Interest', 'Follow Up': 'Follow', 'Meeting Booked': 'Meeting', 'Closed Won': 'Won' }

function nextAction(lead: Lead, calls: Call[]): { title: string; detail: string; kind: 'call' | 'meeting' | 'stop' | 'followup' | 'live' } {
  if (calls.some((c) => LIVE_STATUSES.includes(c.status))) return { title: 'Call in progress', detail: 'The agent is on the phone with this lead right now. The transcript below updates live.', kind: 'live' }
  const connected = calls.filter((c) => c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0))
  if (lead.do_not_call) return { title: 'Do not contact', detail: 'This lead asked not to be called. It is excluded from every call and campaign.', kind: 'stop' }
  if (lead.callback_at) {
    // The scheduler only dials inside calling hours, and a time already past is not a plan: saying
    // "the agent will dial automatically at that time" for either one promised what nothing delivers.
    const at = new Date(lead.callback_at.replace(' ', 'T') + '+05:30')
    const overdue = at.getTime() < Date.now()
    return {
      title: `Callback at ${formatDate(lead.callback_at.replace(' ', 'T') + '+05:30')}`,
      detail: overdue
        ? 'This time has passed. The agent dials on its next run inside calling hours, or call now.'
        : 'The customer asked to be called back. The agent dials automatically at that time.',
      kind: 'followup',
    }
  }
  if (lead.status === 'Not Interested') return { title: 'Nurture later', detail: 'Not interested right now. Revisit in a few months with a new offer.', kind: 'stop' }
  if (lead.meeting_at) {
    const at = new Date(lead.meeting_at.replace(' ', 'T') + '+05:30')
    if (at.getTime() > Date.now()) return { title: `Prepare for the meeting on ${formatDate(lead.meeting_at.replace(' ', 'T') + '+05:30')}`, detail: 'Review the requirements and objections before the meeting.', kind: 'meeting' }
    return { title: 'Meeting time has passed', detail: `It was set for ${formatDate(lead.meeting_at.replace(' ', 'T') + '+05:30')} IST. Call to confirm the outcome or rebook.`, kind: 'call' }
  }
  if (!calls.length) return { title: 'Place the first call', detail: 'No conversation yet. Call now or queue the lead for auto-dial.', kind: 'call' }
  if (!connected.length) return { title: 'Retry at a different time', detail: `${calls.length} attempt(s), none answered. Try another hour of the day.`, kind: 'call' }
  if (lead.follow_up_date) {
    const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' })
    return lead.follow_up_date < today
      ? { title: 'Follow-up overdue', detail: `It was due on ${lead.follow_up_date}. Call back today.`, kind: 'followup' }
      : { title: `Follow up on ${lead.follow_up_date}`, detail: 'The customer asked to be contacted again.', kind: 'followup' }
  }
  if (lead.qualification === 'Hot' || lead.qualification === 'Warm') return { title: 'Book a meeting', detail: 'The lead shows interest. Call to confirm a day and time.', kind: 'call' }
  return { title: 'Qualify further', detail: 'Interest is unclear. The next call should find the requirement and budget.', kind: 'call' }
}

/** 0-100 engagement score from temperature, stage, connect rate and recency. */
function leadScore(lead: Lead, calls: Call[]) {
  if (lead.do_not_call) return 0
  const temp = { Hot: 45, Warm: 28, Cold: 8 }[lead.qualification ?? ''] ?? 12
  const stage = Math.max(0, JOURNEY.indexOf(lead.status)) * 6
  const connected = calls.filter((c) => c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0)).length
  const reach = calls.length ? Math.round((15 * connected) / calls.length) : 0
  const recent = lead.last_contacted_at && Date.now() - Date.parse(lead.last_contacted_at) < 7 * 86_400_000 ? 10 : 0
  return Math.min(100, temp + stage + reach + recent)
}

function CopyChip({ icon, value, href }: { icon: ReactNode; value: string; href?: string }) {
  return (
    <span className="group inline-flex min-h-10 max-w-full items-center gap-1.5 rounded-full border border-border bg-surface py-1 pr-1 pl-2.5 text-[13px] text-fg-2 sm:min-h-0 [&>svg]:size-3.5 [&>svg]:shrink-0">
      {icon}
      {href ? <a href={href} className="min-w-0 truncate hover:text-fg hover:underline">{value}</a> : <span className="min-w-0 truncate">{value}</span>}
      <button type="button" title="Copy" aria-label="Copy" onClick={() => { navigator.clipboard?.writeText(value).then(() => toast.success('Copied'), () => toast.error('Could not copy')) }}
        className="-my-1 grid size-10 shrink-0 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-fg sm:my-0 sm:size-5"><Copy className="size-3" /></button>
    </span>
  )
}

function Metric({ icon, label, value, sub, className, style }: { icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode; className?: string; style?: CSSProperties }) {
  return (
    <div className={cn('min-w-0 px-4 py-3.5 sm:px-5 sm:py-4', className)} style={style}>
      <div className="flex items-center gap-1.5 truncate text-[11px] font-bold tracking-wider whitespace-nowrap text-muted uppercase [&_svg]:size-3.5 [&_svg]:shrink-0">{icon}{label}</div>
      <div className="mt-1.5 text-[22px] leading-none font-extrabold tracking-tight tabular-nums">{value}</div>
      {sub && <div className="mt-1.5 truncate text-xs text-muted">{sub}</div>}
    </div>
  )
}

function Insight({ icon, title, children, empty }: { icon: ReactNode; title: string; children: ReactNode; empty: string }) {
  return (
    <div className="rounded-2xl border border-border bg-surface-2/40 p-4">
      <div className="mb-1.5 flex items-center gap-2 text-[13px] font-bold [&_svg]:size-4">{icon}{title}</div>
      {children ? <div className="text-sm leading-relaxed break-words text-fg-2">{typeof children === 'string' ? <ShowMore text={children} lines={4} limit={320} /> : children}</div> : <p className="text-sm text-muted">{empty}</p>}
    </div>
  )
}

export default function LeadDetail() {
  const { leadId: raw } = useParams()
  const leadId = Number(raw)
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const startCall = useStartCall()
  const [tab, setTab] = useState<'conversation' | 'calls' | 'activity'>('conversation')
  const [callId, setCallId] = useState<number | null>(null)
  const [transcriptOf, setTranscriptOf] = useState<number | null>(null)
  const [editing, setEditing] = useState(false)
  const [notes, setNotes] = useState<string | null>(null)
  const validId = Number.isInteger(leadId) && leadId > 0
  // Navigating from one lead to another reuses this component, so per-lead UI state must not leak across.
  useEffect(() => { setCallId(null); setTranscriptOf(null); setEditing(false); setNotes(null) }, [leadId])

  const lead = useQuery({ queryKey: ['lead', leadId], queryFn: () => api<Lead>(`${base}/leads/${leadId}`), enabled: validId, refetchInterval: (q) => (isGone(q.state.error) ? false : 5000) })
  // Once the lead is gone (404) stop every poll for it instead of re-requesting a deleted record until the user leaves.
  const leadGone = isGone(lead.error)
  const calls = useQuery({ queryKey: ['calls', 'lead', leadId], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { lead_id: leadId, page_size: 100 } }), enabled: validId && !leadGone, refetchInterval: (q) => (q.state.data?.items.some((c) => LIVE_STATUSES.includes(c.status)) ? 2000 : 5000) })
  const activity = useQuery({ queryKey: ['activity', 'lead', leadId], queryFn: () => api<ActivityEvent[]>(`${base}/leads/${leadId}/activity`), enabled: validId && !leadGone, refetchInterval: (q) => (isGone(q.state.error) ? false : 8000) })

  const items = useMemo(() => calls.data?.items ?? [], [calls.data])
  // A conversation = the customer said something (greeting-only calls have 1 turn)
  const talked = useMemo(() => items.filter((c) => (c.status === 'Completed' || c.status === 'Failed') && (c.turns ?? 0) > 1), [items])
  const analyzed = talked.find((c) => c.outcome || c.summary) ?? talked[0]
  const liveCall = items.find((c) => LIVE_STATUSES.includes(c.status))
  const shownCallId = (transcriptOf !== null && items.some((c) => c.id === transcriptOf) ? transcriptOf : null) ?? liveCall?.id ?? talked[0]?.id ?? null
  const conversation = useQuery({
    queryKey: ['call', shownCallId],
    queryFn: () => api<Call>(`${base}/calls/${shownCallId}`),
    enabled: shownCallId !== null,
    // Live transcript while the call runs, then until the AI summary lands. The summary wait is capped: when the
    // analysis LLM is down the summary never arrives, so after a few minutes we fall back to the 5s calls list.
    refetchInterval: (q) => {
      const c = q.state.data
      if (!c) return false
      if (LIVE_STATUSES.includes(c.status)) return 2000
      const ended = (c.status === 'Completed' || c.status === 'Failed') && !c.summary && (c.transcript?.length ?? 0) > 1
      if (!ended) return false
      const endedAt = Date.parse(c.ended_at ?? c.created_at)
      return Number.isFinite(endedAt) && Date.now() - endedAt < SUMMARY_WAIT_MS ? 2000 : false
    },
  })


  const patch = useMutation({
    mutationFn: (body: Partial<Lead>) => api<Lead>(`${base}/leads/${leadId}`, { method: 'PATCH', json: body }),
    onSuccess: (l) => { qc.setQueryData(['lead', leadId], l); qc.invalidateQueries({ queryKey: ['leads'] }); qc.invalidateQueries({ queryKey: ['activity', 'lead', leadId] }); toast.success('Lead updated') },
    onError: (e) => toast.error(e.message),
  })
  const queue = useMutation({
    mutationFn: (at?: string) => api<{ queued?: number; skipped?: number; eta?: string }>(`${base}/leads/bulk/queue`, { method: 'POST', json: { ids: [leadId], at: at || undefined } }),
    onSuccess: (res) => {
      if (res.queued) toast.success('Added to the call queue', { description: res.eta ?? 'It will be called within calling hours.' })
      else toast.warning('Not queued', { description: 'Do-Not-Call or invalid number.' })
      qc.invalidateQueries({ queryKey: ['lead', leadId] })
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['activity', 'lead', leadId] })
    },
    onError: (e) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: () => api(`${base}/leads/${leadId}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Lead deleted'); qc.invalidateQueries({ queryKey: ['leads'] }); navigate(path('/leads')) },
    onError: (e) => toast.error(e.message),
  })

  const stats = useMemo(() => {
    const connected = items.filter((c) => c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0))
    const talk = connected.reduce((a, c) => a + (c.duration || 0), 0)
    const lat = connected.map((c) => c.avg_latency_ms).filter((v): v is number => !!v)
    const hours = new Map<number, { calls: number; connected: number }>()
    for (const c of items) {
      const h = Number(new Date(c.created_at).toLocaleString('en-US', { timeZone: 'Asia/Kolkata', hour: 'numeric', hour12: false })) % 24
      const cur = hours.get(h) ?? { calls: 0, connected: 0 }
      cur.calls += 1
      cur.connected += c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0) ? 1 : 0
      hours.set(h, cur)
    }
    const bestHour = [...hours.entries()].filter(([, v]) => v.connected).sort((a, b) => b[1].connected / b[1].calls - a[1].connected / a[1].calls)[0]
    return {
      total: items.length, connected: connected.length,
      rate: items.length ? Math.round((connected.length / items.length) * 100) : 0,
      talk, avgTalk: connected.length ? Math.round(talk / connected.length) : 0,
      latency: lat.length ? Math.round(lat.reduce((a, v) => a + v, 0) / lat.length) : null,
      trend: [...connected].reverse().filter((c) => c.qualification),
      bestHour: bestHour ? bestHour[0] : null,
    }
  }, [items])

  const transcriptRef = useRef<HTMLDivElement>(null)
  const transcriptLen = conversation.data?.transcript?.length ?? 0
  const shownIsLive = !!conversation.data && LIVE_STATUSES.includes(conversation.data.status)
  useEffect(() => {
    const el = transcriptRef.current
    if (el && shownIsLive) el.scrollTop = el.scrollHeight
  }, [transcriptLen, shownIsLive])

  const l = lead.data
  const notFound = !validId || (lead.isError && lead.error instanceof ApiError && lead.error.status === 404)
  if (notFound) {
    return <Card className="mt-6"><EmptyState icon={<User />} title="Lead not found" description="It may have been deleted, or it belongs to another agent." action={<Link to={path('/leads')}><Button><ArrowLeft />Back to leads</Button></Link>} /></Card>
  }
  if (lead.isError && !l) {
    return <Card className="mt-6"><EmptyState icon={<User />} title="Could not load this lead" description={lead.error.message} action={<div className="flex flex-wrap justify-center gap-2"><Button variant="primary" loading={lead.isFetching} onClick={() => lead.refetch()}><RefreshCw />Try again</Button><Link to={path('/leads')}><Button><ArrowLeft />Back to leads</Button></Link></div>} /></Card>
  }
  if (!l) return <div className="space-y-4"><Skeleton className="h-40" /><Skeleton className="h-24" /><Skeleton className="h-96" /></div>

  const action = nextAction(l, items)
  const score = leadScore(l, items)
  const transcript = conversation.data?.transcript ?? []
  const stageIndex = JOURNEY.indexOf(l.status)
  const offJourney = stageIndex < 0

  return (
    <>
      <PageHeader
        eyebrow={<>
          <Link to={path('/leads')} className="inline-flex items-center gap-1 hover:underline"><ArrowLeft className="size-3.5" />Leads</Link>
          <ChevronRight className="size-3" /><span className="truncate">{agent?.name}</span>
        </>}
        title={<span className="flex flex-wrap items-center gap-3">
          <Avatar name={l.name ?? l.phone} className="size-12 text-base" />
          <span className="min-w-0 break-words">{l.name ?? 'Unnamed lead'}</span>
          {l.do_not_call && <Badge tone="danger"><Ban className="size-3" />Do not call</Badge>}
        </span>}
        description={<span className="mt-1 flex flex-wrap gap-2">
          <CopyChip icon={<Phone />} value={l.phone} href={`tel:${l.phone}`} />
          {l.email && <CopyChip icon={<Mail />} value={l.email} href={`mailto:${l.email}`} />}
          {l.company && <CopyChip icon={<Building2 />} value={l.company} />}
          {l.city && <CopyChip icon={<MapPin />} value={l.city} />}
        </span>}
        actions={<>
          <Button variant="outline-danger" size="icon" title="Delete lead" aria-label="Delete lead" loading={remove.isPending} onClick={async () => {
            if (await confirm({ title: `Delete ${l.name ?? 'this lead'}?`, description: 'The lead and its activity are removed permanently. Call records are kept.', confirmLabel: 'Delete', danger: true })) remove.mutate()
          }}><Trash2 /></Button>
          <Button onClick={() => setEditing(true)}><Pencil />Edit</Button>
          <Button onClick={() => navigate(path(`/emails?lead=${l.id}`))}><Mail />Email</Button>
          <QueueButton disabled={l.do_not_call} loading={queue.isPending} onQueue={(at) => queue.mutate(at)} />
          <Button variant="primary" disabled={l.do_not_call || l.phone_valid === false} loading={startCall.isPending} onClick={() => startCall.mutate(l.id)}><PhoneCall />Call now</Button>
        </>}>
        {/* Journey */}
        <div className="rounded-2xl border border-border bg-surface p-4 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-[11px] font-bold tracking-wider text-muted uppercase">Journey</div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={l.status} disabled={patch.isPending} onChange={(e) => patch.mutate({ status: e.target.value })} className="h-10 w-auto max-w-full text-[13px] sm:h-8" aria-label="Stage">
                {LEAD_STATUSES.map((s) => <option key={s}>{s}</option>)}
              </Select>
              <Select value={l.qualification ?? ''} disabled={patch.isPending} onChange={(e) => patch.mutate({ qualification: e.target.value })} className="h-10 w-auto max-w-full text-[13px] sm:h-8" aria-label="Temperature">
                <option value="">No temperature</option>{['Hot', 'Warm', 'Cold'].map((q) => <option key={q}>{q}</option>)}
              </Select>
            </div>
          </div>
          <ol className="mt-4 grid grid-cols-6 gap-1.5">
            {JOURNEY.map((s, i) => {
              const done = !offJourney && i <= stageIndex
              return (
                <li key={s} className="reveal reveal-in reveal-up" style={{ animationDelay: `${i * 35}ms` }}>
                  <button type="button" disabled={patch.isPending} aria-label={`Move to ${s}`} onClick={() => s !== l.status && patch.mutate({ status: s })} className="group flex min-h-10 w-full flex-col justify-center py-2 text-left disabled:opacity-60 sm:block sm:min-h-0 sm:py-0" title={`Move to ${s}`}>
                    <span className={cn('block h-1.5 rounded-full transition', done ? 'bg-fg' : 'bg-surface-2 group-hover:bg-border-strong')} />
                    <span className={cn('mt-2 flex min-w-0 items-center gap-1 text-[11.5px] font-semibold', i === stageIndex ? 'text-fg' : 'text-muted')}>
                      {done && i < stageIndex && <Check className="size-3 shrink-0 max-sm:hidden" />}
                      <span className="truncate text-[10px] sm:hidden">{JOURNEY_SHORT[s] ?? s}</span>
                      <span className="hidden sm:inline">{s}</span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ol>
          {offJourney && <p className="mt-2 text-xs font-semibold text-danger">Currently: {l.status}</p>}
        </div>
      </PageHeader>

      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <div className="min-w-0 space-y-6">
          {/* Next best action */}
          <div className="flex flex-wrap items-center gap-4 rounded-[var(--radius-card)] border border-border bg-surface p-5 text-fg shadow-card">
            <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><Target className="size-5" /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-bold tracking-widest text-muted uppercase">Next best action</div>
              <div className="mt-0.5 text-lg font-extrabold break-words text-fg">{action.title}</div>
              <div className="text-sm break-words text-fg-2">{action.detail}</div>
            </div>
            {action.kind !== 'stop' && action.kind !== 'live' && (
              <button type="button" disabled={startCall.isPending}
                onClick={() => startCall.mutate({ leadId: l.id, purpose: action.kind === 'meeting' ? 'confirm_meeting' : action.kind === 'followup' ? 'follow_up' : undefined })}
                className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-fg px-4 text-sm font-bold text-bg transition hover:opacity-90 disabled:opacity-60 sm:w-auto">
                <PhoneCall className="size-4" />{action.kind === 'meeting' ? 'Call to confirm' : 'Call now'}
              </button>
            )}
          </div>

          {/* Metrics */}
          <Stagger className="grid grid-cols-2 divide-border overflow-hidden rounded-[var(--radius-card)] border border-border bg-surface shadow-card sm:grid-cols-3 lg:grid-cols-5 lg:divide-x max-lg:[&>*:not(:last-child)]:border-b max-lg:[&>*]:border-border">
            <Metric icon={<PhoneOutgoing />} label="Calls" value={stats.total} sub={`${stats.connected} connected`} />
            <Metric icon={<Check />} label="Connect rate" value={`${stats.rate}%`} sub={stats.total ? `${stats.total - stats.connected} unanswered` : 'No attempts yet'} />
            <Metric icon={<Clock />} label="Talk time" value={formatDuration(stats.talk)} sub={stats.connected ? `${formatDuration(stats.avgTalk)} per call` : '—'} />
            <Metric icon={<Gauge />} label="AI reply" value={stats.latency ? `${(stats.latency / 1000).toFixed(1)}s` : '—'} sub="Average response time" />
            <Metric icon={<CalendarClock />} label="Best hour" className="max-sm:col-span-2 sm:max-lg:col-span-2" value={stats.bestHour !== null ? `${stats.bestHour}:00` : '—'} sub={stats.bestHour !== null ? 'When they pick up · IST' : 'Not enough data'} />
          </Stagger>

          {/* AI analysis */}
          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4" />AI analysis</span>}
              description={talked.length ? `Built from ${talked.length} conversation${talked.length > 1 ? 's' : ''} · latest ${formatDate(talked[0]!.created_at)}` : 'Appears after the first answered call.'} />
            <div className="space-y-3 px-4 pb-4 sm:px-5 sm:pb-5">
              {/* The lead-level summary is written after a call is analysed; until it lands, show the latest
                  call's own summary instead of claiming there is nothing, which contradicted the transcript below. */}
              <Insight icon={<Bot />} title="Summary" empty="No summary yet.">{l.summary || analyzed?.summary}</Insight>
              <div className="grid gap-3 md:grid-cols-2">
                <Insight icon={<Lightbulb />} title="Requirements" empty="No requirement stated by the customer.">{l.requirements}</Insight>
                <Insight icon={<ShieldAlert />} title="Objections" empty="No objections raised.">{l.objections}</Insight>
              </div>
              {talked[0] && (
                <Stagger className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                  {[['Last outcome', analyzed.outcome ? titleCase(analyzed.outcome) : '—'], ['Sentiment', analyzed.sentiment ? <SentimentDot value={analyzed.sentiment} /> : '—'],
                    ['Temperature', <QualificationBadge value={l.qualification ?? analyzed?.qualification} />], ['Language', LANGUAGES[l.language] ?? l.language]].map(([k, v]) => (
                    <div key={k as string} className="min-w-0 rounded-xl border border-border px-3 py-2.5">
                      <div className="text-[11px] font-bold tracking-wider text-muted uppercase">{k as string}</div>
                      <div className="mt-1 font-bold break-words">{v as ReactNode}</div>
                    </div>
                  ))}
                </Stagger>
              )}
            </div>
          </Card>

          {/* Conversation / calls / activity */}
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5 sm:py-3.5">
              <Tabs value={tab} onChange={setTab} items={[
                { value: 'conversation', label: 'Transcript' },
                { value: 'calls', label: `Calls (${stats.total})` },
                { value: 'activity', label: `Timeline (${activity.data?.length ?? 0})` },
              ]} />
              {tab === 'conversation' && shownCallId !== null && (
                <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">
                  <Select value={shownCallId ?? ''} onChange={(e) => setTranscriptOf(Number(e.target.value))} className="h-10 w-auto min-w-0 max-w-full text-[13px] sm:h-8" aria-label="Conversation">
                    {shownCallId !== null && !talked.some((c) => c.id === shownCallId) && <option value={shownCallId}>{liveCall?.id === shownCallId ? 'Live call' : `Call #${shownCallId}`}</option>}
                    {talked.map((c) => <option key={c.id} value={c.id}>{formatDate(c.created_at)} · {formatDuration(c.duration)}</option>)}
                  </Select>
                  {shownCallId && <Button size="sm" onClick={() => setCallId(shownCallId)}>Details</Button>}
                </div>
              )}
            </div>

            <div className="p-4 sm:p-5">
              {tab === 'conversation' && (
                calls.isLoading ? <Skeleton className="h-48" />
                  : calls.isError && shownCallId === null ? <p className="text-sm text-danger">Could not load conversations. {calls.error.message}</p>
                  : shownCallId === null ? <EmptyState icon={<MessageSquareQuote />} title="No conversations yet" description="Transcripts appear here after an answered call." />
                  : conversation.isLoading ? <Skeleton className="h-48" />
                    : conversation.isError ? <p className="text-sm text-danger">Could not load this transcript. {conversation.error.message}</p>
                    : transcript.length === 0 ? <p className="text-sm text-muted">{liveCall && liveCall.id === shownCallId ? 'Waiting for the first words...' : 'No transcript was recorded for this call.'}</p>
                      : (
                        <div className="space-y-3">
                          {conversation.data?.summary && <div className="rounded-xl bg-surface-2 p-3 text-[13px] break-words text-fg-2"><b className="text-fg">Call summary:</b> {conversation.data.summary}</div>}
                          {conversation.data?.recording_url && <audio controls src={conversation.data.recording_url} className="w-full" />}
                          <div ref={transcriptRef} className="space-y-3 max-h-[60vh] overflow-y-auto pr-2">
                            {transcript.map((t, i) => {
                            const agentTurn = t.role === 'assistant'
                            return (
                              <div key={i} className={cn('reveal reveal-in flex gap-2.5', !agentTurn ? 'reveal-right flex-row-reverse' : 'reveal-left')}>
                                <span className={cn('grid size-8 shrink-0 place-items-center rounded-full', agentTurn ? 'bg-fg text-bg' : 'border border-border bg-surface-2 text-fg')}>
                                  {agentTurn ? <Bot className="size-4" /> : <User className="size-4" />}
                                </span>
                                <div className={cn('min-w-0 max-w-[85%] sm:max-w-[78%]', !agentTurn && 'text-right')}>
                                  <div className="mb-0.5 text-[11px] font-semibold text-muted">{agentTurn ? agent?.persona.agent_name ?? 'Agent' : l.name ?? 'Customer'}{t.at && ` · ${new Date(t.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}</div>
                                  <div className={cn('inline-block rounded-2xl px-3.5 py-2 text-left text-sm leading-relaxed break-words', agentTurn ? 'rounded-tl-sm bg-surface-2 ring-1 ring-border' : 'rounded-tr-sm bg-fg text-bg')}>{t.text}</div>
                                </div>
                              </div>
                            )
                          })}
                          </div>
                          <p className="flex items-center gap-1.5 pt-2 text-xs text-muted"><MessageSquareQuote className="size-3.5" />Customer lines come from phone speech recognition and may contain mistakes.</p>
                        </div>
                      )
              )}

              {tab === 'calls' && (
                calls.isLoading ? <div className="space-y-2"><Skeleton className="h-14" /><Skeleton className="h-14" /><Skeleton className="h-14" /></div>
                : calls.isError ? <p className="text-sm text-danger">Could not load calls. {calls.error.message}</p>
                : items.length ? (
                  <ol className="relative space-y-1 before:absolute before:top-3 before:bottom-3 before:left-[27px] before:w-px before:bg-border">
                    {items.map((c, i) => (
                      <li key={c.id} className="reveal reveal-in reveal-up" style={{ animationDelay: `${i * 35}ms` }}>
                        <div className="relative flex w-full min-w-0 items-start gap-3 rounded-xl p-2 text-left hover:bg-surface-2">
                          <button type="button" onClick={() => setCallId(c.id)} aria-label={`Call details, ${formatDate(c.created_at)}`} className="flex min-w-0 flex-1 items-start gap-3 rounded-lg text-left">
                          <span className={cn('z-10 grid size-[38px] shrink-0 place-items-center rounded-full border border-border bg-surface', (c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0)) && 'border-fg bg-fg text-bg')}>
                            {c.direction === 'inbound' ? <PhoneIncoming className="size-4" /> : <PhoneOutgoing className="size-4" />}
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-bold">{formatDate(c.created_at)}</span>
                              <CallStatusBadge status={c.status} />
                              <QualificationBadge value={c.qualification} />
                              <span className="text-xs text-muted">{titleCase(c.trigger)}</span>
                            </div>
                            <div className="mt-0.5 line-clamp-2 text-[13px] break-words text-muted">{c.summary ?? c.error ?? c.hangup_cause ?? `${c.turns ?? 0} turns`}</div>
                          </div>
                          </button>
                          {(c.status === 'Completed' || c.status === 'Failed') && (c.turns ?? 0) > 0 && (
                            <button type="button" title="Show transcript" aria-label="Show transcript"
                              onClick={() => { setTranscriptOf(c.id); setTab('conversation') }}
                              className="grid size-10 shrink-0 place-items-center rounded-lg text-muted hover:bg-surface hover:text-fg sm:size-8"><Play className="size-3.5" /></button>
                          )}
                          <span className="min-w-10 shrink-0 text-right text-xs whitespace-nowrap text-muted tabular-nums sm:min-w-14 sm:text-sm">{formatDuration(c.duration)}</span>
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : <EmptyState icon={<PhoneCall />} title="No calls yet" action={<Button variant="primary" disabled={l.do_not_call} loading={startCall.isPending} onClick={() => startCall.mutate(l.id)}><PhoneCall />Call now</Button>} />
              )}

              {tab === 'activity' && (
                activity.isLoading ? <Skeleton className="h-32" />
                : activity.isError ? <p className="text-sm text-danger">Could not load the timeline. {activity.error.message}</p>
                : activity.data?.length ? <ActivityFeed events={activity.data} onCall={setCallId} /> : <p className="text-sm text-muted">No activity yet.</p>
              )}
            </div>
          </Card>
        </div>

        {/* Side column */}
        <div className="min-w-0 space-y-6">
          <Card className="p-4 sm:p-5">
            <div className="flex items-center gap-4">
              <Ring value={score / 100} size={72} stroke={7}><span className="text-lg font-extrabold">{score}</span></Ring>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-bold tracking-wider text-muted uppercase">Lead score</div>
                <div className="text-lg font-extrabold">{score >= 70 ? 'Very engaged' : score >= 45 ? 'Promising' : score >= 20 ? 'Early stage' : l.do_not_call ? 'Do not call' : 'Cold'}</div>
                <div className="text-xs text-muted">From temperature, stage, answer rate and recency</div>
              </div>
            </div>
            <div className="mt-5">
              <div className="mb-2 text-[11px] font-bold tracking-wider text-muted uppercase">Interest after each answered call</div>
              {stats.trend.length ? (
                <div className="flex h-24 items-end gap-2">
                  {stats.trend.slice(-10).map((c) => (
                    <button key={c.id} type="button" onClick={() => setCallId(c.id)} title={`${formatDate(c.created_at)} · ${c.qualification}`} aria-label={`${formatDate(c.created_at)} · ${c.qualification}`} className="group flex min-w-0 flex-1 flex-col items-center justify-end gap-1 self-stretch">
                      <span className={cn('w-full max-w-7 rounded-md transition group-hover:opacity-70', c.qualification === 'Hot' ? 'bg-danger' : c.qualification === 'Warm' ? 'bg-warning' : 'bg-fg/60')}
                        style={{ height: `${(TEMP_SCORE[c.qualification!] ?? 0.5) * 24}px` }} />
                      <span className="text-[10px] text-muted">{c.qualification?.[0]}</span>
                    </button>
                  ))}
                </div>
              ) : <p className="text-sm text-muted">No qualified calls yet.</p>}
            </div>
          </Card>

          <Card>
            <CardHeader title="Follow-up & preferences" />
            <div className="space-y-4 px-4 pb-4 sm:px-5 sm:pb-5">
              <FollowUpScheduler lead={l} saving={patch.isPending} onSave={(body) => patch.mutate(body)} />
              <label className="grid gap-1.5"><span className="text-[13px] font-semibold text-fg-2">Call language</span>
                <Select value={l.language} disabled={patch.isPending} onChange={(e) => patch.mutate({ language: e.target.value })}>
                  {Object.entries(LANGUAGES).map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </Select></label>
              <div className="flex items-center justify-between gap-3 rounded-xl border border-border p-3">
                <div className="min-w-0"><div className="text-sm font-bold">Do not call</div><div className="text-xs text-muted">Excluded from manual and automated calls</div></div>
                <Switch checked={l.do_not_call} disabled={patch.isPending} onChange={(v) => patch.mutate({ do_not_call: v })} label="Do not call" />
              </div>
            </div>
          </Card>

          {(l.source ?? '').startsWith('website') && (
            <Card className="border-brand/30">
              <CardHeader title={<span className="flex items-center gap-2"><Globe className="size-4 text-brand" />Website enquiry</span>}
                description={`Came in from ${(l.source ?? '').replace(/^website:?/, '') || 'the website'} · the agent opens the call with this`} />
              <div className="px-4 pb-4 sm:px-5 sm:pb-5">
                {(l.notes || '').split('\n').filter((line) => line.trim()).map((line, i) => {
                  const m = /^([^:]{1,40}):\s*(.+)$/.exec(line)
                  return m
                    ? <div key={i} className="flex flex-wrap gap-x-3 gap-y-0.5 border-b border-border/60 py-1.5 text-sm last:border-0"><dt className="w-32 shrink-0 text-muted">{m[1]}</dt><dd className="min-w-0 break-words">{m[2]}</dd></div>
                    : <p key={i} className="py-1.5 text-sm break-words">{line}</p>
                })}
                {!l.notes && <p className="text-sm text-muted">The form carried no message.</p>}
              </div>
            </Card>
          )}

          <Card>
            <CardHeader title="Notes for the agent" description="The AI reads these before every call"
              action={notes === null ? <Button size="sm" variant="ghost" onClick={() => setNotes(l.notes ?? '')}><Pencil />Edit</Button> : undefined} />
            <div className="px-4 pb-4 sm:px-5 sm:pb-5">
              {notes === null
                ? <p className="text-sm break-words whitespace-pre-wrap text-fg-2">{l.notes || <span className="text-muted">No notes yet. Add context like budget, preferred time or previous purchases.</span>}</p>
                : <div className="space-y-2">
                  <Textarea rows={5} autoFocus value={notes} onChange={(e) => setNotes(e.target.value)} disabled={patch.isPending} />
                  <div className="flex justify-end gap-2">
                    <Button size="sm" disabled={patch.isPending} onClick={() => setNotes(null)}>Cancel</Button>
                    <Button size="sm" variant="primary" loading={patch.isPending} onClick={() => patch.mutate({ notes }, { onSuccess: () => setNotes(null) })}>Save notes</Button>
                  </div>
                </div>}
            </div>
          </Card>

          <Card>
            <CardHeader title="Details" />
            <dl className="divide-y divide-border px-4 pb-3 text-sm sm:px-5">
              {([
                ['Meeting', l.meeting_at ? `${l.meeting_at} IST` : null],
                ['Last call', <CallStatusBadge status={l.call_status} />],
                ['Retries', l.retry_count || null],
                ['Last contact', l.last_contacted_at ? timeAgo(l.last_contacted_at) : null],
                ['Source', l.source],
                ['Tags', l.tags?.length ? <span className="flex flex-wrap justify-end gap-1">{l.tags.map((t) => <Badge key={t}>{t}</Badge>)}</span> : null],
                ['Created', formatDate(l.created_at)],
                ['Updated', formatDate(l.updated_at)],
              ] as [string, ReactNode][]).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-4 py-2.5">
                  <dt className="shrink-0 text-muted">{k}</dt><dd className="min-w-0 text-right font-semibold break-words">{v || <span className="font-normal text-muted">—</span>}</dd>
                </div>
              ))}
            </dl>
          </Card>
        </div>
      </div>

      <CallSheet callId={callId} onClose={() => setCallId(null)} />
      <LeadFormSheet key={editing ? l.id : 'closed'} open={editing} lead={editing ? l : null} onClose={() => setEditing(false)} />
    </>
  )
}

const toLocalInput = (v?: string | null) => (v ? v.replace(' ', 'T').slice(0, 16) : '')
const inMinutes = (m: number) => {
  const d = new Date(new Date(Date.now() + m * 60_000).toLocaleString("en-US", {timeZone: "Asia/Kolkata"}))
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Pick when the agent should call back: saved as a scheduled call that dials itself at that time. */
function FollowUpScheduler({ lead, saving, onSave }: { lead: Lead; saving: boolean; onSave: (body: Partial<Lead>) => void }) {
  const [at, setAt] = useState(toLocalInput(lead.callback_at))
  useEffect(() => { setAt(toLocalInput(lead.callback_at)) }, [lead.callback_at])
  const changed = at !== toLocalInput(lead.callback_at)
  return (
    <div className="space-y-2">
      <span className="text-[13px] font-semibold text-fg-2">Follow-up call</span>
      {lead.callback_at
        ? <div className="flex flex-wrap items-center gap-2 rounded-xl bg-brand-soft px-3 py-2 text-sm text-brand"><CalendarClock className="size-4 shrink-0" /><span className="min-w-0 flex-1 font-semibold break-words">Agent calls at {lead.callback_at} IST</span>
            <button type="button" disabled={saving} className="min-h-10 px-1 text-xs font-semibold hover:underline disabled:opacity-60 sm:min-h-8" onClick={() => onSave({ callback_at: '' })}>Clear</button></div>
        : <p className="text-xs text-muted">{lead.call_status === 'Pending'
            ? <span className="inline-flex items-center gap-1.5 text-success"><span className="size-1.5 animate-pulse rounded-full bg-success" />Queued for auto-dial: the agent rings on its next run inside calling hours. Pick a time below for an exact slot.</span>
            : lead.follow_up_date ? `Follow up due ${lead.follow_up_date}. Pick a time to have the agent call automatically.` : 'Nothing scheduled.'}</p>}
      <div className="flex flex-wrap gap-1.5">
        {[['In 1 hour', 60], ['Tomorrow 11 AM', -1], ['In 3 days', 3 * 24 * 60]].map(([label, m]) => (
          <button key={label as string} type="button" onClick={() => {
            if (m === -1) { const tomorrow = new Date(new Date(Date.now() + 86_400_000).toLocaleString("en-US", {timeZone: "Asia/Kolkata"})); const pad = (n: number) => String(n).padStart(2, '0'); setAt(`${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}T11:00`) } else setAt(inMinutes(m as number))
          }} className="min-h-10 rounded-lg border border-border px-2.5 py-1 text-xs font-semibold text-fg-2 hover:border-border-strong hover:text-fg sm:min-h-0">{label}</button>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Input type="datetime-local" value={at} min={inMinutes(1)} disabled={saving} onChange={(e) => setAt(e.target.value)} className="min-w-0 flex-1 basis-40" />
        <Button variant="primary" disabled={!at || !changed} loading={saving} onClick={() => onSave({ callback_at: at })}>Schedule</Button>
      </div>
      <p className="text-xs text-muted">Times are IST. The agent dials at this time only inside your calling hours — a time outside them waits for the next open hour.</p>
    </div>
  )
}

/** Queue now, or pick a date and time for the call. */
function QueueButton({ disabled, loading, onQueue }: { disabled: boolean; loading: boolean; onQueue: (at?: string) => void }) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState('')
  return (
    <>
      <Button onClick={() => setOpen(true)} loading={loading} disabled={disabled}><ListPlus />Queue</Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Add to the call queue" description="Call as soon as a slot is free, or pick when the agent should call."
        footer={<>
          <Button onClick={() => { setOpen(false); setAt('') }}>Cancel</Button>
          <Button onClick={() => { onQueue(); setOpen(false); setAt('') }}><ListPlus />As soon as possible</Button>
          <Button variant="primary" disabled={!at} onClick={() => { onQueue(at); setOpen(false); setAt('') }}><CalendarClock />Schedule</Button>
        </>}>
        <div className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {[['In 30 min', 30], ['In 2 hours', 120], ['Tomorrow 11 AM', -1]].map(([label, m]) => (
              <button key={label as string} type="button" onClick={() => {
                if (m === -1) { const tomorrow = new Date(new Date(Date.now() + 86_400_000).toLocaleString("en-US", {timeZone: "Asia/Kolkata"})); const pad = (n: number) => String(n).padStart(2, '0'); setAt(`${tomorrow.getFullYear()}-${pad(tomorrow.getMonth() + 1)}-${pad(tomorrow.getDate())}T11:00`) } else setAt(inMinutes(m as number))
              }} className="min-h-10 rounded-lg border border-border px-2.5 py-1 text-xs font-semibold text-fg-2 hover:border-border-strong hover:text-fg sm:min-h-0">{label}</button>
            ))}
          </div>
          <Input type="datetime-local" value={at} min={inMinutes(1)} onChange={(e) => setAt(e.target.value)} className="min-w-0" />
          <p className="text-xs text-muted">Times are IST. Scheduled calls ignore the queue order and ring at the chosen minute.</p>
        </div>
      </Dialog>
    </>
  )
}

