import { clsx, type ClassValue } from 'clsx'
import { format, formatDistanceToNowStrict, isToday, isYesterday } from 'date-fns'
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
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const time = format(d, 'h:mm a')
  if (isToday(d)) return withTime ? `Today, ${time}` : 'Today'
  if (isYesterday(d)) return withTime ? `Yesterday, ${time}` : 'Yesterday'
  return format(d, withTime ? 'd MMM, h:mm a' : 'd MMM yyyy')
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
