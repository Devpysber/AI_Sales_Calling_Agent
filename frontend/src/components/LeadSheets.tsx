import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, Building2, CalendarClock, Mail, MapPin, Pencil, Phone, PhoneCall, Trash2 } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import ActivityFeed from '@/components/ActivityFeed'
import CallSheet from '@/components/CallSheet'
import { CallStatusBadge, LeadStatusBadge, QualificationBadge } from '@/components/status'
import { Avatar, Badge, Button, Field, Input, Select, Sheet, Skeleton, Switch, Tabs, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import type { ActivityEvent, Call, Lead, Page } from '@/lib/types'
import { CALL_STATUSES, formatDate, formatDuration, LANGUAGES, LEAD_STATUSES, QUALIFICATIONS } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

export type CallPurpose = 'confirm_meeting' | 'follow_up'

export function useStartCall() {
  const { base } = useAgent()
  const qc = useQueryClient()
  return useMutation({
    /** purpose: 'confirm_meeting' | 'follow_up' makes the agent open with that goal instead of a sales pitch. */
    mutationFn: (arg: number | { leadId: number; purpose?: CallPurpose }) => {
      const { leadId, purpose } = typeof arg === 'number' ? { leadId: arg, purpose: undefined } : arg
      return api<{ call_id: number }>(`${base}/calls`, { method: 'POST', json: { lead_id: leadId, purpose } })
    },
    onSuccess: (_data, arg) => {
      const purpose = typeof arg === 'number' ? undefined : arg.purpose
      const what = purpose === 'confirm_meeting' ? 'to confirm the meeting' : purpose === 'follow_up' ? 'for the follow-up' : 'now'
      toast.success(`Calling ${what}`, { description: 'The phone will ring in a few seconds. Watch it live in Calls.' })
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['calls'] })
    },
    onError: (e) => toast.error('Call not placed', { description: e.message }),
  })
}

