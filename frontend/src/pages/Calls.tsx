import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Clock, Gauge, PhoneCall, PhoneIncoming, PhoneOutgoing, Radio, Search, Timer, X, ListOrdered } from 'lucide-react'
import { useEffect, useState, type CSSProperties } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import CallSheet from '@/components/CallSheet'
import { CallStatusBadge, QualificationBadge, SentimentDot } from '@/components/status'
import CallQueue from '@/components/CallQueue'
import { Button, Card, EmptyState, Input, PageHeader, Pagination, Select, Skeleton, StatTile, Tabs, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { Orb3D, VoiceOrb, Waveform } from '@/components/VoiceViz'
import { Stagger } from '@/lib/motion'
import { useAgent } from '@/lib/agent'
import { useDebounced } from '@/lib/useDebounced'
import type { Call, CallStats, Page } from '@/lib/types'
import { callHandledBy, callParty, CALL_STATUSES, cn, formatDate, formatDuration, timeAgo, titleCase } from '@/lib/utils'

function LiveCallCard({ call, onOpen, index, className, style }: { call: Call; onOpen: () => void; index: number; className?: string; style?: CSSProperties }) {
  const { base } = useAgent()
  // Limit heavy transcript polling to the first 6 cards to prevent network saturation and UI lag with 50-100 concurrent calls.
  const poll = index < 6 && call.status === 'In Progress'
  const detail = useQuery({ queryKey: ['call', call.id], queryFn: () => api<Call>(`${base}/calls/${call.id}`), refetchInterval: poll ? 2500 : false, enabled: poll })
  const turns = detail.data?.transcript ?? call.transcript ?? []
  return (
    <Card className={cn('beam beam-on beam-live is-live-card flex flex-col overflow-hidden', className)} style={style}>
      <div className="flex items-center gap-3 border-b border-border p-4">
        <VoiceOrb state={call.status === 'In Progress' ? 'live' : 'listening'} size={40} />
        <div className="min-w-0 flex-1 leading-tight">
          <div className="truncate font-bold">{callParty(call)}</div>
          <div className="truncate text-xs text-muted">{call.direction === 'inbound' ? 'Inbound' : 'Outbound'} · {(call.direction === 'inbound' ? call.from_number : call.to_number) ?? 'Unknown number'} · {timeAgo(call.created_at)}</div>
        </div>
        <span className="shrink-0"><CallStatusBadge status={call.status} /></span>
      </div>
      <div className="flex h-56 flex-col-reverse overflow-y-auto p-4">
        <div className="space-y-2">
          {turns.length ? turns.slice(-8).map((t, i) => (
            <div key={turns.length > 8 ? turns.length - 8 + i : i} className={cn('reveal reveal-in flex', t.role === 'customer' ? 'reveal-right justify-end' : 'reveal-left')}>
              <div className={cn('max-w-[85%] break-words rounded-2xl px-3 py-1.5 text-[13px] leading-snug', t.role === 'assistant' ? 'rounded-tl-sm bg-fg text-bg' : 'rounded-tr-sm bg-surface-2 ring-1 ring-border')}>{t.text}</div>
            </div>
          )) : <p className="flex flex-col items-center gap-2 text-center text-xs text-muted"><Waveform bars={7} className="h-5 text-success" />{call.status === 'In Progress' ? (index < 6 ? 'Listening…' : 'Live on the line') : 'Waiting for the call to be answered…'}</p>}
        </div>
      </div>
      {turns.length > 0 && call.status === 'In Progress' && (
        <div className="flex items-center gap-2 border-t border-border px-4 py-2 text-[11px] font-semibold text-success">
          <Waveform bars={9} className="h-3.5" />On the line
        </div>
      )}
      <button type="button" onClick={onOpen} className="min-h-10 border-t border-border py-2.5 text-xs font-bold text-muted transition hover:bg-surface-2 hover:text-fg sm:min-h-0">Open full transcript</button>
    </Card>
  )
}

export default function Calls() {
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [params, setParams] = useSearchParams()
  type View = 'all' | 'active' | 'queue'
  const viewFromParams: View = params.get('status') === 'active' ? 'active' : params.get('view') === 'queue' ? 'queue' : 'all'
  const [view, setView] = useState<View>(viewFromParams)
  const [status, setStatus] = useState('')
  const [direction, setDirection] = useState('')
  const [search, setSearch] = useState('')
  const q = useDebounced(search)
  const [page, setPage] = useState(1)
  const [callId, setCallId] = useState<number | null>(null)

  // URL -> state: sidebar / dashboard links push ?status=active or ?view=queue while this page is already mounted.
  useEffect(() => { setView(viewFromParams); setPage(1) }, [viewFromParams])
  // State -> URL: only when the tab actually differs from what the URL says, so we never wipe a param we just received.
  useEffect(() => {
    if (view !== viewFromParams) setParams(view === 'active' ? { status: 'active' } : view === 'queue' ? { view: 'queue' } : {}, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])
  // Reset the page inside the setters (not an effect) so a filter change never fires a throwaway request with the stale page first.
  const changeView = (v: View) => { setView(v); setPage(1) }
  const changeStatus = (v: string) => { setStatus(v); setPage(1) }
  const changeDirection = (v: string) => { setDirection(v); setPage(1) }
  const changeSearch = (v: string) => { setSearch(v); setPage(1) }
  const clearFilters = () => { setStatus(''); setDirection(''); setSearch(''); setPage(1) }

  // The status/direction pickers only exist on the All-calls tab, so the Live view must never inherit them (an 'Inbound' pick would silently hide outbound live calls).
  const listDirection = view === 'active' ? '' : direction
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['calls', view, status, listDirection, q, page],
    queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { status: view === 'active' ? 'active' : status, direction: listDirection, search: q, page, page_size: 25 } }),
    // Keep the previous page while filters/pagination change, but never show 'All calls' rows as live cards (or vice versa).
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === view ? prev : undefined),
    refetchInterval: view === 'active' ? 2000 : 4000,
    enabled: view !== 'queue',
  })
  const stats = useQuery({ queryKey: ['calls', 'stats', 7], queryFn: () => api<CallStats>(`${base}/calls/stats`, { params: { days: 7 } }), refetchInterval: 5000 })
  const s = stats.data
  const statsDown = stats.isError && !s
  const week = s?.series?.reduce((a, d) => ({ total: a.total + d.total, connected: a.connected + d.connected }), { total: 0, connected: 0 })
  const rate = s?.today?.total ? Math.round((100 * s.today.connected) / s.today.total) : null
  const filtered = !!(status || direction || search)
  const items = data?.items ?? []
  const clearHistory = useMutation({
    mutationFn: () => api(`${base}/calls`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Call history deleted'); setPage(1); setCallId(null); qc.invalidateQueries({ queryKey: ['calls'] }); qc.invalidateQueries({ queryKey: ['leads'] }) },
    onError: (e) => toast.error(e instanceof Error ? e.message : 'Could not delete call history'),
  })
  const onClearHistory = async () => {
    if (await confirm({ title: 'Delete all call history?', description: `Every call, transcript and recording link for ${agent?.name ?? 'this agent'} is removed permanently. Leads stay.`, confirmLabel: 'Delete history', danger: true })) clearHistory.mutate()
  }
  const errorText = error instanceof Error ? error.message : 'Something went wrong while loading calls.'

  return (
    <>
      <PageHeader
        eyebrow={<><PhoneCall className="size-3.5" />{agent?.name} · Conversations</>}
        title="Calls"
        description="Every conversation this agent had, with transcript, AI summary, temperature and outcome."
        actions={<Tabs value={view} onChange={changeView} items={[
          { value: 'all', label: 'All calls' },
          { value: 'queue', label: <span className="flex items-center gap-1.5"><ListOrdered className="size-3.5" />Queue</span> },
          { value: 'active', label: <span className="flex items-center gap-1.5"><Radio className="size-3.5" />Live{!!s?.active && <span className="rounded-full bg-success px-1.5 text-[11px] text-white">{s.active}</span>}</span> },
        ]} />}>
        {statsDown && (
          <p className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">Call stats could not be loaded.<Button variant="ghost" size="sm" onClick={() => stats.refetch()}>Retry</Button></p>
        )}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Today" value={s?.today?.total ?? '—'} icon={<PhoneCall />} tone="neutral" sub={statsDown ? 'Stats unavailable' : s?.active ? `${s.active} live now` : 'No live calls'} />
          <StatTile label="Connected today" value={s?.today?.connected ?? '—'} icon={<PhoneIncoming />} tone="success" sub={statsDown ? 'Stats unavailable' : rate !== null ? `${rate}% answer rate` : 'Nothing dialled yet'} />
          <StatTile label="Talk time today" value={s?.today ? formatDuration(s.today.talk_seconds) : '—'} icon={<Timer />} tone="neutral" sub={statsDown ? 'Stats unavailable' : week ? `${week.total} calls · ${week.connected} connected this week` : ' '} />
          <StatTile label="AI response time" value={s?.avg_latency_ms ? `${(s.avg_latency_ms / 1000).toFixed(1)}s` : '—'} icon={<Gauge />} tone="neutral" sub={statsDown ? 'Stats unavailable' : 'Average over the last 7 days'} />
        </div>
      </PageHeader>

      {view === 'queue' ? <CallQueue /> : view === 'active' ? (
        <div className="space-y-4">
          <div className="relative min-w-56 max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <Input value={search} onChange={(e) => changeSearch(e.target.value)} placeholder="Search live calls…" className="pl-9 bg-surface" />
          </div>
          {isLoading ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-80" />)}</div>
            : isError ? (
            <Card><EmptyState icon={<PhoneCall />} title="Could not load live calls" description={errorText} action={<Button onClick={() => refetch()}>Retry</Button>} /></Card>
          ) : items.length ? (
            <Stagger className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{items.map((c, i) => <LiveCallCard key={c.id} call={c} index={i} onOpen={() => setCallId(c.id)} />)}</Stagger>
          ) : search ? (
            <Card><EmptyState icon={<Search />} title="No live calls match your search" description={s?.active ? `${s.active} live call${s.active === 1 ? '' : 's'} hidden by the search.` : 'Clear the search to watch for new calls.'} action={<Button size="sm" onClick={() => changeSearch('')}><X />Clear search</Button>} /></Card>
          ) : (
            <Card className="scan-line overflow-hidden">
              <div className="flex flex-col items-center px-6 py-10 text-center">
                <Orb3D state="listening" size={170} />
                <p className="mt-2 font-semibold text-fg">No live calls right now</p>
                <p className="mt-1 max-w-sm text-sm text-muted">Calls show up here the second they start, with a real-time transcript.</p>
                <span className="mt-4 inline-flex items-center gap-2 rounded-full bg-surface-2 px-3 py-1 text-xs font-semibold text-muted ring-1 ring-border">
                  <span className="size-1.5 animate-pulse-dot rounded-full bg-success" />Listening for calls
                </span>
              </div>
            </Card>
          )}
        </div>
      ) : (
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
            <div className="relative min-w-56 flex-1 sm:max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
              <Input value={search} onChange={(e) => changeSearch(e.target.value)} placeholder="Search lead name or number…" className="pl-9" />
            </div>
            <Select value={status} onChange={(e) => changeStatus(e.target.value)} className="w-auto"><option value="">All results</option>{CALL_STATUSES.filter((x) => x !== 'Pending').map((x) => <option key={x}>{x}</option>)}</Select>
            <Select value={direction} onChange={(e) => changeDirection(e.target.value)} className="w-auto"><option value="">All directions</option><option value="outbound">Outbound</option><option value="inbound">Inbound</option></Select>
            {filtered && <Button variant="ghost" size="sm" onClick={clearFilters}><X />Clear</Button>}
            <span className="ml-auto text-xs font-semibold text-muted">{data ? `${data.total} call${data.total === 1 ? '' : 's'}` : ''}</span>
          </div>
          {isError ? (
            <EmptyState icon={<PhoneCall />} title="Could not load calls" description={errorText} action={<Button onClick={() => refetch()}>Retry</Button>} />
          ) : (
            <>
              {/* Card list below md: the seven-column table needs ~980px and would only be a sideways scroll on a phone. */}
              <ul className="divide-y divide-border md:hidden">
                {isLoading ? Array.from({ length: 4 }, (_, i) => <li key={i} className="p-4"><Skeleton className="h-16" /></li>)
                  : items.map((c) => (
                    <li key={c.id}>
                      <button type="button" onClick={() => setCallId(c.id)} className="flex w-full min-w-0 items-start gap-3 p-4 text-left transition hover:bg-surface-2/70">
                        <span className={cn('grid size-9 shrink-0 place-items-center rounded-full', c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0) ? 'bg-fg text-bg' : 'bg-surface-2 text-muted ring-1 ring-border')}>
                          {c.direction === 'inbound' ? <PhoneIncoming className="size-4" /> : <PhoneOutgoing className="size-4" />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-start justify-between gap-2">
                            <span className="min-w-0">
                              <span className="block truncate font-bold">{c.lead_name ?? (c.lead_id ? 'Unknown' : 'Unknown caller')}</span>
                              <span className="block truncate font-mono text-xs text-muted">{(c.direction === 'inbound' ? c.from_number : c.to_number) ?? '—'}</span>
                            </span>
                            <span className="shrink-0"><CallStatusBadge status={c.status} /></span>
                          </span>
                          <span className="mt-1.5 line-clamp-2 block text-[13px] break-words text-fg-2">{c.summary ?? (c.error ? <span className="text-danger">{c.error}</span> : c.outcome ? titleCase(c.outcome) : <span className="text-muted">{c.turns ? `${c.turns} turns, no summary` : 'No conversation'}</span>)}</span>
                          <span className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
                            <QualificationBadge value={c.qualification} />
                            <SentimentDot value={c.sentiment} />
                            <span className="flex items-center gap-1 tabular-nums"><Clock className="size-3" />{formatDuration(c.duration)}</span>
                            <span className="flex items-center gap-1">{c.trigger !== 'manual' && <Bot className="size-3" />}{titleCase(c.trigger)}</span>
                            <span>{formatDate(c.created_at)}</span>
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
              </ul>
              <div className="hidden overflow-x-auto md:block">
                <table className="rows-in w-full min-w-[980px] text-sm">
                  <thead className="border-b border-border bg-surface-2/50">
                    <tr>{['Lead', 'Result', 'AI summary', 'Temp.', 'Length', 'Trigger', 'When'].map((h) => <th key={h} className="px-4 py-3 text-left text-[11px] font-bold tracking-wider whitespace-nowrap text-muted uppercase">{h}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {isLoading ? Array.from({ length: 6 }, (_, i) => <tr key={i}><td colSpan={7} className="px-4 py-3"><Skeleton className="h-8" /></td></tr>)
                      : items.map((c) => (
                        <tr key={c.id} role="button" aria-label={`Open call with ${c.lead_name ?? (c.direction === 'inbound' ? c.from_number : c.to_number) ?? 'unknown caller'}`} onClick={() => setCallId(c.id)} tabIndex={0} onKeyDown={(e) => { if (e.target !== e.currentTarget) return; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCallId(c.id) } }} className="cursor-pointer transition hover:bg-surface-2/70 focus-visible:bg-surface-2/70 focus-visible:outline-none">
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-3">
                              <span className={cn('grid size-9 shrink-0 place-items-center rounded-full', c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0) ? 'bg-fg text-bg' : 'bg-surface-2 text-muted ring-1 ring-border')}>
                                {c.direction === 'inbound' ? <PhoneIncoming className="size-4" /> : <PhoneOutgoing className="size-4" />}
                              </span>
                              <div className="min-w-0">
                                {c.lead_id
                                  ? <button type="button" onClick={(e) => { e.stopPropagation(); navigate(path(`/leads/${c.lead_id}`)) }} className="block max-w-56 truncate font-bold hover:underline">{c.lead_name ?? 'Unknown'}</button>
                                  : <div className="max-w-56 truncate font-bold">{c.lead_name ?? 'Unknown caller'}</div>}
                                <div className="font-mono text-xs whitespace-nowrap text-muted">{(c.direction === 'inbound' ? c.from_number : c.to_number) ?? '—'}</div>
                                <div className="text-[11px] whitespace-nowrap text-muted">{callHandledBy(c, agent?.name)}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-4"><CallStatusBadge status={c.status} /></td>
                          <td className="max-w-md px-4 py-3">
                            <div className="line-clamp-2 text-[13px] break-words text-fg-2">{c.summary ?? (c.error ? <span className="text-danger">{c.error}</span> : c.outcome ? titleCase(c.outcome) : <span className="text-muted">{c.turns ? `${c.turns} turns, no summary` : 'No conversation'}</span>)}</div>
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
              </div>
              {!isLoading && !items.length && (
                <EmptyState icon={<PhoneCall />} title="No calls found" description={filtered ? 'Try clearing the filters.' : 'Place a call from the Leads page or switch on auto-dial.'} action={filtered ? <Button size="sm" onClick={clearFilters}>Clear filters</Button> : undefined} />
              )}
            </>
          )}
          {!isError && data && data.total > 0 && <Pagination page={page} pageSize={25} total={data.total} onPage={setPage} />}
          {!isError && (data?.total ?? 0) > 0 && (
            <div className="flex flex-wrap items-center justify-end border-t border-border px-4 py-3">
              <Button variant="ghost" size="sm" className="text-danger hover:bg-danger/10 hover:text-danger" loading={clearHistory.isPending} onClick={onClearHistory}>
                Delete call history
              </Button>
            </div>
          )}
        </Card>
      )}

      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); navigate(path(`/leads/${id}`)) }} />
    </>
  )
}
