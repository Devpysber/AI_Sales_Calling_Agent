import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Activity, BarChart3, BookOpen, Bot, CalendarClock, Check, ChevronsLeft, ChevronsRight, ChevronsUpDown, Columns3,
  Download, Keyboard, PhoneIncoming, LayoutDashboard, LayoutGrid, LogOut, Lock, Mail, Menu, MessageSquareText, Moon, Pause, PhoneCall, Plus, Search, Settings,
  SlidersHorizontal, Sparkles, Sun, Upload, UserPlus, Users, X,
} from 'lucide-react'
import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate, useNavigationType, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import AlertsBell from '@/components/AlertsBell'
import IncomingCall, { PREVIEW_EVENT } from '@/components/IncomingCall'
import { CommandPalette, type Command } from '@/components/CommandPalette'
import NewAgentSheet from '@/components/NewAgentSheet'
import { Button, Spinner, Input, Dialog } from '@/components/ui'
import { Aurora, VoiceOrb, Waveform } from '@/components/VoiceViz'
import { AnimatedNumber, getMotionSetting, setMotionSetting, type MotionSetting } from '@/lib/motion'
import { api } from '@/lib/api'
import { AgentProvider, useAgents } from '@/lib/agent'
import type { AgentSummary } from '@/lib/types'
import { cn, initials } from '@/lib/utils'

type NavItem = { to: string; label: string; icon: typeof Users; key: string; count?: (a: AgentSummary) => ReactNode }

const AGENT_NAV: { section: string; items: NavItem[] }[] = [
  {
    section: 'Work', items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard, key: 'o' },
      { to: '/leads', label: 'Leads', icon: Users, key: 'l', count: (a) => a.stats.leads || null },
      { to: '/pipeline', label: 'Pipeline', icon: Columns3, key: 'p', count: (a) => a.stats.hot ? <span className="text-danger">{a.stats.hot} hot</span> : null },
      { to: '/calls', label: 'Calls', icon: PhoneCall, key: 'c', count: (a) => a.stats.live ? <span className="rounded-full bg-success px-1.5 text-white">{a.stats.live} live</span> : a.stats.calls_today || null },
      { to: '/inbound', label: 'Inbound & transfer', icon: PhoneIncoming, key: 'i' },
    ],
  },
  {
    section: 'Insights', items: [
      { to: '/analytics', label: 'Analytics', icon: BarChart3, key: 'a' },
      { to: '/activity', label: 'History', icon: Activity, key: 'h' },
      { to: '/emails', label: 'Email service', icon: Mail, key: 'm' },
    ],
  },
  {
    section: 'Build', items: [
      { to: '/agent', label: 'Persona & playground', icon: Bot, key: 'g' },
      { to: '/knowledge', label: 'Knowledge base', icon: BookOpen, key: 'k', count: (a) => a.stats.documents || null },
      { to: '/automation', label: 'Automation', icon: CalendarClock, key: 'u', count: (a) => a.automation_on ? <span className="text-success">On</span> : null },
      { to: '/settings', label: 'Agent settings', icon: SlidersHorizontal, key: 's' },
    ],
  },
]
const TITLES: Record<string, string> = { '/import': 'Import leads' }
AGENT_NAV.forEach((g) => g.items.forEach((i) => { TITLES[i.to] = i.label }))

const SETUP_STEPS: { key: keyof AgentSummary['setup']; label: string; to: string }[] = [
  { key: 'persona', label: 'Write the persona', to: '/agent' },
  { key: 'knowledge', label: 'Add knowledge', to: '/knowledge' },
  { key: 'leads', label: 'Import leads', to: '/import' },
  { key: 'number', label: 'Assign a phone number', to: '/settings' },
  { key: 'automation', label: 'Switch on automation', to: '/automation' },
]

function useStoredBoolean(key: string, initial: boolean) {
  const [value, setValue] = useState(() => { try { const v = localStorage.getItem(key); return v === null ? initial : v === '1' } catch { return initial } })
  useEffect(() => { try { localStorage.setItem(key, value ? '1' : '0') } catch { /* storage unavailable */ } }, [key, value])
  return [value, setValue] as const
}

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    try { localStorage.theme = dark ? 'dark' : 'light' } catch { /* storage unavailable */ }
  }, [dark])
  return [dark, setDark] as const
}

const isTyping = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))

export function AgentMark({ agent, className }: { agent: Pick<AgentSummary, 'name' | 'color'>; className?: string }) {
  return (
    <span className={cn('grid size-9 shrink-0 place-items-center rounded-xl bg-fg text-[13px] font-bold text-bg shadow-sm ring-1 ring-black/5 dark:ring-ink-fg/10', className)}
      data-agent={agent.name}>
      {initials(agent.name)}
    </span>
  )
}

export function LiveDot({ on, className }: { on: boolean; className?: string }) {
  return (
    <span className={cn('relative inline-grid size-2.5 place-items-center', className)}>
      {/* A ring that leaves the dot and decelerates: Tailwind's animate-ping is linear and reads as a strobe. */}
      {on && <span className="absolute inset-0 animate-live-ring rounded-full bg-success/60" />}
      <span className={cn('relative size-2 rounded-full', on ? 'bg-success' : 'bg-ink-muted/50')} />
    </span>
  )
}


