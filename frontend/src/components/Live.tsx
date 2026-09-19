/**
 * Small pieces that make polled data read as live: a ticking clock, a number that
 * flashes when it changes, a running call timer and a "just arrived" highlight.
 *
 * Everything degrades to plain text when `prefers-reduced-motion` is set, so the
 * dashboard stays usable for people who turn animation off.
 */
import { useEffect, useRef, useState } from 'react'
import { useCountUp } from '@/lib/motion'
import { cn } from '@/lib/utils'

/** A clock that re-renders on an interval, so "updated 4s ago" counts up on its own. */
export function useNow(everyMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs)
    return () => clearInterval(id)
  }, [everyMs])
  return now
}

/** "just now" -> "8s ago" -> "3m ago": short enough to sit in a header chip. */
export function shortAgo(from: number | undefined, now: number) {
  if (!from || !Number.isFinite(from)) return '—'
  const seconds = Math.max(0, Math.round((now - from) / 1000))
  if (seconds < 3) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  return minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago`
}

/** Header chip: green and pulsing while a refetch is in flight, otherwise the age of the data. */
export function LiveStamp({ fetching, updatedAt }: { fetching: boolean; updatedAt?: number }) {
  const now = useNow(1000)
  return (
    <span
      className={cn('inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold transition-colors motion-reduce:transition-none',
        fetching ? 'bg-success/10 text-success' : 'bg-surface-2 text-muted')}
      title={updatedAt && Number.isFinite(updatedAt) ? new Date(updatedAt).toLocaleTimeString() : undefined}
      // Not live, so it is only read when navigated to; gives SR users the age the hidden span omits.
      aria-label={fetching ? 'Updating' : `Updated ${shortAgo(updatedAt, now)}`}>
      <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', fetching ? 'animate-pulse bg-success' : 'bg-border')} />
      {/* No aria-live here: the age ticks every second and would be read out continuously. The
          fetching flip is the only thing worth announcing, and it is visible from the colour. */}
      {fetching ? 'Updating…' : <>Updated <span aria-hidden>{shortAgo(updatedAt, now)}</span></>}
    </span>
  )
}

/**
 * A number that briefly highlights when it changes, so a call landing while you are
 * looking at the page is visible without re-reading every tile.
 */
export function LiveNumber({ value, className }: { value: number | null | undefined; className?: string }) {
  // Optional API fields can arrive as null/undefined while loading: treat them as 0 rather than NaN.
  const safe = typeof value === 'number' && Number.isFinite(value) ? value : 0
  const loaded = typeof value === 'number' && Number.isFinite(value)
  // null until the first real value lands, so the loading-state 0 -> first value does not flash
  // every tile green on page open. Only later changes are "movement".
  const previous = useRef<number | null>(null)
  const [changed, setChanged] = useState<'up' | 'down' | null>(null)
  // Counts to the new value as well as flashing: the movement shows the size of the change, the
  // colour shows its direction.
  const shown = useCountUp(safe)
  useEffect(() => {
    if (!loaded) return
    if (previous.current === null) { previous.current = safe; return }
    if (previous.current === safe) return
    setChanged(safe > previous.current ? 'up' : 'down')
    previous.current = safe
    const id = setTimeout(() => setChanged(null), 900)
    return () => clearTimeout(id)
  }, [safe, loaded])
  return (
    <span className={cn('tabular-nums transition-colors duration-500 motion-reduce:transition-none',
      changed === 'up' && 'text-success', changed === 'down' && 'text-muted', className)}>
      {shown}
    </span>
  )
}

/** mm:ss since a call was answered (or created), ticking every second. */
export function CallTimer({ since, className }: { since: string | null | undefined; className?: string }) {
  const now = useNow(1000)
  if (!since) return null
  const started = new Date(since).getTime()
  if (Number.isNaN(started)) return null
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  const hours = Math.floor(seconds / 3600)
  const mm = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')
  const ss = String(seconds % 60).padStart(2, '0')
  return (
    <span className={cn('tabular-nums whitespace-nowrap', className)}>
      {hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`}
    </span>
  )
}

/**
 * Ids seen on a previous render. Anything not in the set arrived since you last looked,
 * which is what the feed highlights. The first render is never "new" — otherwise the whole
 * list would animate on page load.
 */
export function useArrivals<T extends string | number>(ids: T[]) {
  const seen = useRef<Set<T> | null>(null)
  const [fresh, setFresh] = useState<Set<T>>(new Set())
  // The clear timer lives outside the effect cleanup: a poll or window-focus refetch that changes
  // the list again within 2.5s must not cancel it, or the highlight would stick until the next arrival.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Compared by the list contents, not the array reference a poll rebuilds each time. JSON keeps
  // an id containing the separator from colliding with two neighbouring ids.
  const key = JSON.stringify(ids)
  useEffect(() => {
    if (seen.current === null) {
      // Seed only once real data has landed: on a cold open the caller passes [] while the query
      // loads, and seeding an empty set there would mark the whole first page of results as new.
      if (ids.length) seen.current = new Set(ids)
      return
    }
    const added = ids.filter((id) => !seen.current!.has(id))
    // Keep only what is on screen plus the newcomers, so a long-polled feed does not grow the set forever.
    seen.current = new Set(ids)
    if (!added.length) return
    setFresh(new Set(added))
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => { timer.current = null; setFresh(new Set()) }, 2500)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  return fresh
}

/** Today's point on a chart: a solid dot with a ring leaving it. Every other point draws nothing. */
export function ChartNowDot({ cx, cy, index, last }: { cx?: number; cy?: number; index?: number; last: number }) {
  if (index !== last || cx === undefined || cy === undefined) return <g />
  return (
    <g>
      <circle cx={cx} cy={cy} r={4} fill="var(--fg)" className="now-ring" />
      <circle cx={cx} cy={cy} r={4} fill="var(--fg)" stroke="var(--surface)" strokeWidth={2} />
    </g>
  )
}
