import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Bell, CalendarCheck, Clock, CreditCard, PhoneOff, PlugZap, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

type Alert = { agent_id: number | null; agent?: string | null; kind: string; level: 'danger' | 'warning' | 'success' | 'info'; count: number; text: string; to: string }
type Credit = { provider: string; label: string; value: string; low: boolean; hint: string }
type Summary = { items: Alert[]; credits: Credit[]; attention: number }

const ICONS: Record<string, typeof Bell> = { callback: Clock, meeting: CalendarCheck, invalid: PhoneOff, unreachable: AlertTriangle, credit: CreditCard, inbound: PlugZap }
const TONE = { danger: 'text-danger bg-danger-soft', warning: 'text-warning bg-warning-soft', success: 'text-success bg-success-soft', info: 'text-fg-2 bg-surface-2' }

/** Reminders across agents and provider balances, polled once a minute (balances are cached server-side). */
export default function AlertsBell({ compact }: { compact: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const { data } = useQuery({ queryKey: ['system', 'alerts'], queryFn: () => api<Summary>('/api/system/alerts'), refetchInterval: 60_000, staleTime: 30_000 })
  useEffect(() => { setOpen(false) }, [location.pathname])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  const items = data?.items ?? []
  const urgent = data?.attention ?? 0
  const link = (a: Alert) => (a.agent_id ? `/a/${a.agent_id}${a.to}` : a.to)

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} title="Reminders & balances" aria-label="Reminders"
        className="relative grid size-9 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg">
        <Bell className="size-4" />
        {items.length > 0 && (
          <span className={cn('absolute -top-0.5 -right-0.5 grid min-w-4 place-items-center rounded-full px-1 text-[10px] font-bold text-white tabular-nums', urgent ? 'bg-danger' : 'bg-brand')}>
            {items.length}
          </span>
        )}
      </button>

      {open && (
        <div className={cn('absolute bottom-11 z-50 w-80 animate-pop-in overflow-hidden rounded-2xl border border-border bg-elevated text-fg shadow-pop', compact ? 'left-0' : '-left-2')}>
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div><div className="text-sm font-bold">Reminders</div><div className="text-xs text-muted">{items.length ? `${items.length} item${items.length > 1 ? 's' : ''}${urgent ? ` · ${urgent} need action` : ''}` : 'All clear'}</div></div>
            <button type="button" onClick={() => setOpen(false)} className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-2" aria-label="Close"><X className="size-4" /></button>
          </div>
          <div className="max-h-72 overflow-y-auto p-2">
            {items.length ? items.map((a, i) => {
              const Icon = ICONS[a.kind] ?? Bell
              return (
                <Link key={i} to={link(a)} className="flex items-start gap-2.5 rounded-xl px-2 py-2 hover:bg-surface-2">
                  <span className={cn('mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg', TONE[a.level])}><Icon className="size-3.5" /></span>
                  <span className="min-w-0 flex-1"><span className="block text-[13px] leading-snug">{a.text}</span>{a.agent && <span className="block truncate text-[11px] text-muted">{a.agent}</span>}</span>
                </Link>
              )
            }) : <p className="px-2 py-6 text-center text-sm text-muted">Nothing needs your attention.</p>}
          </div>
          <div className="border-t border-border bg-surface-2/50 px-4 py-3">
            <div className="mb-2 text-[11px] font-bold tracking-wider text-muted uppercase">Balances</div>
            <div className="space-y-1.5">
              {(data?.credits ?? []).map((c) => (
                <div key={c.provider} className="flex items-center gap-2 text-[13px]" title={c.hint}>
                  <span className={cn('size-1.5 rounded-full', c.low ? 'bg-warning' : 'bg-success')} />
                  <span className="font-semibold">{c.provider}</span><span className="truncate text-xs text-muted">{c.label}</span>
                  <span className={cn('ml-auto shrink-0 tabular-nums', c.low && 'font-semibold text-warning')}>{c.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
