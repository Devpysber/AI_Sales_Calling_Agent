import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BellRing, Bell, UserRoundSearch as UserQuestion, CalendarCheck, Clock, CreditCard, ExternalLink, Flame, PhoneOff, PlugZap, RefreshCw, Timer, X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useLocation } from 'react-router-dom'
import { Button, Dialog } from '@/components/ui'
import { api } from '@/lib/api'
import { cn, timeAgo } from '@/lib/utils'

type Level = 'danger' | 'warning' | 'success' | 'info'
type Alert = { key: string; agent_id: number | null; agent?: string | null; kind: string; level: Level; count: number; text: string; to: string; action?: string | null; external?: boolean }
type Balance = { provider: string; label: string; value: string; level: 'ok' | 'low' | 'critical' | 'unknown'; detail: string; facts: [string, string][]; action: { label: string; url: string } | null }
type Summary = { items: Alert[]; snoozed: number; balances: Balance[]; checked_at: number; attention: number; popup: Balance | null }

const ICONS: Record<string, typeof Bell> = {
  callback_soon: Timer, callback: Clock, meeting_today: CalendarCheck, meeting_tomorrow: CalendarCheck, invalid_phone: PhoneOff,
  unreachable: AlertTriangle, warm_idle: Flame, credit: CreditCard, inbound: PlugZap, caller_unknown: UserQuestion,
}
const TONE: Record<Level, string> = { danger: 'text-danger bg-danger-soft', warning: 'text-warning bg-warning-soft', success: 'text-success bg-success-soft', info: 'text-fg-2 bg-surface-2' }
const LEVEL_DOT = { ok: 'bg-success', low: 'bg-warning', critical: 'bg-danger', unknown: 'bg-muted' }

