import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, PhoneIncoming, ArrowDown, ArrowUp, ArrowUpRight, Ban, CalendarCheck, Clock, Download, Flame, Globe, ListPlus, PhoneCall, PhoneOff, Plus,
  Search, Sparkles, Trash2, Upload, Users, X,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { LeadFormSheet, useStartCall } from '@/components/LeadSheets'
import { CallStatusBadge, LeadStatusBadge, QualificationBadge } from '@/components/status'
import { Avatar, Button, Card, EmptyState, Input, PageHeader, Pagination, Select, Skeleton, TableScroll, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { AnimatedNumber } from '@/lib/motion'
import { useAgent } from '@/lib/agent'
import type { Lead, LeadStats, Page } from '@/lib/types'
import { CALL_STATUSES, cn, formatDate, LANGUAGES, LEAD_STATUSES, QUALIFICATIONS, timeAgo } from '@/lib/utils'

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value)
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t) }, [value, ms])
  return v
}

// Every pipeline stage the backend accepts; "Do Not Call" has its own view and bulk button.
const STAGES = LEAD_STATUSES.filter((s) => s !== 'Do Not Call')
const SORT_OPTIONS: [string, string][] = [
  ['id:desc', 'Newest first'], ['last_contacted_at:desc', 'Recently contacted'], ['name:asc', 'Name A–Z'], ['meeting_at:desc', 'Meeting date'],
]
const SORT_LABELS: Record<string, string> = { id: 'Created', name: 'Name', status: 'Stage', qualification: 'Temperature', last_contacted_at: 'Last contact', meeting_at: 'Meeting date' }

const VIEWS = [
  { id: '', label: 'All', icon: Users },
  { id: 'hot_uncalled', label: 'Hot, not called', icon: Flame },
  { id: 'never_called', label: 'Never called', icon: Sparkles },
  { id: 'website', label: 'Website leads', icon: Globe },
  { id: 'new_callers', label: 'New callers', icon: PhoneIncoming },
  { id: 'callbacks', label: 'Callbacks', icon: Clock },
  { id: 'meetings', label: 'Meetings', icon: CalendarCheck },
  { id: 'attention', label: 'Needs attention', icon: AlertTriangle },
  { id: 'dnc', label: 'Do not call', icon: PhoneOff },
] as const
// The backend places at most this many calls per bulk request (app/api/leads.py, `body.ids[:20]`).
const MAX_BULK_CALL = 20
const JOURNEY = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won']

/** 0-100: temperature, pipeline stage and recency (same idea as the lead page score). */
function score(l: Lead) {
  if (l.do_not_call) return 0
  const temp = { Hot: 45, Warm: 28, Cold: 8 }[l.qualification ?? ''] ?? 12
  const stage = Math.max(0, JOURNEY.indexOf(l.status)) * 7
  const recent = l.last_contacted_at && Date.now() - Date.parse(l.last_contacted_at) < 7 * 86_400_000 ? 12 : 0
  return Math.min(100, temp + stage + recent)
}

type Sort = { key: string; order: 'asc' | 'desc' }

