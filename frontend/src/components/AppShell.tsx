import { useQueryClient } from '@tanstack/react-query'
import {
  Activity, AudioWaveform, BarChart3, BookOpen, Bot, CalendarClock, Check, ChevronsLeft, ChevronsRight, ChevronsUpDown, Columns3,
  Download, Keyboard, PhoneIncoming, LayoutDashboard, LayoutGrid, LogOut, Menu, MessageSquareText, Moon, Pause, PhoneCall, Plus, Search, Settings,
  SlidersHorizontal, Sparkles, Sun, Upload, UserPlus, Users, X,
} from 'lucide-react'
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { CommandPalette, type Command } from '@/components/CommandPalette'
import NewAgentSheet from '@/components/NewAgentSheet'
import { Button, Dialog, Spinner } from '@/components/ui'
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
      {on && <span className="absolute inset-0 animate-ping rounded-full bg-success/60" />}
      <span className={cn('relative size-2 rounded-full', on ? 'bg-success' : 'bg-ink-muted/50')} />
    </span>
  )
}

/* ---------------- Agent switcher ---------------- */

function AgentSwitcher({ agents, current, compact, onNew }: { agents: AgentSummary[]; current?: AgentSummary; compact: boolean; onNew: () => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  const location = useLocation()
  useEffect(() => { setOpen(false); setQ('') }, [location.pathname])
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])

  const shown = agents.filter((a) => a.name.toLowerCase().includes(q.toLowerCase().trim()))

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
            <Link to="/" className={cn('flex items-center gap-3 rounded-xl px-2.5 py-2 text-sm hover:bg-surface-2', !current && 'bg-surface-2')}>
              <span className="grid size-8 place-items-center rounded-lg bg-surface-2 ring-1 ring-border"><LayoutGrid className="size-4 text-muted" /></span>
              <span className="flex-1 font-semibold">All agents</span>
              {!current && <Check className="size-4 text-brand" />}
            </Link>
            <div className="px-2.5 pt-2 pb-1 text-[10.5px] font-bold tracking-wider text-muted uppercase">Agents</div>
            {shown.map((a, i) => (
              <Link key={a.id} to={`/a/${a.id}`} className={cn('flex items-center gap-3 rounded-xl px-2.5 py-2 hover:bg-surface-2', a.id === current?.id && 'bg-surface-2')}>
                <span className="relative">
                  <AgentMark agent={a} className={cn('size-8 rounded-lg text-[11px]', a.status === 'paused' && 'grayscale')} />
                  {a.stats.live > 0 && <span className="absolute -right-1 -bottom-1 size-3 rounded-full bg-success ring-2 ring-elevated" />}
                </span>
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="truncate text-sm font-semibold">{a.name}</div>
                  <div className="truncate text-[11.5px] text-muted">
                    {a.status === 'paused' ? 'Paused' : a.stats.live ? `${a.stats.live} live now` : `${a.stats.leads} leads · ${a.stats.calls_today} calls today`}
                  </div>
                </div>
                {a.id === current?.id ? <Check className="size-4 text-brand" /> : i < 9 && <kbd>{i + 1}</kbd>}
              </Link>
            ))}
            {!shown.length && <p className="px-3 py-4 text-center text-sm text-muted">No match</p>}
          </div>
          <button type="button" onClick={() => { setOpen(false); onNew() }}
            className="flex w-full items-center gap-2 border-t border-border px-4 py-3 text-sm font-semibold text-brand hover:bg-surface-2">
            <Plus className="size-4" />Create new agent
          </button>
        </div>
      )}
    </div>
  )
}

/* ---------------- Sidebar ---------------- */

