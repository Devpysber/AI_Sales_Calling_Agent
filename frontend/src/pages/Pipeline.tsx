import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Building2, CalendarClock, Columns3, Flame, GripVertical, PhoneCall, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useStartCall } from '@/components/LeadSheets'
import { CallStatusBadge, QualificationBadge } from '@/components/status'
import { Avatar, Button, Card, Input, PageHeader, Select, Skeleton, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { Board, Lead } from '@/lib/types'
import { cn, QUALIFICATIONS, timeAgo } from '@/lib/utils'

const STAGES = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won', 'Closed Lost', 'Not Interested', 'Do Not Call']
const OPEN = STAGES.slice(0, 6)

function LeadCard({ lead, onOpen, onDragStart }: { lead: Lead; onOpen: () => void; onDragStart: (e: React.DragEvent) => void }) {
  const startCall = useStartCall()
  return (
    <div draggable onDragStart={onDragStart} role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === 'Enter' && onOpen()}
      className="group cursor-grab rounded-2xl border border-border bg-surface p-3.5 shadow-card transition hover:border-border-strong active:cursor-grabbing">
      <div className="flex items-start gap-2.5">
        <Avatar name={lead.name ?? lead.phone} className="size-8 text-[11px]" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 truncate text-[13.5px] font-bold">{lead.name ?? 'Unnamed'}{lead.do_not_call && <Ban className="size-3 text-danger" />}</div>
          <div className="flex items-center gap-1 truncate text-[11.5px] text-muted">{lead.company ? <><Building2 className="size-3 shrink-0" />{lead.company}</> : lead.phone}</div>
        </div>
        <GripVertical className="size-4 shrink-0 text-muted opacity-0 transition group-hover:opacity-100" />
      </div>
      {lead.summary && <p className="mt-2.5 line-clamp-2 text-[12px] leading-snug text-fg-2">{lead.summary}</p>}
      {lead.meeting_at && <div className="mt-2.5 flex items-center gap-1.5 rounded-lg bg-success-soft px-2 py-1 text-[11.5px] font-semibold text-success"><CalendarClock className="size-3" />{lead.meeting_at}</div>}
      <div className="mt-3 flex items-center gap-1.5">
        <QualificationBadge value={lead.qualification} />
        {lead.call_status && <CallStatusBadge status={lead.call_status} />}
        <div className="flex-1" />
        <button type="button" disabled={lead.do_not_call} title="Call now"
          onClick={(e) => { e.stopPropagation(); startCall.mutate(lead.id) }}
          className="grid size-7 place-items-center rounded-lg bg-fg text-bg opacity-0 transition group-hover:opacity-100 disabled:hidden">
          <PhoneCall className="size-3.5" />
        </button>
      </div>
      <div className="mt-2 text-[10.5px] text-muted">{lead.last_contacted_at ? `Contacted ${timeAgo(lead.last_contacted_at)}` : `Added ${timeAgo(lead.created_at)}`}</div>
    </div>
  )
}