/** Motion: follow Windows, always on, or off. Battery saver turns Windows animations off silently. */
function MotionToggle() {
  const [setting, setSetting] = useState<MotionSetting>(getMotionSetting)
  const next: Record<MotionSetting, MotionSetting> = { full: 'off', off: 'system', system: 'full' }
  const label: Record<MotionSetting, string> = { system: 'Motion: follow Windows', full: 'Motion: always on', off: 'Motion: off' }
  return (
    <button type="button" title={`${label[setting]} (click to change)`} aria-label={label[setting]}
      onClick={() => { const n = next[setting]; setMotionSetting(n); setSetting(n); toast(label[n]) }}
      className={cn('grid size-10 place-items-center rounded-xl hover:bg-ink-fg/5 hover:text-ink-fg lg:size-9',
        setting === 'full' ? 'text-ink-fg' : 'text-ink-muted')}>
      <Waveform bars={3} active={setting !== 'off'} className="h-3.5" />
    </button>
  )
}

/* ---------------- Agent switcher ---------------- */

function AgentSwitcher({ agents, current, compact, onNew, canCreate }: { agents: AgentSummary[]; current?: AgentSummary; compact: boolean; onNew: () => void; canCreate: boolean }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const location = useLocation()
  useEffect(() => { setOpen(false); setQ('') }, [location.pathname])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    // stopPropagation: the mobile drawer listens for Escape on window and would close on the same keypress.
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} title={compact ? current?.name ?? 'Switch agent' : undefined}
        className={cn('flex w-full items-center gap-3 rounded-2xl border border-ink-fg/8 bg-ink-2 text-left transition hover:border-ink-fg/15 hover:bg-ink-3',
          compact ? 'justify-center p-1.5' : 'p-2.5')}>
        {current ? <AgentMark agent={current} className={cn('bg-ink-fg text-ink', current.status === 'paused' && 'opacity-60')} />
          : <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-ink-3 text-ink-fg"><LayoutGrid className="size-4" /></span>}
        {!compact && <>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[14px] font-bold text-ink-fg">{current?.name ?? 'All agents'}</div>
            <div className="truncate text-[11.5px] text-ink-muted">{current ? `${current.persona.agent_name} · ${current.persona.company_name}` : `${agents.length} workspace${agents.length === 1 ? '' : 's'}`}</div>
          </div>
          <ChevronsUpDown className="size-4 text-ink-muted" />
        </>}
      </button>

      {open && (
        <div className={cn('absolute z-50 mt-2 w-72 animate-pop-in overflow-hidden rounded-2xl border border-border bg-elevated text-fg shadow-pop', compact ? 'left-0' : 'inset-x-0 w-auto')}>
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search className="size-4 text-muted" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find agent…" className="h-10 flex-1 bg-transparent text-sm outline-none placeholder:text-muted" />
          </div>
          <div className="max-h-80 overflow-y-auto p-1.5">
            <Link to="/" onClick={() => setOpen(false)} className={cn('flex items-center gap-3 rounded-xl px-2.5 py-2 text-sm hover:bg-surface-2', !current && 'bg-surface-2')}>
              <div className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-elevated shadow-sm"><LayoutDashboard className="size-4" /></div>
              <span className="font-semibold">All agents</span>
              {current && <Check className="ml-auto size-4 text-brand" />}
            </Link>
            <div className="my-1.5 px-3 text-[10px] font-bold tracking-wider text-muted uppercase">Agents</div>
            {agents.filter(a => !q || a.name.toLowerCase().includes(q.toLowerCase()) || a.persona.company_name.toLowerCase().includes(q.toLowerCase())).map((a) => (
              <Link key={a.id} to={`/a/${a.id}`} onClick={() => setOpen(false)} className={cn('flex items-center gap-3 rounded-xl px-2.5 py-2 hover:bg-surface-2', a.id === current?.id && 'bg-surface-2')}>
                <AgentMark agent={a} className="size-8 shrink-0 rounded-lg text-xs" />
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="truncate text-[13px] font-semibold">{a.name}</div>
                  <div className="truncate text-[11px] text-muted">{a.stats.leads} leads · {a.stats.calls_today} calls today</div>
                </div>
                {a.id === current?.id ? <Check className="size-4 text-brand" /> : <div className="grid size-5 place-items-center rounded bg-surface text-[10px] font-bold tabular-nums text-muted">{a.id}</div>}
              </Link>
            ))}
          </div>
          {canCreate && (
            <button type="button" onClick={() => { setOpen(false); onNew() }}
              className="flex w-full items-center gap-2 border-t border-border px-4 py-3 text-sm font-semibold text-brand hover:bg-surface-2">
              <Plus className="size-4" />Create new agent
            </button>
          )}
        </div>
      )}
    </div>
  )
}


/**
 * One highlight that glides to whichever nav item is current, instead of each item switching its
 * own background on. The movement shows where you came from and where you went.
 * NavLink marks the current item with aria-current="page"; we measure it inside the scroll area.
 */
