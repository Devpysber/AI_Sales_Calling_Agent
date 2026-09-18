import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Building2, CalendarClock, ChevronLeft, ChevronRight, Columns3, Flame, GripVertical, PhoneCall, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { useStartCall } from '@/components/LeadSheets'
import { CallStatusBadge, QualificationBadge } from '@/components/status'
import { Avatar, Button, Card, Input, PageHeader, Select, Skeleton, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import { AnimatedNumber } from '@/lib/motion'
import { useAgent } from '@/lib/agent'
import { useDebounced } from '@/lib/useDebounced'
import type { Board, Lead } from '@/lib/types'
import { cn, QUALIFICATIONS, timeAgo } from '@/lib/utils'

const STAGES = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won', 'Closed Lost', 'Not Interested', 'Do Not Call']
const OPEN = STAGES.slice(0, 6)

const LIVE_CALL = ['Queued', 'Ringing', 'In Progress']
const RECENT_MS = 3 * 60_000

function LeadCard({ lead, onOpen, onDragStart, onMove }: { lead: Lead; onOpen: () => void; onDragStart: (e: React.DragEvent) => void; onMove: (status: string) => void }) {
  const startCall = useStartCall()
  const onCall = LIVE_CALL.includes(lead.call_status ?? '')
  const fresh = !onCall && Date.now() - Date.parse(lead.updated_at) < RECENT_MS
  return (
    <div draggable onDragStart={onDragStart} role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === 'Enter' && onOpen()}
      className={cn('group min-w-0 shrink-0 cursor-grab rounded-2xl border bg-surface p-3.5 shadow-card transition duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-pop active:scale-[.98] active:cursor-grabbing',
        onCall ? 'beam beam-on beam-live border-success ring-2 ring-success/25' : fresh ? 'border-brand/50 animate-pop-in' : 'border-border')}>
      {(onCall || fresh) && (
        <div className={cn('mb-2 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10.5px] font-bold', onCall ? 'bg-success-soft text-success' : 'bg-surface-2 text-fg-2')}>
          <span className={cn('size-1.5 rounded-full', onCall ? 'animate-pulse bg-success' : 'bg-brand')} />
          {onCall ? (lead.call_status === 'In Progress' ? 'On a call now' : `${lead.call_status}…`) : `Updated ${timeAgo(lead.updated_at)}`}
        </div>
      )}
      <div className="flex items-start gap-2.5">
        <Avatar name={lead.name ?? lead.phone} className="size-8 text-[11px]" />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-start gap-1.5 text-[13.5px] font-bold break-words">{lead.name ?? 'Unnamed'}{lead.do_not_call && <Ban className="mt-0.5 size-3 shrink-0 text-danger" />}</div>
          <div className="font-mono text-[11.5px] text-fg-2">{lead.phone}</div>
          {lead.company && <div className="flex items-start gap-1 text-[11.5px] break-words text-muted"><Building2 className="mt-0.5 size-3 shrink-0" />{lead.company}</div>}
          {(lead.city || lead.email) && <div className="text-[11.5px] break-all text-muted">{[lead.city, lead.email].filter(Boolean).join(' · ')}</div>}
        </div>
        <GripVertical className="size-4 shrink-0 text-muted opacity-0 transition group-hover:opacity-100" />
      </div>
      {lead.summary && <p className="mt-2.5 text-[12px] leading-snug break-words whitespace-pre-wrap text-fg-2">{lead.summary}</p>}
      {lead.requirements && <p className="mt-1.5 text-[11.5px] leading-snug break-words text-muted"><span className="font-semibold text-fg-2">Needs: </span>{lead.requirements}</p>}
      {lead.meeting_at && <div className="mt-2.5 flex min-w-0 items-center gap-1.5 rounded-lg bg-success-soft px-2 py-1 text-[11.5px] font-semibold text-success"><CalendarClock className="size-3 shrink-0" />Meeting {lead.meeting_at}</div>}
      {lead.callback_at && <div className="mt-1.5 flex min-w-0 items-center gap-1.5 rounded-lg bg-brand-soft px-2 py-1 text-[11.5px] font-semibold text-brand"><CalendarClock className="size-3 shrink-0" />Callback {lead.callback_at}</div>}
      {lead.tags.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{lead.tags.map((t) => <span key={t} className="rounded-md bg-surface-2 px-1.5 py-px text-[10.5px] text-fg-2">{t}</span>)}</div>}
      <div className="mt-3 flex min-w-0 flex-wrap items-center gap-1.5">
        <QualificationBadge value={lead.qualification} />
        {lead.call_status && <CallStatusBadge status={lead.call_status} />}
      </div>
      {/* Actions on their own row so badges never push them out of narrow columns */}
      <div className="mt-2 flex min-w-0 items-center gap-1.5">
        {/* Keyboard / touch alternative to dragging */}
        <select aria-label="Move to stage" value={lead.status} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}
          onChange={(e) => onMove(e.target.value)}
          className="h-7 min-w-0 flex-1 rounded-lg border border-border bg-surface px-1 text-[11px] text-fg-2 opacity-70 transition group-hover:opacity-100 focus:opacity-100">
          {STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button type="button" disabled={lead.do_not_call || onCall} title="Call now"
          onClick={(e) => { e.stopPropagation(); startCall.mutate(lead.id) }}
          className="grid size-7 shrink-0 place-items-center rounded-lg bg-fg text-bg opacity-70 transition group-hover:opacity-100 disabled:hidden">
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
  const q = useDebounced(search)
  const [qualification, setQualification] = useState('')
  const [scope, setScope] = useState<'open' | 'all'>('open')
  const [dragging, setDragging] = useState<{ id: number; from: string } | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const pointer = useRef<{ x: number; y: number; column: HTMLElement | null } | null>(null)

  // While dragging, scroll the board near its left/right edge and a column near its top/bottom (faster closer to the edge)
  useEffect(() => {
    if (!dragging) return
    let frame = 0
    const EDGE = 90, MAX = 22
    const tick = () => {
      const board = boardRef.current, p = pointer.current
      if (board && p) {
        const r = board.getBoundingClientRect()
        const left = p.x - r.left, right = r.right - p.x
        if (left < EDGE) board.scrollLeft -= Math.ceil(MAX * (1 - Math.max(left, 0) / EDGE))
        else if (right < EDGE) board.scrollLeft += Math.ceil(MAX * (1 - Math.max(right, 0) / EDGE))
        const col = p.column
        if (col) {
          const c = col.getBoundingClientRect()
          const top = p.y - c.top, bottom = c.bottom - p.y
          if (top < 60) col.scrollTop -= Math.ceil(14 * (1 - Math.max(top, 0) / 60))
          else if (bottom < 60) col.scrollTop += Math.ceil(14 * (1 - Math.max(bottom, 0) / 60))
        }
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    const track = (e: DragEvent) => {
      const el = (e.target as HTMLElement | null)?.closest?.('[data-column-scroll]') as HTMLElement | null
      pointer.current = { x: e.clientX, y: e.clientY, column: el }
    }
    window.addEventListener('dragover', track)
    return () => { cancelAnimationFrame(frame); window.removeEventListener('dragover', track); pointer.current = null }
  }, [dragging])
  const scrollBoard = (dir: 1 | -1) => boardRef.current?.scrollBy({ left: dir * 340, behavior: 'smooth' })

  const board = useQuery({
    queryKey: ['leads', 'board', q, qualification],
    queryFn: () => api<Board>(`${base}/leads/board`, { params: { search: q, qualification, per_column: 100 } }),
    placeholderData: keepPreviousData,
    // Live board: the AI moves leads after every call; faster while a call is running
    refetchInterval: (q) => (Object.values(q.state.data ?? {}).some((c) => c.items.some((l) => LIVE_CALL.includes(l.call_status ?? ''))) ? 2000 : 4000),
    refetchIntervalInBackground: false,
  })

  const move = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api<Lead>(`${base}/leads/${id}`, { method: 'PATCH', json: { status } }),
    onMutate: async ({ id, status }) => {
      const key = ['leads', 'board', q, qualification]
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
        description={<span className="inline-flex flex-wrap items-center gap-x-2">Drag leads between stages or use a card's stage menu. The AI moves them automatically after each call; you can always override.
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-success"><span className="size-1.5 animate-pulse rounded-full bg-success" />Live</span></span>}
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
                <span key={l as string} className="rounded-full border border-border bg-surface px-3 py-1.5 font-semibold text-muted">{l} <b className="ml-1 text-fg tabular-nums">{typeof v === 'number' ? <AnimatedNumber value={v} /> : v}</b></span>
              ))}
            </div>
          )}
        </div>
      </PageHeader>

      {/* Stacked on phones; from tablets up one row of full-detail columns with an outer horizontal scrollbar */}
      <div className="mb-2 hidden items-center justify-end gap-1 sm:flex">
        <span className="mr-2 text-xs text-muted">Drag a card to a board edge to scroll</span>
        <button type="button" onClick={() => scrollBoard(-1)} aria-label="Scroll left" className="grid size-8 place-items-center rounded-lg border border-border bg-surface text-fg-2 hover:text-fg"><ChevronLeft className="size-4" /></button>
        <button type="button" onClick={() => scrollBoard(1)} aria-label="Scroll right" className="grid size-8 place-items-center rounded-lg border border-border bg-surface text-fg-2 hover:text-fg"><ChevronRight className="size-4" /></button>
      </div>
      <div ref={boardRef} onDragEnd={() => { setDragging(null); setOver(null) }}
        className="-mx-4 overflow-x-auto px-4 pb-4 [scrollbar-width:thin] sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-col gap-4 sm:w-max sm:flex-row">
          {columns.map((stage, ci) => {
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
                style={{ animationDelay: `${ci * 70}ms` }}
                className={cn('reveal reveal-in reveal-up flex w-full flex-col rounded-[var(--radius-card)] border bg-surface-2/60 transition sm:w-80 sm:shrink-0', isOver ? 'is-drop-target border-fg bg-surface-2' : 'border-transparent')}>
                <div className="flex items-center gap-2 px-3.5 pt-3.5 pb-2">
                  <span className={cn('size-2 rounded-full', (stage === 'Closed Won' || stage === 'Meeting Booked') && (col?.total ?? 0) > 0 && 'animate-pulse-dot', stage === 'Closed Won' || stage === 'Meeting Booked' ? 'bg-success' : ['Not Interested', 'Do Not Call', 'Closed Lost'].includes(stage) ? 'bg-danger' : 'bg-fg')} />
                  <h3 className="flex-1 text-[13px] font-extrabold">{stage}</h3>
                  {hot > 0 && <span className="flex items-center gap-0.5 text-[11px] font-bold text-danger"><Flame className="size-3" />{hot}</span>}
                  <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] font-bold tabular-nums ring-1 ring-border">{col?.total ?? '…'}</span>
                </div>
                <div data-column-scroll className="flex max-h-[70vh] min-h-24 flex-col gap-2.5 overflow-y-auto px-2.5 pb-3 [scrollbar-width:thin] sm:min-h-40 sm:max-h-[calc(100vh-300px)]">
                  {board.isLoading ? [0, 1].map((i) => <Skeleton key={i} className="h-28" />)
                    : col?.items.length ? col.items.map((lead, li) => (
                      <div key={lead.id} className="reveal reveal-in reveal-up" style={{ animationDelay: `${ci * 70 + 120 + Math.min(li, 6) * 50}ms` }}>
                      <LeadCard lead={lead} onOpen={() => navigate(path(`/leads/${lead.id}`))}
                        onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; setDragging({ id: lead.id, from: stage }) }}
                        onMove={(status) => status !== lead.status && move.mutate({ id: lead.id, status })} />
                      </div>
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
