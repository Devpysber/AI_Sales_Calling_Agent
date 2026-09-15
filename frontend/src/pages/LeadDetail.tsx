import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Ban, Bot, Building2, CalendarClock, Check, ChevronRight, Clock, Copy, Gauge, Lightbulb, ListPlus, Mail, MapPin,
  MessageSquareQuote, Pencil, Phone, PhoneCall, PhoneIncoming, PhoneOutgoing, Play, ShieldAlert, Sparkles, Target, Trash2, User,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import ActivityFeed from '@/components/ActivityFeed'
import CallSheet from '@/components/CallSheet'
import { LeadFormSheet, useStartCall } from '@/components/LeadSheets'
import { CallStatusBadge, QualificationBadge, SentimentDot } from '@/components/status'
import { Avatar, Badge, Button, Card, CardHeader, EmptyState, Input, PageHeader, Ring, Select, ShowMore, Skeleton, Switch, Tabs, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { ActivityEvent, Call, Lead, Page } from '@/lib/types'
import { cn, formatDate, formatDuration, LANGUAGES, LEAD_STATUSES, timeAgo, titleCase } from '@/lib/utils'

const JOURNEY = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won']
const LIVE_STATUSES = ['Queued', 'Ringing', 'In Progress']
const TEMP_SCORE: Record<string, number> = { Cold: 1, Warm: 2, Hot: 3 }

function nextAction(lead: Lead, calls: Call[]): { title: string; detail: string; kind: 'call' | 'meeting' | 'stop' | 'followup' } {
  const connected = calls.filter((c) => c.status === 'Completed')
  if (lead.do_not_call) return { title: 'Do not contact', detail: 'This lead asked not to be called. It is excluded from every call and campaign.', kind: 'stop' }
  if (lead.callback_at) return { title: `Callback at ${lead.callback_at}`, detail: 'The customer asked to be called back. The agent will dial automatically at that time (inside calling hours).', kind: 'followup' }
  if (lead.meeting_at) return { title: `Prepare for the meeting on ${lead.meeting_at}`, detail: 'Review the requirements and objections before the meeting.', kind: 'meeting' }
  if (!calls.length) return { title: 'Place the first call', detail: 'No conversation yet. Call now or queue the lead for auto-dial.', kind: 'call' }
  if (!connected.length) return { title: 'Retry at a different time', detail: `${calls.length} attempt(s), none answered. Try another hour of the day.`, kind: 'call' }
  if (lead.status === 'Not Interested') return { title: 'Nurture later', detail: 'Not interested right now. Revisit in a few months with a new offer.', kind: 'stop' }
  if (lead.follow_up_date) {
    const today = new Date().toLocaleDateString('en-CA')
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
  const connected = calls.filter((c) => c.status === 'Completed').length
  const reach = calls.length ? Math.round((15 * connected) / calls.length) : 0
  const recent = lead.last_contacted_at && Date.now() - Date.parse(lead.last_contacted_at) < 7 * 86_400_000 ? 10 : 0
  return Math.min(100, temp + stage + reach + recent)
}

function CopyChip({ icon, value, href }: { icon: ReactNode; value: string; href?: string }) {
  return (
    <span className="group inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-surface py-1 pr-1 pl-2.5 text-[13px] text-fg-2 [&>svg]:size-3.5 [&>svg]:shrink-0">
      {icon}
      {href ? <a href={href} className="truncate hover:text-fg hover:underline">{value}</a> : <span className="truncate">{value}</span>}
      <button type="button" title="Copy" onClick={() => { void navigator.clipboard.writeText(value); toast.success('Copied') }}
        className="grid size-5 shrink-0 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"><Copy className="size-3" /></button>
    </span>
  )
}

function Metric({ icon, label, value, sub }: { icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="px-5 py-4">
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
      {children ? <p className="text-sm leading-relaxed text-fg-2">{typeof children === 'string' ? <ShowMore text={children} lines={4} limit={320} /> : children}</p> : <p className="text-sm text-muted">{empty}</p>}
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
  const [followUp, setFollowUp] = useState('')

  const lead = useQuery({ queryKey: ['lead', leadId], queryFn: () => api<Lead>(`${base}/leads/${leadId}`), refetchInterval: 5000 })
  const calls = useQuery({ queryKey: ['calls', 'lead', leadId], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { lead_id: leadId, page_size: 100 } }), refetchInterval: (q) => (q.state.data?.items.some((c) => LIVE_STATUSES.includes(c.status)) ? 2000 : 5000) })
  const activity = useQuery({ queryKey: ['activity', 'lead', leadId], queryFn: () => api<ActivityEvent[]>(`${base}/leads/${leadId}/activity`), refetchInterval: 8000 })

  const items = useMemo(() => calls.data?.items ?? [], [calls.data])
  // A conversation = the customer said something (greeting-only calls have 1 turn)
  const talked = useMemo(() => items.filter((c) => c.status === 'Completed' && (c.turns ?? 0) > 1), [items])
  const analyzed = talked.find((c) => c.outcome || c.summary) ?? talked[0]
  const liveCall = items.find((c) => LIVE_STATUSES.includes(c.status))
  const shownCallId = transcriptOf ?? liveCall?.id ?? talked[0]?.id ?? null
  const conversation = useQuery({
    queryKey: ['call', shownCallId],
    queryFn: () => api<Call>(`${base}/calls/${shownCallId}`),
    enabled: shownCallId !== null,
    // Live transcript while the call runs, then until the AI summary lands.
    refetchInterval: (q) => {
      const c = q.state.data
      if (!c) return false
      return LIVE_STATUSES.includes(c.status) || (c.status === 'Completed' && !c.summary && (c.transcript?.length ?? 0) > 1) ? 2000 : false
    },
  })

  useEffect(() => { if (lead.data) setFollowUp(lead.data.follow_up_date ?? '') }, [lead.data?.follow_up_date]) // eslint-disable-line react-hooks/exhaustive-deps

  const patch = useMutation({
    mutationFn: (body: Partial<Lead>) => api<Lead>(`${base}/leads/${leadId}`, { method: 'PATCH', json: body }),
    onSuccess: (l) => { qc.setQueryData(['lead', leadId], l); qc.invalidateQueries({ queryKey: ['leads'] }); qc.invalidateQueries({ queryKey: ['activity', 'lead', leadId] }); toast.success('Lead updated') },
    onError: (e) => toast.error(e.message),
  })
  const queue = useMutation({
    mutationFn: () => api(`${base}/leads/bulk/queue`, { method: 'POST', json: { ids: [leadId] } }),
    onSuccess: () => { toast.success('Queued for auto-dial', { description: 'It will be called within calling hours.' }); qc.invalidateQueries({ queryKey: ['lead', leadId] }) },
    onError: (e) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: () => api(`${base}/leads/${leadId}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Lead deleted'); qc.invalidateQueries({ queryKey: ['leads'] }); navigate(path('/leads')) },
    onError: (e) => toast.error(e.message),
  })

  const stats = useMemo(() => {
    const connected = items.filter((c) => c.status === 'Completed')
    const talk = connected.reduce((a, c) => a + (c.duration || 0), 0)
    const lat = connected.map((c) => c.avg_latency_ms).filter((v): v is number => !!v)
    const hours = new Map<number, { calls: number; connected: number }>()
    for (const c of items) {
      const h = new Date(c.created_at).getHours()
      const cur = hours.get(h) ?? { calls: 0, connected: 0 }
      cur.calls += 1
      cur.connected += c.status === 'Completed' ? 1 : 0
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

  const l = lead.data
  if (lead.isError) {
    return <Card className="mt-6"><EmptyState icon={<User />} title="Lead not found" description="It may have been deleted, or it belongs to another agent." action={<Link to={path('/leads')}><Button><ArrowLeft />Back to leads</Button></Link>} /></Card>
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
          <span className="min-w-0">{l.name ?? 'Unnamed lead'}</span>
          {l.do_not_call && <Badge tone="danger"><Ban className="size-3" />Do not call</Badge>}
        </span>}
        description={<span className="mt-1 flex flex-wrap gap-2">
          <CopyChip icon={<Phone />} value={l.phone} href={`tel:${l.phone}`} />
          {l.email && <CopyChip icon={<Mail />} value={l.email} href={`mailto:${l.email}`} />}
          {l.company && <CopyChip icon={<Building2 />} value={l.company} />}
          {l.city && <CopyChip icon={<MapPin />} value={l.city} />}
        </span>}
        actions={<>
          <Button variant="outline-danger" size="icon" title="Delete lead" aria-label="Delete lead" onClick={async () => {
            if (await confirm({ title: `Delete ${l.name ?? 'this lead'}?`, description: 'The lead and its activity are removed permanently. Call records are kept.', confirmLabel: 'Delete', danger: true })) remove.mutate()
          }}><Trash2 /></Button>
          <Button onClick={() => setEditing(true)}><Pencil />Edit</Button>
          <Button onClick={() => queue.mutate()} loading={queue.isPending} disabled={l.do_not_call}><ListPlus />Queue</Button>
          <Button variant="primary" disabled={l.do_not_call} loading={startCall.isPending} onClick={() => startCall.mutate(l.id)}><PhoneCall />Call now</Button>
        </>}>
        {/* Journey */}
        <div className="rounded-2xl border border-border bg-surface p-4 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-[11px] font-bold tracking-wider text-muted uppercase">Journey</div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={l.status} onChange={(e) => patch.mutate({ status: e.target.value })} className="h-8 w-auto text-[13px]" aria-label="Stage">
                {LEAD_STATUSES.map((s) => <option key={s}>{s}</option>)}
              </Select>
              <Select value={l.qualification ?? ''} onChange={(e) => patch.mutate({ qualification: e.target.value })} className="h-8 w-auto text-[13px]" aria-label="Temperature">
                <option value="">No temperature</option>{['Hot', 'Warm', 'Cold'].map((q) => <option key={q}>{q}</option>)}
              </Select>
            </div>
          </div>
          <ol className="mt-4 grid grid-cols-6 gap-1.5">
            {JOURNEY.map((s, i) => {
              const done = !offJourney && i <= stageIndex
              return (
                <li key={s}>
                  <button type="button" onClick={() => s !== l.status && patch.mutate({ status: s })} className="group w-full text-left" title={`Move to ${s}`}>
                    <span className={cn('block h-1.5 rounded-full transition', done ? 'bg-fg' : 'bg-surface-2 group-hover:bg-border-strong')} />
                    <span className={cn('mt-2 hidden items-center gap-1 text-[11.5px] font-semibold sm:flex', i === stageIndex ? 'text-fg' : 'text-muted')}>
                      {done && i < stageIndex && <Check className="size-3" />}{s}
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
          <div className="flex flex-wrap items-center gap-4 rounded-[var(--radius-card)] bg-fg p-5 text-bg shadow-card">
            <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-bg/10"><Target className="size-5" /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-bold tracking-widest uppercase opacity-60">Next best action</div>
              <div className="mt-0.5 text-lg font-extrabold">{action.title}</div>
              <div className="text-sm opacity-70">{action.detail}</div>
            </div>
            {action.kind !== 'stop' && (
              <button type="button" disabled={startCall.isPending}
                onClick={() => startCall.mutate({ leadId: l.id, purpose: action.kind === 'meeting' ? 'confirm_meeting' : action.kind === 'followup' ? 'follow_up' : undefined })}
                className="inline-flex h-10 items-center gap-2 rounded-xl bg-bg px-4 text-sm font-bold text-fg transition hover:opacity-90 disabled:opacity-60">
                <PhoneCall className="size-4" />{action.kind === 'meeting' ? 'Call to confirm' : 'Call now'}
              </button>
            )}
          </div>

          {/* Metrics */}
          <Card className="grid grid-cols-2 divide-border overflow-hidden sm:grid-cols-3 lg:grid-cols-5 lg:divide-x max-lg:[&>*]:border-b max-lg:[&>*]:border-border">
            <Metric icon={<PhoneOutgoing />} label="Calls" value={stats.total} sub={`${stats.connected} connected`} />
            <Metric icon={<Check />} label="Connect rate" value={`${stats.rate}%`} sub={stats.total ? `${stats.total - stats.connected} unanswered` : 'No attempts yet'} />
            <Metric icon={<Clock />} label="Talk time" value={formatDuration(stats.talk)} sub={stats.connected ? `${formatDuration(stats.avgTalk)} per call` : '—'} />
            <Metric icon={<Gauge />} label="AI reply" value={stats.latency ? `${(stats.latency / 1000).toFixed(1)}s` : '—'} sub="Average response time" />
            <Metric icon={<CalendarClock />} label="Best hour" value={stats.bestHour !== null ? `${stats.bestHour}:00` : '—'} sub={stats.bestHour !== null ? 'When they pick up' : 'Not enough data'} />
          </Card>

          {/* AI analysis */}
          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4" />AI analysis</span>}
              description={talked.length ? `Built from ${talked.length} conversation${talked.length > 1 ? 's' : ''} · latest ${formatDate(talked[0]!.created_at)}` : 'Appears after the first answered call.'} />
            <div className="space-y-3 px-5 pb-5">
              <Insight icon={<Bot />} title="Summary" empty="No summary yet.">{l.summary}</Insight>
              <div className="grid gap-3 md:grid-cols-2">
                <Insight icon={<Lightbulb />} title="Requirements" empty="No requirement stated by the customer.">{l.requirements}</Insight>
                <Insight icon={<ShieldAlert />} title="Objections" empty="No objections raised.">{l.objections}</Insight>
              </div>
              {talked[0] && (
                <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                  {[['Last outcome', analyzed.outcome ? titleCase(analyzed.outcome) : '—'], ['Sentiment', analyzed.sentiment ? <SentimentDot value={analyzed.sentiment} /> : '—'],
                    ['Temperature', <QualificationBadge value={l.qualification} />], ['Language', LANGUAGES[l.language] ?? l.language]].map(([k, v]) => (
                    <div key={k as string} className="rounded-xl border border-border px-3 py-2.5">
                      <div className="text-[11px] font-bold tracking-wider text-muted uppercase">{k as string}</div>
                      <div className="mt-1 font-bold">{v as ReactNode}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>

          {/* Conversation / calls / activity */}
          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3.5">
              <Tabs value={tab} onChange={setTab} items={[
                { value: 'conversation', label: 'Transcript' },
                { value: 'calls', label: `Calls (${stats.total})` },
                { value: 'activity', label: `Timeline (${activity.data?.length ?? 0})` },
              ]} />
              {tab === 'conversation' && talked.length > 0 && (
                <div className="flex items-center gap-2">
                  <Select value={shownCallId ?? ''} onChange={(e) => setTranscriptOf(Number(e.target.value))} className="h-8 w-auto text-[13px]" aria-label="Conversation">
                    {talked.map((c) => <option key={c.id} value={c.id}>{formatDate(c.created_at)} · {formatDuration(c.duration)}</option>)}
                  </Select>
                  {shownCallId && <Button size="sm" onClick={() => setCallId(shownCallId)}>Details</Button>}
                </div>
              )}
            </div>

            <div className="p-5">
              {tab === 'conversation' && (
                !talked.length ? <EmptyState icon={<MessageSquareQuote />} title="No conversations yet" description="Transcripts appear here after an answered call." />
                  : conversation.isLoading ? <Skeleton className="h-48" />
                    : transcript.length === 0 ? <p className="text-sm text-muted">No transcript was recorded for this call.</p>
                      : (
                        <div className="space-y-3">
                          {conversation.data?.summary && <div className="rounded-xl bg-surface-2 p-3 text-[13px] text-fg-2"><b className="text-fg">Call summary:</b> {conversation.data.summary}</div>}
                          {conversation.data?.recording_url && <audio controls src={conversation.data.recording_url} className="w-full" />}
                          {transcript.map((t, i) => {
                            const agentTurn = t.role === 'assistant'
                            return (
                              <div key={i} className={cn('flex gap-2.5', !agentTurn && 'flex-row-reverse')}>
                                <span className={cn('grid size-8 shrink-0 place-items-center rounded-full', agentTurn ? 'bg-fg text-bg' : 'border border-border bg-surface-2 text-fg')}>
                                  {agentTurn ? <Bot className="size-4" /> : <User className="size-4" />}
                                </span>
                                <div className={cn('max-w-[78%]', !agentTurn && 'text-right')}>
                                  <div className="mb-0.5 text-[11px] font-semibold text-muted">{agentTurn ? agent?.persona.agent_name ?? 'Agent' : l.name ?? 'Customer'}{t.at && ` · ${new Date(t.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`}</div>
                                  <div className={cn('inline-block rounded-2xl px-3.5 py-2 text-left text-sm leading-relaxed', agentTurn ? 'rounded-tl-sm bg-surface-2 ring-1 ring-border' : 'rounded-tr-sm bg-fg text-bg')}>{t.text}</div>
                                </div>
                              </div>
                            )
                          })}
                          <p className="flex items-center gap-1.5 pt-2 text-xs text-muted"><MessageSquareQuote className="size-3.5" />Customer lines come from phone speech recognition and may contain mistakes.</p>
                        </div>
                      )
              )}

              {tab === 'calls' && (
                items.length ? (
                  <ol className="relative space-y-1 before:absolute before:top-3 before:bottom-3 before:left-[19px] before:w-px before:bg-border">
                    {items.map((c) => (
                      <li key={c.id}>
                        <button onClick={() => setCallId(c.id)} className="relative flex w-full items-start gap-3 rounded-xl p-2 text-left hover:bg-surface-2">
                          <span className={cn('z-10 grid size-[38px] shrink-0 place-items-center rounded-full border border-border bg-surface', c.status === 'Completed' && 'border-fg bg-fg text-bg')}>
                            {c.direction === 'inbound' ? <PhoneIncoming className="size-4" /> : <PhoneOutgoing className="size-4" />}
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-bold">{formatDate(c.created_at)}</span>
                              <CallStatusBadge status={c.status} />
                              <QualificationBadge value={c.qualification} />
                              <span className="text-xs text-muted">{titleCase(c.trigger)}</span>
                            </div>
                            <div className="mt-0.5 line-clamp-2 text-[13px] text-muted">{c.summary ?? c.error ?? c.hangup_cause ?? `${c.turns ?? 0} turns`}</div>
                          </div>
                          {c.status === 'Completed' && (c.turns ?? 0) > 0 && (
                            <span role="button" tabIndex={0} title="Show transcript" onClick={(e) => { e.stopPropagation(); setTranscriptOf(c.id); setTab('conversation') }}
                              className="grid size-8 shrink-0 place-items-center rounded-lg text-muted hover:bg-surface hover:text-fg"><Play className="size-3.5" /></span>
                          )}
                          <span className="w-14 shrink-0 text-right text-sm text-muted tabular-nums">{formatDuration(c.duration)}</span>
                        </button>
                      </li>
                    ))}
                  </ol>
                ) : <EmptyState icon={<PhoneCall />} title="No calls yet" action={<Button variant="primary" disabled={l.do_not_call} onClick={() => startCall.mutate(l.id)}><PhoneCall />Call now</Button>} />
              )}

              {tab === 'activity' && (activity.data?.length ? <ActivityFeed events={activity.data} onCall={setCallId} /> : <p className="text-sm text-muted">No activity yet.</p>)}
            </div>
          </Card>
        </div>

        {/* Side column */}
        <div className="space-y-6">
          <Card className="p-5">
            <div className="flex items-center gap-4">
              <Ring value={score / 100} size={72} stroke={7}><span className="text-lg font-extrabold">{score}</span></Ring>
              <div className="min-w-0">
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
                    <button key={c.id} onClick={() => setCallId(c.id)} title={`${formatDate(c.created_at)} · ${c.qualification}`} className="group flex flex-1 flex-col items-center gap-1">
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
            <div className="space-y-4 px-5 pb-5">
              <div className="flex items-end gap-2">
                <label className="grid flex-1 gap-1.5"><span className="text-[13px] font-semibold text-fg-2">Follow-up date</span>
                  <Input type="date" value={followUp} onChange={(e) => setFollowUp(e.target.value)} /></label>
                <Button disabled={followUp === (l.follow_up_date ?? '')} loading={patch.isPending} onClick={() => patch.mutate({ follow_up_date: followUp || '' })}>Save</Button>
              </div>
              <label className="grid gap-1.5"><span className="text-[13px] font-semibold text-fg-2">Call language</span>
                <Select value={l.language} onChange={(e) => patch.mutate({ language: e.target.value })}>
                  {Object.entries(LANGUAGES).map(([v, n]) => <option key={v} value={v}>{n}</option>)}
                </Select></label>
              <div className="flex items-center justify-between gap-3 rounded-xl border border-border p-3">
                <div><div className="text-sm font-bold">Do not call</div><div className="text-xs text-muted">Excluded from manual and automated calls</div></div>
                <Switch checked={l.do_not_call} onChange={(v) => patch.mutate({ do_not_call: v })} label="Do not call" />
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader title="Notes for the agent" description="The AI reads these before every call"
              action={notes === null ? <Button size="sm" variant="ghost" onClick={() => setNotes(l.notes ?? '')}><Pencil />Edit</Button> : undefined} />
            <div className="px-5 pb-5">
              {notes === null
                ? <p className="text-sm whitespace-pre-wrap text-fg-2">{l.notes || <span className="text-muted">No notes yet. Add context like budget, preferred time or previous purchases.</span>}</p>
                : <div className="space-y-2">
                  <Textarea rows={5} autoFocus value={notes} onChange={(e) => setNotes(e.target.value)} />
                  <div className="flex justify-end gap-2">
                    <Button size="sm" onClick={() => setNotes(null)}>Cancel</Button>
                    <Button size="sm" variant="primary" loading={patch.isPending} onClick={() => patch.mutate({ notes }, { onSuccess: () => setNotes(null) })}>Save notes</Button>
                  </div>
                </div>}
            </div>
          </Card>

          <Card>
            <CardHeader title="Details" />
            <dl className="divide-y divide-border px-5 pb-3 text-sm">
              {([
                ['Meeting', l.meeting_at ? `${l.meeting_at} IST` : null],
                ['Last call', <CallStatusBadge status={l.call_status} />],
                ['Retries', l.retry_count || null],
                ['Last contact', l.last_contacted_at ? timeAgo(l.last_contacted_at) : null],
                ['Source', l.source],
                ['Tags', l.tags.length ? <span className="flex flex-wrap justify-end gap-1">{l.tags.map((t) => <Badge key={t}>{t}</Badge>)}</span> : null],
                ['Created', formatDate(l.created_at)],
                ['Updated', formatDate(l.updated_at)],
              ] as [string, ReactNode][]).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-4 py-2.5">
                  <dt className="text-muted">{k}</dt><dd className="min-w-0 text-right font-semibold">{v || <span className="font-normal text-muted">—</span>}</dd>
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
