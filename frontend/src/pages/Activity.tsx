import { useInfiniteQuery } from '@tanstack/react-query'
import { Activity as ActivityIcon, Bot, CalendarCheck, Clock, PhoneCall, Search, Settings2, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ActivityFeed from '@/components/ActivityFeed'
import CallSheet from '@/components/CallSheet'
import { Button, Card, EmptyState, Input, PageHeader, Skeleton, Switch } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { ActivityEvent } from '@/lib/types'
import { cn } from '@/lib/utils'

const FILTERS = [
  { value: '', label: 'Everything', icon: ActivityIcon }, { value: 'call', label: 'Calls', icon: PhoneCall },
  { value: 'ai', label: 'AI updates', icon: Bot }, { value: 'meeting', label: 'Meetings', icon: CalendarCheck },
  { value: 'callback', label: 'Callbacks', icon: PhoneCall },
  { value: 'lead', label: 'Leads', icon: Users }, { value: 'automation', label: 'Automation', icon: Clock },
  { value: 'settings', label: 'Settings', icon: Settings2 },
]

function dayLabel(iso: string) {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(Date.now() - 86_400_000)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
}

export default function Activity() {
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const [type, setType] = useState('')
  const [q, setQ] = useState('')
  const [hideRoutine, setHideRoutine] = useState(true)
  const [callId, setCallId] = useState<number | null>(null)
  const query = useInfiniteQuery({
    // Without the agent id the cache is shared between workspaces: opening a second agent showed
    // the first one's history until the refetch landed.
    queryKey: ['activity', 'feed', agent?.id, type],
    queryFn: ({ pageParam }) => api<ActivityEvent[]>(`${base}/activity`, { params: { type, before_id: pageParam, limit: 60 } }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === 60 ? last[last.length - 1]!.id : undefined),
    // Polling refetches every loaded page each tick, so bound the cache: without maxPages a user
    // who paged back five times would fire five 60-row requests every 8 seconds.
    maxPages: 5,
    refetchInterval: 8000,
  })

  // The server matches the type filter as a bare prefix, so a "call" page can be entirely
  // "callback.*" rows that the dotted-prefix filter below drops. Keep pulling older pages while
  // the visible result is empty so the user does not see a false "no events" state.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  const loadedCount = query.data?.pages.flat().length ?? 0
  const visibleCount = useMemo(
    () => (query.data?.pages.flat() ?? []).filter((e) => !type || e.type.startsWith(`${type}.`)).length,
    [query.data, type])
  useEffect(() => {
    if (type && loadedCount > 0 && visibleCount === 0 && hasNextPage && !isFetchingNextPage) void fetchNextPage()
  }, [type, loadedCount, visibleCount, hasNextPage, isFetchingNextPage, fetchNextPage])

  const groups = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const seen = new Set<string>()
    const events = (query.data?.pages.flat() ?? []).filter((e) => {
      // The API matches the filter as a bare prefix, so "call" also returns "callback.*" events
      // even though Callbacks has its own chip. Every event type is "<group>.<name>", so require
      // the dotted prefix here.
      if (type && !e.type.startsWith(`${type}.`)) return false
      if (needle && !`${e.title} ${e.detail ?? ''} ${e.lead_name ?? ''}`.toLowerCase().includes(needle)) return false
      // Scheduler runs every few minutes with the same result: keep only the latest of each per day.
      if (hideRoutine && type !== 'automation' && e.type === 'automation.run') {
        const key = `${dayLabel(e.created_at)}|${e.title}`
        if (seen.has(key)) return false
        seen.add(key)
      }
      return true
    })
    // Keyed by calendar date plus an ordinal (a backfilled event can make the same day label appear
    // twice), never by an event id: that remounted and re-animated the whole day on every new event.
    const out: { day: string; key: string; events: ActivityEvent[] }[] = []
    const ordinal: Record<string, number> = {}
    for (const e of events) {
      const day = dayLabel(e.created_at)
      if (out[out.length - 1]?.day !== day) {
        const date = new Date(e.created_at).toDateString()
        ordinal[date] = (ordinal[date] ?? 0) + 1
        out.push({ day, key: `${date}#${ordinal[date]}`, events: [] })
      }
      out[out.length - 1]!.events.push(e)
    }
    return out
  }, [query.data, q, hideRoutine, type])

  const total = groups.reduce((n, g) => n + g.events.length, 0)

  return (
    <>
      <PageHeader
        eyebrow={<><ActivityIcon className="size-3.5" />{agent?.name} · Audit trail</>}
        title="History"
        description="A complete, time-ordered record of this agent: calls, AI CRM changes, meetings, imports, automation runs and settings changes." />

      <div className="grid min-w-0 gap-4 sm:gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4 lg:sticky lg:top-6 lg:self-start">
          {/* On phones the filters run as a horizontal chip row; from lg they stack into the sidebar. */}
          <Card className="flex gap-1 overflow-x-auto p-2 lg:flex-col lg:overflow-visible">
            {FILTERS.map(({ value, label, icon: Icon }) => (
              <button key={value || 'all'} type="button" onClick={() => setType(value)} aria-pressed={type === value}
                className={cn('flex min-h-10 shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-left text-[13.5px] font-semibold whitespace-nowrap transition lg:w-full lg:gap-3',
                  type === value ? 'bg-fg text-bg' : 'text-fg-2 hover:bg-surface-2')}>
                <Icon className="size-4 shrink-0" />{label}
              </button>
            ))}
          </Card>
          <Card className="space-y-3 p-4">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search history" aria-label="Search history" className="pl-9" />
            </div>
            <label className="flex min-h-10 items-center justify-between gap-3 text-[13px]">
              <span className="min-w-0"><span className="block font-semibold">Hide routine runs</span><span className="text-xs text-muted">Repeated scheduler results</span></span>
              <Switch checked={hideRoutine} onChange={setHideRoutine} label="Hide routine runs" />
            </label>
            <p className="text-xs break-words text-muted">{query.isLoading ? 'Loading…' : `${total} event${total === 1 ? '' : 's'} shown`}
              {/* Filtering happens on what has been loaded, so say so rather than implying the count is everything. */}
              {!query.isLoading && q && query.hasNextPage ? ' — searching loaded events; use “Load older events” to go further back.' : ''}</p>
          </Card>
        </div>

        <div className="min-w-0 space-y-6">
          {query.isLoading ? <Card className="space-y-4 p-4 sm:p-6">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</Card>
            : query.isError && !query.data ? (
              <Card><EmptyState icon={<ActivityIcon />} title="Couldn't load history"
                description={query.error instanceof Error ? query.error.message : 'Something went wrong.'}
                action={<Button loading={query.isFetching} onClick={() => { void query.refetch() }}>Try again</Button>} /></Card>
            ) : !groups.length && query.hasNextPage && query.isFetchingNextPage ? <Card className="space-y-4 p-4 sm:p-6">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</Card>
            : groups.length ? groups.map((g) => (
              <section key={g.key}>
                {/* Sits below the mobile top bar (h-12); on lg the top bar is hidden so it can hug the top. */}
                {/* Floating pills, not a full-width strip: a solid bar over the page gradient read as a black line. */}
                <div className="pointer-events-none sticky top-12 z-10 mb-3 flex items-center justify-between py-1 lg:top-0">
                  <h2 className="reveal reveal-in reveal-left pointer-events-auto inline-flex items-center gap-2 rounded-full border border-border/60 bg-surface/80 px-3 py-1 text-[11px] font-semibold tracking-wider text-muted uppercase shadow-sm backdrop-blur-md">
                    {g.day === 'Today' && <span className="relative flex size-1.5"><span className="absolute inline-flex size-full animate-live-ring rounded-full bg-success" /><span className="relative inline-flex size-1.5 rounded-full bg-success" /></span>}
                    {g.day}
                  </h2>
                  <span className="reveal reveal-in reveal-right pointer-events-auto rounded-full border border-border/60 bg-surface/80 px-2 py-0.5 text-[11px] font-medium text-muted tabular-nums shadow-sm backdrop-blur-md">{g.events.length} events</span>
                </div>
                <Card className="p-4 sm:p-6">
                  <ActivityFeed events={g.events} showLead onLead={(id) => navigate(path(`/leads/${id}`))} onCall={setCallId} />
                </Card>
              </section>
            )) : <Card><EmptyState icon={<ActivityIcon />} title="Nothing here"
              description={query.hasNextPage ? 'No matching events in the loaded range — load older events to keep looking.' : q ? 'No events match your search.' : type ? `No ${FILTERS.find((f) => f.value === type)?.label.toLowerCase() ?? type} events yet.` : 'Events appear as leads are added and calls happen.'}
              action={(q || type) ? <Button variant="secondary" onClick={() => { setQ(''); setType('') }}>Clear filters</Button> : undefined} /></Card>}
          {/* Background polls and next-page fetches can fail while data is retained: keep the loaded
              history on screen and surface the failure inline instead of replacing everything. */}
          {query.isError && query.data && (
            <p role="status" className="text-center text-xs break-words text-danger">
              {query.isFetchNextPageError ? "Couldn't load older events" : "Couldn't refresh history"}
              {query.error instanceof Error && query.error.message ? ` — ${query.error.message}` : ''}
            </p>
          )}
          {query.hasNextPage && <div className="text-center"><Button loading={query.isFetchingNextPage} disabled={query.isFetchingNextPage} onClick={() => { void query.fetchNextPage() }}>Load older events</Button></div>}
        </div>
      </div>

      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); navigate(path(`/leads/${id}`)) }} />
    </>
  )
}
