import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowUpRight, AudioWaveform, BookOpen, CalendarCheck, Flame, LayoutGrid, List, MessageSquareText, Pause, Phone,
  PhoneCall, Plus, Radio, Search, Sparkles, Timer, TrendingUp, Upload, Users,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { AgentMark, LiveDot } from '@/components/AppShell'
import NewAgentSheet from '@/components/NewAgentSheet'
import { CallStatusBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, Input, PageHeader, Ring, Select, Skeleton, StatTile, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import type { AgentOverviewItem, AgentsOverview } from '@/lib/types'
import { cn, formatDuration, LANGUAGES, timeAgo } from '@/lib/utils'

const STAGE_COLORS: Record<string, string> = {
  New: 'var(--border-strong)', Contacted: 'var(--info)', Interested: 'var(--warning)', 'Follow Up': 'color-mix(in srgb, var(--warning) 60%, var(--danger))',
  'Meeting Booked': 'var(--success)', 'Closed Won': 'color-mix(in srgb, var(--success) 70%, #000)',
}
const SETUP_LABELS: Record<string, string> = { persona: 'Persona', knowledge: 'Knowledge', leads: 'Leads', number: 'Number', automation: 'Automation' }
const tooltipStyle = { background: 'var(--elevated)', border: '1px solid var(--border)', borderRadius: 12, fontSize: 12, boxShadow: 'var(--shadow-pop)' }

type Sort = 'activity' | 'name' | 'calls' | 'leads' | 'rate' | 'meetings'
type Status = 'all' | 'active' | 'paused'

function Sparkline({ data, color, id }: { data: { calls: number }[]; color: string; id: string }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.35} /><stop offset="100%" stopColor={color} stopOpacity={0} /></linearGradient></defs>
        <Area type="monotone" dataKey="calls" stroke={color} strokeWidth={2} fill={`url(#${id})`} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function PipelineBar({ pipeline }: { pipeline: Record<string, number> }) {
  const total = Object.values(pipeline).reduce((a, b) => a + b, 0)
  if (!total) return <div className="h-2 rounded-full bg-surface-2" />
  return (
    <div className="flex h-2 gap-0.5 overflow-hidden rounded-full">
      {Object.entries(pipeline).filter(([, v]) => v).map(([k, v]) => (
        <div key={k} title={`${k}: ${v}`} style={{ width: `${(100 * v) / total}%`, background: STAGE_COLORS[k] }} />
      ))}
    </div>
  )
}

function setupScore(a: AgentOverviewItem) {
  const values = Object.values(a.setup)
  return values.filter(Boolean).length / values.length
}

function AgentCard({ agent }: { agent: AgentOverviewItem }) {
  const s = agent.stats
  const p = agent.period
  const missing = Object.entries(agent.setup).filter(([, v]) => !v).map(([k]) => SETUP_LABELS[k])
  const base = `/a/${agent.id}`
  return (
    <Card className="group relative flex flex-col overflow-hidden transition hover:-translate-y-0.5 hover:shadow-pop">
      <Link to={base} className="absolute inset-0 z-0" aria-label={`Open ${agent.name}`} />

      <div className="relative p-5 pb-0">
        <div className="flex items-start gap-3">
          <AgentMark agent={agent} className={cn('size-12 rounded-2xl text-sm', agent.status === 'paused' && 'grayscale')} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-base font-extrabold">{agent.name}</h3>
              {agent.status === 'paused'
                ? <Badge tone="warning"><Pause className="size-2.5" />Paused</Badge>
                : s.live > 0 ? <Badge tone="success" pulse>{s.live} live</Badge> : null}
            </div>
            <p className="truncate text-xs text-muted">{agent.persona.agent_name} · {agent.persona.company_name} · {LANGUAGES[agent.persona.default_language] ?? agent.persona.default_language}</p>
          </div>
          <Ring value={setupScore(agent)} size={40} stroke={4}>{Math.round(setupScore(agent) * 5)}/5</Ring>
        </div>
        <p className="mt-3 line-clamp-2 min-h-10 text-[13px] text-fg-2">{agent.description || <span className="text-muted">No description yet.</span>}</p>
      </div>

      <div className="relative mt-3 h-16 px-1">
        {p.calls ? <Sparkline data={agent.series} color="var(--fg)" id={`spark-${agent.id}`} />
          : <div className="dot-grid mx-4 grid h-full place-items-center rounded-xl text-[11px] font-semibold text-muted">No calls in 14 days</div>}
      </div>

      <div className="relative grid grid-cols-4 gap-px border-y border-border bg-border">
        {[['Calls 14d', p.calls], ['Connect', p.connect_rate === null ? '—' : `${Math.round(p.connect_rate)}%`], ['Meetings', s.meetings], ['Leads', s.leads]].map(([l, v]) => (
          <div key={l as string} className="bg-surface px-2 py-2.5 text-center">
            <div className="text-[17px] font-extrabold tabular-nums">{v}</div>
            <div className="text-[10.5px] font-semibold text-muted">{l}</div>
          </div>
        ))}
      </div>

      <div className="relative space-y-3 p-5 pt-4">
        <div>
          <div className="mb-1.5 flex items-center justify-between text-[11px] font-semibold text-muted">
            <span>Pipeline</span>
            {s.hot > 0 && <span className="flex items-center gap-1 text-danger"><Flame className="size-3" />{s.hot} hot</span>}
          </div>
          <PipelineBar pipeline={agent.pipeline} />
        </div>
        <div className="flex items-center justify-between gap-2 text-[11.5px] text-muted">
          <span className="flex min-w-0 items-center gap-1.5 truncate"><Phone className="size-3 shrink-0" />{agent.phone_number ?? 'Default number'}</span>
          <span className="shrink-0">{s.last_call_at ? `Last call ${timeAgo(s.last_call_at)}` : 'Never called'}</span>
        </div>
        {missing.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 text-[11px]">
            <span className="font-semibold text-muted">To do:</span>
            {missing.map((m) => <span key={m} className="rounded-full bg-surface-2 px-2 py-0.5 font-semibold text-fg-2 ring-1 ring-border">{m}</span>)}
          </div>
        )}
      </div>

      <div className="relative z-10 mt-auto grid grid-cols-4 border-t border-border text-[11.5px] font-semibold text-muted">
        {[[TrendingUp, 'Overview', ''], [Users, 'Leads', '/leads'], [BookOpen, 'Knowledge', '/knowledge'], [MessageSquareText, 'Test', '/agent?tab=playground']].map(([Icon, l, to]) => {
          const I = Icon as typeof Users
          return (
            <Link key={l as string} to={`${base}${to}`} className="flex flex-col items-center gap-1 py-2.5 transition hover:bg-surface-2 hover:text-fg">
              <I className="size-3.5" />{l as string}
            </Link>
          )
        })}
      </div>
    </Card>
  )
}

