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
