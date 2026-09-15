import { useInfiniteQuery } from '@tanstack/react-query'
import { Activity as ActivityIcon, Bot, CalendarCheck, Clock, PhoneCall, Search, Settings2, Users } from 'lucide-react'
import { useMemo, useState } from 'react'
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
    queryKey: ['activity', 'feed', type],
    queryFn: ({ pageParam }) => api<ActivityEvent[]>(`${base}/activity`, { params: { type, before_id: pageParam, limit: 60 } }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === 60 ? last[last.length - 1]!.id : undefined),
    refetchInterval: 8000,
  })

  const groups = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const seen = new Set<string>()
    const events = (query.data?.pages.flat() ?? []).filter((e) => {
      if (needle && !`${e.title} ${e.detail ?? ''} ${e.lead_name ?? ''}`.toLowerCase().includes(needle)) return false
      // Scheduler runs every few minutes with the same result: keep only the latest of each per day.
      if (hideRoutine && type !== 'automation' && e.type === 'automation.run') {
        const key = `${dayLabel(e.created_at)}|${e.title}`
        if (seen.has(key)) return false
        seen.add(key)
      }
      return true
    })
    const out: { day: string; events: ActivityEvent[] }[] = []
    for (const e of events) {
      const day = dayLabel(e.created_at)
      if (out[out.length - 1]?.day !== day) out.push({ day, events: [] })
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

      <div className="grid gap-6 lg:grid-cols-[240px_1fr]">
        <div className="space-y-4 lg:sticky lg:top-6 lg:self-start">
          <Card className="p-2">
            {FILTERS.map(({ value, label, icon: Icon }) => (
              <button key={value || 'all'} type="button" onClick={() => setType(value)}
                className={cn('flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-[13.5px] font-semibold transition',
                  type === value ? 'bg-fg text-bg' : 'text-fg-2 hover:bg-surface-2')}>
                <Icon className="size-4" />{label}
              </button>
            ))}
          </Card>
          <Card className="space-y-3 p-4">
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search history" className="pl-9" />
            </div>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span><span className="block font-semibold">Hide routine runs</span><span className="text-xs text-muted">Repeated scheduler results</span></span>
              <Switch checked={hideRoutine} onChange={setHideRoutine} label="Hide routine automation runs" />
            </label>
            <p className="text-xs text-muted">{query.isLoading ? 'Loading…' : `${total} event${total === 1 ? '' : 's'} shown`}</p>
          </Card>
        </div>

        <div className="space-y-6">
          {query.isLoading ? <Card className="space-y-4 p-6">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-12" />)}</Card>
            : groups.length ? groups.map((g) => (
              <section key={g.day}>
                <div className="sticky top-0 z-10 mb-3 flex items-center gap-3 bg-bg/90 py-1 backdrop-blur">
                  <h2 className="text-[13px] font-extrabold tracking-wider uppercase">{g.day}</h2>
                  <span className="h-px flex-1 bg-border" />
                  <span className="text-xs font-semibold text-muted">{g.events.length}</span>
                </div>
                <Card className="p-6">
                  <ActivityFeed events={g.events} showLead onLead={(id) => navigate(path(`/leads/${id}`))} onCall={setCallId} />
                </Card>
              </section>
            )) : <Card><EmptyState icon={<ActivityIcon />} title="Nothing here" description={q ? 'No events match your search.' : 'Events appear as leads are added and calls happen.'} /></Card>}
          {query.hasNextPage && <div className="text-center"><Button loading={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>Load older events</Button></div>}
        </div>
      </div>

      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); navigate(path(`/leads/${id}`)) }} />
    </>
  )
}
