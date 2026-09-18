import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Bot, Clock, Gauge, PhoneCall, PhoneIncoming, PhoneOutgoing, Radio, Search, Timer, X, ListOrdered } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { LiveDot } from '@/components/AppShell'
import CallSheet from '@/components/CallSheet'
import { CallStatusBadge, QualificationBadge, SentimentDot } from '@/components/status'
import CallQueue from '@/components/CallQueue'
import { Button, Card, EmptyState, Input, PageHeader, Pagination, Select, Skeleton, StatTile, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import { useDebounced } from '@/lib/useDebounced'
import type { Call, CallStats, Page } from '@/lib/types'
import { callHandledBy, callParty, CALL_STATUSES, cn, formatDate, formatDuration, timeAgo, titleCase } from '@/lib/utils'

function LiveCallCard({ call, onOpen }: { call: Call; onOpen: () => void }) {
  const { base } = useAgent()
  const detail = useQuery({ queryKey: ['call', call.id], queryFn: () => api<Call>(`${base}/calls/${call.id}`), refetchInterval: 2000 })
  const turns = detail.data?.transcript ?? []
  return (
    <Card className="flex flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b border-border p-4">
        <span className="relative grid size-10 place-items-center rounded-full bg-success-soft text-success"><PhoneCall className="size-4" />
          <span className="absolute -top-0.5 -right-0.5"><LiveDot on /></span></span>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="truncate font-bold">{callParty(call)}</div>
          <div className="text-xs text-muted">{call.direction === 'inbound' ? 'Inbound' : 'Outbound'} · {call.direction === 'inbound' ? call.from_number : call.to_number} · {timeAgo(call.created_at)}</div>
        </div>
        <CallStatusBadge status={call.status} />
      </div>
      <div className="flex h-56 flex-col-reverse overflow-y-auto p-4">
        <div className="space-y-2">
          {turns.length ? turns.slice(-8).map((t, i) => (
            <div key={i} className={cn('flex', t.role === 'customer' && 'justify-end')}>
              <div className={cn('max-w-[85%] rounded-2xl px-3 py-1.5 text-[13px] leading-snug', t.role === 'assistant' ? 'rounded-tl-sm bg-fg text-bg' : 'rounded-tr-sm bg-surface-2 ring-1 ring-border')}>{t.text}</div>
            </div>
          )) : <p className="text-center text-xs text-muted">{call.status === 'In Progress' ? 'Listening…' : 'Waiting for the call to be answered…'}</p>}
        </div>
      </div>
      <button type="button" onClick={onOpen} className="border-t border-border py-2.5 text-xs font-bold text-muted transition hover:bg-surface-2 hover:text-fg">Open full transcript</button>
    </Card>
  )
}

export default function Calls() {
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [view, setView] = useState<'all' | 'active' | 'queue'>(params.get('status') === 'active' ? 'active' : params.get('view') === 'queue' ? 'queue' : 'all')
  const [status, setStatus] = useState('')
  const [direction, setDirection] = useState('')
  const [search, setSearch] = useState('')
  const q = useDebounced(search)
  const [page, setPage] = useState(1)
  const [callId, setCallId] = useState<number | null>(null)

  useEffect(() => { setPage(1); setParams(view === 'active' ? { status: 'active' } : view === 'queue' ? { view: 'queue' } : {}, { replace: true }) }, [view, status, direction, q, setParams])

  const { data, isLoading } = useQuery({
    queryKey: ['calls', view, status, direction, q, page],
    queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { status: view === 'active' ? 'active' : status, direction, search: q, page, page_size: 25 } }),
    placeholderData: keepPreviousData,
    refetchInterval: view === 'active' ? 2000 : 4000,
    enabled: view !== 'queue',
  })
  const stats = useQuery({ queryKey: ['calls', 'stats', 7], queryFn: () => api<CallStats>(`${base}/calls/stats`, { params: { days: 7 } }), refetchInterval: 5000 })
  const s = stats.data
  const week = s?.series.reduce((a, d) => ({ total: a.total + d.total, connected: a.connected + d.connected }), { total: 0, connected: 0 })
  const rate = s?.today.total ? Math.round((100 * s.today.connected) / s.today.total) : null
  const filtered = !!(status || direction || search)

  return (
    <>
      <PageHeader
        eyebrow={<><PhoneCall className="size-3.5" />{agent?.name} · Conversations</>}
        title="Calls"
        description="Every conversation this agent had, with transcript, AI summary, temperature and outcome."
        actions={<Tabs value={view} onChange={setView} items={[
          { value: 'all', label: 'All calls' },
          { value: 'queue', label: <span className="flex items-center gap-1.5"><ListOrdered className="size-3.5" />Queue</span> },
          { value: 'active', label: <span className="flex items-center gap-1.5"><Radio className="size-3.5" />Live{!!s?.active && <span className="rounded-full bg-success px-1.5 text-[11px] text-white">{s.active}</span>}</span> },
        ]} />}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Today" value={s?.today.total ?? '—'} icon={<PhoneCall />} tone="neutral" sub={s?.active ? `${s.active} live now` : 'No live calls'} />
          <StatTile label="Connected today" value={s?.today.connected ?? '—'} icon={<PhoneIncoming />} tone="success" sub={rate !== null ? `${rate}% answer rate` : 'Nothing dialled yet'} />
          <StatTile label="Talk time today" value={s ? formatDuration(s.today.talk_seconds) : '—'} icon={<Timer />} tone="neutral" sub={week ? `${week.total} calls · ${week.connected} connected this week` : ' '} />
          <StatTile label="AI response time" value={s?.avg_latency_ms ? `${(s.avg_latency_ms / 1000).toFixed(1)}s` : '—'} icon={<Gauge />} tone="neutral" sub="Average over the last 7 days" />
        </div>
      </PageHeader>

      {view === 'queue' ? <CallQueue /> : view === 'active' ? (
        isLoading ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-80" />)}</div>
          : data?.items.length ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{data.items.map((c) => <LiveCallCard key={c.id} call={c} onOpen={() => setCallId(c.id)} />)}</div>
          ) : (
            <Card><EmptyState icon={<Radio />} title="No live calls right now" description="Calls show up here the second they start, with a real-time transcript." /></Card>
          )
      ) : (
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
            <div className="relative min-w-56 flex-1 sm:max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search lead name or number…" className="pl-9" />
            </div>
            <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-auto"><option value="">All results</option>{CALL_STATUSES.filter((x) => x !== 'Pending').map((x) => <option key={x}>{x}</option>)}</Select>
            <Select value={direction} onChange={(e) => setDirection(e.target.value)} className="w-auto"><option value="">All directions</option><option value="outbound">Outbound</option><option value="inbound">Inbound</option></Select>
            {filtered && <Button variant="ghost" size="sm" onClick={() => { setStatus(''); setDirection(''); setSearch('') }}><X />Clear</Button>}
            <span className="ml-auto text-xs font-semibold text-muted">{data ? `${data.total} call${data.total === 1 ? '' : 's'}` : ''}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="border-b border-border bg-surface-2/50">
                <tr>{['Lead', 'Result', 'AI summary', 'Temp.', 'Length', 'Trigger', 'When'].map((h) => <th key={h} className="px-4 py-3 text-left text-[11px] font-bold tracking-wider whitespace-nowrap text-muted uppercase">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-border">
                {isLoading ? Array.from({ length: 6 }, (_, i) => <tr key={i}><td colSpan={7} className="px-4 py-3"><Skeleton className="h-8" /></td></tr>)
                  : data?.items.map((c) => (
                    <tr key={c.id} onClick={() => setCallId(c.id)} className="cursor-pointer transition hover:bg-surface-2/70">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <span className={cn('grid size-9 place-items-center rounded-full', c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0) ? 'bg-fg text-bg' : 'bg-surface-2 text-muted ring-1 ring-border')}>
                            {c.direction === 'inbound' ? <PhoneIncoming className="size-4" /> : <PhoneOutgoing className="size-4" />}
                          </span>
                          <div className="min-w-0">
                            {c.lead_id
                              ? <button type="button" onClick={(e) => { e.stopPropagation(); navigate(path(`/leads/${c.lead_id}`)) }} className="font-bold hover:underline">{c.lead_name ?? 'Unknown'}</button>
                              : <div className="font-bold">{c.lead_name ?? 'Unknown caller'}</div>}
                            <div className="font-mono text-xs whitespace-nowrap text-muted">{c.direction === 'inbound' ? c.from_number : c.to_number}</div>
                            <div className="text-[11px] whitespace-nowrap text-muted">{callHandledBy(c, agent?.name)}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4"><CallStatusBadge status={c.status} /></td>
                      <td className="max-w-md px-4 py-3">
                        <div className="line-clamp-2 text-[13px] text-fg-2">{c.summary ?? (c.error ? <span className="text-danger">{c.error}</span> : c.outcome ? titleCase(c.outcome) : <span className="text-muted">{c.turns ? `${c.turns} turns, no summary` : 'No conversation'}</span>)}</div>
                        <div className="mt-1 flex items-center gap-3">{c.outcome && <span className="text-[11px] font-bold text-muted uppercase">{titleCase(c.outcome)}</span>}<SentimentDot value={c.sentiment} /></div>
                      </td>
                      <td className="px-4"><QualificationBadge value={c.qualification} /></td>
                      <td className="px-4 whitespace-nowrap text-muted tabular-nums"><span className="flex items-center gap-1.5"><Clock className="size-3.5" />{formatDuration(c.duration)}</span></td>
                      <td className="px-4 text-[13px] whitespace-nowrap text-muted"><span className="flex items-center gap-1.5">{c.trigger === 'manual' ? null : <Bot className="size-3.5" />}{titleCase(c.trigger)}</span></td>
                      <td className="px-4 text-[13px] whitespace-nowrap text-muted">{formatDate(c.created_at)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {!isLoading && !data?.items.length && (
              <EmptyState icon={<PhoneCall />} title="No calls found" description={filtered ? 'Try clearing the filters.' : 'Place a call from the Leads page or switch on auto-dial.'} />
            )}
          </div>
          {data && data.total > 0 && <Pagination page={page} pageSize={25} total={data.total} onPage={setPage} />}
        </Card>
      )}

      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); navigate(path(`/leads/${id}`)) }} />
    </>
  )
}
