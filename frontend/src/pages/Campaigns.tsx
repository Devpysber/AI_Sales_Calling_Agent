import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Copy, Eye, Mail, Megaphone, MessageCircle, Phone, Plus, RocketIcon, RefreshCw, StopCircle, Trash2, TrendingUp, Users } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, Dialog, EmptyState, Field, Input, Meter, Select, Sheet, Skeleton, StatTile, Tabs, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import { useAssignableUsers } from '@/lib/useAssignableUsers'
import { LEAD_STATUSES, QUALIFICATIONS } from '@/lib/utils'

type Segment = { status?: string[]; qualification?: string[]; tags?: string[]; source?: string; owner_user_id?: number }
type Stats = { targeted?: number; sent?: number; skipped?: number; failed?: number }
type Campaign = {
  id: number; agent_id: number; name: string; channel: 'call' | 'email' | 'whatsapp'
  status: 'draft' | 'running' | 'cancelling' | 'cancelled' | 'completed'; segment: Segment | null
  subject: string | null; message: string | null; stats: Stats | null
  started_at: string | null; created_at: string
}
type PreviewResult = { count: number; sample: { id: number; name: string; phone: string | null; email: string | null; status: string }[] }
type Draft = { name: string; channel: Campaign['channel']; status: string[]; qualification: string[]; tags: string; source: string; ownerUserId: string; subject: string; message: string }

const CHANNEL_LABEL: Record<string, string> = { call: 'Call', email: 'Email', whatsapp: 'WhatsApp' }
const CHANNEL_ICON: Record<string, typeof Phone> = { call: Phone, email: Mail, whatsapp: MessageCircle }

const emptyDraft = (): Draft => ({ name: '', channel: 'whatsapp', status: [], qualification: [], tags: '', source: '', ownerUserId: '', subject: '', message: '' })

const toDraft = (c: Campaign): Draft => ({
  name: c.name, channel: c.channel,
  status: c.segment?.status ?? [], qualification: c.segment?.qualification ?? [],
  tags: (c.segment?.tags ?? []).join(', '), source: c.segment?.source ?? '',
  ownerUserId: c.segment?.owner_user_id ? String(c.segment.owner_user_id) : '',
  subject: c.subject ?? '', message: c.message ?? '',
})

const segmentOf = (d: Draft): Segment => ({
  status: d.status.length ? d.status : undefined,
  qualification: d.qualification.length ? d.qualification : undefined,
  tags: d.tags.trim() ? d.tags.split(',').map((t) => t.trim()).filter(Boolean) : undefined,
  source: d.source.trim() || undefined,
  owner_user_id: d.ownerUserId ? Number(d.ownerUserId) : undefined,
})

function segmentSummary(s: Segment | null, reps?: { id: number; name: string | null; email: string }[]): string {
  if (!s || (!s.status?.length && !s.qualification?.length && !s.tags?.length && !s.source && !s.owner_user_id)) return 'Everyone'
  const parts: string[] = []
  if (s.status?.length) parts.push(s.status.join('/'))
  if (s.qualification?.length) parts.push(s.qualification.join('/'))
  if (s.tags?.length) parts.push(`tags: ${s.tags.join(', ')}`)
  if (s.source) parts.push(`source: ${s.source}`)
  if (s.owner_user_id) {
    const rep = reps?.find((r) => r.id === s.owner_user_id)
    parts.push(`rep: ${rep?.name || rep?.email || `#${s.owner_user_id}`}`)
  }
  return parts.join(' · ')
}

function statsLine(c: Campaign): string | null {
  const s = c.stats
  if (!s || Object.keys(s).length === 0) return null
  return `${s.targeted ?? 0} targeted · ${s.sent ?? 0} sent · ${s.failed ?? 0} failed · ${s.skipped ?? 0} skipped`
}

function Chips({ options, value, onChange }: { options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const active = value.includes(opt)
        return (
          <button key={opt} type="button" onClick={() => onChange(active ? value.filter((v) => v !== opt) : [...value, opt])}
            className={active ? 'rounded-full border border-brand bg-brand px-3 py-1 text-xs font-semibold text-brand-fg'
              : 'rounded-full border border-border bg-surface px-3 py-1 text-xs font-semibold text-fg-2 hover:border-border-strong'}>
            {opt}
          </button>
        )
      })}
    </div>
  )
}

