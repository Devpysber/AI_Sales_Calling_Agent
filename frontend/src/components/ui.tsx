import { Eye, EyeOff, Loader2, X } from 'lucide-react'
import {
  createContext, forwardRef, useCallback, useContext, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type RefObject,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { AnimatedNumber } from '@/lib/motion'
import { cn, initials } from '@/lib/utils'

/* ---------------- Button ---------------- */

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline-danger'
const variants: Record<Variant, string> = {
  primary: 'bg-brand text-brand-fg shadow-glow hover:brightness-110 active:brightness-95',
  secondary: 'bg-surface text-fg border border-border hover:bg-surface-2 hover:border-border-strong shadow-xs',
  ghost: 'text-fg-2 hover:bg-surface-2 hover:text-fg',
  danger: 'bg-danger text-white hover:brightness-110 shadow-sm',
  'outline-danger': 'border border-border text-danger hover:bg-danger-soft hover:border-danger/40',
}
// Below sm every size is at least 40px tall so it is a comfortable touch target; desktop keeps the tighter heights.
const sizes = {
  sm: 'h-10 sm:h-8 px-3 text-[13px] gap-1.5',
  md: 'h-10 sm:h-9.5 px-4 text-sm gap-2',
  lg: 'h-11 px-5 text-[15px] gap-2',
  icon: 'h-10 w-10 sm:h-9 sm:w-9',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: keyof typeof sizes
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', loading, className, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn('inline-flex shrink-0 items-center justify-center rounded-xl font-semibold whitespace-nowrap',
        'transition duration-150 ease-[var(--ease-pointer)] active:scale-[.975] motion-reduce:active:scale-100',
        'disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4', variants[variant], sizes[size], className)}
      {...props}
    >
      {loading && <Loader2 className="animate-spin" />}
      {children}
    </button>
  ),
)

/* ---------------- Inputs ---------------- */

const fieldBase = 'w-full rounded-xl border border-border bg-surface px-3.5 text-sm text-fg placeholder:text-muted shadow-xs transition focus:border-brand focus:outline-none focus:ring-3 focus:ring-brand/15 disabled:opacity-60'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, type, ...p }, ref) => {
  const [show, setShow] = useState(false)
  const isPassword = type === 'password'
  
  if (isPassword) {
    return (
      <div className="relative min-w-0">
        <input ref={ref} type={show ? 'text' : 'password'} className={cn(fieldBase, 'h-10 pr-10', className)} {...p} />
        <button type="button" onClick={() => setShow(!show)} disabled={p.disabled} aria-label={show ? 'Hide password' : 'Show password'}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-xl text-muted hover:text-fg-2 focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:outline-none disabled:pointer-events-none">
          {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
    )
  }
  
  return <input ref={ref} type={type} className={cn(fieldBase, 'h-10', className)} {...p} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(fieldBase, 'py-2 leading-relaxed', className)} {...p} />
))

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...p }, ref) => (
  <select ref={ref} className={cn(fieldBase, 'h-10 pr-8 cursor-pointer', className)} {...p}>{children}</select>
))

export function Field({ label, hint, error, children, className }: { label: string; hint?: ReactNode; error?: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn('grid min-w-0 gap-1.5', className)}>
      <span className="text-[13px] font-semibold text-fg-2">{label}</span>
      {children}
      {error ? <span className="text-xs break-words text-danger">{error}</span> : hint ? <span className="text-xs break-words text-muted">{hint}</span> : null}
    </label>
  )
}

export function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      // The ::after pseudo widens the hit area to ~40px without changing the drawn size.
      className={cn('group relative inline-flex h-5.5 w-10 shrink-0 items-center rounded-full transition-colors duration-300 disabled:opacity-50',
        "after:absolute after:-inset-y-2.5 after:-inset-x-1 after:content-['']",
        checked ? 'bg-brand' : 'bg-border-strong')}
    >
      {/* The knob springs across and stretches while pressed, like a physical switch. */}
      <span className={cn('inline-block h-4 w-4 rounded-full shadow transition-all duration-300 ease-[cubic-bezier(.34,1.56,.64,1)] group-active:w-5',
        checked ? 'translate-x-5 bg-brand-fg group-active:translate-x-4' : 'translate-x-1 bg-white')} />
    </button>
  )
}

/* ---------------- Display ---------------- */

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'
const tones: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-fg-2 ring-border',
  brand: 'bg-brand-soft text-brand ring-brand/20',
  success: 'bg-success-soft text-success ring-success/20',
  warning: 'bg-warning-soft text-warning ring-warning/20',
  danger: 'bg-danger-soft text-danger ring-danger/20',
  info: 'bg-info-soft text-info ring-info/20',
}

