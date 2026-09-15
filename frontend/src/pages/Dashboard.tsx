import { useQuery } from '@tanstack/react-query'
import {
  ArrowRight, BookOpen, Bot, CalendarCheck, CalendarClock, Check, Clock, Flame, Gauge, Languages, MessageSquareText, Pause, Phone, PhoneCall,
  PhoneIncoming, Sparkles, Upload, UserPlus, Users, Volume2,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LiveDot } from '@/components/AppShell'
import CallSheet from '@/components/CallSheet'
import { LeadFormSheet, LeadSheet } from '@/components/LeadSheets'
import { CallStatusBadge, QualificationBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, EmptyState, Meter, PageHeader, Skeleton, StatTile, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { ActivityEvent, Call, CallStats, Lead, LeadStats, Page } from '@/lib/types'
import { cn, formatDate, formatDuration, LANGUAGES, timeAgo, titleCase } from '@/lib/utils'

const OUTCOME_ORDER = ['meeting_booked', 'interested', 'callback_requested', 'not_interested', 'do_not_call', 'wrong_person', 'no_conversation', 'other']
const tooltipStyle = { background: 'var(--elevated)', border: '1px solid var(--border)', borderRadius: 12, fontSize: 12, boxShadow: 'var(--shadow-pop)' }

const SETUP = [
  { key: 'persona', label: 'Persona written', hint: 'Script, greeting and guardrails', to: '/agent' },
  { key: 'knowledge', label: 'Knowledge added', hint: 'Brochures, pricing, FAQs', to: '/knowledge' },
  { key: 'leads', label: 'Leads imported', hint: 'People for this agent to call', to: '/import' },
  { key: 'number', label: 'Phone number assigned', hint: 'Own caller ID and inbound line', to: '/settings' },
  { key: 'automation', label: 'Automation on', hint: 'Auto-dial and retries', to: '/automation' },
] as const

export default function Dashboard() {
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const [leadId, setLeadId] = useState<number | null>(null)
  const [callId, setCallId] = useState<number | null>(null)
  const [editing, setEditing] = useState<Lead | null>(null)
  const [range, setRange] = useState<'7' | '14' | '30'>('14')
  const leads = useQuery({ queryKey: ['leads', 'stats'], queryFn: () => api<LeadStats>(`${base}/leads/stats`), refetchInterval: 15000 })
  const calls = useQuery({ queryKey: ['calls', 'stats', range], queryFn: () => api<CallStats>(`${base}/calls/stats`, { params: { days: range } }), refetchInterval: 10000 })
  const recent = useQuery({ queryKey: ['calls', 'recent'], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { page_size: 7 } }), refetchInterval: 5000 })
  const live = useQuery({ queryKey: ['calls', 'live'], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { status: 'active', page_size: 10 } }), refetchInterval: 3000 })
  const activity = useQuery({ queryKey: ['activity', 'recent'], queryFn: () => api<ActivityEvent[]>(`${base}/activity`, { params: { limit: 40 } }), refetchInterval: 8000 })
  const profile = useQuery({ queryKey: ['agent'], queryFn: () => api<{ profile: Record<string, string | number | boolean> }>(`${base}/profile`) })

  const c = calls.data
  const l = leads.data
  const q = l?.by_qualification ?? {}
  const periodTotal = c?.series.reduce((a, d) => a + d.total, 0) ?? 0
  const periodConnected = c?.series.reduce((a, d) => a + d.connected, 0) ?? 0
  const periodRate = periodTotal ? Math.round((100 * periodConnected) / periodTotal) : null
  const todayRate = c?.today.total ? Math.round((100 * c.today.connected) / c.today.total) : null
  const outcomes = Object.entries(c?.outcomes ?? {}).sort((a, b) => OUTCOME_ORDER.indexOf(a[0]) - OUTCOME_ORDER.indexOf(b[0]))
  const outcomeMax = Math.max(1, ...outcomes.map(([, v]) => v))

  const funnel = l ? [
    { label: 'Leads', value: l.total },
    { label: 'Contacted', value: l.total - (l.by_status.New ?? 0) },
    { label: 'Interested', value: (l.by_status.Interested ?? 0) + (l.by_status['Follow Up'] ?? 0) + (l.by_status['Meeting Booked'] ?? 0) + (l.by_status['Closed Won'] ?? 0) },
    { label: 'Meeting', value: (l.by_status['Meeting Booked'] ?? 0) + (l.by_status['Closed Won'] ?? 0) },
    { label: 'Won', value: l.by_status['Closed Won'] ?? 0 },
  ] : []

  // Scheduler runs repeat every few minutes; show each distinct message once with a count.
  const feed = useMemo(() => {
    const out: (ActivityEvent & { repeat: number })[] = []
    for (const e of activity.data ?? []) {
      const same = out.find((o) => o.title === e.title && e.type === 'automation.run')
      if (same) same.repeat += 1
      else out.push({ ...e, repeat: 1 })
    }
    return out.slice(0, 9)
  }, [activity.data])

  if (!agent) return null
  const done = SETUP.filter((s) => agent.setup?.[s.key]).length
  const p = profile.data?.profile

  return (
    <>
      <PageHeader
        eyebrow={<>
          <span className="flex items-center gap-1.5"><LiveDot on={agent.stats.live > 0} />{agent.stats.live ? `${agent.stats.live} live` : 'Agent overview'}</span>
          {agent.status === 'paused' && <Badge tone="warning"><Pause className="size-2.5" />Paused</Badge>}
          <span className="font-semibold tracking-normal normal-case text-muted">{agent.within_calling_hours ? '· Inside calling hours' : '· Outside calling hours'}</span>
        </>}
        title={agent.name}
        description={agent.description || `${agent.persona.agent_name} calls on behalf of ${agent.persona.company_name}.`}
        actions={<>
          <Link to={path('/agent?tab=playground')}><Button><MessageSquareText />Test agent</Button></Link>
          <Button onClick={() => setEditing({} as Lead)}><UserPlus />Add lead</Button>
          <Link to={path('/import')}><Button variant="primary"><Upload />Import leads</Button></Link>
        </>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        {c && l ? <>
          <StatTile label="Calls today" value={c.today.total} icon={<PhoneCall />} tone="neutral"
            sub={c.active ? <span className="flex items-center gap-1.5"><LiveDot on />{c.active} on the line</span> : 'No live calls'} />
          <StatTile label="Connected today" value={c.today.connected} icon={<PhoneIncoming />} tone="success"
            sub={todayRate !== null ? <span className="flex items-center gap-2"><Meter value={todayRate} tone="success" className="w-16" />{todayRate}% answer rate</span> : 'Nothing dialled yet'} />
          <StatTile label="Talk time today" value={formatDuration(c.today.talk_seconds)} icon={<Clock />} tone="neutral"
            sub={c.avg_latency_ms ? `AI replies in ${(c.avg_latency_ms / 1000).toFixed(1)}s on average` : 'No AI latency data yet'} />
          <StatTile label="Meetings booked" value={l.meetings} icon={<CalendarCheck />} tone="success" sub={`${l.pending} lead${l.pending === 1 ? '' : 's'} waiting to be called`} />
          <StatTile label="Hot leads" value={q.Hot ?? 0} icon={<Flame />} tone="danger" sub={`${q.Warm ?? 0} warm · ${q.Cold ?? 0} cold`} />
        </> : Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-[124px]" />)}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title="Call volume" description={c ? `${periodTotal} calls · ${periodConnected} connected${periodRate !== null ? ` · ${periodRate}% connect rate` : ''}` : ' '}
            action={<Tabs value={range} onChange={setRange} items={[{ value: '7', label: '7d' }, { value: '14', label: '14d' }, { value: '30', label: '30d' }]} />} />
          <div className="h-72 px-2 pb-3">
            {c ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={c.series} margin={{ left: -18, right: 12, top: 8 }}>
                  <defs>
                    <linearGradient id="gTotal" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--fg)" stopOpacity={0.16} /><stop offset="100%" stopColor="var(--fg)" stopOpacity={0} /></linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(8)} tick={{ fill: 'var(--muted)', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fill: 'var(--muted)', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: 'var(--fg)', fontWeight: 700 }} />
                  <Area type="monotone" dataKey="total" name="Calls" stroke="var(--fg)" strokeWidth={2.5} fill="url(#gTotal)" isAnimationActive={false} />
                  <Area type="monotone" dataKey="connected" name="Connected" stroke="var(--success)" strokeWidth={2} fill="transparent" isAnimationActive={false} />
                  <Area type="monotone" dataKey="meetings" name="Meetings" stroke="var(--muted)" strokeDasharray="4 3" strokeWidth={1.5} fill="transparent" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : <Skeleton className="h-full" />}
          </div>
        </Card>

        <Card className="flex flex-col">
          <CardHeader title={<span className="flex items-center gap-2"><LiveDot on={!!live.data?.items.length} />Live now</span>}
            description={live.data?.items.length ? 'Tap a call to follow the transcript' : 'No one on the line'}
            action={<Link to={path('/calls?status=active')} className="text-xs font-bold text-muted hover:text-fg">Monitor</Link>} />
          <div className="flex-1 px-3 pb-3">
            {live.data?.items.length ? live.data.items.map((call) => (
              <button key={call.id} onClick={() => setCallId(call.id)} className="flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-surface-2">
                <span className="grid size-9 place-items-center rounded-full bg-success-soft text-success"><Phone className="size-4" /></span>
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="truncate text-sm font-bold">{call.lead_name ?? call.to_number}</div>
                  <div className="text-xs text-muted">{call.direction === 'inbound' ? 'Inbound' : 'Outbound'} · started {timeAgo(call.created_at)}</div>
                </div>
                <CallStatusBadge status={call.status} />
              </button>
            )) : (
              <div className="dot-grid grid h-full min-h-40 place-items-center rounded-xl text-center text-xs font-semibold text-muted">
                <div><PhoneCall className="mx-auto mb-2 size-5" />Calls appear here the moment they start</div>
              </div>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Conversion funnel" description="Where this agent's leads are" action={<Link to={path('/pipeline')} className="text-xs font-bold text-muted hover:text-fg">Pipeline</Link>} />
          <div className="space-y-3 px-5 pb-5">
            {l ? funnel.map((f, i) => {
              const pct = funnel[0]!.value ? (100 * f.value) / funnel[0]!.value : 0
              const prev = i ? funnel[i - 1]!.value : 0
              return (
                <div key={f.label}>
                  <div className="mb-1 flex items-baseline justify-between text-[13px]">
                    <span className="font-semibold text-fg-2">{f.label}</span>
                    <span className="flex items-baseline gap-2"><b className="text-base tabular-nums">{f.value}</b>
                      {i > 0 && <span className="w-10 text-right text-[11px] text-muted">{prev ? `${Math.round((100 * f.value) / prev)}%` : '—'}</span>}</span>
                  </div>
                  <div className="h-2.5 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full bg-fg transition-all" style={{ width: `${Math.max(pct, f.value ? 3 : 0)}%`, opacity: 1 - i * 0.15 }} /></div>
                </div>
              )
            }) : <Skeleton className="h-48" />}
          </div>
        </Card>

        <Card>
          <CardHeader title="Call outcomes" description={`AI-classified results, last ${range} days`} />
          <div className="space-y-2.5 px-5 pb-5">
            {outcomes.length ? outcomes.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[120px_1fr_28px] items-center gap-3 text-[13px]">
                <span className="truncate font-semibold text-fg-2">{titleCase(k)}</span>
                <div className="h-2 overflow-hidden rounded-full bg-surface-2">
                  <div className={cn('h-full rounded-full', k === 'meeting_booked' ? 'bg-success' : ['not_interested', 'do_not_call'].includes(k) ? 'bg-danger' : 'bg-fg/70')} style={{ width: `${(100 * v) / outcomeMax}%` }} />
                </div>
                <span className="text-right font-bold tabular-nums">{v}</span>
              </div>
            )) : <p className="py-10 text-center text-sm text-muted">No completed conversations in this period.</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="Agent brief" description="What callers hear" action={<Link to={path('/agent')} className="text-xs font-bold text-muted hover:text-fg">Edit</Link>} />
          <div className="px-5 pb-5">
            <dl className="divide-y divide-border text-[13px]">
              {[
                [Bot, 'Speaks as', `${agent.persona.agent_name} · ${agent.persona.company_name}`],
                [Volume2, 'Voice', <span className="capitalize">{agent.persona.voice_speaker}</span>],
                [Languages, 'Language', LANGUAGES[agent.persona.default_language] ?? agent.persona.default_language],
                [Phone, 'Number', agent.phone_number ?? 'Default Plivo number'],
                [CalendarClock, 'Automation', agent.automation_on ? 'Auto-dial / retries on' : 'Manual calling only'],
                [BookOpen, 'Knowledge', `${agent.stats.documents} document${agent.stats.documents === 1 ? '' : 's'}`],
              ].map(([Icon, k, v]) => {
                const I = Icon as typeof Bot
                return <div key={k as string} className="flex items-center gap-3 py-2"><I className="size-4 text-muted" /><dt className="w-24 text-muted">{k as string}</dt><dd className="min-w-0 flex-1 truncate font-semibold">{v as React.ReactNode}</dd></div>
              })}
            </dl>
            {p?.objective && <p className="mt-3 rounded-xl bg-surface-2 p-3 text-[12.5px] leading-relaxed text-fg-2"><b className="text-fg">Objective:</b> {String(p.objective)}</p>}
          </div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card className="overflow-hidden">
          <CardHeader title="Recent calls" description="Latest conversations with AI summaries"
            action={<Link to={path('/calls')} className="flex items-center gap-1 text-xs font-bold text-muted hover:text-fg">All calls<ArrowRight className="size-3.5" /></Link>} />
          {recent.data?.items.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead><tr className="border-y border-border bg-surface-2/50 text-left text-[11px] font-bold tracking-wider text-muted uppercase">
                  <th className="px-5 py-2.5">Lead</th><th className="px-3 py-2.5">Summary</th><th className="px-3 py-2.5">Temp.</th><th className="px-3 py-2.5 text-right">Length</th><th className="px-5 py-2.5 text-right">Status</th>
                </tr></thead>
                <tbody className="divide-y divide-border">
                  {recent.data.items.map((call) => (
                    <tr key={call.id} onClick={() => setCallId(call.id)} className="cursor-pointer transition hover:bg-surface-2/60">
                      <td className="px-5 py-3"><div className="font-bold">{call.lead_name ?? call.to_number}</div><div className="text-xs text-muted">{formatDate(call.created_at)} · {titleCase(call.trigger)}</div></td>
                      <td className="max-w-md px-3 py-3"><span className="line-clamp-2 text-[13px] text-fg-2">{call.summary ?? <span className="text-muted">{call.turns ? `${call.turns} turns` : 'No conversation'}</span>}</span></td>
                      <td className="px-3 py-3"><QualificationBadge value={call.qualification} /></td>
                      <td className="px-3 py-3 text-right text-muted tabular-nums">{formatDuration(call.duration)}</td>
                      <td className="px-5 py-3 text-right"><CallStatusBadge status={call.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : recent.isLoading ? <div className="p-5"><Skeleton className="h-40" /></div> : (
            <EmptyState icon={<PhoneCall />} title="No calls yet" description="Add a lead and press Call, or switch on auto-dial."
              action={<Link to={path('/leads')}><Button variant="primary">Go to leads</Button></Link>} />
          )}
        </Card>

        <div className="space-y-4">
          {done < SETUP.length && (
            <Card>
              <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4" />Finish setting up</span>} description={`${done} of ${SETUP.length} done`} />
              <div className="px-5"><Meter value={(100 * done) / SETUP.length} tone="neutral" className="h-2 [&>div]:bg-fg" /></div>
              <ul className="space-y-1 p-3">
                {SETUP.map((s) => {
                  const ok = agent.setup?.[s.key]
                  return (
                    <li key={s.key}>
                      <button type="button" onClick={() => navigate(path(s.to))} disabled={ok}
                        className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-surface-2 disabled:hover:bg-transparent">
                        <span className={cn('grid size-6 place-items-center rounded-full ring-1', ok ? 'bg-fg text-bg ring-fg' : 'text-muted ring-border-strong')}>{ok ? <Check className="size-3.5" /> : null}</span>
                        <div className="min-w-0 flex-1 leading-tight">
                          <div className={cn('text-[13px] font-bold', ok && 'text-muted line-through')}>{s.label}</div>
                          {!ok && <div className="text-xs text-muted">{s.hint}</div>}
                        </div>
                        {!ok && <ArrowRight className="size-4 text-muted" />}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </Card>
          )}

          <Card>
            <CardHeader title="History" description="Latest events in this agent"
              action={<Link to={path('/activity')} className="flex items-center gap-1 text-xs font-bold text-muted hover:text-fg">All<ArrowRight className="size-3.5" /></Link>} />
            <ol className="px-5 pb-5">
              {feed.length ? feed.map((e) => (
                <li key={e.id} className="relative border-l border-border pb-4 pl-4 last:pb-0">
                  <span className={cn('absolute top-1 -left-[5px] size-2.5 rounded-full ring-2 ring-surface',
                    e.type.startsWith('call.failed') ? 'bg-danger' : e.type.startsWith('meeting') ? 'bg-success' : e.type.startsWith('ai.') ? 'bg-fg' : 'bg-border-strong')} />
                  <button type="button" onClick={() => e.call_id ? setCallId(e.call_id) : e.lead_id ? setLeadId(e.lead_id) : undefined}
                    className="block text-left text-[13px] leading-snug font-semibold hover:underline">
                    <span className="line-clamp-2">{e.title}</span>
                  </button>
                  <div className="mt-0.5 flex flex-wrap gap-x-1.5 text-[11.5px] text-muted">
                    {e.lead_name && <><span className="font-semibold text-fg-2">{e.lead_name}</span><span>·</span></>}
                    <span>{timeAgo(e.created_at)}</span><span>·</span><span className="capitalize">{e.actor === 'ai' ? 'AI' : e.actor}</span>
                    {e.repeat > 1 && <><span>·</span><span>×{e.repeat}</span></>}
                  </div>
                </li>
              )) : <p className="text-sm text-muted">Nothing has happened yet.</p>}
            </ol>
          </Card>

          <Card className="p-5">
            <div className="flex items-center gap-3">
              <span className="grid size-10 place-items-center rounded-xl bg-surface-2 ring-1 ring-border"><Gauge className="size-5" /></span>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-bold">Lead temperature</div>
                <div className="text-xs text-muted">{l?.total ?? 0} leads qualified by the AI</div>
              </div>
            </div>
            <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-surface-2">
              {l?.total ? [['Hot', 'bg-danger'], ['Warm', 'bg-warning'], ['Cold', 'bg-info']].map(([k, cls]) => (
                <div key={k} className={cls} style={{ width: `${(100 * (q[k!] ?? 0)) / l.total}%` }} title={`${k}: ${q[k!] ?? 0}`} />
              )) : null}
            </div>
            <div className="mt-2 flex gap-4 text-xs text-muted">
              {[['Hot', 'bg-danger'], ['Warm', 'bg-warning'], ['Cold', 'bg-info']].map(([k, cls]) => <span key={k} className="flex items-center gap-1.5"><span className={cn('size-2 rounded-full', cls)} />{k} <b className="text-fg">{q[k!] ?? 0}</b></span>)}
              <Link to={path('/leads?qualification=Hot')} className="ml-auto flex items-center gap-1 font-bold hover:text-fg"><Users className="size-3.5" />View</Link>
            </div>
          </Card>
        </div>
      </div>

      <LeadSheet leadId={leadId} onClose={() => setLeadId(null)} onEdit={setEditing} />
      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); setLeadId(id) }} />
      <LeadFormSheet key={editing?.id ?? 'new'} open={editing !== null} lead={editing?.id ? editing : null} onClose={() => setEditing(null)} />
    </>
  )
}