export default function Campaigns() {
  const { base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const repsQ = useAssignableUsers(base)
  const campaignsQ = useQuery({
    queryKey: ['campaigns', base],
    queryFn: () => api<Campaign[]>(`${base}/crm/campaigns`),
    refetchInterval: (q) => (q.state.data ?? []).some((c) => c.status === 'running' || c.status === 'cancelling') ? 3000 : false,
  })
  const [editing, setEditing] = useState<Campaign | 'new' | null>(null)
  const [previewing, setPreviewing] = useState<{ segment: Segment; channel: Campaign['channel'] } | null>(null)
  const [tab, setTab] = useState<'all' | 'draft' | 'running' | 'completed'>('all')

  const invalidate = () => qc.invalidateQueries({ queryKey: ['campaigns', base] })
  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/campaigns/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Campaign deleted'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })
  const run = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/campaigns/${id}/run`, { method: 'POST' }),
    onSuccess: () => { toast.success('Campaign launched'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })
  const duplicate = useMutation({
    mutationFn: (c: Campaign) => api<Campaign>(`${base}/crm/campaigns`, { method: 'POST', json: {
      name: `${c.name} (copy)`, channel: c.channel, segment: c.segment ?? {}, subject: c.subject ?? undefined, message: c.message ?? undefined,
    } }),
    onSuccess: () => { toast.success('Campaign duplicated'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })
  const cancel = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/campaigns/${id}/cancel`, { method: 'POST' }),
    onSuccess: () => { toast.success('Stopping the campaign'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })

  if (campaignsQ.isError) {
    return <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load campaigns" description={campaignsQ.error?.message}
      action={<Button onClick={() => campaignsQ.refetch()}><RefreshCw />Try again</Button>} /></Card>
  }
  if (!campaignsQ.data) return <div className="grid gap-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24" />)}</div>

  const campaigns = campaignsQ.data
  const totalSent = campaigns.reduce((sum, c) => sum + (c.stats?.sent ?? 0), 0)
  const totalFailed = campaigns.reduce((sum, c) => sum + (c.stats?.failed ?? 0), 0)
  const activeNow = campaigns.filter((c) => c.status === 'running' || c.status === 'cancelling').length
  const successRate = totalSent + totalFailed > 0 ? (totalSent / (totalSent + totalFailed)) * 100 : null
  const successTone = successRate === null ? 'neutral' : successRate >= 80 ? 'success' : successRate >= 40 ? 'warning' : 'danger'
  const tabCounts = {
    all: campaigns.length,
    draft: campaigns.filter((c) => c.status === 'draft').length,
    running: activeNow,
    completed: campaigns.filter((c) => c.status === 'completed' || c.status === 'cancelled').length,
  }
  const filtered = tab === 'all' ? campaigns
    : tab === 'running' ? campaigns.filter((c) => c.status === 'running' || c.status === 'cancelling')
    : tab === 'completed' ? campaigns.filter((c) => c.status === 'completed' || c.status === 'cancelled')
    : campaigns.filter((c) => c.status === tab)

  return (
    <>
      {campaigns.length > 0 && (
        <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label="Campaigns" count={campaigns.length} icon={<Megaphone />} tone="neutral" />
          <StatTile label="Active now" count={activeNow} icon={<RocketIcon />} tone="brand" />
          <StatTile label="Leads reached" count={totalSent} icon={<Users />} tone="success" />
          <StatTile label="Success rate" value={successRate === null ? '—' : `${successRate.toFixed(1)}%`} icon={<TrendingUp />} tone={successTone} />
        </div>
      )}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-sm text-muted">Reach a segment of leads in bulk by call, email or WhatsApp.</p>
        <Button variant="primary" onClick={() => setEditing('new')}><Plus />New campaign</Button>
      </div>
      {campaigns.length > 0 && (
        <div className="mb-3">
          <Tabs value={tab} onChange={setTab} items={[
            { value: 'all', label: `All (${tabCounts.all})` },
            { value: 'draft', label: `Draft (${tabCounts.draft})` },
            { value: 'running', label: `Running (${tabCounts.running})` },
            { value: 'completed', label: `Completed (${tabCounts.completed})` },
          ]} />
        </div>
      )}
      {campaigns.length === 0 ? (
        <Card><EmptyState icon={<Megaphone />} title="No campaigns yet" description="Build a segment and blast it by call, email or WhatsApp."
          action={<Button variant="primary" onClick={() => setEditing('new')}><Plus />New campaign</Button>} /></Card>
      ) : filtered.length === 0 ? (
        <Card><EmptyState icon={<Megaphone />} title="No campaigns match this filter" description="Try a different status tab." /></Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((c) => {
            const ChannelIcon = CHANNEL_ICON[c.channel]
            const sent = c.stats?.sent ?? 0, failed = c.stats?.failed ?? 0, skipped = c.stats?.skipped ?? 0
            const successPct = sent + failed > 0 ? (sent / (sent + failed)) * 100 : null
            return (
              <Card key={c.id} className="flex flex-col gap-3 p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-bold break-words">{c.name}</h3>
                      {ChannelIcon && <ChannelIcon className="size-3.5 text-muted" />}
                      <Badge tone="neutral">{CHANNEL_LABEL[c.channel] ?? c.channel}</Badge>
                      <Badge tone={c.status === 'completed' ? 'success' : c.status === 'running' ? 'brand' : c.status === 'cancelling' ? 'warning' : c.status === 'cancelled' ? 'danger' : 'neutral'}>
                        {c.status === 'cancelling' ? 'stopping…' : c.status}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs break-words text-muted">Targets: {segmentSummary(c.segment, repsQ.data)}</p>
                    {c.stats?.targeted ? (
                      <div className="mt-1.5 max-w-xs">
                        <Meter value={(sent + failed + skipped) / c.stats.targeted * 100} tone={c.status === 'completed' ? 'success' : 'brand'} />
                        <p className="mt-1 text-xs break-words text-muted">
                          {sent} sent · {failed} failed · {skipped} skipped
                          {successPct !== null && ` (${successPct.toFixed(1)}% success)`}
                        </p>
                      </div>
                    ) : (
                      <p className="mt-0.5 text-xs break-words text-muted">{statsLine(c) ?? 'Not launched yet'}</p>
                    )}
                    <p className="mt-0.5 text-xs break-words text-muted">
                      {c.started_at ? `Started ${new Date(c.started_at).toLocaleString()}` : `Created ${new Date(c.created_at).toLocaleString()}`}
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => setPreviewing({ segment: c.segment ?? {}, channel: c.channel })}><Eye />Preview audience</Button>
                  {c.status === 'draft' && <Button size="sm" onClick={() => setEditing(c)}>Edit</Button>}
                  {c.status === 'draft' && (
                    <Button size="sm" variant="primary" loading={run.isPending} onClick={async () => {
                      const preview = await api<PreviewResult>(`${base}/crm/campaigns/preview`, { method: 'POST', json: { segment: c.segment ?? {}, channel: c.channel } })
                      if (await confirm({
                        title: 'Launch this campaign?',
                        description: `This will reach ${preview.count} lead(s) by ${CHANNEL_LABEL[c.channel] ?? c.channel}. This can't be undone.`,
                        confirmLabel: 'Launch', danger: false,
                      })) run.mutate(c.id)
                    }}><RocketIcon />Launch campaign</Button>
                  )}
                  {c.status === 'running' && (
                    <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={async () => {
                      if (await confirm({ title: 'Stop this campaign?', description: `"${c.name}" will stop sending after the lead currently in progress. Already-sent messages aren't recalled.`, confirmLabel: 'Stop', danger: true })) cancel.mutate(c.id)
                    }}><StopCircle />Stop</Button>
                  )}
                  <Button size="sm" loading={duplicate.isPending} onClick={() => duplicate.mutate(c)}><Copy />Duplicate</Button>
                  {c.status === 'draft' && (
                    <Button size="sm" variant="ghost" onClick={async () => {
                      if (await confirm({ title: 'Delete this campaign?', description: `"${c.name}" will be removed.`, confirmLabel: 'Delete', danger: true })) remove.mutate(c.id)
                    }}><Trash2 />Delete</Button>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}

      {editing && <CampaignForm campaign={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); invalidate() }} onPreview={(segment, channel) => setPreviewing({ segment, channel })} />}
      {previewing && <PreviewDialog segment={previewing.segment} channel={previewing.channel} onClose={() => setPreviewing(null)} />}
    </>
  )
}

function CampaignForm({ campaign, onClose, onSaved, onPreview }: {
  campaign: Campaign | null; onClose: () => void; onSaved: () => void
  onPreview: (segment: Segment, channel: Campaign['channel']) => void
}) {
  const { base, path } = useAgent()
  const [draft, setDraft] = useState<Draft>(() => campaign ? toDraft(campaign) : emptyDraft())
  const repsQ = useAssignableUsers(base)

  const livePreviewQ = useQuery({
    queryKey: ['campaigns-live-preview', JSON.stringify(segmentOf(draft)), draft.channel],
    queryFn: () => api<PreviewResult>(`${base}/crm/campaigns/preview`, { method: 'POST', json: { segment: segmentOf(draft), channel: draft.channel } }),
  })
  const templatesQ = useQuery({
    queryKey: ['whatsapp-templates', base],
    queryFn: () => api<{ is_meta_approved: boolean }[]>(`${base}/whatsapp/templates`),
    enabled: draft.channel === 'whatsapp',
  })
  const hasApprovedTemplate = (templatesQ.data ?? []).some((t) => t.is_meta_approved)

  const save = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { name: draft.name, channel: draft.channel, segment: segmentOf(draft) }
      if (draft.channel === 'email') payload.subject = draft.subject
      if (draft.channel === 'email' || draft.channel === 'whatsapp') payload.message = draft.message
      return campaign ? api<Campaign>(`${base}/crm/campaigns/${campaign.id}`, { method: 'PATCH', json: payload })
        : api<Campaign>(`${base}/crm/campaigns`, { method: 'POST', json: payload })
    },
    onSuccess: () => { toast.success(campaign ? 'Campaign updated' : 'Campaign created'); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })

  const problem = !draft.name.trim() ? 'Name is required' : null

  return (
    <Sheet open onClose={onClose} title={campaign ? `Edit "${campaign.name}"` : 'New campaign'}
      description="Pick a segment of leads, then reach them by call, email or WhatsApp."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={() => onPreview(segmentOf(draft), draft.channel)}><Eye />Preview audience</Button>
        <Button variant="primary" loading={save.isPending} disabled={!!problem} title={problem ?? undefined} onClick={() => save.mutate()}>
          {campaign ? 'Save changes' : 'Create campaign'}
        </Button>
      </>}>
      <div className="grid gap-4">
        <Field label="Name"><Input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} placeholder="e.g. Spring re-engagement" /></Field>
        <Field label="Channel">
          <Select value={draft.channel} onChange={(e) => setDraft((d) => ({ ...d, channel: e.target.value as Draft['channel'] }))}>
            <option value="call">Call</option>
            <option value="email">Email</option>
            <option value="whatsapp">WhatsApp</option>
          </Select>
        </Field>

        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold">Audience segment</h4>
            <Badge tone="brand">{livePreviewQ.isLoading ? '…' : livePreviewQ.data?.count ?? '…'} lead(s) match</Badge>
          </div>
          <div className="grid gap-3">
            <Field label="Stage" hint="Leave empty to include every stage">
              <Chips options={LEAD_STATUSES} value={draft.status} onChange={(v) => setDraft((d) => ({ ...d, status: v }))} />
            </Field>
            <Field label="Temperature" hint="Leave empty to include every temperature">
              <Chips options={QUALIFICATIONS} value={draft.qualification} onChange={(v) => setDraft((d) => ({ ...d, qualification: v }))} />
            </Field>
            <Field label="Tags" hint="Comma-separated, matches any">
              <Input value={draft.tags} onChange={(e) => setDraft((d) => ({ ...d, tags: e.target.value }))} placeholder="e.g. vip, newsletter" />
            </Field>
            <Field label="Source" hint="Exact match, e.g. website:demo">
              <Input value={draft.source} onChange={(e) => setDraft((d) => ({ ...d, source: e.target.value }))} placeholder="e.g. website" />
            </Field>
            <Field label="Assigned rep" hint="Leave empty to include leads owned by anyone (or no one)">
              <Select value={draft.ownerUserId} onChange={(e) => setDraft((d) => ({ ...d, ownerUserId: e.target.value }))}>
                <option value="">Any rep</option>
                {(repsQ.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.name || r.email}</option>)}
              </Select>
            </Field>
          </div>
        </div>

        {draft.channel === 'email' && (
          <Field label="Subject"><Input value={draft.subject} onChange={(e) => setDraft((d) => ({ ...d, subject: e.target.value }))} placeholder="e.g. We miss you, {name}!" /></Field>
        )}
        {draft.channel === 'whatsapp' && !templatesQ.isLoading && !hasApprovedTemplate && (
          <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm text-warning">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>No approved WhatsApp template yet. Leads outside the 24h reply window will be <strong>skipped</strong>, not sent — <Link to={path('/templates')} className="underline">create and submit one for approval</Link> first.</span>
          </div>
        )}
        {(draft.channel === 'email' || draft.channel === 'whatsapp') && (
          <Field label="Message" hint={draft.channel === 'whatsapp'
            ? 'Supports {name}/{company} placeholders. Used only for replies inside the 24h WhatsApp window — outside it, an approved Meta template is sent automatically instead.'
            : 'Supports {name}/{company} placeholders.'}>
            <Textarea rows={5} value={draft.message} onChange={(e) => setDraft((d) => ({ ...d, message: e.target.value }))} placeholder="Hi {name}, ..." />
          </Field>
        )}
      </div>
    </Sheet>
  )
}

