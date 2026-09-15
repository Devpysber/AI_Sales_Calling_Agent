import {
  Bot, CalendarCheck, CircleAlert, Clock, FileText, Mail, Phone, PhoneIncoming, PhoneOff, Settings2, Sparkles,
  Upload, UserPlus, UserRoundPen, Users,
} from 'lucide-react'
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
  return (
    <ol className="relative">
      {events.map((e, i) => {
        const [, Icon, color] = iconFor(e.type)
        const data = e.data && e.type.startsWith('ai.') ? Object.entries(e.data).filter(([, v]) => v && typeof v !== 'object') : []
        return (
          <li key={e.id} className="relative flex gap-3 pb-5 last:pb-0">
            {i < events.length - 1 && <span className="absolute top-8 bottom-0 left-[15px] w-px bg-border" />}
            <span className={cn('relative grid size-8 shrink-0 place-items-center rounded-full', color)}><Icon className="size-3.5" /></span>
            <div className="min-w-0 flex-1 pt-1">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-medium text-fg">{e.title}</span>
                {showLead && e.lead_name && e.lead_id && (
                  <button className="text-brand hover:underline" onClick={() => onLead?.(e.lead_id!)}>{e.lead_name}</button>
                )}
              </div>
              {e.detail && !compact && <p className="mt-0.5 text-[13px] break-words text-muted">{e.detail}</p>}
              {data.length > 0 && !compact && (
                <dl className="mt-2 grid gap-1 rounded-lg border border-border bg-surface-2 p-2.5 text-xs">
                  {data.slice(0, 6).map(([k, v]) => (
                    <div key={k} className="flex gap-2"><dt className="w-28 shrink-0 text-muted capitalize">{k.replace(/_/g, ' ')}</dt><dd className="break-words text-fg-2">{String(v)}</dd></div>
                  ))}
                </dl>
              )}
              <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                <span>{timeAgo(e.created_at)}</span>
                <span>·</span>
                <span>{e.actor === "ai" ? "AI" : e.actor.charAt(0).toUpperCase() + e.actor.slice(1)}</span>
                {e.call_id && onCall && <><span>·</span><button className="text-brand hover:underline" onClick={() => onCall(e.call_id!)}>View call</button></>}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
