/**
 * Motion primitives.
 *
 * No animation library: everything here is CSS transitions/keyframes (see index.css) plus three
 * small hooks. The rules the whole app follows:
 * - Motion explains a change (something arrived, a number moved, a panel came from the right).
 *   Nothing moves just to decorate.
 * - Entrances are short (180-420ms) and travel a few pixels. Long or large motion reads as lag on
 *   a dashboard people keep open all day.
 * - Everything is gated on prefers-reduced-motion: the content still appears, it just stops moving.
 */

import {
  Children, cloneElement, isValidElement, useEffect, useMemo, useRef, useState,
  type CSSProperties, type ElementType, type HTMLAttributes, type ReactElement, type ReactNode,
} from 'react'
import { cn } from '@/lib/utils'

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    if (typeof matchMedia !== 'function') return
    const mq = matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

/** True once the element has been on screen, so a reveal plays when scrolled to and never again. */
export function useInView<T extends Element>(options?: IntersectionObserverInit) {
  const ref = useRef<T | null>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const node = ref.current
    if (!node || seen) return
    if (typeof IntersectionObserver !== 'function') { setSeen(true); return }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect() }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05, ...options })
    io.observe(node)
    return () => io.disconnect()
  }, [seen, options])
  return { ref, seen }
}

/**
 * Counts to `value` on mount and on every later change.
 *
 * The easing is ease-out, so the number lands rather than stopping dead, and the duration scales
 * with how far it travelled: a metric ticking 4 -> 5 must not take as long as 0 -> 1,200.
 */
export function useCountUp(value: number, { duration, decimals = 0 }: { duration?: number; decimals?: number } = {}) {
  const reduced = useReducedMotion()
  const [shown, setShown] = useState(reduced ? value : 0)
  const from = useRef(reduced ? value : 0)

  useEffect(() => {
    if (reduced || !Number.isFinite(value)) { setShown(value); from.current = value; return }
    const start = from.current
    const delta = value - start
    if (delta === 0) { setShown(value); return }
    const ms = duration ?? Math.min(1100, 420 + Math.abs(delta) * 6)
    let raf = 0
    const began = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / ms)
      const eased = 1 - (1 - t) ** 3
      const next = start + delta * eased
      setShown(decimals ? Number(next.toFixed(decimals)) : Math.round(next))
      if (t < 1) raf = requestAnimationFrame(step)
      else from.current = value
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value, duration, decimals, reduced])

  return shown
}

/** A metric that counts to its new value instead of jumping, with optional prefix/suffix (%, s, ₹). */
export function AnimatedNumber({ value, decimals = 0, prefix = '', suffix = '', className }: {
  value: number | null | undefined; decimals?: number; prefix?: string; suffix?: string; className?: string
}) {
  const safe = typeof value === 'number' && Number.isFinite(value) ? value : 0
  const shown = useCountUp(safe, { decimals })
  if (value === null || value === undefined) return <span className={className}>—</span>
  return <span className={cn('tabular-nums', className)}>{prefix}{shown.toLocaleString('en-IN', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  })}{suffix}</span>
}

type RevealProps<T extends ElementType> = {
  as?: T
  /** Position in a group: each step delays the entrance by one beat, so a row reads left to right. */
  index?: number
  delay?: number
  from?: 'up' | 'down' | 'left' | 'right' | 'none'
  /** Play as soon as it mounts rather than waiting to be scrolled into view. */
  immediate?: boolean
  children?: ReactNode
} & Omit<HTMLAttributes<HTMLElement>, 'children'>

const STEP_MS = 55
const DIRECTIONS = { up: 'reveal-up', down: 'reveal-down', left: 'reveal-left', right: 'reveal-right', none: 'reveal-none' }

/** Fades and slides its content in once: on mount, or the first time it is scrolled into view. */
export function Reveal<T extends ElementType = 'div'>({
  as, index = 0, delay = 0, from = 'up', immediate = false, className, style, children, ...rest
}: RevealProps<T>) {
  const Tag = (as || 'div') as ElementType
  const reduced = useReducedMotion()
  const { ref, seen } = useInView<HTMLElement>()
  const play = reduced || immediate || seen
  const merged = useMemo<CSSProperties>(() => ({
    ...(style as CSSProperties),
    animationDelay: play && !reduced ? `${delay + index * STEP_MS}ms` : undefined,
  }), [style, play, reduced, delay, index])

  return (
    <Tag ref={immediate ? undefined : ref} style={merged}
      className={cn('reveal', DIRECTIONS[from], play && 'reveal-in', className)} {...rest}>
      {children}
    </Tag>
  )
}

/**
 * Staggers whatever it wraps: every direct child enters one beat after the previous one.
 *
 * Used for stat rows, card grids and lists, where a single simultaneous fade tells you nothing
 * about how many things arrived.
 */
export function Stagger({ children, className, from = 'up', step = STEP_MS, ...rest }: {
  children: ReactNode; className?: string; from?: RevealProps<'div'>['from']; step?: number
} & Omit<HTMLAttributes<HTMLDivElement>, 'children'>) {
  const reduced = useReducedMotion()
  // The classes go on the children themselves, never on a wrapper: an extra div would break the
  // grid or flex layout these lists live in.
  const items = Children.toArray(children).filter(isValidElement) as ReactElement<{ className?: string; style?: CSSProperties }>[]
  return (
    <div className={className} {...rest}>
      {items.map((child, i) => cloneElement(child, {
        className: cn('reveal reveal-in', DIRECTIONS[from], child.props.className),
        style: { ...child.props.style, animationDelay: reduced ? undefined : `${i * step}ms` },
      }))}
    </div>
  )
}