export default function Pipeline() {
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [qualification, setQualification] = useState('')
  const [scope, setScope] = useState<'open' | 'all'>('open')
  const [dragging, setDragging] = useState<{ id: number; from: string } | null>(null)
  const [over, setOver] = useState<string | null>(null)

  const board = useQuery({
    queryKey: ['leads', 'board', search, qualification],
    queryFn: () => api<Board>(`${base}/leads/board`, { params: { search, qualification, per_column: 100 } }),
    refetchInterval: 15000,
  })

  const move = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api<Lead>(`${base}/leads/${id}`, { method: 'PATCH', json: { status } }),
    onMutate: async ({ id, status }) => {
      const key = ['leads', 'board', search, qualification]
      await qc.cancelQueries({ queryKey: key })
      const prev = qc.getQueryData<Board>(key)
      if (prev) {
        const next: Board = Object.fromEntries(Object.entries(prev).map(([k, v]) => [k, { ...v, items: [...v.items] }]))
        let lead: Lead | undefined
        for (const col of Object.values(next)) {
          const i = col.items.findIndex((l) => l.id === id)
          if (i >= 0) { lead = col.items.splice(i, 1)[0]; col.total -= 1 }
        }
        if (lead && next[status]) { next[status]!.items.unshift({ ...lead, status }); next[status]!.total += 1 }
        qc.setQueryData(key, next)
      }
      return { prev, key }
    },
    onError: (e, _v, ctx) => { if (ctx?.prev) qc.setQueryData(ctx.key, ctx.prev); toast.error(e.message) },
    onSuccess: (lead) => toast.success(`${lead.name ?? 'Lead'} moved to ${lead.status}`),
    onSettled: () => qc.invalidateQueries({ queryKey: ['leads'] }),
  })

  const columns = scope === 'open' ? OPEN : STAGES
  const data = board.data
  const totals = useMemo(() => {
    if (!data) return null
    const all = Object.values(data).reduce((n, c) => n + c.total, 0)
    const open = OPEN.reduce((n, s) => n + (data[s]?.total ?? 0), 0)
    const won = data['Closed Won']?.total ?? 0
    const meetings = data['Meeting Booked']?.total ?? 0
    return { all, open, won, meetings, winRate: all ? Math.round((100 * won) / all) : 0 }
  }, [data])

  return (
    <>
      <PageHeader
        eyebrow={<><Columns3 className="size-3.5" />{agent?.name} · Deals</>}
        title="Pipeline"
        description="Drag leads between stages. The AI moves them automatically after each call; you can always override."
        actions={<Tabs value={scope} onChange={setScope} items={[{ value: 'open', label: 'Open stages' }, { value: 'all', label: 'All stages' }]} />}>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-60 flex-1 sm:max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter by name, company, phone, tag…" className="pl-9" />
          </div>
          <Select value={qualification} onChange={(e) => setQualification(e.target.value)} className="w-auto">
            <option value="">Any temperature</option>{QUALIFICATIONS.map((x) => <option key={x}>{x}</option>)}
          </Select>
          {totals && (
            <div className="ml-auto flex flex-wrap gap-2 text-[13px]">
              {[['In pipeline', totals.open], ['Meetings', totals.meetings], ['Won', totals.won], ['Win rate', `${totals.winRate}%`]].map(([l, v]) => (
                <span key={l as string} className="rounded-full border border-border bg-surface px-3 py-1.5 font-semibold text-muted">{l} <b className="ml-1 text-fg tabular-nums">{v}</b></span>
              ))}
            </div>
          )}
        </div>
      </PageHeader>

      <div className="-mx-4 overflow-x-auto px-4 pb-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex gap-4" style={{ minWidth: 'max-content' }}>
          {columns.map((stage) => {
            const col = data?.[stage]
            const hot = col?.items.filter((l) => l.qualification === 'Hot').length ?? 0
            const isOver = over === stage && dragging?.from !== stage
            return (
              <div key={stage}
                onDragOver={(e) => { if (dragging) { e.preventDefault(); setOver(stage) } }}
                onDragLeave={() => setOver((o) => (o === stage ? null : o))}
                onDrop={(e) => {
                  e.preventDefault()
                  if (dragging && dragging.from !== stage) move.mutate({ id: dragging.id, status: stage })
                  setDragging(null); setOver(null)
                }}
                className={cn('flex w-72 shrink-0 flex-col rounded-[var(--radius-card)] border bg-surface-2/60 transition', isOver ? 'border-fg bg-surface-2' : 'border-transparent')}>
                <div className="flex items-center gap-2 px-3.5 pt-3.5 pb-2">
                  <span className={cn('size-2 rounded-full', stage === 'Closed Won' || stage === 'Meeting Booked' ? 'bg-success' : ['Not Interested', 'Do Not Call', 'Closed Lost'].includes(stage) ? 'bg-danger' : 'bg-fg')} />
                  <h3 className="flex-1 text-[13px] font-extrabold">{stage}</h3>
                  {hot > 0 && <span className="flex items-center gap-0.5 text-[11px] font-bold text-danger"><Flame className="size-3" />{hot}</span>}
                  <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] font-bold tabular-nums ring-1 ring-border">{col?.total ?? '…'}</span>
                </div>
                <div className="flex max-h-[calc(100vh-320px)] min-h-40 flex-col gap-2.5 overflow-y-auto px-2.5 pb-3">
                  {board.isLoading ? [0, 1].map((i) => <Skeleton key={i} className="h-28" />)
                    : col?.items.length ? col.items.map((lead) => (
                      <LeadCard key={lead.id} lead={lead} onOpen={() => navigate(path(`/leads/${lead.id}`))}
                        onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; setDragging({ id: lead.id, from: stage }) }} />
                    )) : (
                      <div className={cn('grid flex-1 place-items-center rounded-2xl border-2 border-dashed py-10 text-center text-xs font-semibold text-muted', isOver ? 'border-fg' : 'border-border')}>
                        {dragging ? 'Drop here' : 'No leads'}
                      </div>
                    )}
                  {col && col.total > col.items.length && <p className="text-center text-[11px] text-muted">+{col.total - col.items.length} more in Leads</p>}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {data && totals?.all === 0 && (
        <Card className="mt-2 p-8 text-center">
          <p className="font-bold">The pipeline is empty</p>
          <p className="mt-1 text-sm text-muted">Import leads for this agent to start filling it.</p>
          <Button variant="primary" className="mt-4" onClick={() => navigate(path('/import'))}>Import leads</Button>
        </Card>
      )}
    </>
  )
}
