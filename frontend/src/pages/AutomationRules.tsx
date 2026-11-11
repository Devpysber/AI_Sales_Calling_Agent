import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Pencil, PlayCircle, Plus, RefreshCw, Trash2, XCircle, Zap } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, Dialog, EmptyState, Field, Input, Select, Sheet, Skeleton, Switch, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'

type Condition = { field: string; op: string; value: unknown }
type Action = { type: string } & Record<string, unknown>
type Rule = {
  id: number; agent_id: number; name: string; trigger: string
  conditions: Condition[] | null; actions: Action[] | null
  enabled: boolean; runs: number; failures: number
  last_run_at: string | null; last_error: string | null; created_at: string
}
/** GET /crm/meta: triggers/actions are flat {key: label} maps, no per-action param schema. */
type Meta = { triggers: Record<string, string>; actions: Record<string, string>; ops: string[] }
type TestResult = { matched: boolean; actions: { type: string; ok: boolean; error?: string }[] }

/** An action mid-edit: its extra params stay a free key/value list — the meta endpoint names action
 * types but does not describe each one's param shape, so there is no schema to render typed fields from. */
type ActionDraft = { type: string; params: { key: string; value: string }[] }
type Draft = { name: string; trigger: string; conditions: Condition[]; actions: ActionDraft[] }

// The real param names each action type reads (app/services/rules.py's _run_action) — shown inline since
// there is no "automation docs" page to point to.
const ACTION_PARAMS: Record<string, string> = {
  create_task: 'title, kind (todo/call/email/meeting/follow_up), priority (low/medium/high), assignee, due_in_hours',
  create_deal: 'title, value',
  notify_team: 'subject, body',
  update_lead: 'any lead field to set, e.g. status, qualification, notes',
  add_tag: 'tag',
  send_email: 'subject, body',
  send_whatsapp: 'text',
  queue_call: 'no params needed',
  assign_lead: 'user_id (the rep to assign to; leave blank to unassign)',
}

// update_lead is the one action whose params the backend reads nested under "fields" ({fields: {status:
// "Hot"}}), not flat on the action itself like every other action type -- the generic param editor only
// ever produces flat keys, so round-trip through a "fields" object transparently for this one type.
const toActionDraft = (a: Action): ActionDraft => {
  const type = String(a.type ?? '')
  const source = type === 'update_lead' && a.fields && typeof a.fields === 'object'
    ? a.fields as Record<string, unknown> : Object.fromEntries(Object.entries(a).filter(([k]) => k !== 'type'))
  return { type, params: Object.entries(source).map(([key, value]) => ({ key, value: typeof value === 'string' ? value : JSON.stringify(value) })) }
}
const fromActionDraft = (a: ActionDraft): Action => {
  const values: Record<string, unknown> = {}
  for (const { key, value } of a.params) {
    if (!key.trim()) continue
    values[key.trim()] = value !== '' && !Number.isNaN(Number(value)) ? Number(value) : value
  }
  return a.type === 'update_lead' ? { type: a.type, fields: values } : { type: a.type, ...values }
}

export default function AutomationRules() {
  const { base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const metaQ = useQuery({ queryKey: ['automation-rules-meta'], queryFn: () => api<Meta>(`${base}/crm/meta`) })
  const rulesQ = useQuery({ queryKey: ['automation-rules'], queryFn: () => api<Rule[]>(`${base}/crm/rules`) })
  const [editing, setEditing] = useState<Rule | 'new' | null>(null)
  const [testing, setTesting] = useState<Rule | null>(null)

  const invalidate = () => qc.invalidateQueries({ queryKey: ['automation-rules'] })
  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => api<Rule>(`${base}/crm/rules/${id}`, { method: 'PATCH', json: { enabled } }),
    onSuccess: invalidate, onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/rules/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Rule deleted'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })

  if (metaQ.isError || rulesQ.isError) {
    return <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load automation rules" description={(metaQ.error ?? rulesQ.error)?.message}
      action={<Button onClick={() => { metaQ.refetch(); rulesQ.refetch() }}><RefreshCw />Try again</Button>} /></Card>
  }
  if (!metaQ.data || !rulesQ.data) return <div className="grid gap-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24" />)}</div>

  const meta = metaQ.data
  const rules = rulesQ.data

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-sm text-muted">Fire actions automatically when something happens — a lead comes in, a call ends, a deal is won.</p>
        <Button variant="primary" onClick={() => setEditing('new')}><Plus />New rule</Button>
      </div>
      {rules.length === 0 ? (
        <Card><EmptyState icon={<Zap />} title="No rules yet" description="Create a rule to automate follow-ups, tasks, tags and more."
          action={<Button variant="primary" onClick={() => setEditing('new')}><Plus />New rule</Button>} /></Card>
      ) : (
        <div className="grid gap-3">
          {rules.map((r) => (
            <Card key={r.id} className="flex flex-col gap-3 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-bold break-words">{r.name}</h3>
                    <Badge tone="neutral">{meta.triggers[r.trigger] ?? r.trigger}</Badge>
                    {r.failures > 0 && <Badge tone="danger" dot>{r.failures} failed</Badge>}
                  </div>
                  <p className="mt-1 text-xs break-words text-muted">
                    {r.conditions?.length ? `${r.conditions.length} condition(s) · ` : 'No conditions · '}
                    {r.actions?.length ?? 0} action(s)
                    {r.last_run_at ? ` · last ran ${new Date(r.last_run_at).toLocaleString()}` : ' · never run'}
                  </p>
                  {r.last_error && <p className="mt-1 text-xs break-words text-danger">{r.last_error}</p>}
                </div>
                <Switch checked={r.enabled} onChange={(v) => toggle.mutate({ id: r.id, enabled: v })} label={`Enable ${r.name}`} />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setTesting(r)}><PlayCircle />Test</Button>
                <Button size="sm" onClick={() => setEditing(r)}><Pencil />Edit</Button>
                <Button size="sm" variant="ghost" onClick={async () => {
                  if (await confirm({ title: 'Delete this rule?', description: `"${r.name}" will stop running.`, confirmLabel: 'Delete', danger: true })) remove.mutate(r.id)
                }}><Trash2 />Delete</Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {editing && <RuleForm meta={meta} rule={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); invalidate() }} />}
      {testing && <TestDialog rule={testing} onClose={() => setTesting(null)} />}
    </>
  )
}