function AgentTable({ agents }: { agents: AgentOverviewItem[] }) {
  const navigate = useNavigate()
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-[11px] font-bold tracking-wider text-muted uppercase">
            <th className="px-5 py-3">Agent</th><th className="px-3 py-3">Status</th><th className="px-3 py-3 text-right">Leads</th>
            <th className="px-3 py-3 text-right">Calls 14d</th><th className="px-3 py-3 text-right">Connect</th><th className="px-3 py-3 text-right">Meetings</th>
            <th className="px-3 py-3 text-right">Talk time</th><th className="w-40 px-3 py-3">Trend</th><th className="px-5 py-3">Setup</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {agents.map((a) => (
            <tr key={a.id} onClick={() => navigate(`/a/${a.id}`)} className="cursor-pointer transition hover:bg-surface-2/60">
              <td className="px-5 py-3">
                <div className="flex items-center gap-3">
                  <AgentMark agent={a} className={cn('size-9', a.status === 'paused' && 'grayscale')} />
                  <div className="min-w-0"><div className="truncate font-bold">{a.name}</div><div className="truncate text-xs text-muted">{a.persona.company_name}</div></div>
                </div>
              </td>
              <td className="px-3 py-3">
                {a.status === 'paused' ? <Badge tone="warning">Paused</Badge> : a.stats.live ? <Badge tone="success" pulse>{a.stats.live} live</Badge>
                  : <Badge tone={a.within_calling_hours ? 'info' : 'neutral'}>{a.within_calling_hours ? 'Ready' : 'After hours'}</Badge>}
              </td>
              <td className="px-3 py-3 text-right font-semibold tabular-nums">{a.stats.leads}</td>
              <td className="px-3 py-3 text-right font-semibold tabular-nums">{a.period.calls}</td>
              <td className="px-3 py-3 text-right tabular-nums">{a.period.connect_rate === null ? '—' : `${Math.round(a.period.connect_rate)}%`}</td>
              <td className="px-3 py-3 text-right tabular-nums">{a.stats.meetings}</td>
              <td className="px-3 py-3 text-right text-muted tabular-nums">{formatDuration(a.period.talk_seconds)}</td>
              <td className="h-12 px-3 py-1">{a.period.calls ? <Sparkline data={a.series} color="var(--fg)" id={`row-${a.id}`} /> : <span className="text-xs text-muted">—</span>}</td>
              <td className="px-5 py-3"><Ring value={setupScore(a)} size={32} stroke={3.5}>{Math.round(setupScore(a) * 5)}</Ring></td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

export default function Home() {
  const [creating, setCreating] = useState(false)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<Status>('all')
  const [sort, setSort] = useState<Sort>('activity')
  const [view, setView] = useState<'grid' | 'table'>(() => { try { return (localStorage.getItem('agents-view') as 'grid' | 'table') || 'grid' } catch { return 'grid' } })
  const { data, isLoading } = useQuery({ queryKey: ['agents', 'overview'], queryFn: () => api<AgentsOverview>('/api/agents/overview'), refetchInterval: 8000 })
  const agents = useMemo(() => data?.agents ?? [], [data])

  const setViewStored = (v: 'grid' | 'table') => { setView(v); try { localStorage.setItem('agents-view', v) } catch { /* storage unavailable */ } }

  const totals = useMemo(() => {
    const t = agents.reduce((acc, a) => ({
      live: acc.live + a.stats.live, calls: acc.calls + a.period.calls, connected: acc.connected + a.period.connected,
      meetings: acc.meetings + a.stats.meetings, leads: acc.leads + a.stats.leads, hot: acc.hot + a.stats.hot, talk: acc.talk + a.period.talk_seconds,
      today: acc.today + a.stats.calls_today,
    }), { live: 0, calls: 0, connected: 0, meetings: 0, leads: 0, hot: 0, talk: 0, today: 0 })
    return { ...t, rate: t.calls ? Math.round((100 * t.connected) / t.calls) : null }
  }, [agents])

  const shown = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const list = agents.filter((a) => (status === 'all' || a.status === status)
      && `${a.name} ${a.description ?? ''} ${a.persona.company_name} ${a.persona.agent_name}`.toLowerCase().includes(needle))
    const by: Record<Sort, (a: AgentOverviewItem) => number | string> = {
      activity: (a) => -(a.stats.live * 1e12 + (a.stats.last_call_at ? Date.parse(a.stats.last_call_at) : 0)),
      name: (a) => a.name.toLowerCase(), calls: (a) => -a.period.calls, leads: (a) => -a.stats.leads,
      rate: (a) => -(a.period.connect_rate ?? -1), meetings: (a) => -a.stats.meetings,
    }
    return [...list].sort((x, y) => { const a = by[sort](x), b = by[sort](y); return a < b ? -1 : a > b ? 1 : 0 })
  }, [agents, q, status, sort])

  const attention = useMemo(() => agents.flatMap((a) => [
    ...(a.status === 'paused' ? [{ a, text: 'Paused: no calls are being placed', to: '/settings' }] : []),
    ...(!a.setup.knowledge ? [{ a, text: 'No knowledge: the agent can\'t answer specifics', to: '/knowledge' }] : []),
    ...(!a.setup.leads ? [{ a, text: 'No leads to call yet', to: '/import' }] : []),
    ...(a.period.calls >= 5 && (a.period.connect_rate ?? 0) < 20 ? [{ a, text: `Low connect rate (${Math.round(a.period.connect_rate ?? 0)}%)`, to: '/analytics' }] : []),
  ]), [agents])

  // Scheduler runs repeat every few minutes; collapse identical consecutive entries so real events stay visible.
  const activity = useMemo(() => {
    const out: (AgentsOverview['activity'][number] & { repeat: number })[] = []
    for (const e of data?.activity ?? []) {
      const last = out[out.length - 1]
      if (last && last.agent_id === e.agent_id && last.title === e.title) last.repeat += 1
      else out.push({ ...e, repeat: 1 })
    }
    return out.slice(0, 8)
  }, [data])

  const comparison = agents.map((a) => ({ name: a.name.length > 14 ? `${a.name.slice(0, 13)}…` : a.name, connected: a.period.connected, other: a.period.calls - a.period.connected }))
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  if (isLoading) return <div className="space-y-4"><Skeleton className="h-28" /><div className="grid gap-4 md:grid-cols-5">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-28" />)}</div><Skeleton className="h-96" /></div>

  if (!agents.length) {
    return (
      <>
        <div className="mx-auto flex max-w-3xl flex-col items-center py-14 text-center">
          <div className="grid size-20 place-items-center rounded-3xl bg-brand text-brand-fg shadow-glow"><AudioWaveform className="size-10" /></div>
          <h1 className="mt-8 text-4xl font-extrabold tracking-tight">Build your first voice agent</h1>
          <p className="mt-4 max-w-xl text-[15px] text-muted">Run a separate AI caller for every product, campaign or client. Each agent gets its own persona, voice, phone number,
            knowledge base, leads and call history, so nothing ever mixes.</p>
          <Button variant="primary" size="lg" className="mt-8" onClick={() => setCreating(true)}><Plus />Create agent</Button>
          <div className="mt-14 grid w-full gap-4 text-left sm:grid-cols-3">
            {[[Sparkles, '1. Shape the persona', 'Name, voice, script and guardrails.'], [BookOpen, '2. Teach it', 'Upload brochures, price lists and FAQs.'],
              [Upload, '3. Give it leads', 'Import a list and let it call.']].map(([Icon, t, d]) => {
              const I = Icon as typeof Users
              return <Card key={t as string} className="p-5"><span className="grid size-10 place-items-center rounded-xl bg-brand-soft text-brand"><I className="size-5" /></span><div className="mt-3 font-bold">{t as string}</div><div className="mt-1 text-sm text-muted">{d as string}</div></Card>
            })}
          </div>
        </div>
        <NewAgentSheet open={creating} onClose={() => setCreating(false)} />
      </>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow={<><Radio className="size-3.5" />Command center · {new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</>}
        title={`${greeting}.`}
        description={`${agents.length} agent${agents.length > 1 ? 's' : ''} · ${totals.live ? `${totals.live} call${totals.live > 1 ? 's' : ''} live right now` : 'no calls live right now'} · ${totals.today} call${totals.today === 1 ? '' : 's'} today`}
        actions={<Button variant="primary" onClick={() => setCreating(true)}><Plus />New agent</Button>}
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatTile label="Live now" value={totals.live} icon={<PhoneCall />} tone="success" sub={totals.live ? 'Across all agents' : 'All lines quiet'}
          trend={totals.live > 0 ? <LiveDot on /> : undefined} />
        <Card className="relative overflow-hidden p-5 sm:col-span-2 xl:col-span-2">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-[13px] font-semibold text-muted">Calls, last {data?.days} days</div>
              <div className="mt-2 flex items-baseline gap-3">
                <span className="text-[30px] leading-none font-extrabold tabular-nums">{totals.calls}</span>
                <span className="text-sm text-muted"><b className="text-fg">{totals.connected}</b> connected{totals.rate !== null && ` · ${totals.rate}%`}</span>
              </div>
            </div>
            <span className="grid size-9 place-items-center rounded-xl bg-info-soft text-info ring-1 ring-info/20"><Phone className="size-4" /></span>
          </div>
          <div className="mt-3 h-16">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data?.series} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="tot" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--fg)" stopOpacity={0.18} /><stop offset="100%" stopColor="var(--fg)" stopOpacity={0} /></linearGradient>
                </defs>
                <Tooltip contentStyle={tooltipStyle} labelFormatter={(d) => String(d)} />
                <Area type="monotone" dataKey="calls" name="Calls" stroke="var(--fg)" strokeWidth={2} fill="url(#tot)" isAnimationActive={false} />
                <Area type="monotone" dataKey="connected" name="Connected" stroke="var(--muted)" strokeDasharray="4 3" strokeWidth={1.5} fill="transparent" isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <StatTile label="Meetings booked" value={totals.meetings} icon={<CalendarCheck />} tone="success" sub={`${totals.hot} hot lead${totals.hot === 1 ? '' : 's'} in play`} />
        <StatTile label="Leads" value={totals.leads} icon={<Users />} tone="info" sub={`Talk time ${formatDuration(totals.talk)} · 14d`} />
      </div>

      <div className="mt-6 grid gap-6 2xl:grid-cols-[1fr_360px]">
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="mr-2 text-lg font-extrabold">Agents</h2>
            <Tabs value={status} onChange={setStatus} items={[
              { value: 'all', label: `All ${agents.length}` },
              { value: 'active', label: `Active ${agents.filter((a) => a.status === 'active').length}` },
              { value: 'paused', label: `Paused ${agents.filter((a) => a.status === 'paused').length}` },
            ]} />
            <div className="flex-1" />
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an agent" className="w-52 pl-9" aria-label="Find an agent" />
            </div>
            <Select value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="w-44" aria-label="Sort agents">
              <option value="activity">Sort: recent activity</option><option value="name">Sort: name</option><option value="calls">Sort: most calls</option>
              <option value="rate">Sort: connect rate</option><option value="meetings">Sort: meetings</option><option value="leads">Sort: leads</option>
            </Select>
            <div className="inline-flex rounded-xl border border-border bg-surface-2 p-1">
              {([['grid', LayoutGrid], ['table', List]] as const).map(([v, Icon]) => (
                <button key={v} type="button" onClick={() => setViewStored(v)} aria-label={`${v} view`}
                  className={cn('grid size-8 place-items-center rounded-lg transition', view === v ? 'bg-surface text-fg shadow-sm ring-1 ring-border' : 'text-muted hover:text-fg')}>
                  <Icon className="size-4" />
                </button>
              ))}
            </div>
          </div>

          {view === 'table' ? (shown.length ? <AgentTable agents={shown} /> : null) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {shown.map((a) => <AgentCard key={a.id} agent={a} />)}
              {!q && status !== 'paused' && (
                <button type="button" onClick={() => setCreating(true)}
                  className="dot-grid flex min-h-[420px] flex-col items-center justify-center gap-3 rounded-[var(--radius-card)] border-2 border-dashed border-border text-muted transition hover:border-brand hover:text-brand">
                  <span className="grid size-12 place-items-center rounded-2xl bg-surface shadow-card ring-1 ring-border"><Plus className="size-6" /></span>
                  <span className="text-sm font-bold">New agent</span>
                  <span className="max-w-52 text-center text-xs">For another product, campaign, city or client</span>
                </button>
              )}
            </div>
          )}
          {!shown.length && <Card className="py-14 text-center text-sm text-muted">No agent matches these filters.</Card>}

          {agents.length > 1 && (
            <Card>
              <CardHeader title="Agent comparison" description={`Calls over the last ${data?.days} days, connected vs. not connected`} />
              <div className="h-64 px-3 pb-4">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={comparison} margin={{ left: -20, right: 8, top: 8 }} barCategoryGap="30%">
                    <CartesianGrid vertical={false} stroke="var(--border)" />
                    <XAxis dataKey="name" tick={{ fill: 'var(--muted)', fontSize: 12 }} axisLine={false} tickLine={false} />
                    <YAxis allowDecimals={false} tick={{ fill: 'var(--muted)', fontSize: 12 }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'var(--surface-2)' }} />
                    <Bar dataKey="connected" name="Connected" stackId="c" fill="var(--fg)" radius={[0, 0, 4, 4]} isAnimationActive={false} />
                    <Bar dataKey="other" name="Not connected" stackId="c" fill="var(--border-strong)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><LiveDot on={!!data?.live_calls.length} />Live calls</span>}
              description={data?.live_calls.length ? 'In progress across every agent' : 'Nothing on the line right now'} />
            <div className="px-3 pb-3">
              {data?.live_calls.length ? data.live_calls.map((c) => {
                const a = agents.find((x) => x.id === c.agent_id)
                return (
                  <Link key={c.id} to={`/a/${c.agent_id}/calls?status=active`} className="flex items-center gap-3 rounded-xl px-2 py-2.5 hover:bg-surface-2">
                    {a && <AgentMark agent={a} className="size-8 rounded-lg text-[10px]" />}
                    <div className="min-w-0 flex-1 leading-tight">
                      <div className="truncate text-sm font-bold">{c.lead_name ?? c.to_number}</div>
                      <div className="truncate text-xs text-muted">{c.agent_name} · {timeAgo(c.created_at)}</div>
                    </div>
                    <CallStatusBadge status={c.status} />
                  </Link>
                )
              }) : (
                <div className="dot-grid grid h-24 place-items-center rounded-xl text-xs font-semibold text-muted"><span className="flex items-center gap-2"><Timer className="size-4" />Waiting for the next call</span></div>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><AlertTriangle className="size-4 text-warning" />Needs attention</span>}
              description={attention.length ? `${attention.length} item${attention.length > 1 ? 's' : ''} to review` : 'Every agent is set up'} />
            <div className="px-3 pb-3">
              {attention.slice(0, 6).map(({ a, text, to }, i) => (
                <Link key={i} to={`/a/${a.id}${to}`} className="group flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-surface-2">
                  <AgentMark agent={a} className="size-6 rounded-md text-[9px]" />
                  <div className="min-w-0 flex-1 leading-tight"><div className="truncate text-[13px] font-semibold">{text}</div><div className="truncate text-xs text-muted">{a.name}</div></div>
                  <ArrowUpRight className="size-4 text-muted opacity-0 group-hover:opacity-100" />
                </Link>
              ))}
              {!attention.length && <p className="px-2 pb-2 text-sm text-muted">Nothing needs your attention.</p>}
            </div>
          </Card>

          <Card>
            <CardHeader title="Recent activity" description="Latest events from all agents" />
            <ol className="px-5 pb-5">
              {activity.map((e) => {
                return (
                  <li key={e.id} className="relative border-l border-border pb-4 pl-4 last:pb-0">
                    <span className="absolute top-1 -left-[5px] size-2.5 rounded-full ring-2 ring-surface" style={{ background: 'var(--fg)' }} />
                    <Link to={`/a/${e.agent_id}/activity`} className="block text-[13px] leading-snug font-semibold hover:text-brand">
                      <span className="line-clamp-2">{e.title}</span>
                    </Link>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-muted">
                      <span className="font-semibold text-fg-2">{e.agent_name}</span><span>·</span><span>{timeAgo(e.created_at)}</span>
                      {e.repeat > 1 && <><span>·</span><span>×{e.repeat}</span></>}
                    </div>
                  </li>
                )
              })}
              {!activity.length && <p className="text-sm text-muted">No activity yet.</p>}
            </ol>
          </Card>
        </div>
      </div>

      <NewAgentSheet open={creating} onClose={() => setCreating(false)} />
    </>
  )
}