export default function AlertsBell({ compact }: { compact: boolean }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'reminders' | 'balances'>('reminders')
  const ref = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const qc = useQueryClient()
  const { data, isFetching, refetch } = useQuery({
    queryKey: ['system', 'alerts'], queryFn: () => api<Summary>('/api/system/alerts'), refetchInterval: 60_000, staleTime: 30_000,
  })
  const refresh = useMutation({
    mutationFn: () => api<Summary>('/api/system/alerts', { params: { refresh: true } }),
    onSuccess: (d) => qc.setQueryData(['system', 'alerts'], d),
  })
  const snooze = useMutation({
    mutationFn: ({ key, hours }: { key: string; hours: number }) => api('/api/system/alerts/snooze', { method: 'POST', json: { key, hours } }),
    onSuccess: () => void refetch(),
  })
  useEffect(() => { setOpen(false) }, [location.pathname])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !panel.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  // Tolerate an older API response while the server restarts
  const items = Array.isArray(data?.items) ? data!.items : []
  const urgent = data?.attention ?? 0
  const link = (a: Alert) => (a.agent_id ? `/a/${a.agent_id}${a.to}` : a.to)
  const sections: [string, Alert[]][] = [
    ['Needs action', items.filter((i) => i.level === 'danger' || i.level === 'warning')],
    ['Today & upcoming', items.filter((i) => i.level === 'success' || i.level === 'info')],
  ]
  const lowBalances = (Array.isArray(data?.balances) ? data.balances : []).filter((b) => b.level === 'low' || b.level === 'critical').length

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} title="Reminders & balances" aria-label="Reminders"
        className={cn('relative grid size-9 place-items-center rounded-xl hover:bg-ink-fg/5 hover:text-ink-fg', urgent ? 'text-ink-fg' : 'text-ink-muted')}>
        {urgent ? <BellRing className="size-4" /> : <Bell className="size-4" />}
        {items.length > 0 && (
          <span className={cn('absolute -top-0.5 -right-0.5 grid min-w-4 place-items-center rounded-full px-1 text-[10px] font-bold text-white tabular-nums', urgent ? 'bg-danger' : 'bg-brand')}>{items.length}</span>
        )}
      </button>

      {open && createPortal(
        <div ref={panel} className="fixed bottom-4 left-4 z-[70] w-[min(380px,calc(100vw-2rem))] animate-pop-in overflow-hidden rounded-2xl border border-border bg-elevated text-fg shadow-pop lg:left-[calc(var(--sidebar-w,272px)+12px)]"
          style={{ ['--sidebar-w' as string]: compact ? '76px' : '272px' }}>
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-bold">Reminders & balances</div>
              <div className="truncate text-xs text-muted">
                {data?.checked_at ? `Checked ${timeAgo(new Date(data.checked_at * 1000).toISOString())}` : data ? 'Checked just now' : 'Loading…'}{data?.snoozed ? ` · ${data.snoozed} snoozed` : ''}
              </div>
            </div>
            <button type="button" onClick={() => refresh.mutate()} title="Fetch live balances now" className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-2 hover:text-fg">
              <RefreshCw className={cn('size-3.5', (refresh.isPending || isFetching) && 'animate-spin')} />
            </button>
            <button type="button" onClick={() => setOpen(false)} className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-2" aria-label="Close"><X className="size-4" /></button>
          </div>
          <div className="flex gap-1 border-b border-border px-3 py-2">
            {([['reminders', `Reminders${items.length ? ` · ${items.length}` : ''}`]] as const)
              .concat((data?.balances?.length ? [['balances', `Balances${lowBalances ? ` · ${lowBalances} low` : ''}`]] : []) as any)
              .map(([k, l]) => (
              <button key={k} type="button" onClick={() => setTab(k as any)} className={cn('rounded-lg px-2.5 py-1 text-xs font-semibold', tab === k ? 'bg-fg text-bg' : 'text-muted hover:bg-surface-2')}>{l}</button>
            ))}
          </div>

          <div className="max-h-[420px] overflow-y-auto p-2">
            {tab === 'reminders' && (items.length ? sections.map(([title, list]) => list.length > 0 && (
              <div key={title} className="mb-1">
                <div className="px-2 pt-1 pb-1 text-[10.5px] font-bold tracking-wider text-muted uppercase">{title}</div>
                {list.map((a) => {
                  const Icon = ICONS[a.kind] ?? Bell
                  return (
                    <div key={a.key} className="group flex items-start gap-2.5 rounded-xl px-2 py-2 hover:bg-surface-2">
                      <span className={cn('mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg', TONE[a.level])}><Icon className="size-3.5" /></span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] leading-snug">{a.text}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px]">
                          {a.agent && <span className="truncate text-muted">{a.agent}</span>}
                          {a.action && (a.external
                            ? <a href={a.to} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-brand hover:underline">{a.action}<ExternalLink className="size-3" /></a>
                            : <Link to={link(a)} className="font-semibold text-brand hover:underline">{a.action} →</Link>)}
                          <span className="ml-auto flex gap-1 opacity-0 transition group-hover:opacity-100">
                            {[['1h', 1], ['Tomorrow', 16]].map(([l, h]) => (
                              <button key={l} type="button" onClick={() => snooze.mutate({ key: a.key, hours: h as number })}
                                className="rounded-md border border-border px-1.5 py-px text-[10.5px] text-muted hover:text-fg">Snooze {l}</button>
                            ))}
                          </span>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )) : <p className="px-2 py-8 text-center text-sm text-muted">All clear. Nothing needs your attention.</p>)}

            {tab === 'balances' && (Array.isArray(data?.balances) ? data.balances : []).map((b) => (
              <div key={b.provider} className="mb-2 rounded-xl border border-border p-3">
                <div className="flex items-center gap-2">
                  <span className={cn('size-2 rounded-full', LEVEL_DOT[b.level])} />
                  <span className="text-sm font-bold">{b.provider}</span>
                  <span className="truncate text-xs text-muted">{b.label}</span>
                  <span className={cn('ml-auto text-sm font-extrabold tabular-nums', b.level === 'critical' ? 'text-danger' : b.level === 'low' ? 'text-warning' : 'text-fg')}>{b.value}</span>
                </div>
                <p className="mt-1 text-xs text-fg-2">{b.detail}</p>
                {b.facts?.length > 0 && (
                  <dl className="mt-2 space-y-0.5 text-[11.5px]">
                    {b.facts.map(([k, v]) => <div key={k} className="flex gap-2"><dt className="w-28 shrink-0 text-muted">{k}</dt><dd className="min-w-0 text-fg-2">{v}</dd></div>)}
                  </dl>
                )}
                {b.action && <a href={b.action.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-brand hover:underline">{b.action.label}<ExternalLink className="size-3" /></a>}
              </div>
            ))}
          </div>
        </div>
      , document.body)}

      <LowCreditPopup popup={data?.popup && Array.isArray(data.popup.facts) ? data.popup : null} onSnooze={(hours) => data?.popup && snooze.mutate({ key: `popup:${data.popup.provider}`, hours })} />
    </div>
  )
}

/** Blocking reminder when a provider is about to run out (e.g. Plivo covers under an hour of calls). */
function LowCreditPopup({ popup, onSnooze }: { popup: Balance | null; onSnooze: (hours: number) => void }) {
  const [dismissed, setDismissed] = useState<string | null>(null)
  const open = !!popup && dismissed !== `${popup.provider}:${popup.value}`
  if (!popup) return null
  const close = (hours: number) => { setDismissed(`${popup.provider}:${popup.value}`); onSnooze(hours) }
  return (
    <Dialog open={open} onClose={() => close(1)} title={`${popup.provider} is almost out of credit`}
      description={`${popup.value} left · ${popup.detail}. Calls stop when it runs out.`}
      footer={<>
        <Button onClick={() => close(1)}>Remind me in 1 hour</Button>
        <Button onClick={() => close(12)}>Tomorrow</Button>
        {popup.action && <a href={popup.action.url} target="_blank" rel="noreferrer"><Button variant="primary"><ExternalLink />{popup.action.label}</Button></a>}
      </>}>
      <dl className="space-y-1 text-sm">
        {popup.facts.map(([k, v]) => <div key={k} className="flex gap-3"><dt className="w-32 shrink-0 text-muted">{k}</dt><dd className="text-fg">{v}</dd></div>)}
      </dl>
    </Dialog>
  )
}