export function LeadSheet({ leadId, onClose, onEdit }: { leadId: number | null; onClose: () => void; onEdit: (lead: Lead) => void }) {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [tab, setTab] = useState<'overview' | 'calls' | 'activity'>('overview')
  const [callId, setCallId] = useState<number | null>(null)
  const enabled = leadId !== null
  const lead = useQuery({ queryKey: ['lead', leadId], queryFn: () => api<Lead>(`${base}/leads/${leadId}`), enabled, refetchInterval: 5000 })
  const calls = useQuery({ queryKey: ['calls', 'lead', leadId], queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { lead_id: leadId, page_size: 50 } }), enabled, refetchInterval: 5000 })
  const activity = useQuery({ queryKey: ['activity', 'lead', leadId], queryFn: () => api<ActivityEvent[]>(`${base}/leads/${leadId}/activity`), enabled, refetchInterval: 8000 })
  const startCall = useStartCall()
  const remove = useMutation({
    mutationFn: () => api(`${base}/leads/${leadId}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Lead deleted'); qc.invalidateQueries({ queryKey: ['leads'] }); onClose() },
  })

  const l = lead.data
  const row = (label: string, value: ReactNode) => (
    <div className="grid grid-cols-[140px_1fr] gap-3 py-2 text-sm"><dt className="text-muted">{label}</dt><dd className="min-w-0 break-words text-fg">{value || <span className="text-muted">—</span>}</dd></div>
  )

  return (
    <>
      <Sheet open={enabled} onClose={onClose} width="max-w-2xl"
        title={l ? <span className="flex items-center gap-3"><Avatar name={l.name ?? l.phone} className="size-9" />{l.name ?? 'Unnamed lead'}</span> : 'Lead'}
        description={l && <span className="flex flex-wrap items-center gap-2 pl-12"><LeadStatusBadge status={l.status} /><QualificationBadge value={l.qualification} />{l.do_not_call && <Badge tone="danger"><Ban className="size-3" />Do not call</Badge>}</span>}
        footer={l && <>
          <Button variant="outline-danger" onClick={async () => {
            if (await confirm({ title: `Delete ${l.name ?? 'this lead'}?`, description: 'The lead and its activity will be removed permanently. Call records are kept.', confirmLabel: 'Delete', danger: true })) remove.mutate()
          }}><Trash2 />Delete</Button>
          <div className="flex-1" />
          <Link to={path(`/leads/${l.id}`)}><Button variant="ghost">Full analysis</Button></Link>
          <Button onClick={() => onEdit(l)}><Pencil />Edit</Button>
          <Button variant="primary" loading={startCall.isPending} disabled={l.do_not_call} onClick={() => startCall.mutate(l.id)}><PhoneCall />Call now</Button>
        </>}>
        {!l ? <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-48" /></div> : (
          <div className="space-y-5">
            <div className="grid gap-2 sm:grid-cols-2">
              {[[Phone, l.phone], [Mail, l.email], [Building2, l.company], [MapPin, l.city]].map(([Icon, value], i) => {
                const I = Icon as typeof Phone
                return <div key={i} className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm"><I className="size-4 text-muted" /><span className="truncate">{(value as string) || <span className="text-muted">—</span>}</span></div>
              })}
            </div>
            {l.meeting_at && (
              <div className="flex items-center gap-3 rounded-xl border border-success/25 bg-success-soft p-3 text-sm text-success">
                <CalendarClock className="size-5" /><span><b>Meeting booked</b> · {l.meeting_at} IST</span>
              </div>
            )}

            <Tabs value={tab} onChange={setTab} items={[
              { value: 'overview', label: 'Overview' },
              { value: 'calls', label: `Calls (${calls.data?.total ?? 0})` },
              { value: 'activity', label: 'Activity' },
            ]} />

            {tab === 'overview' && (
              <dl className="divide-y divide-border">
                {row('Last call', <span className="flex items-center gap-2"><CallStatusBadge status={l.call_status} />{l.retry_count > 0 && <span className="text-xs text-muted">{l.retry_count} retries</span>}</span>)}
                {row('Last contacted', formatDate(l.last_contacted_at))}
                {row('AI summary', l.summary)}
                {row('Requirements', l.requirements)}
                {row('Objections', l.objections)}
                {row('Follow-up', l.follow_up_date)}
                {row('Language', LANGUAGES[l.language] ?? l.language)}
                {row('Source', l.source)}
                {row('Tags', l.tags.length ? <span className="flex flex-wrap gap-1">{l.tags.map((t) => <Badge key={t}>{t}</Badge>)}</span> : null)}
                {row('Notes', l.notes && <span className="whitespace-pre-wrap">{l.notes}</span>)}
                {row('Created', formatDate(l.created_at))}
              </dl>
            )}

            {tab === 'calls' && (
              calls.data?.items.length ? (
                <div className="divide-y divide-border rounded-xl border border-border">
                  {calls.data.items.map((c) => (
                    <button key={c.id} onClick={() => setCallId(c.id)} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-2">
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">{formatDate(c.created_at)}</div>
                        <div className="truncate text-xs text-muted">{c.summary ?? `${c.turns ?? 0} turns · ${c.trigger}`}</div>
                      </div>
                      <QualificationBadge value={c.qualification} />
                      <span className="w-14 text-right text-sm text-muted tabular-nums">{formatDuration(c.duration)}</span>
                      <CallStatusBadge status={c.status} />
                    </button>
                  ))}
                </div>
              ) : <p className="text-sm text-muted">No calls yet.</p>
            )}

            {tab === 'activity' && (activity.data?.length ? <ActivityFeed events={activity.data} onCall={setCallId} /> : <p className="text-sm text-muted">No activity yet.</p>)}
          </div>
        )}
      </Sheet>
      <CallSheet callId={callId} onClose={() => setCallId(null)} />
    </>
  )
}

export function LeadFormSheet({ lead, open, onClose }: { lead: Lead | null; open: boolean; onClose: () => void }) {
  const { base } = useAgent()
  const qc = useQueryClient()
  const editing = !!lead
  const [dnc, setDnc] = useState(lead?.do_not_call ?? false)
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<Lead>(editing ? `${base}/leads/${lead!.id}` : `${base}/leads`, { method: editing ? 'PATCH' : 'POST', json: body }),
    onSuccess: () => {
      toast.success(editing ? 'Lead updated' : 'Lead added')
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['lead'] })
      onClose()
    },
    onError: (e) => toast.error(e.message),
  })

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const data = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>
    const body: Record<string, unknown> = { ...data, tags: (data.tags ?? '').split(',').map((t) => t.trim()).filter(Boolean) }
    if (editing) body.do_not_call = dnc
    for (const k of Object.keys(body)) if (body[k] === '' && !editing) delete body[k]
    save.mutate(body)
  }

  return (
    <Sheet open={open} onClose={onClose} title={editing ? 'Edit lead' : 'Add lead'} description={editing ? lead!.phone : 'Indian 10-digit numbers get +91 automatically.'}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" type="submit" form="lead-form" loading={save.isPending}>{editing ? 'Save changes' : 'Add lead'}</Button></>}>
      <form id="lead-form" key={lead?.id ?? 'new'} onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name"><Input name="name" defaultValue={lead?.name ?? ''} placeholder="Rahul Sharma" /></Field>
        <Field label="Phone *"><Input name="phone" required defaultValue={lead?.phone ?? ''} placeholder="98765 43210" inputMode="tel" /></Field>
        <Field label="Company"><Input name="company" defaultValue={lead?.company ?? ''} /></Field>
        <Field label="Email"><Input name="email" type="email" defaultValue={lead?.email ?? ''} /></Field>
        <Field label="City"><Input name="city" defaultValue={lead?.city ?? ''} /></Field>
        <Field label="Call language">
          <Select name="language" defaultValue={lead?.language ?? 'en-IN'}>{Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
        </Field>
        <Field label="Status"><Select name="status" defaultValue={lead?.status ?? 'New'}>{LEAD_STATUSES.map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <Field label="Tags" hint="Comma separated"><Input name="tags" defaultValue={lead?.tags.join(', ') ?? ''} placeholder="webinar, enterprise" /></Field>
        {editing && <>
          <Field label="Qualification"><Select name="qualification" defaultValue={lead!.qualification ?? ''}><option value="">—</option>{QUALIFICATIONS.map((q) => <option key={q}>{q}</option>)}</Select></Field>
          <Field label="Call status"><Select name="call_status" defaultValue={lead!.call_status ?? ''}><option value="">—</option>{CALL_STATUSES.map((q) => <option key={q}>{q}</option>)}</Select></Field>
          <Field label="Meeting (IST)" hint="YYYY-MM-DD HH:MM"><Input name="meeting_at" defaultValue={lead!.meeting_at ?? ''} /></Field>
          <Field label="Follow-up date"><Input name="follow_up_date" type="date" defaultValue={lead!.follow_up_date ?? ''} /></Field>
        </>}
        <Field label="Notes" className="sm:col-span-2" hint="Visible to the AI agent during calls"><Textarea name="notes" rows={3} defaultValue={lead?.notes ?? ''} /></Field>
        {editing && (
          <div className="flex items-center justify-between rounded-lg border border-border p-3 sm:col-span-2">
            <div><div className="text-sm font-medium">Do not call</div><div className="text-xs text-muted">Excludes this lead from all manual and automated calls.</div></div>
            <Switch checked={dnc} onChange={setDnc} label="Do not call" />
          </div>
        )}
      </form>
    </Sheet>
  )
}
