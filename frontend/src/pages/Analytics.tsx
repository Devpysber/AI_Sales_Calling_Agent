import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3, CalendarCheck, Clock, Flame, Gauge, IndianRupee, PhoneCall, Printer, TrendingUp, UserPlus } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Button, Card, CardHeader, EmptyState, Meter, PageHeader, Skeleton, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import { ChartNowDot } from '@/components/Live'
import { AnimatedNumber, Stagger } from '@/lib/motion'
import { useAgent } from '@/lib/agent'
import type { AnalyticsKpis, AnalyticsReport, AnalyticsUsage } from '@/lib/types'
import { cn, DAYS, formatDuration, titleCase } from '@/lib/utils'

const tooltipStyle = { background: 'var(--elevated)', border: '1px solid var(--border)', borderRadius: 12, fontSize: 12, boxShadow: 'var(--shadow-pop)' }
const RANGES = [{ value: '7', label: '7d' }, { value: '14', label: '14d' }, { value: '30', label: '30d' }, { value: '90', label: '90d' }] as const

// The API already returns rates as percentages (0-100).
const pct = (n: number | null | undefined) => (n == null ? '—' : `${n.toFixed(1)}%`)

function Delta({ curr, prev, invert }: { curr: number | null | undefined; prev: number | null | undefined; invert?: boolean }) {
  if (curr == null || prev == null || prev === 0) return <span className="text-[11px] font-semibold text-muted">no prior data</span>
  const d = ((curr - prev) / prev) * 100
  const good = invert ? d <= 0 : d >= 0
  const Icon = d >= 0 ? ArrowUpRight : ArrowDownRight
  return <span className={cn('inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-bold', good ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger')}><Icon className="size-3" />{Math.abs(d).toFixed(0)}%</span>
}

function Kpi({ icon, label, value, curr, prev, sub, invert }: {
  icon: ReactNode; label: string; value: ReactNode; curr?: number | null; prev?: number | null; sub?: ReactNode; invert?: boolean
}) {
  return (
    <Card className="glint group p-5 transition duration-300 hover:-translate-y-0.5 hover:shadow-pop">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[13px] font-semibold text-muted">{label}</span>
        <span className="grid size-8 place-items-center rounded-xl bg-surface-2 text-fg-2 ring-1 ring-border transition-transform duration-300 group-hover:-rotate-6 group-hover:scale-110 [&_svg]:size-4">{icon}</span>
      </div>
      <div className="mt-2 min-w-0 truncate text-[26px] leading-none font-extrabold tracking-tight tabular-nums sm:text-[28px]">{typeof value === 'number' ? <AnimatedNumber value={value} /> : value}</div>
      <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs text-muted">
        {curr !== undefined && <Delta curr={curr} prev={prev} invert={invert} />}
        {sub}
      </div>
    </Card>
  )
}

// The heatmap only draws 08:00 - 20:00 IST. The backend emits every hour a call happened (inbound can land at any
// hour), so both the "best slot" and the shading scale must be computed over the drawn cells only.
const HOURS = Array.from({ length: 13 }, (_, i) => i + 8)
const visibleCells = (cells: AnalyticsReport['heatmap'] | undefined) => (cells ?? []).filter((c) => c.hour >= HOURS[0]! && c.hour <= HOURS[HOURS.length - 1]!)
const bestSlot = (cells: AnalyticsReport['heatmap'] | undefined) =>
  visibleCells(cells).filter((c) => c.calls >= 3).sort((a, b) => b.connected / b.calls - a.connected / a.calls)[0]

function Heatmap({ cells }: { cells: AnalyticsReport['heatmap'] }) {
  const hours = HOURS
  const shown = visibleCells(cells)
  const map = new Map(shown.map((c) => [`${c.weekday}-${c.hour}`, c]))
  const max = Math.max(1, ...shown.map((c) => c.calls))
  // The slot with the best connect rate (3+ calls) keeps a pulse: it is the answer this chart exists for.
  const best = bestSlot(cells)
  return (
    <div className="w-full min-w-0 overflow-x-auto overscroll-x-contain">
      <table className="w-full min-w-[560px] border-separate border-spacing-1 text-[11px]">
        <caption className="sr-only">Calls and connect rate by weekday and hour (IST)</caption>
        <thead><tr><th scope="col"><span className="sr-only">Day</span></th>{hours.map((h) => <th key={h} scope="col" className="font-semibold text-muted">{h}</th>)}</tr></thead>
        <tbody>
          {DAYS.map((d, w) => (
            <tr key={d}>
              <th scope="row" className="pr-2 text-right font-semibold text-muted">{d}</th>
              {hours.map((h) => {
                const c = map.get(`${w}-${h}`)
                const rate = c?.calls ? Math.round((100 * c.connected) / c.calls) : null
                const label = c ? `${d} ${h}:00 · ${c.calls} calls · ${rate}% connected` : `${d} ${h}:00 · no calls`
                return (
                  <td key={h} title={label} aria-label={label} tabIndex={c ? 0 : undefined}
                    className={cn('heat-cell h-7 rounded-md ring-1 ring-border/60 transition-transform hover:scale-110 focus-visible:scale-110 focus-visible:ring-2 focus-visible:ring-fg focus-visible:outline-none', c === best && 'heat-best')}
                    style={{ animationDelay: `${(w + (h - 8)) * 28}ms`, background: c ? `color-mix(in srgb, var(--fg) ${Math.round(12 + (78 * c.calls) / max)}%, transparent)` : 'var(--surface-2)' }}>
                    {c && rate !== null && <span className="block text-center text-[9.5px] font-bold text-bg mix-blend-difference" aria-hidden>{c.calls >= 3 ? rate : c.calls}</span>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Analytics() {
  const { agent, base } = useAgent()
  const [days, setDays] = useState<'7' | '14' | '30' | '90'>('30')
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery<AnalyticsReport>({
    queryKey: ['analytics', days],
    queryFn: () => api<AnalyticsReport>(`${base}/analytics`, { params: { days } }),
    placeholderData: keepPreviousData,
    refetchInterval: 30000,
  })

  const k: AnalyticsKpis | undefined = data?.kpis
  const p = data?.previous
  const outcomes = Object.entries(data?.outcomes ?? {}).sort((a, b) => b[1] - a[1])
  const outcomeTotal = outcomes.reduce((n, [, v]) => n + v, 0)
  // Sentiment is stored raw from the LLM (no whitelist), so fold 'Positive' / ' positive ' into the expected keys.
  const sentiment = Object.entries(data?.sentiment ?? {}).reduce<Record<string, number>>((acc, [key, v]) => {
    const k = key.trim().toLowerCase()
    acc[k] = (acc[k] ?? 0) + (v ?? 0)
    return acc
  }, {})
  const sentimentTotal = Object.values(sentiment).reduce((a, b) => a + b, 0)
  const qual = data?.qualification ?? {}
  const funnel = data?.funnel ?? []
  const series = data?.series ?? []
  const heatmap = data?.heatmap ?? []
  const triggers = data?.triggers ?? []
  const sources = data?.sources ?? []
  const failures = data?.failures ?? []
  const funnelTop = funnel[0]?.count ?? 0
  const best = bestSlot(heatmap)

  return (
    <>
      <PageHeader
        eyebrow={<><BarChart3 className="size-3.5" />{agent?.name} · Performance</>}
        title="Analytics"
        description="How this agent performs: reach, conversations, conversions and the best time to call. Compared with the previous period of the same length."
        actions={<>
          <Tabs value={days} onChange={setDays} items={RANGES.map((r) => ({ ...r }))} />
          <Button onClick={() => window.print()} className="no-print"><Printer />Print</Button>
        </>} />

      {isError ? (
        <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load analytics" description={error instanceof Error ? error.message : 'Something went wrong while fetching the report.'}
          action={<Button onClick={() => refetch()} loading={isFetching}>Try again</Button>} /></Card>
      ) : isLoading || !data || !k ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-32" />)}</div>
      ) : (
        <div className="space-y-4">
          <Stagger className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" step={60}>
            <Kpi icon={<PhoneCall />} label="Calls" value={k.calls} curr={k.calls} prev={p?.calls} sub={`${k.connected} connected`} />
            <Kpi icon={<TrendingUp />} label="Connect rate" value={pct(k.connect_rate)} curr={k.connect_rate} prev={p?.connect_rate}
              sub={k.connect_rate != null && <Meter value={k.connect_rate} tone="success" className="w-16" />} />
            <Kpi icon={<CalendarCheck />} label="Meetings booked" value={k.meetings} curr={k.meetings} prev={p?.meetings} sub={`${pct(k.meeting_rate)} of connected calls`} />
            <Kpi icon={<Flame />} label="Hot leads" value={k.hot} curr={k.hot} prev={p?.hot} sub="Qualified Hot on a call" />
            <Kpi icon={<Clock />} label="Talk time" value={formatDuration(k.talk_seconds)} curr={k.talk_seconds} prev={p?.talk_seconds} />
            <Kpi icon={<BarChart3 />} label="Avg. conversation" value={k.avg_duration != null ? formatDuration(k.avg_duration) : '—'} curr={k.avg_duration} prev={p?.avg_duration} />
            <Kpi icon={<Gauge />} label="AI response time" value={k.avg_latency_ms != null ? `${(k.avg_latency_ms / 1000).toFixed(1)}s` : '—'} curr={k.avg_latency_ms} prev={p?.avg_latency_ms} invert />
            <Kpi icon={<UserPlus />} label="New leads" value={data.new_leads ?? 0} sub={`Added in the last ${days} days`} />
          </Stagger>

          {data.usage && <UsageCard usage={data.usage} />}

          <Card>
            <CardHeader title="Daily activity" description="Calls placed, calls connected and meetings booked per day (IST)" />
            <div className="draw-in h-64 min-w-0 px-2 pb-3 sm:h-72">
              {series.length ? <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: -16 }}>
                  <defs><linearGradient id="aCalls" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--fg)" stopOpacity={0.16} /><stop offset="100%" stopColor="var(--fg)" stopOpacity={0} /></linearGradient></defs>
                  <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={16} />
                  <YAxis allowDecimals={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 700 }} />
                  <Area type="monotone" dataKey="calls" name="Calls" stroke="var(--fg)" fill="url(#aCalls)" strokeWidth={2.5} isAnimationActive={false}
                    dot={(d: { cx?: number; cy?: number; index?: number }) => <ChartNowDot key={d.index} {...d} last={series.length - 1} />} />
                  <Area type="monotone" dataKey="connected" name="Connected" stroke="var(--success)" fill="transparent" strokeWidth={2} dot={false} isAnimationActive={false} />
                  <Area type="monotone" dataKey="meetings" name="Meetings" stroke="var(--muted)" fill="transparent" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer> : <p className="py-10 text-center text-sm text-muted">No calls in this period.</p>}
            </div>
          </Card>

          <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
            <Card className="min-w-0">
              <CardHeader title="Best time to call" description={best ? `Highest connect rate: ${DAYS[best.weekday]} around ${best.hour}:00 IST (${Math.round((100 * best.connected) / best.calls)}% of ${best.calls} calls)` : 'Darker = more calls. Numbers show connect % where there were 3+ calls. Hours in IST.'} />
              <div className="min-w-0 px-4 pb-4 sm:px-5 sm:pb-5">{heatmap.length ? <Heatmap cells={heatmap} /> : <p className="py-10 text-center text-sm text-muted">No calls in this period.</p>}</div>
            </Card>
            <Card className="min-w-0">
              <CardHeader title="Conversion funnel" description="All-time, for this agent's leads" />
              <div className="space-y-3 px-4 pb-4 sm:px-5 sm:pb-5">
                {funnel.length ? funnel.map((f, i) => (
                  <div key={f.stage}>
                    <div className="mb-1 flex items-baseline justify-between text-[13px]">
                      <span className="min-w-0 truncate font-semibold text-fg-2">{f.stage}</span>
                      <span className="flex shrink-0 items-baseline gap-2"><b className="text-base tabular-nums"><AnimatedNumber value={f.count} /></b>
                        <span className="w-10 text-right text-[11px] text-muted">{i && funnel[i - 1]?.count ? `${Math.round((100 * f.count) / funnel[i - 1]!.count)}%` : ''}</span></span>
                    </div>
                    <div className="h-2.5 overflow-hidden rounded-full bg-surface-2"><div className="grow-x flow h-full rounded-full bg-fg" style={{ width: `${funnelTop ? Math.max((100 * f.count) / funnelTop, f.count ? 3 : 0) : 0}%`, opacity: 1 - i * 0.15, animationDelay: `${i * 110}ms` }} /></div>
                  </div>
                )) : <p className="py-10 text-center text-sm text-muted">No leads yet.</p>}
              </div>
            </Card>
          </div>

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            <Card>
              <CardHeader title="Outcomes" description="What each completed conversation achieved" />
              <div className="space-y-2.5 px-4 pb-4 sm:px-5 sm:pb-5">
                {outcomes.length ? outcomes.map(([name, v], i) => (
                  <div key={name} className="grid grid-cols-[1fr_auto] gap-x-3 text-[13px]">
                    <span className="truncate font-semibold text-fg-2">{titleCase(name)}</span><span className="font-bold tabular-nums"><AnimatedNumber value={v} /> <span className="text-xs font-semibold text-muted">{outcomeTotal ? Math.round((100 * v) / outcomeTotal) : 0}%</span></span>
                    <div className="col-span-2 mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2">
                      <div className={cn('grow-x flow h-full rounded-full', name === 'meeting_booked' ? 'bg-success' : ['not_interested', 'do_not_call'].includes(name) ? 'bg-danger' : 'bg-fg/70')} style={{ width: `${outcomeTotal ? (100 * v) / outcomeTotal : 0}%`, animationDelay: `${i * 90}ms` }} />
                    </div>
                  </div>
                )) : <EmptyState icon={<PhoneCall />} title="No outcomes yet" />}
              </div>
            </Card>

            <Card>
              <CardHeader title="Lead temperature & sentiment" description="AI assessment from conversations" />
              <div className="space-y-5 px-4 pb-4 sm:px-5 sm:pb-5">
                <div className="grid grid-cols-3 gap-2 text-center">
                  {[['Hot', 'text-danger'], ['Warm', 'text-warning'], ['Cold', 'text-info']].map(([t, cls]) => (
                    <div key={t} className="rounded-xl bg-surface-2 py-3 ring-1 ring-border transition hover:-translate-y-0.5 hover:ring-border-strong"><div className={cn('text-2xl font-extrabold tabular-nums', cls)}><AnimatedNumber value={qual[t!] ?? 0} /></div><div className="text-[11px] font-semibold text-muted">{t}</div></div>
                  ))}
                </div>
                <div>
                  <div className="mb-2 text-[12px] font-bold text-muted uppercase">Sentiment</div>
                  <div className="flex h-3 overflow-hidden rounded-full bg-surface-2">
                    {sentimentTotal ? [['positive', 'bg-success'], ['neutral', 'bg-border-strong'], ['negative', 'bg-danger']].map(([s, cls], i) => (
                      <div key={s} className={cn('grow-x flow', cls)} style={{ width: `${(100 * (sentiment[s!] ?? 0)) / sentimentTotal}%`, animationDelay: `${150 + i * 140}ms` }} />
                    )) : null}
                  </div>
                  <div className="mt-2 flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs text-muted">
                    {['positive', 'neutral', 'negative'].map((s) => <span key={s} className="capitalize">{s} <b className="text-fg">{sentiment[s] ?? 0}</b></span>)}
                  </div>
                </div>
              </div>
            </Card>

            <Card>
              <CardHeader title="Why calls didn't connect" description="Most common reasons in this period" />
              <div className="px-4 pb-4 sm:px-5 sm:pb-5">
                {failures.length ? (
                  <ul className="divide-y divide-border">
                    {failures.map((f, i) => (
                      <li key={f.reason} style={{ animationDelay: `${i * 60}ms` }} className="reveal reveal-in reveal-left flex items-center justify-between gap-3 py-2 text-[13px]"><span className="min-w-0 [overflow-wrap:anywhere] text-fg-2">{f.reason}</span><b className="shrink-0 tabular-nums"><AnimatedNumber value={f.count} /></b></li>
                    ))}
                  </ul>
                ) : <p className="py-10 text-center text-sm text-muted">Every call connected. 🎉</p>}
              </div>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="overflow-hidden">
              <CardHeader title="By trigger" description="Manual, bulk, auto-dial, retry and inbound" />
              <div className="w-full min-w-0 overflow-x-auto overscroll-x-contain">
                <table className="rows-in w-full min-w-[420px] text-sm">
                  <thead><tr className="border-y border-border bg-surface-2/50 text-left text-[11px] font-bold tracking-wider text-muted uppercase"><th className="px-5 py-2.5">Trigger</th><th className="px-3 py-2.5 text-right">Calls</th><th className="px-3 py-2.5 text-right">Connected</th><th className="px-5 py-2.5 text-right">Meetings</th></tr></thead>
                  <tbody className="divide-y divide-border">
                    {triggers.map((t) => (
                      <tr key={t.trigger}><td className="px-5 py-2.5 font-semibold">{titleCase(t.trigger)}</td><td className="px-3 text-right tabular-nums">{t.calls}</td>
                        <td className="px-3 text-right tabular-nums">{t.connected} <span className="text-xs text-muted">{t.calls ? `${Math.round((100 * t.connected) / t.calls)}%` : ''}</span></td><td className="px-5 text-right tabular-nums">{t.meetings}</td></tr>
                    ))}
                    {!triggers.length && <tr><td colSpan={4} className="px-5 py-8 text-center text-muted">No calls in this period.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>
            <Card className="overflow-hidden">
              <CardHeader title="Lead sources" description="Where this agent's best leads come from" />
              <div className="w-full min-w-0 overflow-x-auto overscroll-x-contain">
                <table className="rows-in w-full min-w-[420px] text-sm">
                  <thead><tr className="border-y border-border bg-surface-2/50 text-left text-[11px] font-bold tracking-wider text-muted uppercase"><th className="px-5 py-2.5">Source</th><th className="px-3 py-2.5 text-right">Leads</th><th className="px-3 py-2.5 text-right">Hot</th><th className="px-5 py-2.5 text-right">Meetings</th></tr></thead>
                  <tbody className="divide-y divide-border">
                    {sources.map((s) => (
                      <tr key={s.source}><td className="max-w-[200px] truncate px-5 py-2.5 font-semibold">{s.source}</td><td className="px-3 text-right tabular-nums">{s.leads}</td><td className="px-3 text-right tabular-nums">{s.hot}</td>
                        <td className="px-5 text-right tabular-nums">{s.meetings} <span className="text-xs text-muted">{s.leads ? `${Math.round((100 * s.meetings) / s.leads)}%` : ''}</span></td></tr>
                    ))}
                    {!sources.length && <tr><td colSpan={4} className="px-5 py-8 text-center text-muted">No leads yet.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        </div>
      )}
    </>
  )
}

function UsageCard({ usage: u }: { usage: AnalyticsUsage }) {
  const cost = u.cost ?? { telephony: 0, tts: 0, stt: 0, llm: 0 }
  const money = (v: number | null | undefined) => `${u.currency ?? '₹'}${(v ?? 0).toLocaleString('en-IN', { maximumFractionDigits: (v ?? 0) < 10 ? 2 : 0 })}`
  const rows: [string, string, number][] = [
    ['Telephony', `${u.call_minutes ?? 0} connected min`, cost.telephony ?? 0],
    ['Voice (TTS)', `${(u.tts_chars ?? 0).toLocaleString('en-IN')} characters`, cost.tts ?? 0],
    ['Speech recognition', (u.stt_seconds ?? 0) < 120 ? `${u.stt_seconds ?? 0}s of caller audio` : `${Math.round((u.stt_seconds ?? 0) / 60)} min of caller audio`, cost.stt ?? 0],
    ['Conversation AI', `${(u.llm_requests ?? 0).toLocaleString('en-IN')} replies`, cost.llm ?? 0],
  ]
  const max = Math.max(...rows.map((r) => r[2]), 0.0001)
  return (
    <Card>
      <CardHeader title={<span className="inline-flex items-center gap-2"><IndianRupee className="size-4" />Usage & cost</span>}
        description={u.metered_calls ? `Billable usage measured on ${u.metered_calls} call${u.metered_calls === 1 ? '' : 's'} in this period`
          : 'Usage is measured on calls placed from now on.'}
        action={u.rates_configured && <div className="text-right"><div className="text-xl font-extrabold tabular-nums">{money(u.total_cost)}</div>
          <div className="text-xs text-muted">{u.cost_per_connected_call != null ? `${money(u.cost_per_connected_call)} per connected call` : 'estimated'}</div>
          {u.per_minute && <div className="mt-1 text-xs font-semibold tabular-nums text-fg-2">{money(u.per_minute.total_cost)}/min · TTS {money(u.per_minute.tts_cost)}/min · {u.per_minute.tts_chars.toLocaleString('en-IN')} chars/min</div>}
          {u.per_call && <div className="mt-1 text-xs tabular-nums text-muted">{u.per_call.tts_chars.toLocaleString('en-IN')} chars/call{u.budget ? ` (budget ${u.budget.tts_chars})` : ''} · {Math.round(u.per_call.duration / 60)}m{u.per_call.duration % 60}s avg{u.cost_per_qualified_lead != null ? ` · ${money(u.cost_per_qualified_lead)} per qualified lead` : ''}</div>}</div>} />
      {u.rates_configured && u.per_call && (
        // The 100-call baseline: what one connected call actually costs, part by part, and what the LLM is fed.
        <div className="mx-4 mb-3 grid gap-2 rounded-xl border border-border bg-surface-2/60 p-3 text-xs tabular-nums sm:mx-5 sm:grid-cols-2 lg:grid-cols-4">
          <div><div className="text-muted">Per connected call</div><div className="font-bold text-sm">{money(u.per_call.total_cost ?? 0)}</div>
            <div className="text-muted">tel {money(u.per_call.telephony_cost ?? 0)} · STT {money(u.per_call.stt_cost ?? 0)} · TTS {money(u.per_call.tts_cost)} · LLM {money(u.per_call.llm_cost ?? 0)}</div></div>
          <div><div className="text-muted">LLM requests / call</div><div className="font-bold text-sm">{u.per_call.llm_requests ?? 0}</div>
            <div className="text-muted">{u.llm_billing === 'tokens' ? 'billed by tokens' : 'billed per request'}</div></div>
          <div><div className="text-muted">LLM tokens / call</div><div className="font-bold text-sm">{u.per_call.llm_input_tokens != null ? `${u.per_call.llm_input_tokens.toLocaleString('en-IN')} in · ${(u.per_call.llm_output_tokens ?? 0).toLocaleString('en-IN')} out` : 'not measured yet'}</div>
            <div className="text-muted">{u.per_call.llm_input_tokens_per_request != null ? `${u.per_call.llm_input_tokens_per_request.toLocaleString('en-IN')} in per request · ${u.token_calls} calls measured` : 'counts start with the next calls'}</div></div>
          <div><div className="text-muted">TTS chars / call</div><div className="font-bold text-sm">{u.per_call.tts_chars.toLocaleString('en-IN')}{u.budget ? ` / ${u.budget.tts_chars}` : ''}</div>
            <div className="text-muted">{Math.round(u.per_call.duration / 60)}m{u.per_call.duration % 60}s avg{u.budget ? ` / ${u.budget.target_minutes}m target` : ''}</div></div>
        </div>
      )}
      <div className="grid gap-3 px-4 pb-4 sm:grid-cols-2 sm:px-5 sm:pb-5 lg:grid-cols-4">
        {rows.map(([label, amount, cost], i) => (
          <div key={label} style={{ animationDelay: `${i * 70}ms` }} className="reveal reveal-in reveal-up glint rounded-xl border border-border p-3 transition hover:-translate-y-0.5 hover:border-border-strong">
            <div className="text-xs text-muted">{label}</div>
            <div className="mt-1 truncate font-bold tabular-nums">{amount}</div>
            {u.rates_configured && <>
              <div className="mt-2 h-1.5 rounded-full bg-surface-2"><div className="grow-x h-full rounded-full bg-fg" style={{ width: `${(100 * cost) / max}%` }} /></div>
              <div className="mt-1 text-xs text-muted tabular-nums">{money(cost)}</div>
            </>}
          </div>
        ))}
      </div>
      {!u.rates_configured && (
        <p className="border-t border-border px-4 py-3 text-xs break-words text-muted sm:px-5">
          Add your provider rates on the server (COST_PER_CALL_MINUTE, COST_PER_10K_TTS_CHARS, COST_PER_STT_HOUR, COST_PER_LLM_REQUEST) to see rupee estimates.
        </p>
      )}
    </Card>
  )
}