function RuleForm({ meta, rule, onClose, onSaved }: { meta: Meta; rule: Rule | null; onClose: () => void; onSaved: () => void }) {
  const { base } = useAgent()
  const triggerKeys = Object.keys(meta.triggers)
  const actionKeys = Object.keys(meta.actions)
  const [draft, setDraft] = useState<Draft>(() => rule ? {
    name: rule.name, trigger: rule.trigger,
    conditions: (rule.conditions ?? []).map((c) => ({ field: c.field, op: c.op, value: String(c.value ?? '') })),
    actions: (rule.actions ?? []).map(toActionDraft),
  } : { name: '', trigger: triggerKeys[0] ?? '', conditions: [], actions: [] })

  const save = useMutation({
    mutationFn: () => {
      const payload = { name: draft.name, trigger: draft.trigger, conditions: draft.conditions, actions: draft.actions.map(fromActionDraft) }
      return rule ? api<Rule>(`${base}/crm/rules/${rule.id}`, { method: 'PATCH', json: payload }) : api<Rule>(`${base}/crm/rules`, { method: 'POST', json: payload })
    },
    onSuccess: () => { toast.success(rule ? 'Rule updated' : 'Rule created'); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })

  const problem = !draft.name.trim() ? 'Name is required' : !draft.trigger ? 'Pick a trigger'
    : draft.actions.length === 0 ? 'Add at least one action' : draft.actions.some((a) => !a.type) ? 'Every action needs a type' : null

  return (
    <Sheet open onClose={onClose} title={rule ? `Edit "${rule.name}"` : 'New automation rule'}
      description="Runs automatically when the trigger happens, if every condition matches."
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={save.isPending} disabled={!!problem} title={problem ?? undefined} onClick={() => save.mutate()}>
          {rule ? 'Save changes' : 'Create rule'}
        </Button>
      </>}>
      <div className="grid gap-4">
        <Field label="Name"><Input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} placeholder="e.g. Tag hot leads" /></Field>
        <Field label="Trigger" hint="The event that checks this rule">
          <Select value={draft.trigger} onChange={(e) => setDraft((d) => ({ ...d, trigger: e.target.value }))}>
            {triggerKeys.map((k) => <option key={k} value={k}>{meta.triggers[k]}</option>)}
          </Select>
        </Field>

        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold">Conditions <span className="text-muted">(all must match — leave empty to always run)</span></h4>
            <Button size="sm" onClick={() => setDraft((d) => ({ ...d, conditions: [...d.conditions, { field: '', op: meta.ops[0] ?? 'eq', value: '' }] }))}><Plus />Add condition</Button>
          </div>
          <div className="grid gap-2">
            {draft.conditions.map((c, i) => (
              <div key={i} className="grid grid-cols-[1fr_auto_1fr_auto] items-end gap-2">
                <Field label="Field"><Input value={c.field} placeholder="e.g. status, score, tags" onChange={(e) => setDraft((d) => ({ ...d, conditions: d.conditions.map((x, j) => j === i ? { ...x, field: e.target.value } : x) }))} /></Field>
                <Field label="Op"><Select value={c.op} onChange={(e) => setDraft((d) => ({ ...d, conditions: d.conditions.map((x, j) => j === i ? { ...x, op: e.target.value } : x) }))}>
                  {meta.ops.map((o) => <option key={o} value={o}>{o}</option>)}
                </Select></Field>
                <Field label="Value"><Input value={String(c.value ?? '')} disabled={c.op === 'exists' || c.op === 'not_exists'}
                  onChange={(e) => setDraft((d) => ({ ...d, conditions: d.conditions.map((x, j) => j === i ? { ...x, value: e.target.value } : x) }))} /></Field>
                <Button size="icon" variant="ghost" aria-label="Remove condition" onClick={() => setDraft((d) => ({ ...d, conditions: d.conditions.filter((_, j) => j !== i) }))}><Trash2 /></Button>
              </div>
            ))}
            {draft.conditions.length === 0 && <p className="text-xs text-muted">No conditions — this rule runs on every matching event.</p>}
          </div>
        </div>

        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold">Actions</h4>
            <Button size="sm" onClick={() => setDraft((d) => ({ ...d, actions: [...d.actions, { type: actionKeys[0] ?? '', params: [] }] }))}><Plus />Add action</Button>
          </div>
          <div className="grid gap-3">
            {draft.actions.map((a, i) => (
              <Card key={i} className="p-3">
                <div className="flex items-end gap-2">
                  <Field label="Action" className="flex-1">
                    <Select value={a.type} onChange={(e) => setDraft((d) => ({ ...d, actions: d.actions.map((x, j) => j === i ? { ...x, type: e.target.value } : x) }))}>
                      {actionKeys.map((k) => <option key={k} value={k}>{meta.actions[k]}</option>)}
                    </Select>
                  </Field>
                  <Button size="icon" variant="ghost" aria-label="Remove action" onClick={() => setDraft((d) => ({ ...d, actions: d.actions.filter((_, j) => j !== i) }))}><Trash2 /></Button>
                </div>
                <p className="mt-1 text-xs text-muted">Params this action reads: {ACTION_PARAMS[a.type] ?? 'e.g. title, tag, text'}.</p>
                <div className="mt-2 grid gap-2">
                  {a.params.map((p, pi) => (
                    <div key={pi} className="grid grid-cols-[1fr_1fr_auto] items-end gap-2">
                      <Field label="Param"><Input value={p.key} placeholder="e.g. title, tag, text"
                        onChange={(e) => setDraft((d) => ({ ...d, actions: d.actions.map((x, j) => j === i ? { ...x, params: x.params.map((y, k) => k === pi ? { ...y, key: e.target.value } : y) } : x) }))} /></Field>
                      <Field label="Value"><Input value={p.value}
                        onChange={(e) => setDraft((d) => ({ ...d, actions: d.actions.map((x, j) => j === i ? { ...x, params: x.params.map((y, k) => k === pi ? { ...y, value: e.target.value } : y) } : x) }))} /></Field>
                      <Button size="icon" variant="ghost" aria-label="Remove param" onClick={() => setDraft((d) => ({ ...d, actions: d.actions.map((x, j) => j === i ? { ...x, params: x.params.filter((_, k) => k !== pi) } : x) }))}><Trash2 /></Button>
                    </div>
                  ))}
                  <Button size="sm" variant="ghost" onClick={() => setDraft((d) => ({ ...d, actions: d.actions.map((x, j) => j === i ? { ...x, params: [...x.params, { key: '', value: '' }] } : x) }))}><Plus />Add param</Button>
                </div>
              </Card>
            ))}
            {draft.actions.length === 0 && <p className="text-xs text-muted">No actions yet — add at least one.</p>}
          </div>
        </div>
      </div>
    </Sheet>
  )
}