function NavGlider({ container, watch }: { container: React.RefObject<HTMLDivElement | null>; watch: string }) {
  const [box, setBox] = useState<{ top: number; height: number; left: number; width: number } | null>(null)
  useLayoutEffect(() => {
    const root = container.current
    if (!root) return
    const measure = () => {
      const el = root.querySelector<HTMLElement>('a[aria-current="page"]')
      if (!el) { setBox(null); return }
      setBox({ top: el.offsetTop, height: el.offsetHeight, left: el.offsetLeft, width: el.offsetWidth })
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(root)
    return () => ro.disconnect()
  }, [container, watch])
  if (!box) return null
  return (
    <span aria-hidden className="pointer-events-none absolute z-0 rounded-xl bg-ink-fg/[0.08] shadow-[inset_0_0_0_1px_rgb(128_128_128/0.12)] transition-[transform,height,width] duration-300 ease-[var(--ease-entrance)] motion-reduce:transition-none"
      style={{ top: 0, left: box.left, width: box.width, height: box.height, transform: `translateY(${box.top}px)` }}>
      <span className="absolute top-1.5 bottom-1.5 left-0 w-[3px] rounded-r-full bg-ink-fg" />
    </span>
  )
}

/* ---------------- Sidebar ---------------- */

function Sidebar({ agents, agent, compact, setCompact, onNew, onPalette, onHelp, onLogout, dark, setDark, user, role, canCreate, mobile, onClose }: {
  agents: AgentSummary[]; agent?: AgentSummary; compact: boolean; setCompact: (c: boolean) => void
  onNew: () => void; onPalette: () => void; onHelp: () => void; onLogout: () => void
  dark: boolean; setDark: (d: boolean) => void; user: string; role: string; canCreate: boolean; mobile?: boolean; onClose?: () => void
}) {
  const location = useLocation()
  const navRef = useRef<HTMLDivElement>(null)
  // The nav list scrolls on its own. After a refresh (or opening a section low in the list) the active
  // item sat below the fold; keep it in view, and keep the list where the user left it across refreshes.
  useEffect(() => {
    const nav = navRef.current
    if (!nav) return
    try {
      const saved = sessionStorage.getItem('nav:scroll')
      if (saved && !nav.dataset.restored) { nav.scrollTop = Number(saved) || 0; nav.dataset.restored = '1' }
    } catch { /* storage unavailable */ }
    const active = nav.querySelector<HTMLElement>('[aria-current="page"]')
    if (active) {
      const r = active.getBoundingClientRect(), box = nav.getBoundingClientRect()
      if (r.top < box.top || r.bottom > box.bottom) active.scrollIntoView({ block: 'nearest' })
    }
    const remember = () => { try { sessionStorage.setItem('nav:scroll', String(nav.scrollTop)) } catch { /* ignore */ } }
    nav.addEventListener('scroll', remember, { passive: true })
    return () => nav.removeEventListener('scroll', remember)
  }, [location.pathname, agent?.id])
  const path = (to: string) => agent ? `/a/${agent.id}${to === '/' ? '' : to}` : to
  const done = agent ? SETUP_STEPS.filter((s) => agent.setup?.[s.key]).length : 0
  const next = agent ? (agent.setup ? SETUP_STEPS.find((s) => !agent.setup[s.key]) : undefined) : undefined
  // Same section (leads, calls…) in the other workspace, without ids that only exist in this one
  const section = agent ? (location.pathname.replace(`/a/${agent.id}`, '').match(/^\/[^/]+/)?.[0] ?? '') : ''
  const connectRate = agent?.stats.calls_today ? Math.round((100 * agent.stats.connected_today) / agent.stats.calls_today) : null

  const navLink = (to: string, label: string, Icon: typeof Users, end: boolean, count?: ReactNode) => (
    <NavLink key={to} to={to} end={end} title={compact ? label : undefined}
      className={({ isActive }) => cn('group relative flex h-10 items-center gap-3 rounded-xl text-[13.5px] font-semibold transition lg:h-9',
        compact ? 'justify-center' : 'px-3',
        isActive ? 'text-ink-fg' : 'text-ink-muted hover:bg-ink-fg/[0.04] hover:text-ink-fg')}>
      {({ isActive }) => <>
        <Icon className={cn('size-[18px] shrink-0 transition-transform duration-200 ease-[var(--ease-pointer)]',
          'group-hover:scale-110 motion-reduce:transition-none motion-reduce:group-hover:scale-100',
          isActive ? 'text-ink-fg' : 'text-ink-muted group-hover:text-ink-fg')} />
        {!compact && <span className="flex-1 truncate">{label}</span>}
        {!compact && count != null && <span className="text-[11px] font-semibold text-ink-muted tabular-nums">{count}</span>}
      </>}
    </NavLink>
  )

  return (
    <div className={cn('relative z-40 flex h-full min-w-0 flex-col border-r border-border text-ink-fg',
      mobile ? 'w-[min(18rem,85vw)] bg-ink' : cn('bg-ink/70 backdrop-blur-2xl transition-[width] duration-200', compact ? 'w-[76px]' : 'w-[272px]'))}>
      <div className={cn('flex items-center gap-2.5 pt-4 pb-3', compact ? 'flex-col px-2' : 'px-4')}>
        <Link to="/" className="grid size-9 shrink-0 place-items-center rounded-xl bg-ink-fg text-ink shadow-sm"><Waveform bars={4} className="h-4" /></Link>
        {!compact && <div className="min-w-0 flex-1 leading-tight"><div className="text-sheen truncate text-[15px] font-extrabold tracking-tight">Samvaad AI</div><div className="truncate text-[11px] text-ink-muted">Multi-agent calling</div></div>}
        {mobile ? (
          <button type="button" onClick={onClose} aria-label="Close menu" data-autofocus
            className="grid size-10 shrink-0 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg">
            <X className="size-5" />
          </button>
        ) : (
          <button type="button" onClick={() => setCompact(!compact)} title={compact ? 'Expand sidebar ( [ )' : 'Collapse sidebar ( [ )'} aria-label={compact ? 'Expand sidebar' : 'Collapse sidebar'}
            className="grid size-9 place-items-center rounded-lg text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg lg:size-8">
            {compact ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
          </button>
        )}
      </div>

      <div className={cn('pb-3', compact ? 'px-2' : 'px-3')}>
        <AgentSwitcher agents={agents} current={agent} compact={compact} onNew={onNew} canCreate={canCreate} />
      </div>

      <div ref={navRef} className={cn('relative min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain pb-4', compact ? 'px-2' : 'px-3')}>
        <NavGlider container={navRef} watch={`${location.pathname}${location.search}|${compact}|${agent?.id ?? ''}`} />
        {agent ? <>
          {/* Live status */}
          {!compact ? (
            <Link to={path('/calls?status=active')} className={cn('beam relative block overflow-hidden rounded-2xl border border-ink-fg/8 bg-gradient-to-br from-ink-2 to-ink-3/40 p-3 transition hover:border-ink-fg/15',
              agent.stats.live > 0 && 'is-live-card beam-on beam-live')}>
              <Aurora className="opacity-60" />
              <div className="relative flex items-center gap-2.5">
                <VoiceOrb state={agent.status === 'paused' ? 'idle' : agent.stats.live > 0 ? 'live' : 'listening'} size={30} />
                <span className="flex-1 text-[13px] font-bold">{agent.stats.live ? `${agent.stats.live} call${agent.stats.live > 1 ? 's' : ''} live` : 'Idle'}</span>
                {agent.status === 'paused'
                  ? <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[10.5px] font-bold text-warning"><Pause className="size-2.5" />Paused</span>
                  : <span className={cn('rounded-full px-2 py-0.5 text-[10.5px] font-bold', agent.within_calling_hours ? 'bg-success/15 text-success' : 'bg-ink-fg/5 text-ink-muted')}>
                    {agent.within_calling_hours ? 'In hours' : 'After hours'}</span>}
              </div>
              <div className="relative mt-3 grid grid-cols-3 gap-2 text-center">
                {([['Today', agent.stats.calls_today], ['Connected', agent.stats.connected_today], ['Rate', connectRate]] as const).map(([l, v]) => (
                  <div key={l} className="rounded-xl bg-ink-fg/[0.04] py-1.5">
                    <div className="text-[15px] font-extrabold tabular-nums">{v === null ? '—' : <AnimatedNumber value={v} suffix={l === 'Rate' ? '%' : ''} />}</div>
                    <div className="text-[10px] font-semibold text-ink-muted">{l}</div>
                  </div>
                ))}
              </div>
            </Link>
          ) : (
            <Link to={path('/calls?status=active')} title={`${agent.stats.live} live`} className="mx-auto grid size-10 place-items-center rounded-xl bg-ink-2">
              <VoiceOrb state={agent.stats.live > 0 ? 'live' : 'listening'} size={26} />
            </Link>
          )}

          {/* Quick actions */}
          {!compact && (
            <div className="grid grid-cols-3 gap-1.5">
              {[[UserPlus, 'Add lead', '/leads?new=1'], [Upload, 'Import', '/import'], [MessageSquareText, 'Test', '/agent?tab=playground']].map(([Icon, l, to]) => {
                const I = Icon as typeof Users
                return (
                  <Link key={l as string} to={path(to as string)} className="group flex flex-col items-center gap-1 rounded-xl border border-ink-fg/6 py-2 text-[11px] font-semibold text-ink-muted transition hover:border-ink-fg/15 hover:bg-ink-fg/[0.04] hover:text-ink-fg">
                    <I className="size-4 transition-transform duration-200 ease-[var(--ease-pointer)] group-hover:-translate-y-0.5 group-hover:scale-110 motion-reduce:transform-none" />{l as string}
                  </Link>
                )
              })}
            </div>
          )}

          {AGENT_NAV.map((group) => {
            // Team members see the full agent menu; admin-only actions (e.g. deleting an agent) are gated on the backend.
            const items = group.items
            if (!items.length) return null
            return (
              <div key={group.section} className="mb-4">
                {!compact && <div className="mb-1 px-3 text-[10.5px] font-bold tracking-wider text-ink-muted/70 uppercase">{group.section}</div>}
                <div className="space-y-0.5">
                  {items.map((i) => navLink(path(i.to), i.label, i.icon, i.to === '/', i.count?.(agent)))}
                </div>
              </div>
            )
          })}

          {/* One-click switch between company workspaces */}
          {agents.length > 1 && (
            <div className={cn('mb-4 rounded-xl border border-border/50 bg-surface-2 p-1', !compact && 'px-2 pb-2.5 pt-2')}>
              {!compact ? <div className="mb-2.5 px-1.5 pt-1 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">Switch workspace</div>
                : <div className="mx-auto mb-2 mt-1 h-px w-6 bg-ink-fg/10" />}
              <div className="space-y-0.5">
                {agents.filter((a) => a.id !== agent.id).slice(0, 8).map((a) => (
                  <Link key={a.id} to={`/a/${a.id}${section}`} title={compact ? a.name : `${a.name} · ${a.persona.company_name}`}
                    className={cn('flex h-10 items-center gap-2.5 rounded-xl text-[13px] font-semibold text-ink-muted transition hover:bg-ink-fg/[0.04] hover:text-ink-fg lg:h-9', compact ? 'justify-center' : 'px-2')}>
                    <span className="relative"><AgentMark agent={a} className={cn('size-6 rounded-md bg-ink-fg text-[9px] text-ink', a.status === 'paused' && 'opacity-60')} />
                      {a.stats.live > 0 && <span className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full bg-success ring-2 ring-ink" />}</span>
                    {!compact && <span className="flex-1 truncate">{a.name}</span>}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {canCreate && (
            <button type="button" onClick={onNew}
              className={cn('group flex items-center justify-center gap-2 rounded-2xl border border-dashed border-ink-fg/20 font-bold text-ink-fg/70 transition hover:border-ink-fg/40 hover:bg-ink-fg/[0.03] hover:text-ink-fg',
                compact ? 'size-10' : 'h-auto w-full py-3')}>
              <Plus className="size-4" />
              {!compact && <div className="flex flex-col items-start"><span className="text-[13px] leading-tight">New agent</span><span className="mt-0.5 text-[10px] font-normal leading-tight opacity-70">For another product, campaign, city or client</span></div>}
            </button>
          )}

          {/* Setup progress */}
          {!compact && next && (
            <Link to={path(next.to)} className="block rounded-2xl border border-dashed border-ink-fg/12 p-3 transition hover:border-ink-fg/40">
              <div className="flex items-center justify-between text-[12px] font-bold"><span className="flex items-center gap-1.5"><Sparkles className="size-3.5 text-ink-fg" />Setup {done}/{SETUP_STEPS.length}</span><span className="text-ink-muted">{Math.round((100 * done) / SETUP_STEPS.length)}%</span></div>
              <div className="mt-2 flex gap-1">{SETUP_STEPS.map((s, i) => (
                <span key={s.key} className="h-1 flex-1 overflow-hidden rounded-full bg-ink-fg/10">
                  {agent.setup?.[s.key] && <span className="grow-x block h-full rounded-full bg-ink-fg" style={{ animationDelay: `${200 + i * 120}ms` }} />}
                </span>
              ))}</div>
              <div className="mt-2 text-[12px] text-ink-muted">Next: <span className="font-semibold text-ink-fg">{next.label}</span> →</div>

            </Link>
          )}
        </> : <>
          <div>
            {!compact && <div className="px-3 pb-1.5 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">Workspace</div>}
            <div className="space-y-0.5">
              {navLink('/', role === 'team' ? 'My agent' : 'All agents', LayoutGrid, true, agents.length)}
              {role !== 'team' && navLink('/settings', 'Integrations & system', Settings, false)}
            </div>
          </div>
          <div>
            {!compact && <div className="px-3 pb-1.5 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">{role === 'team' ? 'My Workspace' : 'Agents'}</div>}
            <div className="space-y-0.5">
              {agents.map((a) => (
                <Link key={a.id} to={`/a/${a.id}`} title={compact ? a.name : undefined}
                  className={cn('flex h-10 items-center gap-3 rounded-xl text-[13.5px] font-semibold text-ink-muted transition hover:bg-ink-fg/[0.04] hover:text-ink-fg', compact ? 'justify-center' : 'px-2')}>
                  <span className="relative"><AgentMark agent={a} className={cn('size-7 rounded-lg bg-ink-fg text-[10px] text-ink', a.status === 'paused' && 'opacity-60')} />
                    {a.stats.live > 0 && <span className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full bg-success ring-2 ring-ink" />}</span>
                  {!compact && <><span className="flex-1 truncate">{a.name}</span><span className="text-[11px] tabular-nums">{a.stats.live ? <span className="text-success">{a.stats.live} live</span> : a.stats.leads}</span></>}
                </Link>
              ))}
              {canCreate && (
                <button type="button" onClick={onNew} title="New agent"
                  className={cn('flex h-10 w-full items-center gap-3 rounded-xl text-[13.5px] font-semibold text-ink-muted transition hover:bg-ink-fg/[0.04] hover:text-ink-fg', compact ? 'justify-center' : 'px-2')}>
                  <span className="grid size-7 place-items-center rounded-lg border border-dashed border-ink-fg/20"><Plus className="size-3.5" /></span>{!compact && 'New agent'}
                </button>
              )}
            </div>
          </div>
        </>}
      </div>

      <div className={cn('border-t border-ink-fg/8', compact ? 'space-y-1 p-2' : 'p-3')}>
        <button type="button" onClick={onPalette} title="Search (Ctrl K)" className={cn('flex h-10 min-w-0 items-center gap-2 rounded-xl text-[13px] text-ink-muted transition hover:bg-ink-fg/5 hover:text-ink-fg lg:h-9', compact ? 'mx-auto w-10 justify-center' : 'mb-1 w-full px-3')}>
          <Search className="size-4 shrink-0" />{!compact && <><span className="flex-1 truncate text-left">Search</span><kbd className="hidden shrink-0 !border-ink-fg/10 !bg-ink-fg/5 whitespace-nowrap !text-ink-muted sm:inline-block">Ctrl K</kbd></>}
        </button>
        <div className={cn('flex items-center', compact ? 'flex-col gap-1' : 'justify-between gap-1')}>
          {agent && role !== 'team' && <Link to="/settings" title="Integrations & system" aria-label="Integrations & system" className={cn('grid size-10 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg lg:size-9', location.pathname === '/settings' && 'text-ink-fg')}><Settings className="size-4" /></Link>}
          <AlertsBell compact={compact} />
          <button type="button" onClick={onHelp} title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts" className="grid size-10 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg lg:size-9"><Keyboard className="size-4" /></button>
          <button type="button" onClick={() => setDark(!dark)} title="Toggle theme" aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'} className="grid size-10 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg lg:size-9">{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</button>
          <MotionToggle />
        </div>
        <div className={cn('mt-2 flex items-center gap-2.5 rounded-xl bg-ink-fg/[0.04] p-2', compact && 'justify-center')}>
          {role === 'team' ? (
            <div title="Team Member" className={cn('flex min-w-0 items-center gap-2.5 rounded-lg', !compact && 'flex-1')}>
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ink-fg text-xs font-bold text-ink uppercase">{user[0]}</span>
              {!compact && <div className="min-w-0 flex-1 leading-tight"><div className="truncate text-[13px] font-bold">{user}</div><div className="truncate text-[11px] text-ink-muted">Team Member</div></div>}
            </div>
          ) : (
            <Link to="/profile" title="Admin profile" className={cn('flex min-w-0 items-center gap-2.5 rounded-lg transition hover:opacity-80', !compact && 'flex-1')}>
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ink-fg text-xs font-bold text-ink uppercase">{user[0]}</span>
              {!compact && <div className="min-w-0 flex-1 leading-tight"><div className="truncate text-[13px] font-bold">{user}</div><div className="truncate text-[11px] text-ink-muted">Administrator · Profile</div></div>}
            </Link>
          )}
          {!compact && <button type="button" onClick={onLogout} title="Sign out" aria-label="Sign out" className="grid size-10 shrink-0 place-items-center rounded-lg text-ink-muted hover:bg-ink-fg/5 hover:text-danger lg:size-8"><LogOut className="size-4" /></button>}
        </div>
      </div>
    </div>
  )
}

/* ---------------- Shell ---------------- */

// Dropped on every agent switch except these: the session, the agent list and system-level state are
// app-wide, and removing them would unmount the tree that renders this shell.
const APP_WIDE_QUERIES = new Set(['agents', 'me', 'system'])

export default function AppShell({ user, role, canCreateAgent }: { user: string; role: string; canCreateAgent: boolean }) {
  const [mobile, setMobile] = useState(false)
  const [compact, setCompact] = useStoredBoolean('sidebar-compact', false)
  const [palette, setPalette] = useState(false)
  const [help, setHelp] = useState(false)
  const [creating, setCreating] = useState(false)
  const [dark, setDark] = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const pending = useRef<{ key: string; at: number } | null>(null)
  // The window is the scroll container (the sidebar is sticky), so a section opened from the sidebar
  // used to keep the previous page's scroll offset and land mid-page. Back/forward keep the browser's own restore.
  const navType = useNavigationType()
  useEffect(() => {
    if (navType === 'POP') return
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior })
  }, [location.pathname, navType])
  const { agentId } = useParams()
  const agentsQuery = useAgents()
  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data])
  const validId = agentId !== undefined && /^[1-9]\d*$/.test(agentId)
  const id = validId ? Number(agentId) : null
  const agent = agents.find((a) => a.id === id)

  const menuButton = useRef<HTMLButtonElement>(null)
  const drawer = useRef<HTMLElement>(null)
  // Any navigation (path or query, e.g. /leads → /leads?new=1) closes the drawer.
  useEffect(() => { setMobile(false) }, [location.key])
  // Drawer: Escape closes, the page behind stops scrolling, focus lands inside and returns to the menu button.
  useEffect(() => {
    if (!mobile) return
    // Escape closes the drawer unless another dialog (palette, sheet) is on top of it.
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !document.querySelector('[role="dialog"]:not([aria-label="Navigation"])')) setMobile(false) }
    // Tab wraps inside the drawer; the page behind the scrim is also `inert` (see the content wrapper).
    const trap = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !drawer.current || document.querySelector('[role="dialog"]:not([aria-label="Navigation"])')) return
      const focusable = Array.from(drawer.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'))
        .filter((el) => el.offsetParent !== null)
      if (!focusable.length) return
      const first = focusable[0]!, last = focusable[focusable.length - 1]!
      const active = document.activeElement as HTMLElement | null
      if (e.shiftKey && (active === first || !drawer.current.contains(active))) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && (active === last || !drawer.current.contains(active))) { e.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', esc)
    window.addEventListener('keydown', trap)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    drawer.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus({ preventScroll: true })
    const mq = window.matchMedia('(min-width: 1024px)')
    const onWide = (e: MediaQueryListEvent) => { if (e.matches) setMobile(false) }
    mq.addEventListener('change', onWide)
    return () => {
      window.removeEventListener('keydown', esc)
      window.removeEventListener('keydown', trap)
      document.body.style.overflow = prevOverflow
      mq.removeEventListener('change', onWide)
      menuButton.current?.focus({ preventScroll: true })
    }
  }, [mobile])

  // Most workspace queries are keyed by path alone ('automation', 'knowledge', 'calls'…), so moving
  // to another agent would show the previous agent's data until each refetch landed. Drop everything
  // workspace-scoped when the agent changes; the agent list itself is shared and stays.
  useEffect(() => {
    qc.removeQueries({ predicate: (query) => !APP_WIDE_QUERIES.has(String(query.queryKey[0])) })
  }, [id, qc])

  const logout = useCallback(async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
    qc.clear()
    navigate('/login')
  }, [navigate, qc])

  const sub = id ? location.pathname.replace(`/a/${id}`, '') || '/' : location.pathname
  // First path segment only: /leads/5 (LeadDetail) still reads "Leads".
  const pageTitle = id ? TITLES[`/${sub.split('/')[1] ?? ''}`] ?? '' : sub === '/settings' ? 'Integrations & system' : sub === '/profile' ? 'Admin profile' : role === 'team' ? 'My agent' : 'All agents'
  useEffect(() => {
    const live = agents.reduce((n, a) => n + a.stats.live, 0)
    const parts = [pageTitle, agent?.name, 'Samvaad AI'].filter(Boolean).join(' · ')
    document.title = `${live ? `(${live} live) ` : ''}${parts}`
  }, [agent, pageTitle, agents])

  const go = useCallback((to: string) => navigate(id ? `/a/${id}${to === '/' ? '' : to}` : to), [id, navigate])

  const commands = useMemo<Command[]>(() => [
    ...agents.map((a) => ({ id: `agent-${a.id}`, group: 'Switch agent', label: a.name, icon: Bot, keywords: `${a.persona.company_name} ${a.description ?? ''}`, run: () => navigate(`/a/${a.id}`) })),
    ...(canCreateAgent ? [{ id: 'new-agent', group: 'Actions', label: 'Create a new agent', icon: Plus, keywords: 'add workspace', run: () => setCreating(true) }] : []),
    ...(id ? [
      ...AGENT_NAV.flatMap((g) => g.items.map((i) => ({ id: i.to, group: 'Go to', label: i.label, icon: i.icon, hint: `G ${i.key.toUpperCase()}`, run: () => go(i.to) }))),
      { id: 'new-lead', group: 'Actions', label: 'Add lead', icon: Plus, hint: 'N', keywords: 'create prospect', run: () => go('/leads?new=1') },
      { id: 'import', group: 'Actions', label: 'Import leads from CSV / Excel', icon: Upload, keywords: 'upload spreadsheet', run: () => go('/import') },
      { id: 'export', group: 'Actions', label: "Export this agent's leads (CSV)", icon: Download, keywords: 'download', run: () => { window.location.href = `/api/agents/${id}/leads/export` } },
    ] : []),
    { id: 'home', group: 'Go to', label: role === 'team' ? 'My agent' : 'All agents', icon: LayoutGrid, run: () => navigate('/') },
    ...(role === 'team' ? [] : [{ id: 'system', group: 'Go to', label: 'Integrations & system', icon: Settings, run: () => navigate('/settings') }]),
    ...(role === 'team' ? [] : [{ id: 'profile', group: 'Go to', label: 'Admin profile & password', icon: UserPlus, keywords: 'account security', run: () => navigate('/profile') }]),
    { id: 'preview-call', group: 'Preferences', label: 'Preview incoming call', icon: PhoneIncoming, keywords: 'demo test ring banner',
      run: () => window.dispatchEvent(new CustomEvent(PREVIEW_EVENT, { detail: { agentId: agent?.id ?? agents[0]?.id ?? 0, agentName: agent?.name ?? agents[0]?.name ?? null } })) },
    { id: 'theme', group: 'Preferences', label: dark ? 'Switch to light theme' : 'Switch to dark theme', icon: dark ? Sun : Moon, keywords: 'dark mode', run: () => setDark(!dark) },
    { id: 'logout', group: 'Preferences', label: 'Sign out', icon: LogOut, run: logout },
  ], [agents, id, go, navigate, dark, setDark, logout, role, canCreateAgent])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((p) => !p); return }
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || document.querySelector('[role="dialog"]')) return
      const key = e.key.toLowerCase()
      if (key === '?' || (e.shiftKey && key === '/')) { e.preventDefault(); setHelp(true); return }
      if (key === '[') { setCompact((c) => !c); return }
      if (/^[1-9]$/.test(key) && agents[Number(key) - 1]) { navigate(`/a/${agents[Number(key) - 1]!.id}`); return }
      if (!id) return
      if (key === 'n') { e.preventDefault(); go('/leads?new=1'); return }
      if (pending.current?.key === 'g' && Date.now() - pending.current.at < 1200) {
        const item = AGENT_NAV.flatMap((g) => g.items).find((i) => i.key === key)
        pending.current = null
        if (item) { e.preventDefault(); go(item.to) }
        return
      }
      if (key === 'g') pending.current = { key, at: Date.now() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, id, navigate, agents, setCompact])

  // Only bounce once the list is settled: right after creating an agent (or on a deep link during a
  // poll) the cached list is stale and the new id is not in it yet.
  if (id && agentsQuery.data && !agentsQuery.isFetching && !agent) return <Navigate to="/" replace />

  const sidebarProps = {
    agents, agent, onNew: () => setCreating(true), onPalette: () => setPalette(true), onHelp: () => setHelp(true),
    onLogout: logout, dark, setDark, user, role, canCreate: canCreateAgent,
  }

  const frame = (children: ReactNode) => (
    <div className="flex min-h-full">
      <Aurora className="fixed top-0 inset-x-0 h-[500px] opacity-50 z-0 pointer-events-none" />
      <aside className="sticky top-0 z-40 hidden h-screen shrink-0 lg:block">
        <Sidebar {...sidebarProps} compact={compact} setCompact={setCompact} />
      </aside>

      {/* The drawer stays mounted and slides with a transition: mounting it on open re-laid out the whole
          page (the content wrapper flips to inert at the same moment) and the first animation frame painted
          the panel before its layer existed, which read as a flicker on phones. Closed, it is invisible,
          inert and off-screen, so it costs nothing and cannot take focus. */}
      <div className={cn('fixed inset-0 z-40 lg:hidden', !mobile && 'pointer-events-none')} inert={!mobile || undefined} aria-hidden={!mobile}>
        <div className={cn('absolute inset-0 bg-black/50 transition-opacity duration-200 ease-[var(--ease-pointer)]', mobile ? 'opacity-100' : 'opacity-0')}
          onClick={() => setMobile(false)} aria-hidden />
        <aside ref={drawer} role="dialog" aria-modal="true" aria-label="Navigation"
          className={cn('absolute inset-y-0 left-0 max-w-full shadow-pop transition-[transform,visibility] duration-200 ease-[var(--ease-entrance)] will-change-transform',
            mobile ? 'visible translate-x-0' : 'invisible -translate-x-full')}>
          <Sidebar {...sidebarProps} compact={false} setCompact={() => undefined} mobile onClose={() => setMobile(false)} />
        </aside>
      </div>

      <div className="relative z-10 flex min-w-0 flex-1 flex-col" inert={mobile || undefined}>
        <header className="sticky top-0 z-30 flex min-h-12 items-center gap-1.5 border-b border-border bg-bg/85 px-2 py-1 backdrop-blur-md sm:px-4 lg:hidden">
          <Button ref={menuButton} variant="ghost" size="icon" className="size-10 shrink-0" onClick={() => setMobile(true)} aria-label="Open menu" aria-expanded={mobile}><Menu /></Button>
          {agent && <AgentMark agent={agent} className="size-7 rounded-lg text-[10px]" />}
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-sm font-bold">{agent ? agent.name : pageTitle}</div>
            {agent && pageTitle && <div className="truncate text-[11px] text-muted">{pageTitle}</div>}
          </div>
          <Button variant="ghost" size="icon" className="size-10 shrink-0" onClick={() => setPalette(true)} aria-label="Search (Ctrl K)"><Search /></Button>
        </header>
        {/* key=pathname: React remounts the page, which replays .page-in, so a route change reads as
            a new page arriving rather than the old one blinking out. */}
        <main key={location.pathname} className="page-in mx-auto w-full min-w-0 max-w-[1480px] flex-1 px-4 py-4 sm:px-6 sm:py-6 lg:px-8">
          <Suspense fallback={<div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>}>{children}</Suspense>
        </main>
      </div>

      {/* On every page: a new inbound call slides in, then folds into a live-calls pill. */}
      <IncomingCall />

      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands}
        leadsBase={id ? `/api/agents/${id}` : undefined} onLead={(leadId) => go(`/leads?open=${leadId}`)} />
      <NewAgentSheet open={creating} onClose={() => setCreating(false)} />
      <Dialog open={help} onClose={() => setHelp(false)} title="Keyboard shortcuts" footer={<Button onClick={() => setHelp(false)}>Close</Button>}>
        <div className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {[['Search & commands', 'Ctrl K'], ['Switch to agent 1–9', '1'], ['Collapse sidebar', '['], ['Add lead', 'N'], ['Shortcuts', '?'],
            ...AGENT_NAV.flatMap((g) => g.items.map((i) => [i.label, `G ${i.key.toUpperCase()}`]))].map(([l, k]) => (
            <div key={l} className="flex items-center justify-between gap-3 py-0.5"><span className="text-fg-2">{l}</span><span className="flex gap-1">{k!.split(' ').map((x, i) => <kbd key={i}>{x}</kbd>)}</span></div>
          ))}
        </div>
      </Dialog>
    </div>
  )

  if (agentId !== undefined && !validId) return <Navigate to="/" replace />
  if (!id) return frame(<Outlet />)
  if (!agent) {
    if (agentsQuery.isError) return frame(
      <div className="grid h-64 place-items-center px-4 text-center">
        <div className="space-y-3">
          <div className="text-sm font-semibold">Couldn't load your agents</div>
          <div className="text-[13px] text-muted break-words">{agentsQuery.error instanceof Error ? agentsQuery.error.message : 'Please check your connection and try again.'}</div>
          <Button variant="secondary" onClick={() => { void agentsQuery.refetch() }} loading={agentsQuery.isFetching}>Try again</Button>
        </div>
      </div>,
    )
    return frame(<div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>)
  }
  if (agent.locked) return frame(<UnlockModal agent={agent} />)
  
  // key: remount the whole workspace on switch so no state from the previous agent survives
  return <AgentProvider key={id} id={id} agent={agent}>{frame(<Outlet />)}</AgentProvider>
}

