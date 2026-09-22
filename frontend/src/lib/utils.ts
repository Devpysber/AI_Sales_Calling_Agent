import { clsx, type ClassValue } from 'clsx'
import { formatDistanceToNowStrict } from 'date-fns'
import { twMerge } from 'tailwind-merge'

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs))

export const LANGUAGES: Record<string, string> = {
  'en-IN': 'English', 'hi-IN': 'Hindi', 'bn-IN': 'Bengali', 'ta-IN': 'Tamil', 'te-IN': 'Telugu', 'kn-IN': 'Kannada',
  'ml-IN': 'Malayalam', 'mr-IN': 'Marathi', 'gu-IN': 'Gujarati', 'pa-IN': 'Punjabi', 'od-IN': 'Odia',
}
export const LEAD_STATUSES = ['New', 'Contacted', 'Interested', 'Meeting Booked', 'Follow Up', 'Not Interested', 'Do Not Call', 'Closed Won', 'Closed Lost']
export const CALL_STATUSES = ['Pending', 'Queued', 'Ringing', 'In Progress', 'Completed', 'No Answer', 'Busy', 'Failed', 'Canceled']
export const QUALIFICATIONS = ['Hot', 'Warm', 'Cold']
export const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function formatDate(iso: string | null | undefined, withTime = true) {
  if (!iso) return '—'
  // If it's a UTC date from the database missing the Z (has T), make it explicit
  const isUtc = iso.includes('T') && !iso.endsWith('Z') && !iso.includes('+')
  const d = new Date(isUtc ? iso + 'Z' : iso)
  if (Number.isNaN(d.getTime())) return iso

  const opts: Intl.DateTimeFormatOptions = {
    timeZone: 'Asia/Kolkata',
    month: 'short',
    day: 'numeric',
    year: withTime ? undefined : 'numeric',
    hour: withTime ? 'numeric' : undefined,
    minute: withTime ? '2-digit' : undefined,
    hour12: true,
  }
  const formatter = new Intl.DateTimeFormat('en-IN', opts)
  return formatter.format(d) + (withTime ? " IST" : "")
}

export const timeAgo = (iso: string | null | undefined) =>
  iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: true }) : '—'

export function formatDuration(seconds: number | null | undefined) {
  if (!seconds) return '—'
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  return `${m}m ${String(seconds % 60).padStart(2, '0')}s`
}

export const initials = (name?: string | null) =>
  (name ?? '?').split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join('') || '?'

export const titleCase = (s: string) => s.replace(/[_.]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function playAudio(url: string) {
  const audio = new Audio(url)
  void audio.play()
  return audio
}

/** The number on the other end of a call: the caller for inbound, the person we dialled for outbound. */
export const callerNumber = (call: { direction: string; from_number: string | null; to_number: string | null }) =>
  (call.direction === 'inbound' ? call.from_number : call.to_number) ?? null

/** Who the row is about: the lead's name, else the other party's number, never our own platform number. */
export const callParty = (call: { direction: string; lead_name: string | null; from_number: string | null; to_number: string | null }) =>
  call.lead_name || callerNumber(call) || 'Unknown caller'

/** Who actually spoke to the caller: the human line the call reached, otherwise the AI agent by name. */
export const callHandledBy = (
  call: { trigger: string; transferred_to?: string | null; transferred_to_name?: string | null },
  agentName?: string | null,
) =>
  call.transferred_to
    ? `${call.transferred_to_name || 'Team'} · ${call.transferred_to}`
    : call.trigger === 'forwarded'
      ? 'Team · number not recorded'
      : `${agentName || 'AI agent'} (AI)`


export const LEAD_JOURNEY = ['New', 'Contacted', 'Interested', 'Follow Up', 'Meeting Booked', 'Closed Won']

/**
 * 0-100 engagement score: temperature, pipeline stage, how often they pick up, and recency.
 *
 * One formula for every page. The list and the lead page used to carry their own, weighted
 * differently (stage x7 against x6, recency 12 against 10, and only the lead page counted
 * connect rate at all), so the same lead showed two different scores depending on where you
 * looked. `connected`/`total` come from the lead row on the list and from the loaded calls on
 * the lead page; with no call data the reach term is simply absent, not guessed.
 */
export function leadScore(lead: { do_not_call?: boolean; qualification?: string | null; status: string; last_contacted_at?: string | null; total_calls?: number; connected_calls?: number }, calls?: { status: string; duration: number }[]): number {
  if (lead.do_not_call) return 0
  const temp = ({ Hot: 45, Warm: 28, Cold: 8 } as Record<string, number>)[lead.qualification ?? ''] ?? 12
  const stage = Math.max(0, LEAD_JOURNEY.indexOf(lead.status)) * 6
  const total = calls ? calls.length : lead.total_calls ?? 0
  const connected = calls
    ? calls.filter((c) => c.status === 'Completed' || (c.status === 'Failed' && c.duration > 0)).length
    : lead.connected_calls ?? 0
  const reach = total ? Math.round((15 * connected) / total) : 0
  const recent = lead.last_contacted_at && Date.now() - Date.parse(lead.last_contacted_at) < 7 * 86_400_000 ? 10 : 0
  return Math.min(100, temp + stage + reach + recent)
}