function Th({ k, sort, onSort, children, className }: { k?: string; sort: Sort; onSort: (k: string) => void; children: ReactNode; className?: string }) {
  return (
    <th aria-sort={k && sort.key === k ? (sort.order === 'desc' ? 'descending' : 'ascending') : undefined}
      className={cn('px-3 py-3 text-left text-[11px] font-bold tracking-wider whitespace-nowrap text-muted uppercase', className)}>
      {k ? (
        <button type="button" onClick={() => onSort(k)} className="inline-flex min-h-10 items-center gap-1 uppercase hover:text-fg sm:min-h-8">
          {children}{sort.key === k && (sort.order === 'desc' ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
        </button>
      ) : children}
    </th>
  )
}

function NextStep({ l }: { l: Lead }) {
  if (l.do_not_call) return <span className="text-muted">—</span>
  if (l.phone_valid === false) return <span className="inline-flex items-center gap-1 text-danger"><AlertTriangle className="size-3.5" />Fix number</span>
  if (l.callback_at) return <span className="inline-flex items-center gap-1 text-fg"><Clock className="size-3.5" />Callback {l.callback_at.slice(5)}</span>
  if (l.meeting_at) {
    // A meeting that has already happened is not a next step: it needs confirming, not preparing.
    const past = new Date(l.meeting_at.replace(' ', 'T') + '+05:30').getTime() < Date.now()
    return past
      ? <span className="inline-flex items-center gap-1 text-warning"><AlertTriangle className="size-3.5" />Meeting passed {l.meeting_at.slice(5)}</span>
      : <span className="inline-flex items-center gap-1 text-success"><CalendarCheck className="size-3.5" />Meeting {l.meeting_at.slice(5)}</span>
  }
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
  const [search, setSearch] = useState(params.get('search') ?? '')
  const [filters, setFilters] = useState({
    status: params.get('status') ?? '', call_status: params.get('call_status') ?? '',
    qualification: params.get('qualification') ?? '', view: params.get('view') ?? '',
  })
  const [sort, setSort] = useState<Sort>({ key: 'id', order: 'desc' })
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [editing, setEditing] = useState<Lead | null>(null)
  const [adding, setAdding] = useState(params.get('new') === '1')
  const q = useDebounced(search)

  useEffect(() => { setPage(1) }, [q, filters, sort])
  // The address bar describes what is on screen: reload, share or come back and the view is the same.
  useEffect(() => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries({ search: q, ...filters })) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    if (next.toString() !== params.toString()) setParams(next, { replace: true })
  }, [q, filters]) // eslint-disable-line react-hooks/exhaustive-deps
  // The reverse: in-page navigation (sidebar, palette, Home tiles) changes the query without remounting this page,
  // so re-read it. Search compares against the debounced value so it never clobbers what is being typed.
  useEffect(() => {
    const fromUrl = {
      status: params.get('status') ?? '', call_status: params.get('call_status') ?? '',
      qualification: params.get('qualification') ?? '', view: params.get('view') ?? '',
    }
    setFilters((prev) => (Object.keys(fromUrl) as (keyof typeof fromUrl)[]).every((k) => prev[k] === fromUrl[k]) ? prev : fromUrl)
    const urlSearch = params.get('search') ?? ''
    if (urlSearch !== q) setSearch(urlSearch)
    if (params.get('new') === '1') setAdding(true)
  }, [params]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const open = params.get('open')
    if (open) navigate(path(`/leads/${open}`), { replace: true })
  }, [params, navigate, path])

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['leads', base, q, filters, sort, page],
    queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { search: q, ...filters, sort: sort.key, order: sort.order, page, page_size: 25 } }),
    placeholderData: keepPreviousData,
    refetchInterval: 8000,
  })
  // With keepPreviousData a failed refetch keeps the old rows on screen, so say so instead of silently showing stale data.
  useEffect(() => {
    if (isError && data) toast.error('Could not refresh leads', { id: 'leads-stale', description: (error as Error).message })
  }, [isError, error, data])
  const stats = useQuery({ queryKey: ['leads', 'stats', base], queryFn: () => api<LeadStats>(`${base}/leads/stats`), refetchInterval: 15000 })
  const views = useQuery({ queryKey: ['leads', 'views', base], queryFn: () => api<Record<string, number>>(`${base}/leads/views`), refetchInterval: 15000 })
  // Without this the tiles show '—', every chip shows 0 and saved views vanish with no explanation.
  useEffect(() => {
    if (!stats.isError && !views.isError) return
    const err = (stats.error ?? views.error) as Error | null
    toast.error('Could not load lead counts', {
      id: 'leads-counts', description: err?.message,
      action: { label: 'Retry', onClick: () => { void stats.refetch(); void views.refetch() } },
    })
  }, [stats.isError, views.isError]) // eslint-disable-line react-hooks/exhaustive-deps
  const bulkUpdate = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<{ updated: number }>(`${base}/leads/bulk/update`, { method: 'POST', json: { ids: [...selected], ...body } }),
    onSuccess: (r) => { toast.success(`Updated ${r.updated} lead(s)`); setSelected(new Set()); qc.invalidateQueries({ queryKey: ['leads'] }) },
    onError: (e) => toast.error('Update failed', { description: e.message }),
  })
  const startCall = useStartCall()

  const bulk = useMutation({
    mutationFn: ({ action, ids }: { action: 'delete' | 'queue' | 'call'; ids: number[] }) =>
      api<{ deleted?: number; queued?: number; skipped?: number; placed?: unknown[]; errors?: { error: string }[] }>(`${base}/leads/bulk/${action}`, { method: 'POST', json: { ids } }),
    onSuccess: (res, { action }) => {
      if (action === 'delete') toast.success(`Deleted ${res.deleted} lead(s)`)
      if (action === 'queue') toast.success(`Queued ${res.queued} lead(s)`, { description: res.skipped ? `${res.skipped} skipped (Do-Not-Call or invalid number). Auto-dial will call the rest within calling hours.` : 'Auto-dial will call them within calling hours.' })
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
  const [selectingAll, setSelectingAll] = useState(false)

  // Acting on a filtered set of 400 leads should not mean paging through it 25 at a time.
  const selectAllMatching = async () => {
    setSelectingAll(true)
    try {
      const { ids } = await api<{ ids: number[] }>(`${base}/leads/ids`, { params: { search: q, ...filters } })
      setSelected(new Set(ids))
      if (data && ids.length < data.total) toast.info(`Selected the first ${ids.length} of ${data.total}`)
    } catch (e) {
      toast.error('Could not select them all', { description: (e as Error).message })
    } finally {
      setSelectingAll(false)
    }
  }
  const allSelected = items.length > 0 && items.every((l) => selected.has(l.id))
  const someSelected = !allSelected && items.some((l) => selected.has(l.id))
  const headerCheckbox = useRef<HTMLInputElement>(null)
  useEffect(() => { if (headerCheckbox.current) headerCheckbox.current.indeterminate = someSelected }, [someSelected])
  const toggle = (id: number) => setSelected((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const sortBy = (key: string) => setSort((prev) => ({ key, order: prev.key === key && prev.order === 'desc' ? 'asc' : 'desc' }))
  const activeFilters = Object.values(filters).filter(Boolean).length
  const busy = bulk.isPending || bulkUpdate.isPending
  const clearFilters = () => { setFilters({ status: '', call_status: '', qualification: '', view: '' }); setSearch('') }

  const summary: [typeof Users, string, number | undefined, () => void, boolean][] = [
    [Users, 'All leads', s?.total, clearFilters, activeFilters === 0],
    [Clock, 'Waiting to call', s?.pending, () => setFilters({ status: '', call_status: '', qualification: '', view: 'waiting' }), filters.view === 'waiting'],
    [Flame, 'Hot', s ? s.by_qualification?.Hot ?? 0 : undefined, () => setFilters({ status: '', call_status: '', qualification: 'Hot', view: '' }), filters.qualification === 'Hot'],
    [CalendarCheck, 'Meetings', s?.meetings, () => setFilters({ status: 'Meeting Booked', call_status: '', qualification: '', view: '' }), filters.status === 'Meeting Booked'],
  ]

  return (
    <>
      <PageHeader
        eyebrow={<><Users className="size-3.5" />{agent?.name} · CRM</>}
        title="Leads"
        description="Everyone this agent calls, with the AI's qualification and the result of every conversation. Leads here are never shared with other agents."
        actions={<>
          <a href={`${base}/leads/export?${new URLSearchParams({ search: q, ...filters }).toString()}`} tabIndex={-1} className="rounded-xl">
            <Button><Download />Export{data && data.total !== s?.total ? ` ${data.total}` : ''} CSV</Button>
          </a>
          <Link to={path('/import')}><Button><Upload />Import</Button></Link>
          <Button variant="primary" onClick={() => setAdding(true)}><Plus />Add lead</Button>
        </>}>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {summary.map(([Icon, label, value, onClick, active], i) => (
            <button key={label} type="button" onClick={onClick} style={{ animationDelay: `${120 + i * 70}ms` }}
              className={cn('reveal reveal-in reveal-up glint group flex items-center gap-3 rounded-2xl border bg-surface p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop',
                active ? 'border-fg ring-1 ring-fg' : 'border-border')}>
              <span className={cn('grid size-10 place-items-center rounded-xl transition-transform duration-300 group-hover:-rotate-6 group-hover:scale-105', active ? 'bg-fg text-bg' : 'bg-surface-2 text-fg-2')}><Icon className="size-4.5" /></span>
              <span className="min-w-0">
                <span className="block truncate text-2xl leading-none font-extrabold tabular-nums">{value == null ? '—' : <AnimatedNumber value={value} />}</span>
                <span className="mt-1 block text-xs font-semibold text-muted">{label}</span>
              </span>
            </button>
          ))}
        </div>
      </PageHeader>

      <div className="-mx-4 mb-2 flex min-w-0 gap-1.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
        {VIEWS.map(({ id, label, icon: Icon }) => {
          const active = filters.view === id
          const count = id ? views.data?.[id] : s?.total
          // Hide the situational views only once we know they are empty; while loading or after an error keep them reachable.
          if (id && views.isSuccess && !count && !active && ['attention', 'dnc', 'callbacks', 'website', 'new_callers'].includes(id)) return null
          return (
            <button key={id || 'all-views'} type="button" onClick={() => setFilters({ ...filters, view: id })}
              className={cn('flex min-h-10 shrink-0 items-center gap-1.5 rounded-xl border px-3 py-1.5 text-[13px] font-semibold transition sm:min-h-8',
                active ? 'border-brand bg-brand text-brand-fg' : 'border-border bg-surface text-fg-2 hover:border-border-strong',
                id === 'attention' && !active && 'border-warning/40 text-warning')}>
              <Icon className="size-3.5" />{label}<span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', active ? 'bg-white/20' : 'bg-surface-2 text-muted')}>{count ?? 0}</span>
            </button>
          )
        })}
      </div>

      <div className="-mx-4 mb-3 flex min-w-0 gap-1.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
        {['', ...STAGES].map((st) => {
          const count = st ? s?.by_status?.[st] ?? 0 : s?.total
          const active = filters.status === st
          return (
            <button key={st || 'all'} type="button" onClick={() => setFilters({ ...filters, status: st })}
              className={cn('flex min-h-10 shrink-0 items-center gap-2 rounded-full border px-3.5 py-1.5 text-[13px] font-semibold transition sm:min-h-8',
                active ? 'border-fg bg-fg text-bg' : 'border-border bg-surface text-fg-2 hover:border-border-strong')}>
              {st || 'All stages'}<span className={cn('rounded-full px-1.5 text-[11px] tabular-nums', active ? 'bg-bg/20' : 'bg-surface-2 text-muted')}>{count ?? 0}</span>
            </button>
          )
        })}
      </div>

      {/* No overflow-hidden here: it would become the sticky containing block and the bulk bar would never stick. */}
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="relative min-w-0 flex-1 basis-full sm:basis-56 sm:max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <Input value={search} onChange={(e) => { setSearch(e.target.value); if (e.target.value && filters.view) setFilters({ ...filters, view: '' }) }} placeholder="Search name, company, phone, city, tag…" className="pl-9" aria-label="Search leads" />
          </div>
          <Select value={filters.call_status} onChange={(e) => setFilters({ ...filters, call_status: e.target.value })} className="w-auto" aria-label="Last call">
            <option value="">Any call result</option>{CALL_STATUSES.map((x) => <option key={x}>{x}</option>)}
          </Select>
          <Select value={filters.qualification} onChange={(e) => setFilters({ ...filters, qualification: e.target.value })} className="w-auto" aria-label="Temperature">
            <option value="">Any temperature</option>{QUALIFICATIONS.map((x) => <option key={x}>{x}</option>)}
          </Select>
          <Select value={`${sort.key}:${sort.order}`} onChange={(e) => { const [key, order] = e.target.value.split(':'); setSort({ key: key!, order: order as 'asc' | 'desc' }) }} className="w-auto" aria-label="Sort">
            {SORT_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            {/* Column-header sorts (e.g. Stage, Temp., Name Z–A) have no preset above: keep the select truthful with the current one. */}
            {!SORT_OPTIONS.some(([v]) => v === `${sort.key}:${sort.order}`) && (
              <option value={`${sort.key}:${sort.order}`}>{SORT_LABELS[sort.key] ?? sort.key} {sort.order === 'asc' ? '↑' : '↓'}</option>
            )}
          </Select>
          {activeFilters > 0 && <Button variant="ghost" size="sm" onClick={clearFilters}><X />Clear filters</Button>}
          <span className="ml-auto text-xs font-semibold text-muted">{isFetching && !isLoading ? 'Updating…' : data ? `${data.total} result${data.total === 1 ? '' : 's'}` : ''}</span>
        </div>

        {selected.size > 0 && (
          <div className="sticky top-12 z-10 flex flex-wrap lg:top-0 items-center gap-2 border-b border-border bg-surface-2 px-4 py-2.5 text-sm text-fg">
            <span className="font-bold">{selected.size} selected</span>
            {data && data.total > items.length && selected.size < data.total && (
              <Button size="sm" variant="ghost" loading={selectingAll} onClick={selectAllMatching}>
                Select all {data.total}
              </Button>
            )}
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setSelected(new Set())}>Clear</Button>
            <div className="flex-1" />
            <Button size="sm" disabled={busy} loading={bulk.isPending && bulk.variables?.action === 'call'} onClick={async () => {
              // The server only attempts the first 20 ids and drops the rest silently: say so and send exactly what will be called.
              const ids = [...selected].slice(0, MAX_BULK_CALL)
              const capped = selected.size > MAX_BULK_CALL
              if (await confirm({
                title: `Call ${ids.length} lead(s) now?`,
                description: capped
                  ? `Only ${MAX_BULK_CALL} calls can be started at once; the other ${selected.size - MAX_BULK_CALL} selected lead(s) will not be called. Use "Queue for auto-dial" for the rest.`
                  : 'Calls start immediately (up to your concurrent call limit), even outside calling hours.',
                confirmLabel: capped ? `Start ${MAX_BULK_CALL} calls` : 'Start calls',
              }))
                bulk.mutate({ action: 'call', ids })
            }}><PhoneCall />Call now{selected.size > MAX_BULK_CALL ? ` (${MAX_BULK_CALL})` : ''}</Button>
            <Button size="sm" disabled={busy} loading={bulk.isPending && bulk.variables?.action === 'queue'} onClick={() => bulk.mutate({ action: 'queue', ids: [...selected] })}><ListPlus />Queue for auto-dial</Button>
            <select aria-label="Set stage" value="" disabled={busy} onChange={(e) => e.target.value && bulkUpdate.mutate({ status: e.target.value })}
              className="h-10 rounded-lg border border-border bg-surface px-2 text-[13px] font-semibold text-fg disabled:opacity-50 sm:h-8">
              <option value="">Set stage…</option>{STAGES.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <select aria-label="Set temperature" value="" disabled={busy} onChange={(e) => e.target.value && bulkUpdate.mutate({ qualification: e.target.value })}
              className="h-10 rounded-lg border border-border bg-surface px-2 text-[13px] font-semibold text-fg disabled:opacity-50 sm:h-8">
              <option value="">Temperature…</option>{QUALIFICATIONS.map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <Button size="sm" disabled={busy} loading={bulkUpdate.isPending && bulkUpdate.variables?.do_not_call === true} onClick={async () => {
              if (await confirm({ title: `Mark ${selected.size} lead(s) Do Not Call?`, description: 'They are excluded from manual and automated calls.', confirmLabel: 'Mark Do Not Call', danger: true }))
                bulkUpdate.mutate({ do_not_call: true })
            }}><PhoneOff />Do not call</Button>
            <Button size="sm" variant="danger" disabled={busy} loading={bulk.isPending && bulk.variables?.action === 'delete'} onClick={async () => {
              if (await confirm({ title: `Delete ${selected.size} lead(s)?`, description: 'This cannot be undone.', confirmLabel: 'Delete', danger: true }))
                bulk.mutate({ action: 'delete', ids: [...selected] })
            }}><Trash2 />Delete</Button>
            <button type="button" disabled={busy} className="min-h-10 px-2 text-sm font-semibold text-muted hover:text-fg disabled:opacity-50 sm:min-h-8" onClick={() => setSelected(new Set())}>Cancel</button>
          </div>
        )}

        {isError && data && (
          <div role="alert" className="flex flex-wrap items-center gap-2 border-b border-warning/40 bg-warning/10 px-4 py-2 text-[13px] font-semibold text-warning">
            <AlertTriangle className="size-4 shrink-0" /><span className="min-w-0 flex-1 break-words">Showing the last loaded list: {(error as Error).message}</span>
            <Button size="sm" variant="ghost" onClick={() => refetch()}>Retry</Button>
          </div>
        )}
        <TableScroll className="rounded-b-[var(--radius-card)]">
          <table className="rows-in w-full min-w-[1040px] text-sm">
            <thead className="border-b border-border bg-surface-2/50">
              <tr>
                <th className="w-12 p-0"><label className="grid min-h-10 cursor-pointer place-items-center px-3"><input ref={headerCheckbox} type="checkbox" aria-label="Select all on this page" checked={allSelected}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((l) => l.id)))} className="size-4 accent-[var(--fg)]" /></label></th>
                <Th k="name" sort={sort} onSort={sortBy}>Lead</Th><Th sort={sort} onSort={sortBy}>Phone</Th><Th k="status" sort={sort} onSort={sortBy}>Stage</Th><Th sort={sort} onSort={sortBy}>Score</Th><Th sort={sort} onSort={sortBy}>Last call</Th><Th k="qualification" sort={sort} onSort={sortBy}>Temp.</Th>
                <Th sort={sort} onSort={sortBy}>Next step</Th><Th k="last_contacted_at" sort={sort} onSort={sortBy}>Last contact</Th><Th className="text-right" sort={sort} onSort={sortBy}>&nbsp;</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading ? Array.from({ length: 8 }, (_, i) => (
                <tr key={i}><td colSpan={10} className="px-4 py-3"><Skeleton className="h-8" /></td></tr>
              )) : items.map((l, i) => (
                <tr key={l.id} onClick={() => navigate(path(`/leads/${l.id}`))} style={{ animationDelay: `${Math.min(i, 20) * 45}ms` }} className={cn('reveal reveal-in reveal-up group cursor-pointer transition hover:bg-surface-2/70', selected.has(l.id) && 'bg-surface-2')}>
                  <td className="p-0" onClick={(e) => e.stopPropagation()}>
                    {/* The whole cell toggles: a 16px box alone is too small a target on a phone. */}
                    <label className="grid min-h-12 cursor-pointer place-items-center px-3">
                      <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l.id)} aria-label={`Select ${l.name ?? l.phone}`} className="size-4 cursor-pointer accent-[var(--fg)]" />
                    </label>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-3">
                      <Avatar name={l.name ?? l.phone} className="size-9" />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 font-bold">
                          <Link to={path(`/leads/${l.id}`)} onClick={(e) => e.stopPropagation()} className="max-w-[220px] truncate rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-fg/40">{l.name ?? 'Unnamed'}</Link>
                          {l.do_not_call && <Ban className="size-3.5 text-danger" />}
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
                        <div className="h-1.5 w-12 overflow-hidden rounded-full bg-surface-2"><div className={cn('grow-x h-full rounded-full', v >= 60 ? 'flow bg-success' : v >= 35 ? 'bg-warning' : 'bg-muted')} style={{ width: `${v}%` }} /></div>
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
        </TableScroll>
        {isError && !data && (
          <EmptyState icon={<AlertTriangle />} title="Could not load leads" description={(error as Error).message}
            action={<Button onClick={() => refetch()}>Try again</Button>} />
        )}
        {!isLoading && !isError && items.length === 0 && (
          <EmptyState icon={<Users />} title={q || activeFilters ? 'No leads match' : 'No leads yet'}
            description={q || activeFilters ? 'Try a different search or clear filters.' : 'Import a CSV/Excel file or add your first lead to start calling.'}
            action={!q && !activeFilters ? <div className="flex flex-wrap justify-center gap-2"><Link to={path('/import')}><Button><Upload />Import</Button></Link><Button variant="primary" onClick={() => setAdding(true)}><Plus />Add lead</Button></div> : <Button onClick={clearFilters}><X />Clear filters</Button>} />
        )}
        {data && data.total > 0 && <Pagination page={page} pageSize={25} total={data.total} onPage={setPage} />}
      </Card>

      <LeadFormSheet key={editing?.id ?? (adding ? 'new' : 'closed')} open={adding || editing !== null} lead={editing}
        onClose={() => { setAdding(false); setEditing(null); if (params.get('new')) { const next = new URLSearchParams(params); next.delete('new'); setParams(next, { replace: true }) } }} />
    </>
  )
}