export function Badge({ tone = 'neutral', dot, pulse, children, className }: { tone?: Tone; dot?: boolean; pulse?: boolean; children: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap ring-1 ring-inset', tones[tone], className)}>
      {(dot || pulse) && <span className={cn('size-1.5 rounded-full bg-current', pulse && 'animate-pulse-dot')} />}
      {children}
    </span>
  )
}

export function Card({ interactive, className, ...p }: HTMLAttributes<HTMLDivElement> & { interactive?: boolean }) {
  // interactive: the card is a link or opens something, so it lifts towards the pointer.
  return <div className={cn('min-w-0 rounded-[var(--radius-card)] border border-border bg-surface shadow-card',
    interactive && 'lift cursor-pointer', className)} {...p} />
}

export function CardHeader({ title, description, action, className }: { title: ReactNode; description?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-4 pt-4 pb-3 sm:px-5 sm:pt-5', className)}>
      <div className="min-w-0 flex-1 basis-40">
        <h3 className="text-[15px] font-bold break-words text-fg">{title}</h3>
        {description && <p className="mt-0.5 text-[13px] break-words text-muted">{description}</p>}
      </div>
      {action && <div className="flex min-w-0 max-w-full shrink-0 flex-wrap items-center gap-2">{action}</div>}
    </div>
  )
}

/** Horizontal scroll wrapper for wide content (tables) so the page itself never scrolls sideways. */
export function TableScroll({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn('w-full min-w-0 overflow-x-auto overscroll-x-contain [-webkit-overflow-scrolling:touch]', className)}>
      {children}
    </div>
  )
}

/** Long text clamped to a few lines with a Show more / Show less toggle. */
export function ShowMore({ text, lines = 3, limit = 220, className }: { text: string; lines?: number; limit?: number; className?: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > limit
  return (
    <span className={className}>
      <span className={cn('whitespace-pre-wrap', long && !open && 'line-clamp-[var(--lines)]')} style={{ ['--lines' as string]: lines }}>{text}</span>
      {long && <button type="button" onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
        className="mt-1 flex min-h-10 items-center text-xs font-semibold text-brand hover:underline sm:min-h-0">{open ? 'Show less' : 'Show more'}</button>}
    </span>
  )
}

export const Skeleton = ({ className }: { className?: string }) => (
  <div className={cn('sheen rounded-xl bg-surface-2', className)} />
)

export const Spinner = ({ className }: { className?: string }) => <Loader2 className={cn('size-4 animate-spin text-muted', className)} />

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-10 text-center sm:px-6 sm:py-14">
      {icon && <div className="mb-4 grid size-14 place-items-center rounded-2xl bg-brand-soft text-brand [&_svg]:size-6">{icon}</div>}
      <p className="font-semibold break-words text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm break-words text-muted">{description}</p>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}

export function Avatar({ name, className }: { name?: string | null; className?: string }) {
  return (
    <span className={cn('grid size-8 shrink-0 place-items-center rounded-full bg-fg text-xs font-semibold text-bg', className)}>
      {initials(name)}
    </span>
  )
}

