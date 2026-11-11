import { Flame, Snowflake, Sun } from 'lucide-react'
import { Badge } from '@/components/ui'

type Tone = Parameters<typeof Badge>[0]['tone']

const CALL_TONE: Record<string, Tone> = {
  Completed: 'success', 'In Progress': 'info', Ringing: 'info', Queued: 'info', Pending: 'warning',
  'No Answer': 'warning', Busy: 'warning', Failed: 'danger', Canceled: 'neutral',
}
const LEAD_TONE: Record<string, Tone> = {
  New: 'brand', Contacted: 'neutral', Interested: 'info', 'Meeting Booked': 'success', 'Follow Up': 'warning',
  'Not Interested': 'danger', 'Do Not Call': 'danger', 'Closed Won': 'success', 'Closed Lost': 'neutral',
}
const LIVE_CALL = new Set(['In Progress', 'Ringing', 'Queued'])

// Badges never grow past their cell; a long unknown status truncates instead of pushing a 360px row sideways.
const BADGE = 'max-w-full shrink-0'
const Empty = () => <span className="text-muted">—</span>

export function CallStatusBadge({ status }: { status?: string | null }) {
  const s = status?.trim()
  if (!s) return <Empty />
  const live = LIVE_CALL.has(s)
  return <Badge className={BADGE} tone={CALL_TONE[s] ?? 'neutral'} dot={!live} pulse={live}><span className="truncate">{s}</span></Badge>
}

export function LeadStatusBadge({ status }: { status?: string | null }) {
  const s = status?.trim()
  if (!s) return <Empty />
  return <Badge className={BADGE} tone={LEAD_TONE[s] ?? 'neutral'}><span className="truncate">{s}</span></Badge>
}

export function QualificationBadge({ value }: { value?: string | null }) {
  const v = value?.trim()
  if (!v) return <Empty />
  // The LLM occasionally returns "hot"/"WARM"; normalise so temperature never falls through to a grey badge.
  const key = v.charAt(0).toUpperCase() + v.slice(1).toLowerCase()
  if (key === 'Hot') return <Badge className={BADGE} tone="danger"><Flame className="size-3 shrink-0" aria-hidden />Hot</Badge>
  if (key === 'Warm') return <Badge className={BADGE} tone="warning"><Sun className="size-3 shrink-0" aria-hidden />Warm</Badge>
  if (key === 'Cold') return <Badge className={BADGE} tone="info"><Snowflake className="size-3 shrink-0" aria-hidden />Cold</Badge>
  return <Badge className={BADGE}><span className="truncate">{v}</span></Badge>
}

export function SentimentDot({ value }: { value?: string | null }) {
  const v = value?.trim().toLowerCase()
  if (!v) return null
  const color = v === 'positive' ? 'bg-success' : v === 'negative' ? 'bg-danger' : 'bg-muted'
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 text-xs text-muted capitalize" title={`Sentiment: ${v}`}>
      <span className={`size-2 shrink-0 rounded-full ${color}`} aria-hidden />
      <span className="truncate">{v}</span>
    </span>
  )
}
