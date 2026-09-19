import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ChevronsUp, GripVertical, ListOrdered, PhoneCall, Plus, Trash2, Search } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, EmptyState, Input, Skeleton } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { Lead } from '@/lib/types'
import { cn } from '@/lib/utils'

type QueueItem = Lead & { position: number; state: 'ready' | 'blocked' | 'scheduled' | 'cooldown'; wait: string }
type Queue = { items?: QueueItem[]; active_calls?: number; slots?: number; open_now?: boolean; hours?: string }

const STATE: Record<QueueItem['state'], string> = {
  ready: 'bg-success-soft text-success', scheduled: 'bg-surface-2 text-fg-2', cooldown: 'bg-warning-soft text-warning', blocked: 'bg-danger-soft text-danger',
}

/** Position box: edits locally, commits on blur / Enter so typing "12" does not jump to 1 first. */
function PositionInput({ index, max, disabled, onCommit }: { index: number; max: number; disabled?: boolean; onCommit: (to: number) => void }) {
  const [value, setValue] = useState(String(index + 1))
  useEffect(() => { setValue(String(index + 1)) }, [index])
  const commit = () => {
    const reset = () => setValue(String(index + 1))
    if (value.trim() === '') { reset(); return }
    const raw = Math.round(Number(value)) - 1
    if (!Number.isFinite(raw)) { reset(); return }
    const to = Math.max(0, Math.min(raw, max - 1))
    if (to === index || disabled) { reset(); return }
    onCommit(to)
  }
  return (
    <Input type="number" min={1} max={max} value={value} aria-label="Queue position" inputMode="numeric" disabled={disabled}
      onChange={(e) => setValue(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() } }}
      className="h-10 w-16 shrink-0 px-1 text-center font-bold tabular-nums sm:h-8" />
  )
}

const IST = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit' })

/** Ticking IST clock kept in its own component so the 1s tick does not re-render every queue row. */
function IstClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { const timer = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(timer) }, [])
  return <>Now: {IST.format(now)}</>
}