function TestDialog({ rule, onClose }: { rule: Rule; onClose: () => void }) {
  const { base } = useAgent()
  const [leadId, setLeadId] = useState('')
  const test = useMutation({
    mutationFn: () => api<TestResult>(`${base}/crm/rules/${rule.id}/test`, { method: 'POST', json: { lead_id: leadId ? Number(leadId) : null, run: false } }),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Dialog open onClose={onClose} title={`Test "${rule.name}"`} description="Dry run only — checks whether conditions would match. No actions are executed."
      footer={<>
        <Button onClick={onClose}>Close</Button>
        <Button variant="primary" loading={test.isPending} onClick={() => test.mutate()}>Run test</Button>
      </>}>
      <div className="grid gap-3">
        <Field label="Lead ID" hint="Needed for lead-scoped triggers; leave empty otherwise">
          <Input value={leadId} onChange={(e) => setLeadId(e.target.value)} placeholder="e.g. 123" inputMode="numeric" />
        </Field>
        {test.data && (
          <div className="rounded-xl border border-border p-3">
            <div className="flex items-center gap-2">
              {test.data.matched ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className="size-4 text-muted" />}
              <span className="font-semibold">{test.data.matched ? 'Conditions matched — this would fire' : 'Conditions did not match — this would not fire'}</span>
            </div>
            {test.data.matched && (
              <ul className="mt-2 grid gap-1 text-sm text-muted">
                {(rule.actions ?? []).map((a, i) => <li key={i}>• Would run: {a.type}</li>)}
              </ul>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}
