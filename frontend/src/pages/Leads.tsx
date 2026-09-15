import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowDown, ArrowUp, ArrowUpRight, Ban, CalendarCheck, Clock, Download, Flame, Globe, ListPlus, PhoneCall, PhoneOff, Plus,
  Search, Sparkles, Trash2, Upload, Users, X,
} from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { LeadFormSheet, useStartCall } from '@/components/LeadSheets'
import { CallStatusBadge, LeadStatusBadge, QualificationBadge } from '@/components/status'
import { Avatar, Button, Card, EmptyState, Input, PageHeader, Pagination, Select, Skeleton, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { Lead, LeadStats, Page } from '@/lib/types'
import { CALL_STATUSES, cn, formatDate, LANGUAGES, QUALIFICATIONS, timeAgo } from '@/lib/utils'

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

const STAGES = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won', 'Not Interested']

const VIEWS = [
  { id: '', label: 'All', icon: Users },
  { id: 'hot_uncalled', label: 'Hot, not called', icon: Flame },
  { id: 'never_called', label: 'Never called', icon: Sparkles },
  { id: 'website', label: 'Website leads', icon: Globe },
  { id: 'callbacks', label: 'Callbacks', icon: Clock },
  { id: 'meetings', label: 'Meetings', icon: CalendarCheck },
  { id: 'attention', label: 'Needs attention', icon: AlertTriangle },
  { id: 'dnc', label: 'Do not call', icon: PhoneOff },
] as const
const JOURNEY = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won']

/** 0-100: temperature, pipeline stage and recency (same idea as the lead page score). */
function score(l: Lead) {
  if (l.do_not_call) return 0
  const temp = { Hot: 45, Warm: 28, Cold: 8 }[l.qualification ?? ''] ?? 12
  const stage = Math.max(0, JOURNEY.indexOf(l.status)) * 7
  const recent = l.last_contacted_at && Date.now() - Date.parse(l.last_contacted_at) < 7 * 86_400_000 ? 12 : 0
  return Math.min(100, temp + stage + recent)
}

function NextStep({ l }: { l: Lead }) {
  if (l.do_not_call) return <span className="text-muted">—</span>
  if (l.phone_valid === false) return <span className="inline-flex items-center gap-1 text-danger"><AlertTriangle className="size-3.5" />Fix number</span>
  if (l.callback_at) return <span className="inline-flex items-center gap-1 text-fg"><Clock className="size-3.5" />Callback {l.callback_at.slice(5)}</span>
  if (l.meeting_at) return <span className="inline-flex items-center gap-1 text-success"><CalendarCheck className="size-3.5" />Meeting {l.meeting_at.slice(5)}</span>
  if (l.retry_count >= 3) return <span className="text-warning">Unreachable ×{l.retry_count}</span>
  if (!l.last_contacted_at) return <span className="text-fg-2">First call</span>
  if (['Interested', 'Follow Up'].includes(l.status)) return <span className="text-fg-2">Follow up</span>
  return <span className="text-muted">—</span>
}

export default function Leads() {
  const { agent, base, path } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState({ status: '', call_status: '', qualification: params.get('qualification') ?? '', view: '' })
  const [sort, setSort] = useState({ key: 'id', order: 'desc' as 'asc' | 'desc' })
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [editing, setEditing] = useState<Lead | null>(null)
  const [adding, setAdding] = useState(params.get('new') === '1')
  const q = useDebounced(search)

  useEffect(() => { setPage(1) }, [q, filters])
  useEffect(() => {
    const open = params.get('open')
    if (open) navigate(path(`/leads/${open}`), { replace: true })
  }, [params, navigate, path])

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['leads', q, filters, sort, page],
    queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { search: q, ...filters, sort: sort.key, order: sort.order, page, page_size: 25 } }),
    placeholderData: keepPreviousData,
    refetchInterval: 8000,
  })
  const stats = useQuery({ queryKey: ['leads', 'stats'], queryFn: () => api<LeadStats>(`${base}/leads/stats`), refetchInterval: 15000 })
  const views = useQuery({ queryKey: ['leads', 'views'], queryFn: () => api<Record<string, number>>(`${base}/leads/views`), refetchInterval: 15000 })
  const bulkUpdate = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<{ updated: number }>(`${base}/leads/bulk/update`, { method: 'POST', json: { ids: [...selected], ...body } }),
    onSuccess: (r) => { toast.success(`Updated ${r.updated} lead(s)`); setSelected(new Set()); qc.invalidateQueries({ queryKey: ['leads'] }) },
    onError: (e) => toast.error('Update failed', { description: e.message }),
  })
  const startCall = useStartCall()

  const bulk = useMutation({
    mutationFn: ({ action, ids }: { action: 'delete' | 'queue' | 'call'; ids: number[] }) =>
      api<{ deleted?: number; queued?: number; placed?: unknown[]; errors?: { error: string }[] }>(`${base}/leads/bulk/${action}`, { method: 'POST', json: { ids } }),
    onSuccess: (res, { action }) => {
      if (action === 'delete') toast.success(`Deleted ${res.deleted} lead(s)`)
      if (action === 'queue') toast.success(`Queued ${res.queued} lead(s)`, { description: 'Auto-dial will call them within calling hours.' })
      if (action === 'call') {
        toast.success(`Placed ${res.placed?.length ?? 0} call(s)`)
        if (res.errors?.length) toast.warning(`${res.errors.length} not placed`, { description: res.errors[0]!.error })
      }
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['calls'] })
    },
    onError: (e) => toast.error(e.message),
  })

  const s = stats.data
  const items = data?.items ?? []
  const allSelected = items.length > 0 && items.every((l) => selected.has(l.id))
  const toggle = (id: number) => setSelected((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const sortBy = (key: string) => setSort((prev) => ({ key, order: prev.key === key && prev.order === 'desc' ? 'asc' : 'desc' }))
  const activeFilters = Object.values(filters).filter(Boolean).length
  const clearFilters = () => { setFilters({ status: '', call_status: '', qualification: '', view: '' }); setParams({}, { replace: true }) }

  const Th = ({ k, children, className }: { k?: string; children: ReactNode; className?: string }) => (
    <th className={cn('px-3 py-3 text-left text-[11px] font-bold tracking-wider whitespace-nowrap text-muted uppercase', className)}>
      {k ? (
        <button onClick={() => sortBy(k)} className="inline-flex items-center gap-1 uppercase hover:text-fg">
          {children}{sort.key === k && (sort.order === 'desc' ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
        </button>
      ) : children}
    </th>
  )

  const summary: [typeof Users, string, number | undefined, () => void, boolean][] = [
    [Users, 'All leads', s?.total, clearFilters, activeFilters === 0],
    [Clock, 'Waiting to call', s?.pending, () => setFilters({ status: 'New', call_status: '', qualification: '', view: '' }), filters.status === 'New'],
    [Flame, 'Hot', s?.by_qualification.Hot ?? 0, () => setFilters({ status: '', call_status: '', qualification: 'Hot', view: '' }), filters.qualification === 'Hot'],
    [CalendarCheck, 'Meetings', s?.meetings, () => setFilters({ status: 'Meeting Booked', call_status: '', qualification: '', view: '' }), filters.status === 'Meeting Booked'],
  ]

  return (
    <>
      <PageHeader
        eyebrow={<><Users className="size-3.5" />{agent?.name} · CRM</>}
        title="Leads"
        description="Everyone this agent calls, with the AI's qualification and the result of every conversation. Leads here are never shared with other agents."
        actions={<>
          <a href={`${base}/leads/export`}><Button><Download />Export CSV</Button></a>
          <Link to={path('/import')}><Button><Upload />Import</Button></Link>
          <Button variant="primary" onClick={() => setAdding(true)}><Plus />Add lead</Button>
        </>}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {summary.map(([Icon, label, value, onClick, active]) => (
            <button key={label} type="button" onClick={onClick}
              className={cn('flex items-center gap-3 rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:border-border-strong',
                active ? 'border-fg ring-1 ring-fg' : 'border-border')}>
              <span className={cn('grid size-10 place-items-center rounded-xl', active ? 'bg-fg text-bg' : 'bg-surface-2 text-fg-2')}><Icon className="size-4.5" /></span>
              <span>
                <span className="block text-2xl leading-none font-extrabold tabular-nums">{value ?? '—'}</span>
                <span className="mt-1 block text-xs font-semibold text-muted">{label}</span>
              </span>
            </button>
          ))}
        </div>
      </PageHeader>

      <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1">
        {VIEWS.map(({ id, label, icon: Icon }) => {
          const active = filters.view === id
          const count = id ? views.data?.[id] : s?.total
          if (id && !count && !active && ['attention', 'dnc', 'callbacks', 'website'].includes(id)) return null
          return (
            <button key={id || 'all-views'} type="button" onClick={() => setFilters({ ...filters, view: id })}
              className={cn('flex shrink-0 items-center gap-1.5 rounded-xl border px-3 py-1.5 text-[13px] font-semibold transition',
                active ? 'border-brand bg-brand text-brand-fg' : 'border-border bg-surface text-fg-2 hover:border-border-strong',
                id === 'attention' && !active && 'border-warning/40 text-warning')}>
              <Icon className="size-3.5" />{label}<span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', active ? 'bg-white/20' : 'bg-surface-2 text-muted')}>{count ?? 0}</span>
            </button>
          )
        })}
      </div>

      <div className="mb-3 flex gap-1.5 overflow-x-auto pb-1">
        {['', ...STAGES].map((st) => {
          const count = st ? s?.by_status[st] ?? 0 : s?.total
          const active = filters.status === st
          return (
            <button key={st || 'all'} type="button" onClick={() => setFilters({ ...filters, status: st })}
              className={cn('flex shrink-0 items-center gap-2 rounded-full border px-3.5 py-1.5 text-[13px] font-semibold transition',
                active ? 'border-fg bg-fg text-bg' : 'border-border bg-surface text-fg-2 hover:border-border-strong')}>
              {st || 'All stages'}<span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', active ? 'bg-bg/20' : 'bg-surface-2 text-muted')}>{count ?? 0}</span>
            </button>
          )
        })}
      </div>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="relative min-w-56 flex-1 sm:max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, company, phone, city, tag…" className="pl-9" aria-label="Search leads" />
          </div>
          <Select value={filters.call_status} onChange={(e) => setFilters({ ...filters, call_status: e.target.value })} className="w-auto" aria-label="Last call">
            <option value="">Any call result</option>{CALL_STATUSES.map((x) => <option key={x}>{x}</option>)}
          </Select>
          <Select value={filters.qualification} onChange={(e) => setFilters({ ...filters, qualification: e.target.value })} className="w-auto" aria-label="Temperature">
            <option value="">Any temperature</option>{QUALIFICATIONS.map((x) => <option key={x}>{x}</option>)}
          </Select>
          <Select value={`${sort.key}:${sort.order}`} onChange={(e) => { const [key, order] = e.target.value.split(':'); setSort({ key: key!, order: order as 'asc' | 'desc' }) }} className="w-auto" aria-label="Sort">
            <option value="id:desc">Newest first</option><option value="last_contacted_at:desc">Recently contacted</option>
            <option value="name:asc">Name A–Z</option><option value="meeting_at:desc">Meeting date</option>
          </Select>
          {activeFilters > 0 && <Button variant="ghost" size="sm" onClick={clearFilters}><X />Clear filters</Button>}
          <span className="ml-auto text-xs font-semibold text-muted">{isFetching && !isLoading ? 'Updating…' : data ? `${data.total} result${data.total === 1 ? '' : 's'}` : ''}</span>
        </div>

        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-fg px-4 py-2.5 text-sm text-bg">
            <span className="font-bold">{selected.size} selected</span>
            <div className="flex-1" />
            <Button size="sm" loading={bulk.isPending && bulk.variables?.action === 'call'} onClick={async () => {
              if (await confirm({ title: `Call ${selected.size} lead(s) now?`, description: 'Calls start immediately (up to your concurrent call limit), even outside calling hours.', confirmLabel: 'Start calls' }))
                bulk.mutate({ action: 'call', ids: [...selected] })
            }}><PhoneCall />Call now</Button>
            <Button size="sm" onClick={() => bulk.mutate({ action: 'queue', ids: [...selected] })}><ListPlus />Queue for auto-dial</Button>
            <select aria-label="Set stage" value="" onChange={(e) => e.target.value && bulkUpdate.mutate({ status: e.target.value })}
              className="h-8 rounded-lg border border-bg/30 bg-bg/10 px-2 text-[13px] font-semibold text-bg">
              <option value="" className="text-fg">Set stage…</option>{STAGES.map((x) => <option key={x} value={x} className="text-fg">{x}</option>)}
            </select>
            <select aria-label="Set temperature" value="" onChange={(e) => e.target.value && bulkUpdate.mutate({ qualification: e.target.value })}
              className="h-8 rounded-lg border border-bg/30 bg-bg/10 px-2 text-[13px] font-semibold text-bg">
              <option value="" className="text-fg">Temperature…</option>{QUALIFICATIONS.map((x) => <option key={x} value={x} className="text-fg">{x}</option>)}
            </select>
            <Button size="sm" onClick={async () => {
              if (await confirm({ title: `Mark ${selected.size} lead(s) Do Not Call?`, description: 'They are excluded from manual and automated calls.', confirmLabel: 'Mark Do Not Call', danger: true }))
                bulkUpdate.mutate({ do_not_call: true })
            }}><PhoneOff />Do not call</Button>
            <Button size="sm" variant="danger" onClick={async () => {
              if (await confirm({ title: `Delete ${selected.size} lead(s)?`, description: 'This cannot be undone.', confirmLabel: 'Delete', danger: true }))
                bulk.mutate({ action: 'delete', ids: [...selected] })
            }}><Trash2 />Delete</Button>
            <button type="button" className="px-2 text-sm font-semibold opacity-70 hover:opacity-100" onClick={() => setSelected(new Set())}>Cancel</button>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] text-sm">
            <thead className="border-b border-border bg-surface-2/50">
              <tr>
                <th className="w-11 px-4"><input type="checkbox" aria-label="Select all" checked={allSelected}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((l) => l.id)))} className="size-4 accent-[var(--fg)]" /></th>
                <Th k="name">Lead</Th><Th>Phone</Th><Th k="status">Stage</Th><Th>Score</Th><Th>Last call</Th><Th k="qualification">Temp.</Th>
                <Th>Next step</Th><Th k="last_contacted_at">Last contact</Th><Th className="text-right">&nbsp;</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading ? Array.from({ length: 8 }, (_, i) => (
                <tr key={i}><td colSpan={10} className="px-4 py-3"><Skeleton className="h-8" /></td></tr>
              )) : items.map((l) => (
                <tr key={l.id} onClick={() => navigate(path(`/leads/${l.id}`))} className={cn('group cursor-pointer transition hover:bg-surface-2/70', selected.has(l.id) && 'bg-surface-2')}>
                  <td className="px-4" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.name}`} className="size-4 accent-[var(--fg)]" />
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={l.name ?? l.phone} className="size-9" />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 font-bold">{l.name ?? 'Unnamed'}{l.do_not_call && <Ban className="size-3.5 text-danger" />}
                          <ArrowUpRight className="size-3.5 text-muted opacity-0 transition group-hover:opacity-100" /></div>
                        <div className="flex max-w-[260px] items-center gap-1.5 truncate text-xs text-muted">
                          {l.source?.startsWith('website') && <span className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-brand-soft px-1.5 py-px text-[10.5px] font-semibold text-brand" title={l.source}><Globe className="size-3" />{l.source.replace('website:', '') || 'web'}</span>}
                          <span className="truncate">{[l.company, l.city, LANGUAGES[l.language]].filter(Boolean).join(' · ') || '—'}</span>
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-3 font-mono text-[12.5px] whitespace-nowrap text-fg-2">
                    <span className={cn(l.phone_valid === false && 'text-danger')} title={l.phone_valid === false ? 'This number looks incomplete: calls will fail' : undefined}>{l.phone}</span>
                  </td>
                  <td className="px-3"><LeadStatusBadge status={l.status} /></td>
                  <td className="px-3">
                    {(() => { const v = score(l); return (
                      <div className="flex items-center gap-2" title={`Lead score ${v}/100`}>
                        <div className="h-1.5 w-12 overflow-hidden rounded-full bg-surface-2"><div className={cn('h-full rounded-full', v >= 60 ? 'bg-success' : v >= 35 ? 'bg-warning' : 'bg-muted')} style={{ width: `${v}%` }} /></div>
                        <span className="text-xs font-semibold tabular-nums text-fg-2">{v}</span>
                      </div>) })()}
                  </td>
                  <td className="px-3"><div className="flex items-center gap-1.5 whitespace-nowrap"><CallStatusBadge status={l.call_status} />{l.retry_count > 0 && <span className="text-xs text-muted">×{l.retry_count}</span>}</div></td>
                  <td className="px-3"><QualificationBadge value={l.qualification} /></td>
                  <td className="px-3 text-[13px] whitespace-nowrap"><NextStep l={l} /></td>
                  <td className="px-3 text-[13px] whitespace-nowrap text-muted" title={formatDate(l.last_contacted_at)}>{l.last_contacted_at ? timeAgo(l.last_contacted_at) : 'Never'}</td>
                  <td className="px-4 text-right" onClick={(e) => e.stopPropagation()}>
                    <Button size="sm" variant="primary" disabled={l.do_not_call || l.phone_valid === false} loading={startCall.isPending && startCall.variables === l.id}
                      onClick={() => startCall.mutate(l.id)}><PhoneCall />Call</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!isLoading && items.length === 0 && (
            <EmptyState icon={<Users />} title={q || activeFilters ? 'No leads match' : 'No leads yet'}
              description={q || activeFilters ? 'Try a different search or clear filters.' : 'Import a CSV/Excel file or add your first lead to start calling.'}
              action={!q && !activeFilters && <div className="flex gap-2"><Link to={path('/import')}><Button><Upload />Import</Button></Link><Button variant="primary" onClick={() => setAdding(true)}><Plus />Add lead</Button></div>} />
          )}
        </div>
        {data && data.total > 0 && <Pagination page={page} pageSize={25} total={data.total} onPage={setPage} />}
      </Card>

      <LeadFormSheet key={editing?.id ?? (adding ? 'new' : 'closed')} open={adding || editing !== null} lead={editing}
        onClose={() => { setAdding(false); setEditing(null); if (params.get('new')) setParams({}, { replace: true }) }} />
    </>
  )
}