/** The call queue in dialling order: drag, arrows or type a number to change who is called next. */
export default function CallQueue() {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const { data, isLoading, isError, error, refetch } = useQuery({ queryKey: ['calls', 'queue', base], queryFn: () => api<Queue>(`${base}/leads/queue`), refetchInterval: 2000 })
  const [order, setOrder] = useState<QueueItem[]>([])
  const [dragging, setDragging] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [isDesktop, setIsDesktop] = useState(() => typeof window !== 'undefined' && window.matchMedia('(min-width: 640px)').matches)
  const navigate = useNavigate()

  const save = useMutation({
    mutationFn: (ids: number[]) => api(`${base}/leads/queue/order`, { method: 'POST', json: { ids } }),
    // Stop an in-flight poll from landing mid-save and snapping the list back to the old order
    onMutate: () => qc.cancelQueries({ queryKey: ['calls', 'queue'] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['calls', 'queue'] }),
    onError: (e) => { toast.error('Could not reorder', { description: e.message }); qc.invalidateQueries({ queryKey: ['calls', 'queue'] }) },
    // Drag keeps `dragging` set until the save (and its refetch) settles, so the sync effect below cannot snap the row back mid-save
    onSettled: () => setDragging(null),
  })
  const remove = useMutation({
    mutationFn: (ids: number[]) => api(`${base}/leads/queue/remove`, { method: 'POST', json: { ids } }),
    onSuccess: () => { toast.success('Removed from queue'); qc.invalidateQueries({ queryKey: ['calls', 'queue'] }); qc.invalidateQueries({ queryKey: ['leads'] }) },
    onError: (e) => toast.error('Could not remove', { description: e.message }),
  })
  // Keep the server order unless the user is mid-drag or a reorder is still being saved
  useEffect(() => { if (data && dragging === null && !save.isPending) setOrder(data.items ?? []) }, [data, dragging, save.isPending])
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 640px)')
    const onChange = () => setIsDesktop(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const callNow = useMutation({
    mutationFn: (leadId: number) => api(`${base}/calls`, { method: 'POST', json: { lead_id: leadId } }),
    onSuccess: () => { toast.success('Calling now'); qc.invalidateQueries({ queryKey: ['calls'] }); qc.invalidateQueries({ queryKey: ['leads'] }) },
    onError: (e) => toast.error('Call not placed', { description: e.message }),
  })

  const commit = (next: QueueItem[]) => { setOrder(next); save.mutate(next.map((l) => l.id)) }
  const move = (from: number, to: number) => {
    if (from < 0 || from >= order.length) return
    const next = [...order]
    const [item] = next.splice(from, 1)
    next.splice(Math.max(0, Math.min(to, next.length)), 0, item!)
    commit(next)
  }

  if (isLoading) return <Card className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</Card>
  if (!data) {
    return (
      <Card>
        <EmptyState icon={<ListOrdered />} title="Could not load the queue" description={(error as Error | null)?.message || 'The server did not answer.'}
          action={<Button size="sm" onClick={() => refetch()}>Try again</Button>} />
      </Card>
    )
  }
  const stale = isError && !!data

  const slots = data.slots ?? 1
  const hours = data.hours ?? ''
  const closeAt = hours.split(/[–-]/)[1]?.trim()
  const ready = order.filter((l) => l.state === 'ready').length
  const q = search.trim().toLowerCase()
  const visible = q ? order.filter((l) => (l.name || '').toLowerCase().includes(q) || (l.phone || '').includes(q) || (l.company || '').toLowerCase().includes(q)) : order
  const filtering = q.length > 0

  const stats: [string, string | number, ReactNode?][] = [
    ['In queue', order.length], ['Ready to dial', ready], ['Calls running', `${data.active_calls ?? 0}/${slots}`],
    ['Calling hours (IST)', data.open_now ? (closeAt ? `Open until ${closeAt}` : 'Open now') : (hours ? `Closed · ${hours}` : 'Closed'), <IstClock />],
  ]

  return (
    <div className="min-w-0 space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map(([l, v, sub]) => (
          <Card key={l} className={cn('min-w-0 p-4', sub && 'col-span-2 lg:col-span-1')}>
            <div className="truncate text-xs text-muted">{l}</div>
            <div className="mt-1 break-words text-lg font-extrabold text-fg tabular-nums">{v}</div>
            {sub && <div className="truncate text-xs text-muted tabular-nums">{sub}</div>}
          </Card>
        ))}
      </div>

      {stale && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-warning/30 bg-warning-soft px-4 py-2 text-xs text-warning">
          <span className="min-w-0 flex-1">Live updates paused: {(error as Error | null)?.message || 'the server did not answer'}. Showing the last known queue.</span>
          <Button size="sm" variant="ghost" onClick={() => refetch()}>Retry</Button>
        </div>
      )}
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3 sm:px-5">
          <ListOrdered className="size-4 shrink-0 text-fg-2" />
          <div className="min-w-0 flex-1 basis-48">
            <div className="text-sm font-bold text-fg">Dialling order</div>
            <div className="text-xs text-muted">Drag rows, use the arrows, or type a position. The queue calls {slots} at a time as calls finish &middot; updates live.</div>
          </div>
          <div className="relative w-full min-w-0 sm:w-auto sm:min-w-[200px]">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search in queue&hellip;" className="h-10 pl-9 text-[13px] sm:h-8" />
          </div>
          <Button size="sm" className="w-full sm:w-auto" onClick={() => navigate(path('/leads'))}><Plus />Add leads</Button>
        </div>

        {order.length === 0 ? (
          <EmptyState icon={<ListOrdered />} title="The queue is empty" description={<>Select leads on the Leads page and click &ldquo;Queue for auto-dial&rdquo;, or use Queue on a lead.</>} />
        ) : visible.length === 0 ? (
          <EmptyState icon={<Search />} title="No matches" description={<>Nothing in the queue matches &ldquo;{search.trim()}&rdquo;.</>}
            action={<Button size="sm" variant="ghost" onClick={() => setSearch('')}>Clear search</Button>} />
        ) : (
          <ol className="divide-y divide-border">
            {visible.map((lead) => {
              const index = order.findIndex((l) => l.id === lead.id)
              const last = index === order.length - 1
              const busy = save.isPending || remove.isPending
              return (
                <li key={lead.id} draggable={!filtering && isDesktop}
                  onDragStart={(e) => {
                    if (filtering || !isDesktop) { e.preventDefault(); return }
                    // Firefox refuses to start a native drag unless dragstart sets some data
                    e.dataTransfer.setData('text/plain', String(lead.id))
                    e.dataTransfer.effectAllowed = 'move'
                    setDragging(index)
                  }}
                  onDragOver={(e) => {
                    if (filtering) return
                    e.preventDefault()
                    if (dragging !== null && dragging !== index) { const next = [...order]; const [it] = next.splice(dragging, 1); next.splice(index, 0, it!); setOrder(next); setDragging(index) }
                  }}
                  onDragEnd={() => { if (dragging === null) return; save.mutate(order.map((l) => l.id)) }}
                  className={cn('group flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3 transition sm:px-4', dragging === index && 'bg-surface-2 opacity-80')}>
                  <GripVertical className={cn('hidden size-4 shrink-0 text-muted sm:block', filtering ? 'cursor-not-allowed opacity-40' : 'cursor-grab')} />
                  <PositionInput index={index} max={order.length} disabled={busy} onCommit={(to) => move(index, to)} />
                  <div className="min-w-0 flex-1 basis-40">
                    <Link to={path(`/leads/${lead.id}`)} className="block truncate text-sm font-bold text-fg hover:underline">{lead.name || 'Unnamed'}</Link>
                    <div className="truncate text-xs text-muted">{lead.phone || 'No phone'}{lead.company ? ` · ${lead.company}` : ''}{lead.qualification ? ` · ${lead.qualification}` : ''}</div>
                  </div>
                  <span className={cn('max-w-full truncate rounded-full px-2 py-0.5 text-[11px] font-semibold', STATE[lead.state] ?? STATE.scheduled)}>{lead.wait}</span>
                  <div className="flex w-full flex-wrap items-center gap-1 sm:w-auto">
                    <Button size="icon" variant="ghost" disabled={index <= 0 || busy} onClick={() => move(index, 0)} title="Move to top" aria-label="Move to top"><ChevronsUp /></Button>
                    <Button size="icon" variant="ghost" disabled={index <= 0 || busy} onClick={() => move(index, index - 1)} title="Move up" aria-label="Move up"><ArrowUp /></Button>
                    <Button size="icon" variant="ghost" disabled={last || busy} onClick={() => move(index, index + 1)} title="Move down" aria-label="Move down"><ArrowDown /></Button>
                    <Button size="sm" className="ml-auto sm:ml-0" disabled={lead.state === 'blocked' || !lead.phone || callNow.isPending}
                      loading={callNow.isPending && callNow.variables === lead.id} onClick={() => callNow.mutate(lead.id)}><PhoneCall />Call now</Button>
                    <Button size="icon" variant="ghost" disabled={busy} loading={remove.isPending && remove.variables?.includes(lead.id)}
                      onClick={() => remove.mutate([lead.id])} title="Remove from queue" aria-label="Remove from queue"><Trash2 /></Button>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
        {order.length > 0 && (
          <div className="border-t border-border px-4 py-2 text-xs text-muted sm:px-5">
            <Badge tone="success" dot>Live</Badge>
            <span className="ml-1">Positions save instantly{filtering ? ' · clear the search to drag rows' : ''}. Leads just called wait 10 minutes; scheduled callbacks keep their time.</span>
          </div>
        )}
      </Card>
    </div>
  )
}
