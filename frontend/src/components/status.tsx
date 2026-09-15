import { Flame, Snowflake, Sun } from 'lucide-react'
import { Badge } from '@/components/ui'

const CALL_TONE: Record<string, Parameters<typeof Badge>[0]['tone']> = {
  Completed: 'success', 'In Progress': 'info', Ringing: 'info', Queued: 'info', Pending: 'warning',
  'No Answer': 'warning', Busy: 'warning', Failed: 'danger', Canceled: 'neutral',
}
const LEAD_TONE: Record<string, Parameters<typeof Badge>[0]['tone']> = {
  New: 'brand', Contacted: 'neutral', Interested: 'info', 'Meeting Booked': 'success', 'Follow Up': 'warning',
  'Not Interested': 'danger', 'Do Not Call': 'danger', 'Closed Won': 'success', 'Closed Lost': 'neutral',
}

export function CallStatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-muted">—</span>
  const live = ['In Progress', 'Ringing', 'Queued'].includes(status)
  return <Badge tone={CALL_TONE[status] ?? 'neutral'} dot={!live} pulse={live}>{status}</Badge>
}

export function LeadStatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-muted">—</span>
  return <Badge tone={LEAD_TONE[status] ?? 'neutral'}>{status}</Badge>
}

export function QualificationBadge({ value }: { value: string | null }) {
  if (!value) return <span className="text-muted">—</span>
  if (value === 'Hot') return <Badge tone="danger"><Flame className="size-3" />Hot</Badge>
  if (value === 'Warm') return <Badge tone="warning"><Sun className="size-3" />Warm</Badge>
  if (value === 'Cold') return <Badge tone="info"><Snowflake className="size-3" />Cold</Badge>
  return <Badge>{value}</Badge>
}

export function SentimentDot({ value }: { value: string | null }) {
  if (!value) return null
  const color = value === 'positive' ? 'bg-success' : value === 'negative' ? 'bg-danger' : 'bg-muted'
  return <span className="inline-flex items-center gap-1.5 text-xs text-muted capitalize"><span className={`size-2 rounded-full ${color}`} />{value}</span>
}