export function PageHeader({ title, description, actions, eyebrow, children, visual }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode; children?: ReactNode
  /** Shown left of the title, e.g. the agent's voice orb. */
  visual?: ReactNode
}) {
  return (
    <div className="hero-wash relative -mx-4 -mt-4 mb-6 sm:-mt-6 overflow-hidden border-b border-border/70 px-4 pt-7 pb-6 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
      <div className="relative flex flex-wrap items-end justify-between gap-4">
        {visual && <div className="hidden shrink-0 animate-rise sm:block">{visual}</div>}
        <div className="min-w-0 flex-1 basis-56 animate-rise">
          {eyebrow && <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-bold tracking-wider text-brand uppercase">{eyebrow}</div>}
          <h1 className="text-2xl leading-tight font-extrabold tracking-tight break-words text-fg sm:text-[28px]">{title}</h1>
          {description && <p className="mt-1.5 max-w-2xl text-[14.5px] break-words text-muted">{description}</p>}
        </div>
        {actions && <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">{actions}</div>}
      </div>
      {children && <div className="relative mt-5 min-w-0">{children}</div>}
    </div>
  )
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[] }) {
  const active = Math.max(0, items.findIndex((i) => i.value === value))
  return (
    // Equal columns sized to the widest label (auto-cols-fr), so the pill's width and offset are exact
    // and no label wraps; flex-1 gave each button basis 0 and broke "All 1" over two lines.
    // The outer wrapper scrolls sideways on narrow screens instead of pushing the page wider.
    <div role="tablist" className="max-w-full overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      <div className="relative inline-grid auto-cols-fr grid-flow-col rounded-xl border border-border bg-surface-2 p-1">
        {/* One pill that travels to the selected tab: the movement is what shows which way you went. */}
        <span aria-hidden
          className="absolute top-1 bottom-1 left-1 rounded-lg bg-surface shadow-sm ring-1 ring-border transition-transform duration-250 ease-[var(--ease-entrance)] motion-reduce:transition-none"
          style={{ width: `calc((100% - 0.5rem) / ${Math.max(1, items.length)})`, transform: `translateX(${active * 100}%)` }} />
        {items.map((i) => (
          <button key={i.value} type="button" role="tab" aria-selected={value === i.value} onClick={() => onChange(i.value)}
            className={cn('relative z-10 min-h-10 rounded-lg px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap transition-colors sm:min-h-0',
              value === i.value ? 'text-brand' : 'text-muted hover:text-fg')}>
            {i.label}
          </button>
        ))}
      </div>
    </div>
  )
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3 text-[13px] text-muted">
      <span className="whitespace-nowrap">{total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}` : '0 results'}</span>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <span className="tabular-nums">{page} / {pages}</span>
        <Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </div>
  )
}

/* ---------------- Overlays ---------------- */

// Module-level stack of open overlays: only the topmost one closes on Escape, and the body scroll lock is
// taken when the first overlay opens and released only when the last one closes (a Dialog over a Sheet
// used to restore each other's saved overflow out of order and leave the page locked).
const overlayStack: symbol[] = []
let savedOverflow = ''

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Escape-to-close, body scroll lock, and focus management (initial focus, Tab trap, restore on close) for one overlay. */
function useEscape(open: boolean, onClose: () => void, panelRef?: RefObject<HTMLElement | null>) {
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  useEffect(() => {
    if (!open) return
    const token = Symbol('overlay')
    overlayStack.push(token)
    if (overlayStack.length === 1) {
      savedOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
    }
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusables = () => {
      const panel = panelRef?.current
      return panel ? Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)) : []
    }
    // Move focus inside on the next frame (after the portal paints) unless a caller's `autoFocus` already did.
    const raf = requestAnimationFrame(() => {
      const panel = panelRef?.current
      if (!panel || panel.contains(document.activeElement)) return
      const first = focusables()[0]
      if (first) first.focus()
      else { panel.tabIndex = -1; panel.focus() }
    })
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      if (overlayStack[overlayStack.length - 1] !== token) return
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef?.current
      if (!panel) return
      const list = focusables()
      if (list.length === 0) { e.preventDefault(); panel.focus(); return }
      const first = list[0], last = list[list.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || !panel.contains(active))) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && (active === last || !panel.contains(active))) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKey)
      const i = overlayStack.indexOf(token)
      if (i >= 0) overlayStack.splice(i, 1)
      if (overlayStack.length === 0) document.body.style.overflow = savedOverflow
      if (previouslyFocused && previouslyFocused.isConnected) previouslyFocused.focus()
    }
  }, [open, panelRef])
}

export function Sheet({ open, onClose, title, description, children, footer, width = 'max-w-xl' }: {
  open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children: ReactNode; footer?: ReactNode; width?: string
}) {
  const panelRef = useRef<HTMLElement>(null)
  useEscape(open, onClose, panelRef)
  const id = useId()
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 animate-fade-in bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
      {/* Full-screen below sm; a floating right-hand panel from sm up. */}
      <aside ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={id}
        className={cn('absolute inset-0 flex w-full animate-slide-in flex-col overflow-hidden border border-border bg-surface shadow-pop',
          'sm:inset-y-2 sm:right-2 sm:left-auto sm:w-[calc(100%-1rem)] sm:rounded-2xl', width)}>
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-6 sm:py-4">
          <div className="min-w-0 flex-1">
            <h2 id={id} className="truncate text-lg font-bold">{title}</h2>
            {description && <div className="mt-0.5 text-sm break-words text-muted">{description}</div>}
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close"><X /></Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-6 sm:py-5">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">{footer}</footer>}
      </aside>
    </div>,
    document.body,
  )
}

export function Dialog({ open, onClose, title, description, children, footer }: {
  open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children?: ReactNode; footer?: ReactNode
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  useEscape(open, onClose, panelRef)
  const id = useId()
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-[60] grid place-items-center p-4">
      <div className="absolute inset-0 animate-fade-in bg-black/45 backdrop-blur-[2px]" onClick={onClose} />
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={id}
        className="relative flex max-h-[calc(100dvh-2rem)] w-full max-w-[min(28rem,calc(100vw-2rem))] animate-pop-in flex-col rounded-2xl border border-border bg-elevated shadow-pop">
        <div className="px-5 pt-5 sm:px-6">
          <h2 id={id} className="text-base font-semibold break-words">{title}</h2>
          {description && <p className="mt-1.5 text-sm break-words text-muted">{description}</p>}
        </div>
        {children && <div className={cn('min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pt-4 sm:px-6', !footer && 'pb-5 sm:pb-6')}>{children}</div>}
        {footer && <div className="mt-5 flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3 sm:px-6">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

type ConfirmOptions = { title: string; description?: string; confirmLabel?: string; danger?: boolean }
const ConfirmContext = createContext<(o: ConfirmOptions) => Promise<boolean>>(async () => false)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<(v: boolean) => void>(undefined)
  // A second confirm while one is open settles the first as "cancelled" so no caller awaits forever.
  const confirm = useCallback((o: ConfirmOptions) => new Promise<boolean>((resolve) => { resolver.current?.(false); resolver.current = resolve; setState(o) }), [])
  const close = (value: boolean) => { const r = resolver.current; resolver.current = undefined; r?.(value); setState(null) }
  useEffect(() => () => { resolver.current?.(false); resolver.current = undefined }, [])
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={!!state} onClose={() => close(false)} title={state?.title} description={state?.description}
        footer={<>
          <Button onClick={() => close(false)}>Cancel</Button>
          <Button variant={state?.danger ? 'danger' : 'primary'} onClick={() => close(true)} autoFocus>{state?.confirmLabel ?? 'Confirm'}</Button>
        </>} />
    </ConfirmContext.Provider>
  )
}

export const useConfirm = () => useContext(ConfirmContext)

/* ---------------- Metrics ---------------- */

export function StatTile({ label, value, count, decimals, prefix, suffix, sub, icon, tone = 'brand', trend, className }: {
  label: string; value?: ReactNode; sub?: ReactNode; icon?: ReactNode; tone?: Tone; trend?: ReactNode; className?: string
  /** A number: counts up on first paint and re-counts whenever it changes, instead of jumping. */
  count?: number | null; decimals?: number; prefix?: string; suffix?: string
}) {
  const counted = count !== undefined
  return (
    <Card className={cn("glint group relative overflow-hidden p-4 sm:p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 text-[13px] font-semibold break-words text-muted">{label}</span>
        {icon && (
          <span className={cn('grid size-9 place-items-center rounded-xl ring-1 ring-inset [&_svg]:size-4',
            'transition-transform duration-300 ease-[var(--ease-entrance)] group-hover:-rotate-6 group-hover:scale-105',
            'motion-reduce:transition-none motion-reduce:group-hover:rotate-0 motion-reduce:group-hover:scale-100', tones[tone])}>
            {icon}
          </span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-2">
        <span className="min-w-0 truncate text-[26px] leading-none font-extrabold tracking-tight tabular-nums sm:text-[30px]">
          {counted ? <AnimatedNumber value={count} decimals={decimals} prefix={prefix} suffix={suffix} /> : value}
        </span>
        {trend}
      </div>
      {sub && <div className="mt-2 text-xs text-muted">{sub}</div>}
    </Card>
  )
}

export function Meter({ value, className, tone = 'brand' }: { value: number; className?: string; tone?: Tone }) {
  const bar: Record<Tone, string> = { neutral: 'bg-muted', brand: 'bg-brand', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger', info: 'bg-info' }
  // Starts at zero and transitions to `value` on the frame after mount: a bar that is already full
  // when it appears says nothing about how full it is.
  const [width, setWidth] = useState(0)
  useEffect(() => { const id = requestAnimationFrame(() => setWidth(value)); return () => cancelAnimationFrame(id) }, [value])
  return (
    <div className={cn('h-1.5 overflow-hidden rounded-full bg-surface-2', className)}>
      <div className={cn('h-full origin-left rounded-full transition-[width] duration-700 ease-[var(--ease-entrance)]', bar[tone])}
        style={{ width: `${Math.max(0, Math.min(100, width))}%` }} />
    </div>
  )
}

export function Ring({ value, size = 44, stroke = 5, children }: { value: number; size?: number; stroke?: number; children?: ReactNode }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  // Fills from empty on the frame after mount, like Meter: the sweep is what shows how complete it is.
  const [shown, setShown] = useState(0)
  useEffect(() => { const id = requestAnimationFrame(() => setShown(value)); return () => cancelAnimationFrame(id) }, [value])
  return (
    <span className="relative inline-grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--brand)" strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.max(0, Math.min(1, shown)))}
          className="transition-[stroke-dashoffset] duration-1000 ease-[var(--ease-entrance)]" />
      </svg>
      <span className="absolute text-[11px] font-bold tabular-nums">{children}</span>
    </span>
  )
}