function PreviewDialog({ segment, channel, onClose }: { segment: Segment; channel: Campaign['channel']; onClose: () => void }) {
  const { base } = useAgent()
  const previewQ = useQuery({
    queryKey: ['campaigns', 'preview', base, JSON.stringify(segment), channel],
    queryFn: () => api<PreviewResult>(`${base}/crm/campaigns/preview`, { method: 'POST', json: { segment, channel } }),
  })
  return (
    <Dialog open onClose={onClose} title="Audience preview" description={`Who this campaign would reach by ${CHANNEL_LABEL[channel] ?? channel}, based on the current segment.`}
      footer={<Button onClick={onClose}>Close</Button>}>
      {previewQ.isLoading ? <Skeleton className="h-24" /> : previewQ.isError ? (
        <p className="text-sm text-danger">{(previewQ.error as Error).message}</p>
      ) : (
        <div className="grid gap-3">
          <p className="text-sm font-semibold">{previewQ.data!.count} lead(s) match</p>
          {previewQ.data!.sample.length > 0 && (
            <ul className="grid gap-1 text-sm text-muted">
              {previewQ.data!.sample.map((l) => (
                <li key={l.id}>• {l.name} — {l.phone || l.email || '—'} <span className="text-xs">({l.status})</span></li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Dialog>
  )
}
