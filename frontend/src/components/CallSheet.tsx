import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Clock, Gauge, PhoneOff, Timer, User } from 'lucide-react'
import { toast } from 'sonner'
import ActivityFeed from '@/components/ActivityFeed'
import { CallStatusBadge, QualificationBadge, SentimentDot } from '@/components/status'
import { Badge, Button, Sheet, Skeleton, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import type { Call } from '@/lib/types'
import { callHandledBy, callParty, cn, formatDate, formatDuration, titleCase } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

const LIVE = ['Queued', 'Ringing', 'In Progress']
// How long after a call ends we still expect the backend to produce a summary.
// Beyond this the LLM step has either failed silently or was skipped, so we stop polling.
const SUMMARY_GRACE_MS = 2 * 60 * 1000

// A summary is only "on its way" when the call finished moments ago and the customer actually spoke;
// the backend skips summarising assistant-only transcripts and gives up silently when the LLM is down.
const summaryPending = (c: Call) => {
  if (c.summary || c.status !== 'Completed' || c.trigger === 'internal') return false // team check-ins are never summarised
  const turns = c.transcript ?? []
  if (turns.length < 2 || !turns.some((t) => t.role === 'customer')) return false
  const ended = Date.parse(c.ended_at ?? c.created_at)
  return Number.isFinite(ended) && Date.now() - ended < SUMMARY_GRACE_MS
}

import LiveSupervision from '@/components/LiveSupervision'
import { VoiceOrb, Waveform } from '@/components/VoiceViz'

export default function CallSheet({ callId, onClose, onOpenLead }: { callId: number | null; onClose: () => void; onOpenLead?: (id: number) => void }) {
  const { base, agent } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data: call, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['call', callId],
    queryFn: () => api<Call>(`${base}/calls/${callId}`),
    enabled: callId !== null,
    refetchInterval: (q) => (q.state.data && (LIVE.includes(q.state.data.status) || summaryPending(q.state.data)) ? 2500 : false),
  })
  const hangup = useMutation({
    mutationFn: () => api(`${base}/calls/${callId}/hangup`, { method: 'POST' }),
    onSuccess: () => { toast.success('Hanging up'); qc.invalidateQueries({ queryKey: ['calls'] }); qc.invalidateQueries({ queryKey: ['call', callId] }) },
    onError: (e) => toast.error(e.message),
  })

  const live = call && LIVE.includes(call.status)

  return (
    <Sheet open={callId !== null} onClose={onClose} width="max-w-2xl"
      title={call ? <span className="flex min-w-0 items-center gap-2"><span className="min-w-0 truncate">{callParty(call)}</span><CallStatusBadge status={call.status} /></span> : 'Call'}
      description={call && `${call.direction === 'inbound' ? 'Inbound' : 'Outbound'} · ${formatDate(call.created_at)} · ${(call.direction === 'inbound' ? call.from_number : call.to_number) ?? 'Unknown number'} · ${callHandledBy(call, agent?.name)}`}
      footer={call && <>
        {call.lead_id && onOpenLead && <Button onClick={() => onOpenLead(call.lead_id!)}><User />Open lead</Button>}
        {live && <Button variant="danger" loading={hangup.isPending} onClick={async () => {
          if (await confirm({ title: 'Hang up this call?', description: 'The customer will be disconnected immediately.', confirmLabel: 'Hang up', danger: true })) hangup.mutate()
        }}><PhoneOff />Hang up</Button>}
      </>}>
      {isError && !call ? (
        <div className="flex flex-col items-start gap-3 rounded-lg bg-danger-soft px-3 py-3 text-sm text-danger">
          <span className="break-words">{(error as Error)?.message || 'Could not load this call.'}</span>
          <Button variant="secondary" size="sm" onClick={() => refetch()}>Retry</Button>
        </div>
      ) : isLoading || !call ? <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-64" /></div> : (
        <div className="space-y-6">
          {isError && <p className="rounded-lg bg-warning-soft px-3 py-2 text-xs break-words text-warning">Could not refresh this call — retrying…</p>}
          {live && <LiveSupervision callId={call.id} />}
          
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              [Timer, 'Duration', formatDuration(call.duration)],
              [Clock, 'Trigger', titleCase(call.trigger)],
              [Gauge, 'AI latency', call.avg_latency_ms ? `${(call.avg_latency_ms / 1000).toFixed(1)}s` : '—'],
              [Bot, 'Outcome', call.outcome ? titleCase(call.outcome) : '—'],
            ].map(([Icon, label, value]) => {
              const I = Icon as typeof Timer
              return (
                <div key={label as string} className="min-w-0 rounded-lg border border-border bg-surface-2 p-3">
                  <div className="flex items-center gap-1.5 text-xs text-muted"><I className="size-3.5" />{label as string}</div>
                  <div className="mt-1 truncate text-sm font-semibold" title={value as string}>{value as string}</div>
                </div>
              )
            })}
          </div>

          {(call.summary || (call.status === 'Completed' && (call.transcript?.length ?? 0) > 1)) && (
            <section className="rounded-xl border border-brand/20 bg-brand-soft/50 p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-brand">AI summary</span>
                <QualificationBadge value={call.qualification} />
                <SentimentDot value={call.sentiment} />
              </div>
              <p className={cn('text-sm leading-relaxed', call.summary ? 'text-fg-2' : 'text-muted')}>{call.summary ?? (summaryPending(call) ? 'Generating summary…' : 'No summary available for this call.')}</p>
            </section>
          )}

          {call.error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm break-words text-danger">{call.error}</p>}
          {call.recording_url && <audio controls src={call.recording_url} className="w-full" />}

          <section>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Transcript</h3>
              {live && <Badge tone="info" pulse>Live</Badge>}
            </div>
            {call.transcript?.length ? (
              <div className="space-y-3">
                {call.transcript.map((t, i) => (
                  // Keyed by position, so only turns that arrive on a later poll play their entrance:
                  // on a live call the transcript visibly grows instead of being redrawn.
                  <div key={i} className={cn('reveal reveal-in flex gap-2.5', t.role === 'customer' ? 'reveal-right flex-row-reverse' : 'reveal-left')}>
                    <span className={cn('grid size-7 shrink-0 place-items-center rounded-full text-xs',
                      t.role === 'assistant' ? 'bg-brand text-brand-fg' : 'bg-surface-2 text-fg-2 ring-1 ring-border')}>
                      {t.role === 'assistant' ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
                    </span>
                    <div className={cn('min-w-0 max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed break-words sm:max-w-[80%]',
                      t.role === 'assistant' ? 'rounded-tl-sm bg-brand-soft text-fg' : 'rounded-tr-sm border border-border bg-surface text-fg')}>
                      {t.text}
                    </div>
                  </div>
                ))}
                {live && (
                  <div className="flex items-center gap-2 pl-9 text-xs text-muted">
                    <Waveform bars={5} className="h-3.5 text-success" />Conversation in progress
                  </div>
                )}
              </div>
            ) : live ? (
              <div className="flex flex-col items-center gap-3 py-6 text-sm text-muted">
                <VoiceOrb state="listening" size={56} />Waiting for the conversation to start…
              </div>
            ) : <p className="text-sm text-muted">No conversation was captured on this call.</p>}
          </section>

          {call.events && call.events.length > 0 && (
            <section>
              <h3 className="mb-3 text-sm font-semibold">Timeline</h3>
              <ActivityFeed events={call.events} compact />
            </section>
          )}

          <p className="text-xs break-words text-muted">Plivo ID: <code className="font-mono break-all">{call.call_uuid ?? '—'}</code>{call.hangup_cause && ` · ${call.hangup_cause}`}</p>
        </div>
      )}
    </Sheet>
  )
}
