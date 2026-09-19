import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  ArrowRight, BookOpen, Bot, CalendarCheck, CalendarClock, Check, Clock, Flame, Gauge, Languages, MessageSquareText, Pause, Phone, PhoneCall,
  PhoneIncoming, Sparkles, Upload, UserPlus, Users, Volume2,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LiveDot } from '@/components/AppShell'
import CallSheet from '@/components/CallSheet'
import { ChartNowDot } from '@/components/Live'
import { LeadFormSheet, LeadSheet } from '@/components/LeadSheets'
import { CallStatusBadge, QualificationBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, EmptyState, Meter, PageHeader, Skeleton, StatTile, Tabs, TableScroll } from '@/components/ui'
import { api } from '@/lib/api'
import { AnimatedNumber, Stagger } from '@/lib/motion'
import { Orb3D, VoiceOrb } from '@/components/VoiceViz'
import { useAgent } from '@/lib/agent'
import type { ActivityEvent, Call, CallStats, Lead, LeadStats, Page } from '@/lib/types'
import { callHandledBy, callParty, cn, formatDate, formatDuration, LANGUAGES, timeAgo, titleCase } from '@/lib/utils'

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
  const calls = useQuery({ queryKey: ['calls', 'stats', range], queryFn: () => api<CallStats>(`${base}/calls/stats`, { params: { days: range } }), placeholderData: keepPreviousData, refetchInterval: 10000 })
  const recent = useQuery({ queryKey: ['calls', 'recent'], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { page_size: 7 } }), refetchInterval: 5000 })
  const live = useQuery({ queryKey: ['calls', 'live'], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { status: 'active', page_size: 10 } }), refetchInterval: 3000 })
  const activity = useQuery({ queryKey: ['activity', 'recent'], queryFn: () => api<ActivityEvent[]>(`${base}/activity`, { params: { limit: 40 } }), refetchInterval: 8000 })
  const profile = useQuery({ queryKey: ['agent'], queryFn: () => api<{ profile: Record<string, string | number | boolean> }>(`${base}/profile`) })

  const c = calls.data
  const l = leads.data
  const q = l?.by_qualification ?? {}
  const series = c?.series ?? []
  const periodTotal = series.reduce((a, d) => a + d.total, 0)
  const periodConnected = series.reduce((a, d) => a + d.connected, 0)
  const periodRate = periodTotal ? Math.round((100 * periodConnected) / periodTotal) : null
  const todayRate = c?.today?.total ? Math.round((100 * c.today.connected) / c.today.total) : null
  const outcomeRank = (k: string) => { const i = OUTCOME_ORDER.indexOf(k); return i === -1 ? OUTCOME_ORDER.length : i }
  const outcomes = Object.entries(c?.outcomes ?? {}).sort((a, b) => outcomeRank(a[0]) - outcomeRank(b[0]) || a[0].localeCompare(b[0]))
  const outcomeMax = Math.max(1, ...outcomes.map(([, v]) => v))

  const st = l?.by_status ?? {}
  const funnel = l ? [
    { label: 'Leads', value: l.total },
    { label: 'Contacted', value: Math.max(0, l.total - (st.New ?? 0)) },
    { label: 'Interested', value: (st.Interested ?? 0) + (st['Follow Up'] ?? 0) + (st['Meeting Booked'] ?? 0) + (st['Closed Won'] ?? 0) },
    { label: 'Meeting', value: (st['Meeting Booked'] ?? 0) + (st['Closed Won'] ?? 0) },
    { label: 'Won', value: st['Closed Won'] ?? 0 },
  ] : []
  const statsError = (calls.isError && !c) || (leads.isError && !l)

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
        visual={<Orb3D state={live.data?.items.length ? 'live' : 'listening'} size={92} />}
        eyebrow={<>
          <span className="flex items-center gap-1.5"><LiveDot on={agent.stats.live > 0} />{agent.stats.live ? `${agent.stats.live} live` : 'Agent overview'}</span>
          {agent.status === 'paused' && <Badge tone="warning"><Pause className="size-2.5" />Paused</Badge>}
          <span className="inline-flex items-center gap-1.5 font-semibold tracking-normal normal-case text-muted">
            <span className={cn('size-1.5 rounded-full animate-pulse-dot', agent.within_calling_hours ? 'bg-success' : 'bg-muted')} />
            {agent.within_calling_hours ? 'Inside calling hours' : 'Outside calling hours'}</span>
        </>}
        title={agent.name}
        description={agent.description || `${agent.persona.agent_name} calls on behalf of ${agent.persona.company_name}.`}
        actions={<>
          <Link to={path('/agent?tab=playground')}><Button><MessageSquareText />Test agent</Button></Link>
          <Button onClick={() => setEditing({} as Lead)}><UserPlus />Add lead</Button>
          <Link to={path('/import')}><Button variant="primary"><Upload />Import leads</Button></Link>
        </>}
      />

      <Stagger className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {c && l ? [
          <StatTile key="calls" className="glint" label="Calls today" count={c.today.total} icon={<PhoneCall />} tone="neutral"
            sub={c.active ? <span className="flex items-center gap-1.5"><LiveDot on />{c.active} on the line</span> : 'No live calls'} />,
          <StatTile key="connected" className="glint" label="Connected today" count={c.today.connected} icon={<PhoneIncoming />} tone="success"
            sub={todayRate !== null ? <span className="flex items-center gap-2"><Meter value={todayRate} tone="success" className="w-16" />{todayRate}% answer rate</span> : 'Nothing dialled yet'} />,
          <StatTile key="talk" className="glint" label="Talk time today" value={formatDuration(c.today.talk_seconds)} icon={<Clock />} tone="neutral"
            sub={c.avg_latency_ms ? `AI replies in ${(c.avg_latency_ms / 1000).toFixed(1)}s on average` : 'No AI latency data yet'} />,
          <StatTile key="meetings" className="glint" label="Meetings booked" count={l.meetings} icon={<CalendarCheck />} tone="success" sub={`${l.pending} lead${l.pending === 1 ? '' : 's'} waiting to be called`} />,
          <StatTile key="hot" className="glint" label="Hot leads" count={q.Hot ?? 0} icon={<Flame />} tone="danger" sub={`${q.Warm ?? 0} warm · ${q.Cold ?? 0} cold`} />,
        ] : statsError ? (
          <Card key="err" className="col-span-full">
            <EmptyState title="Couldn't load today's numbers" description="The API did not answer. Check the backend is running and try again."
              action={<Button onClick={() => { calls.refetch(); leads.refetch() }} loading={calls.isFetching || leads.isFetching}>Retry</Button>} />
          </Card>
        ) : Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-[124px]" />)}
      </Stagger>

      <Stagger delay={300} step={110} className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card>
          <CardHeader title="Call volume" description={c ? `${periodTotal} calls · ${periodConnected} connected${periodRate !== null ? ` · ${periodRate}% connect rate` : ''}` : ' '}
            action={<span className={cn('block transition-opacity', calls.isFetching && !calls.isLoading && 'opacity-60')}><Tabs value={range} onChange={setRange} items={[{ value: '7', label: '7d' }, { value: '14', label: '14d' }, { value: '30', label: '30d' }]} /></span>} />
          <div className="draw-in h-60 px-2 pb-3 sm:h-72">
            {c ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ left: -18, right: 12, top: 8 }}>
                  <defs>
                    <linearGradient id="gTotal" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--fg)" stopOpacity={0.16} /><stop offset="100%" stopColor="var(--fg)" stopOpacity={0} /></linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(8)} tick={{ fill: 'var(--muted)', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fill: 'var(--muted)', fontSize: 12 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: 'var(--fg)', fontWeight: 700 }} />
                  <Area type="monotone" dataKey="total" name="Calls" stroke="var(--fg)" strokeWidth={2.5} fill="url(#gTotal)" isAnimationActive={false}
                    dot={({ cx, cy, index }: { cx?: number; cy?: number; index?: number }) => <ChartNowDot key={index} cx={cx} cy={cy} index={index} last={series.length - 1} />} />
                  <Area type="monotone" dataKey="connected" name="Connected" stroke="var(--success)" strokeWidth={2} fill="transparent" isAnimationActive={false} />
                  <Area type="monotone" dataKey="meetings" name="Meetings" stroke="var(--muted)" strokeDasharray="4 3" strokeWidth={1.5} fill="transparent" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : calls.isError ? <p className="grid h-full place-items-center text-sm text-muted">Call volume is unavailable right now.</p> : <Skeleton className="h-full" />}
          </div>
        </Card>

        <Card className="flex flex-col">
          <CardHeader title={<span className="flex items-center gap-2"><LiveDot on={!!live.data?.items.length} />Live now</span>}
            description={live.data?.items.length ? 'Tap a call to follow the transcript' : 'No one on the line'}
            action={<Link to={path('/calls?status=active')} className="-mr-2 flex min-h-10 items-center px-2 text-xs font-bold text-muted hover:text-fg">Monitor</Link>} />
          <div className="flex-1 px-3 pb-3">
            {live.data?.items.length ? live.data.items.map((call) => (
              <button key={call.id} type="button" onClick={() => setCallId(call.id)} className="flex min-h-10 w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-surface-2">
                <span className="grid size-9 place-items-center rounded-full bg-success-soft text-success"><Phone className="size-4" /></span>
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="truncate text-sm font-bold">{callParty(call)}</div>
                  <div className="truncate text-xs text-muted">{call.direction === 'inbound' ? 'Inbound' : 'Outbound'} · started {timeAgo(call.created_at)}</div>
                </div>
                <span className="shrink-0"><CallStatusBadge status={call.status} /></span>
              </button>
            )) : live.isLoading ? <Skeleton className="h-40" /> : live.isError ? (
              <div className="grid h-full min-h-40 place-items-center rounded-xl px-4 text-center text-xs font-semibold text-muted">
                <div className="flex flex-col items-center gap-3">Live calls are unavailable right now.
                  <Button size="sm" onClick={() => live.refetch()} loading={live.isFetching}>Retry</Button></div>
              </div>
            ) : (
              <div className="dot-grid grid h-full min-h-40 place-items-center rounded-xl text-center text-xs font-semibold text-muted">
                <div className="flex flex-col items-center gap-3"><VoiceOrb state="listening" size={64} />Listening for the next call</div>
              </div>
            )}
          </div>
        </Card>
      </Stagger>

      <Stagger onView step={110} className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader title="Conversion funnel" description="Where this agent's leads are" action={<Link to={path('/pipeline')} className="-mr-2 flex min-h-10 items-center px-2 text-xs font-bold text-muted hover:text-fg">Pipeline</Link>} />
          <div className="space-y-3 px-5 pb-5">
            {l ? funnel.map((f, i) => {
              const pct = funnel[0]!.value ? (100 * f.value) / funnel[0]!.value : 0
              const prev = i ? funnel[i - 1]!.value : 0
              return (
                <div key={f.label}>
                  <div className="mb-1 flex items-baseline justify-between text-[13px]">
                    <span className="font-semibold text-fg-2">{f.label}</span>
                    <span className="flex items-baseline gap-2"><b className="text-base tabular-nums"><AnimatedNumber value={f.value} /></b>
                      {i > 0 && <span className="w-10 text-right text-[11px] text-muted">{prev ? `${Math.round((100 * f.value) / prev)}%` : '—'}</span>}</span>
                  </div>
                  <div className="h-2.5 overflow-hidden rounded-full bg-surface-2"><div className="grow-x flow h-full rounded-full bg-fg transition-all" style={{ width: `${Math.max(pct, f.value ? 3 : 0)}%`, opacity: 1 - i * 0.15, animationDelay: `${i * 110}ms` }} /></div>
                </div>
              )
            }) : leads.isError ? <p className="py-10 text-center text-sm text-muted">Lead stats are unavailable right now.</p> : <Skeleton className="h-48" />}
          </div>
        </Card>

        <Card>
          <CardHeader title="Call outcomes" description={`AI-classified results, last ${range} days`} />
          <div className="space-y-2.5 px-5 pb-5">
            {outcomes.length ? outcomes.map(([k, v], i) => (
              <div key={k} className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,2fr)_28px] items-center gap-3 text-[13px] sm:grid-cols-[120px_minmax(0,1fr)_28px]">
                <span title={titleCase(k)} className="min-w-0 font-semibold leading-tight break-words text-fg-2">{titleCase(k)}</span>
                <div className="h-2 overflow-hidden rounded-full bg-surface-2">
                  <div className={cn('grow-x flow h-full rounded-full', k === 'meeting_booked' ? 'bg-success' : ['not_interested', 'do_not_call'].includes(k) ? 'bg-danger' : 'bg-fg/70')} style={{ width: `${(100 * v) / outcomeMax}%`, animationDelay: `${i * 90}ms` }} />
                </div>
                <span className="text-right font-bold tabular-nums"><AnimatedNumber value={v} /></span>
              </div>
            )) : !c && calls.isLoading ? <Skeleton className="h-40" /> : !c && calls.isError ? <p className="py-10 text-center text-sm text-muted">Call outcomes are unavailable right now.</p> : <p className="py-10 text-center text-sm text-muted">No completed conversations in this period.</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="Agent brief" description="What callers hear" action={<Link to={path('/agent')} className="-mr-2 flex min-h-10 items-center px-2 text-xs font-bold text-muted hover:text-fg">Edit</Link>} />
          <div className="px-5 pb-5">
            <dl className="divide-y divide-border text-[13px]">
              {[
                [Bot, 'Speaks as', `${agent.persona.agent_name} · ${agent.persona.company_name}`],
                [Volume2, 'Voice', <span className="capitalize">{agent.persona.voice_speaker}</span>],
                [Languages, 'Language', LANGUAGES[agent.persona.default_language] ?? agent.persona.default_language],
                [Phone, 'Number', agent.phone_number ?? 'Default Plivo number'],
                [CalendarClock, 'Automation', agent.automation_on ? 'Auto-dial / retries on' : 'Manual calling only'],
                [BookOpen, 'Knowledge', `${agent.stats.documents} document${agent.stats.documents === 1 ? '' : 's'}`],
              ].map(([Icon, k, v], idx) => {
                const I = Icon as typeof Bot
                return <div key={k as string} style={{ animationDelay: `${idx * 55}ms` }} className="reveal reveal-in reveal-left group flex items-center gap-3 py-2"><I className="size-4 text-muted transition-transform duration-200 group-hover:scale-110 group-hover:text-fg" /><dt className="w-20 shrink-0 text-muted xl:w-24">{k as string}</dt><dd className="min-w-0 flex-1 truncate font-semibold">{v as React.ReactNode}</dd></div>
              })}
            </dl>
            {p?.objective && <p className="mt-3 rounded-xl bg-surface-2 p-3 text-[12.5px] leading-relaxed break-words text-fg-2"><b className="text-fg">Objective:</b> {String(p.objective)}</p>}
          </div>
        </Card>
      </Stagger>

      <Stagger onView step={120} className="mt-4 grid gap-4 xl:grid-cols-[1fr_380px]">
        <Card className="overflow-hidden">
          <CardHeader title="Recent calls" description="Latest conversations with AI summaries"
            action={<Link to={path('/calls')} className="-mr-2 flex min-h-10 items-center gap-1 px-2 text-xs font-bold text-muted hover:text-fg">All calls<ArrowRight className="size-3.5" /></Link>} />
          {recent.data?.items.length ? (
            <TableScroll>
              <table className="rows-in w-full min-w-[640px] text-sm">
                <thead><tr className="border-y border-border bg-surface-2/50 text-left text-[11px] font-bold tracking-wider text-muted uppercase">
                  <th className="px-5 py-2.5">Lead</th><th className="px-3 py-2.5">Handled by</th><th className="px-3 py-2.5">Summary</th><th className="px-3 py-2.5">Temp.</th><th className="px-3 py-2.5 text-right">Length</th><th className="px-5 py-2.5 text-right">Status</th>
                </tr></thead>
                <tbody className="divide-y divide-border">
                  {recent.data.items.map((call) => (
                    <tr key={call.id} role="button" tabIndex={0} aria-label={`Open call with ${callParty(call)}`} onClick={() => setCallId(call.id)}
                      onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); setCallId(call.id) } }}
                      className="cursor-pointer transition outline-none hover:bg-surface-2/60 focus-visible:bg-surface-2/60">
                      <td className="px-5 py-3"><div className="font-bold break-words">{callParty(call)}</div><div className="text-xs text-muted">{formatDate(call.created_at)} · {titleCase(call.trigger)}</div></td>
                      <td className="px-3 py-3 text-[13px] whitespace-nowrap text-fg-2">{callHandledBy(call, agent.name)}</td>
                      <td className="max-w-md px-3 py-3"><span className="line-clamp-2 text-[13px] text-fg-2">{call.summary ?? <span className="text-muted">{call.turns ? `${call.turns} turns` : 'No conversation'}</span>}</span></td>
                      <td className="px-3 py-3"><QualificationBadge value={call.qualification} /></td>
                      <td className="px-3 py-3 text-right text-muted tabular-nums">{formatDuration(call.duration)}</td>
                      <td className="px-5 py-3 text-right"><CallStatusBadge status={call.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          ) : recent.isLoading ? <div className="p-5"><Skeleton className="h-40" /></div> : recent.isError ? (
            <EmptyState icon={<PhoneCall />} title="Couldn't load recent calls" description="The API did not answer."
              action={<Button onClick={() => recent.refetch()} loading={recent.isFetching}>Retry</Button>} />
          ) : (
            <EmptyState icon={<PhoneCall />} title="No calls yet" description="Add a lead and press Call, or switch on auto-dial."
              action={<Link to={path('/leads')}><Button variant="primary">Go to leads</Button></Link>} />
          )}
        </Card>

        <Stagger onView from="right" step={110} className="space-y-4">
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
                        className="flex min-h-10 w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-surface-2 disabled:hover:bg-transparent">
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
              action={<Link to={path('/activity')} className="-mr-2 flex min-h-10 items-center gap-1 px-2 text-xs font-bold text-muted hover:text-fg">All<ArrowRight className="size-3.5" /></Link>} />
            <ol className="px-5 pb-5">
              {feed.length ? feed.map((e, i) => (
                <li key={e.id} style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }} className="reveal reveal-in reveal-left relative border-l border-border pb-4 pl-4 last:pb-0">
                  {i === 0 && <span className="absolute top-1 -left-[5px] size-2.5 animate-live-ring rounded-full bg-fg/40" />}
                  <span className={cn('absolute top-1 -left-[5px] size-2.5 rounded-full ring-2 ring-surface',
                    e.type.startsWith('call.failed') ? 'bg-danger' : e.type.startsWith('meeting') ? 'bg-success' : e.type.startsWith('ai.') ? 'bg-fg' : 'bg-border-strong')} />
                  {e.call_id || e.lead_id ? (
                    <button type="button" onClick={() => e.call_id ? setCallId(e.call_id) : setLeadId(e.lead_id!)}
                      className="block min-h-10 w-full min-w-0 py-1.5 text-left text-[13px] leading-snug font-semibold break-words hover:underline">
                      <span className="line-clamp-2">{e.title}</span>
                    </button>
                  ) : (
                    <div className="min-w-0 py-0.5 text-[13px] leading-snug font-semibold break-words"><span className="line-clamp-2">{e.title}</span></div>
                  )}
                  <div className="mt-0.5 flex flex-wrap gap-x-1.5 text-[11.5px] text-muted">
                    {e.lead_name && <><span className="font-semibold text-fg-2">{e.lead_name}</span><span>·</span></>}
                    <span>{timeAgo(e.created_at)}</span><span>·</span><span className="capitalize">{e.actor === 'ai' ? 'AI' : e.actor}</span>
                    {e.repeat > 1 && <><span>·</span><span>×{e.repeat}</span></>}
                  </div>
                </li>
              )) : activity.isLoading ? <Skeleton className="h-32" /> : activity.isError ? <p className="text-sm text-muted">History is unavailable right now.</p> : <p className="text-sm text-muted">Nothing has happened yet.</p>}
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
              {l?.total ? [['Hot', 'bg-danger'], ['Warm', 'bg-warning'], ['Cold', 'bg-info']].map(([k, cls], i) => (
                <div key={k} className={cn('grow-x flow', cls)} style={{ width: `${(100 * (q[k!] ?? 0)) / l.total}%`, animationDelay: `${150 + i * 140}ms` }} title={`${k}: ${q[k!] ?? 0}`} />
              )) : null}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted">
              {[['Hot', 'bg-danger'], ['Warm', 'bg-warning'], ['Cold', 'bg-info']].map(([k, cls]) => <span key={k} className="flex items-center gap-1.5"><span className={cn('size-2 rounded-full', cls)} />{k} <b className="text-fg"><AnimatedNumber value={q[k!] ?? 0} /></b></span>)}
              <Link to={path('/leads?qualification=Hot')} className="-my-2 -mr-2 ml-auto flex min-h-10 items-center gap-1 px-2 font-bold hover:text-fg"><Users className="size-3.5" />View</Link>
            </div>
          </Card>
        </Stagger>
      </Stagger>

      <LeadSheet leadId={leadId} onClose={() => setLeadId(null)} onEdit={setEditing} />
      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); setLeadId(id) }} />
      <LeadFormSheet key={editing?.id ?? 'new'} open={editing !== null} lead={editing?.id ? editing : null} onClose={() => setEditing(null)} />
    </>
  )
}