function UnlockModal({ agent }: { agent: { id: number; name: string; persona: { company_name: string } } }) {
  const qc = useQueryClient()
  const [pwd, setPwd] = useState('')
  const { mutate, isPending } = useMutation({
    mutationFn: () => api(`/api/agents/${agent.id}/unlock`, { method: 'POST', json: { password: pwd } }),
    onSuccess: () => {
      toast.success('Agent unlocked')
      qc.invalidateQueries({ queryKey: ['agents'] })
    },
    onError: (e) => toast.error(e.message)
  })
  
  return (
    <div className="flex h-[80vh] items-center justify-center p-4">
      <form onSubmit={(e) => { e.preventDefault(); mutate() }} className="w-full max-w-sm space-y-5 rounded-2xl border border-border bg-surface-2 p-6 shadow-xl">
        <div className="space-y-1 text-center">
          <div className="mx-auto mb-4 grid size-12 place-items-center rounded-full bg-fg text-bg"><Lock className="size-5" /></div>
          <h2 className="text-xl font-bold">Unlock {agent.name}</h2>
          <p className="text-sm text-muted">Enter the vault password to access {agent.persona.company_name}'s CRM.</p>
        </div>
        <Input type="password" autoFocus required disabled={isPending} placeholder="Password" value={pwd} onChange={(e) => setPwd(e.target.value)} />
        <Button type="submit" className="w-full" loading={isPending}>Unlock Workspace</Button>
      </form>
    </div>
  )
}

