import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, ChevronsUp, GripVertical, ListOrdered, PhoneCall, Plus, Trash2, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, EmptyState, Input, Skeleton } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { Lead } from '@/lib/types'
import { cn } from '@/lib/utils'

type QueueItem = Lead & { position: number; state: 'ready' | 'blocked' | 'scheduled' | 'cooldown'; wait: string }
type Queue = { items: QueueItem[]; active_calls: number; slots: number; open_now: boolean; hours: string }

const STATE = {
  ready: 'bg-success-soft text-success', scheduled: 'bg-surface-2 text-fg-2', cooldown: 'bg-warning-soft text-warning', blocked: 'bg-danger-soft text-danger',
}

/** The call queue in dialling order: drag, arrows or type a number to change who is called next. */
export default function CallQueue() {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['calls', 'queue'], queryFn: () => api<Queue>(`${base}/leads/queue`), refetchInterval: 2000 })
  const [order, setOrder] = useState<QueueItem[]>([])
  const [dragging, setDragging] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [now, setNow] = useState(new Date())

  // Keep the server order unless the user is mid-drag
  useEffect(() => { if (data && dragging === null) setOrder(data.items) }, [data, dragging])
  useEffect(() => { const timer = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(timer) }, [])

  const save = useMutation({
    mutationFn: (ids: number[]) => api(`${base}/leads/queue/order`, { method: 'POST', json: { ids } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['calls', 'queue'] }),
    onError: (e) => toast.error('Could not reorder', { description: e.message }),
  })
  const remove = useMutation({
    mutationFn: (ids: number[]) => api(`${base}/leads/queue/remove`, { method: 'POST', json: { ids } }),
    onSuccess: () => { toast.success('Removed from queue'); qc.invalidateQueries({ queryKey: ['calls', 'queue'] }); qc.invalidateQueries({ queryKey: ['leads'] }) },
  })
  const callNow = useMutation({
    mutationFn: (leadId: number) => api(`${base}/calls`, { method: 'POST', json: { lead_id: leadId } }),
    onSuccess: () => { toast.success('Calling now'); qc.invalidateQueries({ queryKey: ['calls'] }) },
    onError: (e) => toast.error('Call not placed', { description: e.message }),
  })

  const commit = (next: QueueItem[]) => { setOrder(next); save.mutate(next.map((l) => l.id)) }
  const move = (from: number, to: number) => {
    const next = [...order]
    const [item] = next.splice(from, 1)
    next.splice(Math.max(0, Math.min(to, next.length)), 0, item!)
    commit(next)
  }

  if (isLoading || !data) return <Card className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</Card>

  const ready = order.filter((l) => l.state === 'ready').length
  const istTime = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(now)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[['In queue', order.length], ['Ready to dial', ready], ['Calls running', `${data.active_calls}/${data.slots}`], ['Calling hours (IST)', data.open_now ? `Open until ${data.hours.split('–')[1]} · Now: ${istTime}` : `Closed · ${data.hours} · Now: ${istTime}`]].map(([l, v]) => (
          <Card key={l as string} className="p-4"><div className="text-xs text-muted">{l}</div><div className="mt-1 truncate text-lg font-extrabold text-fg tabular-nums">{v}</div></Card>
        ))}
      </div>

      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3">
          <ListOrdered className="size-4 text-fg-2" />
          <div className="mr-auto"><div className="text-sm font-bold text-fg">Dialling order</div>
            <div className="text-xs text-muted">Drag rows, use the arrows, or type a position. The queue calls {data.slots} at a time as calls finish · updates live.</div></div>
          
          <div className="relative min-w-[200px]">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search in queue…" className="pl-9 h-8 text-[13px]" />
          </div>
          <Link to={path('/leads')}><Button size="sm"><Plus />Add leads</Button></Link>
        </div>

        {order.length === 0 ? (
          <EmptyState icon={<ListOrdered />} title="The queue is empty" description="Select leads on the Leads page and click “Queue for auto-dial”, or use Queue on a lead." />
        ) : (
          <ol className="divide-y divide-border">
            {order.filter(l => !search || (l.name?.toLowerCase() || '').includes(search.toLowerCase()) || (l.phone || '').includes(search)).map((lead, index) => (
              <li key={lead.id} draggable
                onDragStart={() => setDragging(index)}
                onDragOver={(e) => { e.preventDefault(); if (dragging !== null && dragging !== index) { const next = [...order]; const [it] = next.splice(dragging, 1); next.splice(index, 0, it!); setOrder(next); setDragging(index) } }}
                onDragEnd={() => { setDragging(null); save.mutate(order.map((l) => l.id)) }}
                className={cn('group flex flex-wrap items-center gap-3 px-4 py-3 transition', dragging === index && 'bg-surface-2 opacity-80')}>
                <GripVertical className="size-4 shrink-0 cursor-grab text-muted" />
                <Input type="number" min={1} max={order.length} value={index + 1} aria-label="Queue position"
                  onChange={(e) => { const to = Number(e.target.value) - 1; if (Number.isFinite(to) && to !== index) move(index, to) }}
                  className="h-8 w-14 text-center font-bold tabular-nums" />
                <div className="min-w-0 flex-1">
                  <Link to={path(`/leads/${lead.id}`)} className="block truncate text-sm font-bold text-fg hover:underline">{lead.name || 'Unnamed'}</Link>
                  <div className="truncate text-xs text-muted">{lead.phone}{lead.company ? ` · ${lead.company}` : ''}{lead.qualification ? ` · ${lead.qualification}` : ''}</div>
                </div>
                <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-semibold', STATE[lead.state])}>{lead.wait}</span>
                <div className="flex items-center gap-1">
                  <Button size="icon" variant="ghost" disabled={index === 0} onClick={() => move(index, 0)} title="Move to top" aria-label="Move to top"><ChevronsUp /></Button>
                  <Button size="icon" variant="ghost" disabled={index === 0} onClick={() => move(index, index - 1)} aria-label="Move up"><ArrowUp /></Button>
                  <Button size="icon" variant="ghost" disabled={index === order.length - 1} onClick={() => move(index, index + 1)} aria-label="Move down"><ArrowDown /></Button>
                  <Button size="sm" disabled={lead.state === 'blocked'} loading={callNow.isPending && callNow.variables === lead.id} onClick={() => callNow.mutate(lead.id)}><PhoneCall />Call now</Button>
                  <Button size="icon" variant="ghost" onClick={() => remove.mutate([lead.id])} title="Remove from queue" aria-label="Remove"><Trash2 /></Button>
                </div>
              </li>
            ))}
          </ol>
        )}
        {order.length > 0 && <div className="border-t border-border px-5 py-2 text-xs text-muted"><Badge tone="success" dot>Live</Badge> <span className="ml-1">Positions save instantly. Leads just called wait 10 minutes; scheduled callbacks keep their time.</span></div>}
      </Card>
    </div>
  )
}
