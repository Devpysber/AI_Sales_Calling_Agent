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
  if (!from) return '—'
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
      className={cn('flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold transition-colors',
        fetching ? 'bg-success/10 text-success' : 'bg-surface-2 text-muted')}
      title={updatedAt ? new Date(updatedAt).toLocaleTimeString() : undefined}>
      <span className={cn('size-1.5 rounded-full', fetching ? 'animate-pulse bg-success' : 'bg-border')} />
      {fetching ? 'Updating…' : `Updated ${shortAgo(updatedAt, now)}`}
    </span>
  )
}

/**
 * A number that briefly highlights when it changes, so a call landing while you are
 * looking at the page is visible without re-reading every tile.
 */
export function LiveNumber({ value, className }: { value: number; className?: string }) {
  const previous = useRef(value)
  const [changed, setChanged] = useState<'up' | 'down' | null>(null)
  // Counts to the new value as well as flashing: the movement shows the size of the change, the
  // colour shows its direction.
  const shown = useCountUp(value)
  useEffect(() => {
    if (previous.current === value) return
    setChanged(value > previous.current ? 'up' : 'down')
    previous.current = value
    const id = setTimeout(() => setChanged(null), 900)
    return () => clearTimeout(id)
  }, [value])
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
  return (
    <span className={cn('tabular-nums', className)}>
      {String(Math.floor(seconds / 60)).padStart(2, '0')}:{String(seconds % 60).padStart(2, '0')}
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
  useEffect(() => {
    if (seen.current === null) {
      seen.current = new Set(ids)
      return
    }
    const added = ids.filter((id) => !seen.current!.has(id))
    ids.forEach((id) => seen.current!.add(id))
    if (!added.length) return
    setFresh(new Set(added))
    const id = setTimeout(() => setFresh(new Set()), 2500)
    return () => clearTimeout(id)
    // Compared by identity of the list contents, not the array reference a poll rebuilds each time.
  }, [ids.join('|')]) // eslint-disable-line react-hooks/exhaustive-deps
  return fresh
}