function Sidebar({ agents, agent, compact, setCompact, onNew, onPalette, onHelp, onLogout, dark, setDark, user, mobile }: {
  agents: AgentSummary[]; agent?: AgentSummary; compact: boolean; setCompact: (v: boolean) => void; onNew: () => void; onPalette: () => void
  onHelp: () => void; onLogout: () => void; dark: boolean; setDark: (v: boolean) => void; user: string; mobile?: boolean
}) {
  const location = useLocation()
  const path = (to: string) => agent ? `/a/${agent.id}${to === '/' ? '' : to}` : to
  const done = agent ? SETUP_STEPS.filter((s) => agent.setup?.[s.key]).length : 0
  const next = agent ? (agent.setup ? SETUP_STEPS.find((s) => !agent.setup[s.key]) : undefined) : undefined
  // Same section (leads, calls…) in the other workspace, without ids that only exist in this one
  const section = agent ? (location.pathname.replace(`/a/${agent.id}`, '').match(/^\/[^/]+/)?.[0] ?? '') : ''
  const connectRate = agent?.stats.calls_today ? Math.round((100 * agent.stats.connected_today) / agent.stats.calls_today) : null

  const navLink = (to: string, label: string, Icon: typeof Users, end: boolean, count?: ReactNode) => (
    <NavLink key={to} to={to} end={end} title={compact ? label : undefined}
      className={({ isActive }) => cn('group relative flex h-9 items-center gap-3 rounded-xl text-[13.5px] font-semibold transition',
        compact ? 'justify-center' : 'px-3',
        isActive ? 'bg-ink-fg/[0.07] text-ink-fg' : 'text-ink-muted hover:bg-ink-fg/[0.04] hover:text-ink-fg')}>
      {({ isActive }) => <>
        {isActive && <span className="absolute top-1.5 bottom-1.5 left-0 w-[3px] rounded-r-full bg-ink-fg" />}
        <Icon className={cn('size-[18px] shrink-0', isActive ? 'text-ink-fg' : 'text-ink-muted group-hover:text-ink-fg')} />
        {!compact && <span className="flex-1 truncate">{label}</span>}
        {!compact && count != null && <span className="text-[11px] font-semibold text-ink-muted tabular-nums">{count}</span>}
      </>}
    </NavLink>
  )

  return (
    <div className={cn('flex h-full flex-col border-r border-border bg-ink text-ink-fg', mobile ? 'w-72' : compact ? 'w-[76px]' : 'w-[272px]', 'transition-[width] duration-200')}>
      <div className={cn('flex items-center gap-2.5 pt-4 pb-3', compact ? 'flex-col px-2' : 'px-4')}>
        <Link to="/" className="grid size-9 shrink-0 place-items-center rounded-xl bg-ink-fg text-ink shadow-sm"><AudioWaveform className="size-5" /></Link>
        {!compact && <div className="min-w-0 flex-1 leading-tight"><div className="text-[15px] font-extrabold tracking-tight">Psyber Voice</div><div className="text-[11px] text-ink-muted">Multi-agent calling</div></div>}
        {!mobile && (
          <button type="button" onClick={() => setCompact(!compact)} title={compact ? 'Expand sidebar ( [ )' : 'Collapse sidebar ( [ )'}
            className="grid size-7 place-items-center rounded-lg text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg">
            {compact ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
          </button>
        )}
      </div>

      <div className={cn('pb-3', compact ? 'px-2' : 'px-3')}>
        <AgentSwitcher agents={agents} current={agent} compact={compact} onNew={onNew} />
      </div>

      <div className={cn('min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain pb-4', compact ? 'px-2' : 'px-3')}>
        {agent ? <>
          {/* Live status */}
          {!compact ? (
            <Link to={path('/calls?status=active')} className="block rounded-2xl border border-ink-fg/8 bg-gradient-to-br from-ink-2 to-ink-3/40 p-3 transition hover:border-ink-fg/15">
              <div className="flex items-center gap-2">
                <LiveDot on={agent.stats.live > 0} />
                <span className="flex-1 text-[13px] font-bold">{agent.stats.live ? `${agent.stats.live} call${agent.stats.live > 1 ? 's' : ''} live` : 'Idle'}</span>
                {agent.status === 'paused'
                  ? <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[10.5px] font-bold text-warning"><Pause className="size-2.5" />Paused</span>
                  : <span className={cn('rounded-full px-2 py-0.5 text-[10.5px] font-bold', agent.within_calling_hours ? 'bg-success/15 text-success' : 'bg-ink-fg/5 text-ink-muted')}>
                    {agent.within_calling_hours ? 'In hours' : 'After hours'}</span>}
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                {[['Today', agent.stats.calls_today], ['Connected', agent.stats.connected_today], ['Rate', connectRate === null ? '—' : `${connectRate}%`]].map(([l, v]) => (
                  <div key={l as string} className="rounded-xl bg-ink-fg/[0.04] py-1.5">
                    <div className="text-[15px] font-extrabold tabular-nums">{v}</div>
                    <div className="text-[10px] font-semibold text-ink-muted">{l}</div>
                  </div>
                ))}
              </div>
            </Link>
          ) : (
            <Link to={path('/calls?status=active')} title={`${agent.stats.live} live`} className="mx-auto grid size-10 place-items-center rounded-xl bg-ink-2"><LiveDot on={agent.stats.live > 0} /></Link>
          )}

          {/* Quick actions */}
          {!compact && (
            <div className="grid grid-cols-3 gap-1.5">
              {[[UserPlus, 'Add lead', '/leads?new=1'], [Upload, 'Import', '/import'], [MessageSquareText, 'Test', '/agent?tab=playground']].map(([Icon, l, to]) => {
                const I = Icon as typeof Users
                return (
                  <Link key={l as string} to={path(to as string)} className="flex flex-col items-center gap-1 rounded-xl border border-ink-fg/6 py-2 text-[11px] font-semibold text-ink-muted transition hover:border-ink-fg/15 hover:bg-ink-fg/[0.04] hover:text-ink-fg">
                    <I className="size-4" />{l as string}
                  </Link>
                )
              })}
            </div>
          )}

          {AGENT_NAV.map((group) => (
            <div key={group.section}>
              {!compact ? <div className="px-3 pb-1.5 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">{group.section}</div> : <div className="mx-auto mb-2 h-px w-6 bg-ink-fg/10" />}
              <div className="space-y-0.5">{group.items.map((i) => navLink(path(i.to), i.label, i.icon, i.to === '/', i.count?.(agent)))}</div>
            </div>
          ))}

          {/* One-click switch between company workspaces */}
          {agents.length > 1 && (
            <div>
              {!compact ? <div className="flex items-center px-3 pb-1.5 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">
                <span className="flex-1">Companies</span>
                <Link to="/" className="normal-case tracking-normal hover:text-ink-fg">All</Link>
              </div> : <div className="mx-auto mb-2 h-px w-6 bg-ink-fg/10" />}
              <div className="space-y-0.5">
                {agents.filter((a) => a.id !== agent.id).slice(0, 8).map((a) => (
                  <Link key={a.id} to={`/a/${a.id}${section}`} title={compact ? a.name : `${a.name} · ${a.persona.company_name}`}
                    className={cn('flex h-9 items-center gap-2.5 rounded-xl text-[13px] font-semibold text-ink-muted transition hover:bg-ink-fg/[0.04] hover:text-ink-fg', compact ? 'justify-center' : 'px-2')}>
                    <span className="relative"><AgentMark agent={a} className={cn('size-6 rounded-md bg-ink-fg text-[9px] text-ink', a.status === 'paused' && 'opacity-60')} />
                      {a.stats.live > 0 && <span className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full bg-success ring-2 ring-ink" />}</span>
                    {!compact && <><span className="flex-1 truncate">{a.persona.company_name || a.name}</span>
                      <span className="text-[11px] tabular-nums">{a.stats.live ? <span className="text-success">{a.stats.live} live</span> : a.status === 'paused' ? 'Paused' : a.stats.calls_today || ''}</span></>}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* Setup progress */}
          {!compact && next && (
            <Link to={path(next.to)} className="block rounded-2xl border border-dashed border-ink-fg/12 p-3 transition hover:border-ink-fg/40">
              <div className="flex items-center justify-between text-[12px] font-bold"><span className="flex items-center gap-1.5"><Sparkles className="size-3.5 text-ink-fg" />Setup {done}/{SETUP_STEPS.length}</span><span className="text-ink-muted">{Math.round((100 * done) / SETUP_STEPS.length)}%</span></div>
              <div className="mt-2 flex gap-1">{SETUP_STEPS.map((s) => <span key={s.key} className={cn('h-1 flex-1 rounded-full', agent.setup?.[s.key] ? 'bg-ink-fg' : 'bg-ink-fg/10')} />)}</div>
              <div className="mt-2 text-[12px] text-ink-muted">Next: <span className="font-semibold text-ink-fg">{next.label}</span> →</div>
            </Link>
          )}
        </> : <>
          <div>
            {!compact && <div className="px-3 pb-1.5 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">Workspace</div>}
            <div className="space-y-0.5">
              {navLink('/', 'All agents', LayoutGrid, true, agents.length)}
              {navLink('/settings', 'Integrations & system', Settings, false)}
            </div>
          </div>
          <div>
            {!compact && <div className="px-3 pb-1.5 text-[10.5px] font-bold tracking-[0.12em] text-ink-muted/80 uppercase">Agents</div>}
            <div className="space-y-0.5">
              {agents.map((a) => (
                <Link key={a.id} to={`/a/${a.id}`} title={compact ? a.name : undefined}
                  className={cn('flex h-10 items-center gap-3 rounded-xl text-[13.5px] font-semibold text-ink-muted transition hover:bg-ink-fg/[0.04] hover:text-ink-fg', compact ? 'justify-center' : 'px-2')}>
                  <span className="relative"><AgentMark agent={a} className={cn('size-7 rounded-lg bg-ink-fg text-[10px] text-ink', a.status === 'paused' && 'opacity-60')} />
                    {a.stats.live > 0 && <span className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full bg-success ring-2 ring-ink" />}</span>
                  {!compact && <><span className="flex-1 truncate">{a.name}</span><span className="text-[11px] tabular-nums">{a.stats.live ? <span className="text-success">{a.stats.live} live</span> : a.stats.leads}</span></>}
                </Link>
              ))}
              <button type="button" onClick={onNew} title="New agent"
                className={cn('flex h-10 w-full items-center gap-3 rounded-xl text-[13.5px] font-semibold text-ink-muted transition hover:bg-ink-fg/[0.04] hover:text-ink-fg', compact ? 'justify-center' : 'px-2')}>
                <span className="grid size-7 place-items-center rounded-lg border border-dashed border-ink-fg/20"><Plus className="size-3.5" /></span>{!compact && 'New agent'}
              </button>
            </div>
          </div>
        </>}
      </div>

      <div className={cn('border-t border-ink-fg/8', compact ? 'space-y-1 p-2' : 'p-3')}>
        <div className={cn('flex items-center', compact ? 'flex-col gap-1' : 'gap-1')}>
          <button type="button" onClick={onPalette} title="Search (Ctrl K)" className={cn('flex h-9 items-center gap-2 rounded-xl text-[13px] text-ink-muted transition hover:bg-ink-fg/5 hover:text-ink-fg', compact ? 'w-10 justify-center' : 'flex-1 px-3')}>
            <Search className="size-4" />{!compact && <><span className="flex-1 text-left">Search</span><kbd className="!border-ink-fg/10 !bg-ink-fg/5 whitespace-nowrap !text-ink-muted">Ctrl K</kbd></>}
          </button>
          {agent && <Link to="/settings" title="Integrations & system" className={cn('grid size-9 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg', location.pathname === '/settings' && 'text-ink-fg')}><Settings className="size-4" /></Link>}
          <button type="button" onClick={onHelp} title="Keyboard shortcuts (?)" className="grid size-9 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg"><Keyboard className="size-4" /></button>
          <button type="button" onClick={() => setDark(!dark)} title="Toggle theme" className="grid size-9 place-items-center rounded-xl text-ink-muted hover:bg-ink-fg/5 hover:text-ink-fg">{dark ? <Sun className="size-4" /> : <Moon className="size-4" />}</button>
        </div>
        <div className={cn('mt-2 flex items-center gap-2.5 rounded-xl bg-ink-fg/[0.04] p-2', compact && 'justify-center')}>
          <Link to="/profile" title="Admin profile" className={cn('flex min-w-0 items-center gap-2.5 rounded-lg transition hover:opacity-80', !compact && 'flex-1')}>
            <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ink-fg text-xs font-bold text-ink uppercase">{user[0]}</span>
            {!compact && <div className="min-w-0 flex-1 leading-tight"><div className="truncate text-[13px] font-bold">{user}</div><div className="text-[11px] text-ink-muted">Administrator · Profile</div></div>}
          </Link>
          {!compact && <button type="button" onClick={onLogout} title="Sign out" className="grid size-8 place-items-center rounded-lg text-ink-muted hover:bg-ink-fg/5 hover:text-danger"><LogOut className="size-4" /></button>}
        </div>
      </div>
    </div>
  )
}

/* ---------------- Shell ---------------- */

export default function AppShell({ user }: { user: string }) {
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
  const { agentId } = useParams()
  const agentsQuery = useAgents()
  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data])
  const id = agentId ? Number(agentId) : null
  const agent = agents.find((a) => a.id === id)

  useEffect(() => { setMobile(false) }, [location.pathname])

  const logout = useCallback(async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
    qc.clear()
    navigate('/login')
  }, [navigate, qc])

  const sub = id ? location.pathname.replace(`/a/${id}`, '') || '/' : location.pathname
  const pageTitle = id ? TITLES[sub] ?? '' : sub === '/settings' ? 'Integrations & system' : sub === '/profile' ? 'Admin profile' : 'All agents'
  useEffect(() => {
    const live = agents.reduce((n, a) => n + a.stats.live, 0)
    document.title = `${live ? `(${live} live) ` : ''}${agent ? `${pageTitle} · ${agent.name}` : pageTitle} · Psyber Voice`
  }, [agent, pageTitle, agents])

  const go = useCallback((to: string) => navigate(id ? `/a/${id}${to === '/' ? '' : to}` : to), [id, navigate])

  const commands = useMemo<Command[]>(() => [
    ...agents.map((a) => ({ id: `agent-${a.id}`, group: 'Switch agent', label: a.name, icon: Bot, keywords: `${a.persona.company_name} ${a.description ?? ''}`, run: () => navigate(`/a/${a.id}`) })),
    { id: 'new-agent', group: 'Actions', label: 'Create a new agent', icon: Plus, keywords: 'add workspace', run: () => setCreating(true) },
    ...(id ? [
      ...AGENT_NAV.flatMap((g) => g.items.map((i) => ({ id: i.to, group: 'Go to', label: i.label, icon: i.icon, hint: `G ${i.key.toUpperCase()}`, run: () => go(i.to) }))),
      { id: 'new-lead', group: 'Actions', label: 'Add lead', icon: Plus, hint: 'N', keywords: 'create prospect', run: () => go('/leads?new=1') },
      { id: 'import', group: 'Actions', label: 'Import leads from CSV / Excel', icon: Upload, keywords: 'upload spreadsheet', run: () => go('/import') },
      { id: 'export', group: 'Actions', label: 'Export this agent\'s leads (CSV)', icon: Download, keywords: 'download', run: () => { window.location.href = `/api/agents/${id}/leads/export` } },
    ] : []),
    { id: 'home', group: 'Go to', label: 'All agents', icon: LayoutGrid, run: () => navigate('/') },
    { id: 'system', group: 'Go to', label: 'Integrations & system', icon: Settings, run: () => navigate('/settings') },
    { id: 'profile', group: 'Go to', label: 'Admin profile & password', icon: UserPlus, keywords: 'account security', run: () => navigate('/profile') },
    { id: 'theme', group: 'Preferences', label: dark ? 'Switch to light theme' : 'Switch to dark theme', icon: dark ? Sun : Moon, keywords: 'dark mode', run: () => setDark(!dark) },
    { id: 'logout', group: 'Preferences', label: 'Sign out', icon: LogOut, run: logout },
  ], [agents, id, go, navigate, dark, setDark, logout])

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

  if (id && agentsQuery.data && !agent) return <Navigate to="/" replace />

  const sidebarProps = {
    agents, agent, onNew: () => setCreating(true), onPalette: () => setPalette(true), onHelp: () => setHelp(true),
    onLogout: logout, dark, setDark, user,
  }

  const frame = (children: ReactNode) => (
    <div className="flex min-h-full">
      <aside className="sticky top-0 hidden h-screen shrink-0 lg:block">
        <Sidebar {...sidebarProps} compact={compact} setCompact={setCompact} />
      </aside>

      {mobile && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobile(false)} />
          <aside className="absolute inset-y-0 left-0 animate-slide-in">
            <Sidebar {...sidebarProps} compact={false} setCompact={() => undefined} mobile />
            <Button variant="ghost" size="icon" className="absolute top-3 -right-11 bg-surface" onClick={() => setMobile(false)} aria-label="Close menu"><X /></Button>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b border-border bg-bg/85 px-4 backdrop-blur-md lg:hidden">
          <Button variant="ghost" size="icon" onClick={() => setMobile(true)} aria-label="Open menu"><Menu /></Button>
          {agent && <AgentMark agent={agent} className="size-7 rounded-lg text-[10px]" />}
          <span className="truncate text-sm font-bold">{agent ? agent.name : pageTitle}</span>
          <div className="flex-1" />
          <Button variant="ghost" size="icon" onClick={() => setPalette(true)} aria-label="Search"><Search /></Button>
        </header>
        <main className="mx-auto w-full max-w-[1480px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <Suspense fallback={<div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>}>{children}</Suspense>
        </main>
      </div>

      <CommandPalette open={palette} onClose={() => setPalette(false)} commands={commands}
        leadsBase={id ? `/api/agents/${id}` : undefined} onLead={(leadId) => go(`/leads?open=${leadId}`)} />
      <NewAgentSheet open={creating} onClose={() => setCreating(false)} />
      <Dialog open={help} onClose={() => setHelp(false)} title="Keyboard shortcuts" footer={<Button onClick={() => setHelp(false)}>Close</Button>}>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          {[['Search & commands', 'Ctrl K'], ['Switch to agent 1–9', '1'], ['Collapse sidebar', '['], ['Add lead', 'N'], ['Shortcuts', '?'],
            ...AGENT_NAV.flatMap((g) => g.items.map((i) => [i.label, `G ${i.key.toUpperCase()}`]))].map(([l, k]) => (
            <div key={l} className="flex items-center justify-between gap-3 py-0.5"><span className="text-fg-2">{l}</span><span className="flex gap-1">{k!.split(' ').map((x, i) => <kbd key={i}>{x}</kbd>)}</span></div>
          ))}
        </div>
      </Dialog>
    </div>
  )

  if (!id) return frame(<Outlet />)
  if (!agent) return frame(<div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>)
  // key: remount the whole workspace on switch so no state from the previous agent survives
  return <AgentProvider key={id} id={id} agent={agent}>{frame(<Outlet />)}</AgentProvider>
}
