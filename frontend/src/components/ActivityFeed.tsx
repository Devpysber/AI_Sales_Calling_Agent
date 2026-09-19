import {
  Bot, CalendarCheck, CircleAlert, Clock, FileText, Mail, Phone, PhoneIncoming, PhoneOff, Settings2, Sparkles,
  Upload, UserPlus, UserRoundPen, Users,
} from 'lucide-react'
import { ShowMore } from '@/components/ui'
import type { ActivityEvent } from '@/lib/types'
import { cn, timeAgo } from '@/lib/utils'

const ICONS: [string, typeof Phone, string][] = [
  ['call.started', Phone, 'text-info bg-info-soft'],
  ['call.answered', Phone, 'text-success bg-success-soft'],
  ['call.inbound', PhoneIncoming, 'text-info bg-info-soft'],
  ['call.ended', PhoneOff, 'text-fg-2 bg-surface-2'],
  ['call.failed', CircleAlert, 'text-danger bg-danger-soft'],
  ['meeting.booked', CalendarCheck, 'text-success bg-success-soft'],
  ['ai.summary', Sparkles, 'text-brand bg-brand-soft'],
  ['ai.', Bot, 'text-brand bg-brand-soft'],
  ['lead.created', UserPlus, 'text-brand bg-brand-soft'],
  ['lead.imported', Upload, 'text-brand bg-brand-soft'],
  ['lead.queued', Clock, 'text-warning bg-warning-soft'],
  ['lead.', UserRoundPen, 'text-fg-2 bg-surface-2'],
  ['document.', FileText, 'text-info bg-info-soft'],
  ['email', Mail, 'text-fg-2 bg-surface-2'],
  ['automation', Clock, 'text-warning bg-warning-soft'],
  ['settings', Settings2, 'text-fg-2 bg-surface-2'],
]

function iconFor(type: string) {
  return ICONS.find(([prefix]) => type.startsWith(prefix)) ?? ['', Users, 'text-fg-2 bg-surface-2'] as const
}

export default function ActivityFeed({ events, showLead, onLead, onCall, compact }: {
  events: ActivityEvent[]; showLead?: boolean; onLead?: (id: number) => void; onCall?: (id: number) => void; compact?: boolean
}) {
  // Collapse identical consecutive events (same title, lead and actor), e.g. repeated settings saves
  const grouped: (ActivityEvent & { repeat: number })[] = []
  for (const e of events) {
    const last = grouped[grouped.length - 1]
    if (last && !e.call_id && last.title === e.title && last.lead_id === e.lead_id && last.actor === e.actor && last.type === e.type) last.repeat++
    else grouped.push({ ...e, repeat: 1 })
  }
  if (grouped.length === 0) return <p className="text-sm text-muted">No activity yet.</p>
  return (
    <ol className="relative min-w-0">
      {grouped.map((e, i) => {
        const [, Icon, color] = iconFor(e.type)
        const data = e.data && typeof e.data === 'object' && e.type.startsWith('ai.') ? Object.entries(e.data).filter(([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object') : []
        const actor = e.actor ?? ''
        return (
          // Events arrive down the timeline in order; the rail draws down behind them and the
          // newest event keeps a ring leaving its icon.
          <li key={e.id} style={{ animationDelay: `${Math.min(i, 10) * 55}ms` }} className="reveal reveal-in reveal-left group relative flex gap-3 pb-5 last:pb-0">
            {i < grouped.length - 1 && <span className="line-down absolute top-8 bottom-0 left-[15px] w-px bg-border" style={{ animationDelay: `${Math.min(i, 10) * 55 + 120}ms` }} />}
            <span className={cn('relative grid size-8 shrink-0 place-items-center rounded-full transition-transform duration-300 group-hover:scale-110', color)}>
              {i === 0 && <span className="absolute inset-0 animate-live-ring rounded-full bg-current opacity-30" />}
              <Icon className="relative size-3.5" />
            </span>
            <div className="min-w-0 flex-1 pt-1">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm">
                <span className="min-w-0 font-medium break-words text-fg">{e.title}</span>
                {e.repeat > 1 && <span className="rounded-full bg-surface-2 px-1.5 text-[11px] font-semibold text-muted tabular-nums">×{e.repeat}</span>}
                {showLead && e.lead_name && e.lead_id && (
                  <button type="button" className="relative min-w-0 truncate py-1 text-left text-brand hover:underline after:absolute after:-inset-y-2 after:-inset-x-1 after:content-['']" onClick={() => onLead?.(e.lead_id!)}>{e.lead_name}</button>
                )}
              </div>
              {e.detail && !compact && <p className="mt-0.5 text-[13px] break-words text-muted"><ShowMore text={e.detail} lines={2} limit={180} /></p>}
              {data.length > 0 && !compact && (
                <dl className="mt-2 grid gap-1 rounded-lg border border-border bg-surface-2 p-2.5 text-xs">
                  {data.slice(0, 6).map(([k, v]) => (
                    <div key={k} className="flex min-w-0 flex-col gap-0.5 sm:flex-row sm:gap-2"><dt className="shrink-0 text-muted capitalize sm:w-28">{k.replace(/_/g, ' ')}</dt><dd className="min-w-0 break-words text-fg-2"><ShowMore text={String(v)} lines={2} limit={160} /></dd></div>
                  ))}
                </dl>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <span>{timeAgo(e.created_at)}</span>
                <span>·</span>
                <span>{actor === 'ai' ? 'AI' : actor ? actor.charAt(0).toUpperCase() + actor.slice(1) : 'System'}</span>
                {e.call_id && onCall && <><span>·</span><button type="button" className="relative py-1 text-brand hover:underline after:absolute after:-inset-y-2 after:-inset-x-1 after:content-['']" onClick={() => onCall(e.call_id!)}>View call</button></>}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
