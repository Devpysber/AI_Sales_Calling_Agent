import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Calendar, CheckSquare, ListTodo, Mail, Pencil, Phone, Plus, RefreshCw, Trash2, User } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, EmptyState, Field, Input, Select, Sheet, Skeleton, Switch, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { Lead, Page } from '@/lib/types'
import { cn } from '@/lib/utils'

type Task = {
  id: number; agent_id: number; title: string; description: string | null
  kind: 'todo' | 'call' | 'email' | 'meeting' | 'follow_up'
  priority: 'low' | 'medium' | 'high'
  status: 'open' | 'done'
  due_at: string | null
  recurrence: 'daily' | 'weekly' | 'monthly' | null
  assignee: string | null
  lead_id: number | null
  deal_id: number | null
  contact_id: number | null
  created_at: string
}

const KIND_ICON: Record<Task['kind'], typeof ListTodo> = { todo: CheckSquare, call: Phone, email: Mail, meeting: Calendar, follow_up: CheckSquare }
const KIND_LABEL: Record<Task['kind'], string> = { todo: 'To-do', call: 'Call', email: 'Email', meeting: 'Meeting', follow_up: 'Follow up' }
const PRIORITY_TONE: Record<Task['priority'], 'neutral' | 'warning' | 'danger'> = { low: 'neutral', medium: 'warning', high: 'danger' }

/** due_at is "YYYY-MM-DD HH:MM" IST text, like Lead.callback_at — compare against IST "now" the same way. */
const istNow = () => new Date(Date.now() + 5.5 * 3600_000).toISOString().slice(0, 16).replace('T', ' ')
const isOverdue = (t: Task) => t.status === 'open' && !!t.due_at && t.due_at < istNow()

type Draft = {
  title: string; description: string; kind: Task['kind']; priority: Task['priority']
  due_at: string; recurrence: '' | Task['recurrence']; assignee: string; lead_id: string
}
const emptyDraft: Draft = { title: '', description: '', kind: 'todo', priority: 'medium', due_at: '', recurrence: '', assignee: '', lead_id: '' }
const toDraft = (t: Task): Draft => ({
  title: t.title, description: t.description ?? '', kind: t.kind, priority: t.priority, due_at: t.due_at ?? '',
  recurrence: t.recurrence ?? '', assignee: t.assignee ?? '', lead_id: t.lead_id ? String(t.lead_id) : '',
})

export default function Tasks() {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [status, setStatus] = useState<'open' | 'done' | 'all'>('open')
  const [kind, setKind] = useState('')
  const [priority, setPriority] = useState('')
  const [assignee, setAssignee] = useState('')
  const [overdueOnly, setOverdueOnly] = useState(false)
  const [editing, setEditing] = useState<Task | 'new' | null>(null)

  const tasksQ = useQuery({
    queryKey: ['tasks', base, status],
    queryFn: () => api<Task[]>(`${base}/crm/tasks`, { params: status === 'all' ? {} : { status } }),
  })
  const leadsQ = useQuery({ queryKey: ['tasks', 'leads', base], queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 200 } }) })
  const leadsById = useMemo(() => new Map((leadsQ.data?.items ?? []).map((l) => [l.id, l])), [leadsQ.data])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['tasks', base] })
  const toggleDone = useMutation({
    mutationFn: ({ id, status: s }: { id: number; status: Task['status'] }) => api<Task>(`${base}/crm/tasks/${id}`, { method: 'PATCH', json: { status: s } }),
    onMutate: ({ id, status: s }) => {
      const prev = qc.getQueryData<Task[]>(['tasks', base, status])
      qc.setQueryData<Task[]>(['tasks', base, status], (old) => old?.map((t) => t.id === id ? { ...t, status: s } : t))
      return { prev }
    },
    onError: (e: Error, _v, ctx) => { toast.error(e.message); if (ctx?.prev) qc.setQueryData(['tasks', base, status], ctx.prev) },
    onSuccess: invalidate,
  })
  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/tasks/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Task deleted'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })

  if (tasksQ.isError) {
    return <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load tasks" description={tasksQ.error.message}
      action={<Button onClick={() => tasksQ.refetch()}><RefreshCw />Try again</Button>} /></Card>
  }
  if (!tasksQ.data) return <div className="grid gap-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>

  const all = tasksQ.data
  const assignees = Array.from(new Set(all.map((t) => t.assignee).filter((a): a is string => !!a))).sort()
  const overdueCount = all.filter(isOverdue).length

  const tasks = all
    .filter((t) => !kind || t.kind === kind)
    .filter((t) => !priority || t.priority === priority)
    .filter((t) => !assignee || t.assignee === assignee)
    .filter((t) => !overdueOnly || isOverdue(t))
    .slice()
    .sort((a, b) => (a.due_at ?? '9999') < (b.due_at ?? '9999') ? -1 : (a.due_at ?? '9999') > (b.due_at ?? '9999') ? 1 : 0)

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-sm text-muted">Everything the AI (and your team) needs to follow up on.</p>
        <Button variant="primary" onClick={() => setEditing('new')}><Plus />New task</Button>
      </div>

      {overdueCount > 0 && (
        <Card className="mb-3 flex items-center gap-3 border-danger/40 bg-danger/5 p-3.5">
          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-danger/15 text-danger"><AlertTriangle className="size-4.5" /></span>
          <div className="min-w-0 flex-1">
            <div className="font-bold text-danger">{overdueCount} task{overdueCount === 1 ? '' : 's'} overdue</div>
            <div className="text-xs text-muted">Past their due time and still open.</div>
          </div>
          {!overdueOnly && <Button size="sm" onClick={() => { setOverdueOnly(true); setStatus('open') }}>Show them</Button>}
        </Card>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select value={status} onChange={(e) => setStatus(e.target.value as typeof status)} className="w-auto" aria-label="Status">
          <option value="open">Open</option>
          <option value="done">Done</option>
          <option value="all">All</option>
        </Select>
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="w-auto" aria-label="Kind">
          <option value="">All kinds</option>
          {(['todo', 'call', 'email', 'meeting', 'follow_up'] as const).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
        </Select>
        <Select value={priority} onChange={(e) => setPriority(e.target.value)} className="w-auto" aria-label="Priority">
          <option value="">All priorities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </Select>
        <Select value={assignee} onChange={(e) => setAssignee(e.target.value)} className="w-auto" aria-label="Assignee">
          <option value="">Everyone</option>
          {assignees.map((a) => <option key={a} value={a}>{a}</option>)}
        </Select>
        <span className="flex items-center gap-1.5 text-sm font-semibold text-fg-2">
          <Switch checked={overdueOnly} onChange={setOverdueOnly} label="Overdue only" />Overdue only
        </span>
      </div>

      {tasks.length === 0 ? (
        <Card><EmptyState icon={<ListTodo />} title="No tasks" description="Nothing matches these filters yet."
          action={<Button variant="primary" onClick={() => setEditing('new')}><Plus />New task</Button>} /></Card>
      ) : (
        <div className="grid gap-2">
          {tasks.map((t) => {
            const Icon = KIND_ICON[t.kind]
            const overdue = isOverdue(t)
            const lead = t.lead_id ? leadsById.get(t.lead_id) : undefined
            return (
              <Card key={t.id} className={cn('flex flex-wrap items-start gap-3 p-3.5', overdue && 'border-danger/40 bg-danger/5')}>
                <input type="checkbox" checked={t.status === 'done'} aria-label={`Mark "${t.title}" ${t.status === 'done' ? 'open' : 'done'}`}
                  onChange={() => toggleDone.mutate({ id: t.id, status: t.status === 'done' ? 'open' : 'done' })}
                  className="mt-1 size-4 shrink-0 cursor-pointer accent-[var(--fg)]" />
                <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-fg-2"><Icon className="size-4" /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn('font-semibold break-words', t.status === 'done' && 'text-muted line-through')}>{t.title}</span>
                    <Badge tone="neutral">{KIND_LABEL[t.kind]}</Badge>
                    <Badge tone={PRIORITY_TONE[t.priority]}>{t.priority}</Badge>
                    {overdue && <Badge tone="danger" dot>Overdue</Badge>}
                  </div>
                  {t.description && <p className="mt-0.5 text-sm break-words text-fg-2">{t.description}</p>}
                  <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted">
                    {t.due_at && <span className={cn(overdue && 'font-semibold text-danger')}>Due {t.due_at} IST</span>}
                    {t.assignee && <span className="inline-flex items-center gap-1"><User className="size-3" />{t.assignee}</span>}
                    {lead && <Link to={path(`/leads/${lead.id}`)} className="hover:underline">Lead: {lead.name ?? lead.phone}</Link>}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1.5">
                  <Button size="icon" variant="ghost" aria-label="Edit task" onClick={() => setEditing(t)}><Pencil /></Button>
                  <Button size="icon" variant="ghost" aria-label="Delete task" onClick={async () => {
                    if (await confirm({ title: 'Delete this task?', description: `"${t.title}" will be removed.`, confirmLabel: 'Delete', danger: true })) remove.mutate(t.id)
                  }}><Trash2 /></Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}

      {editing && <TaskForm task={editing === 'new' ? null : editing} leads={leadsQ.data?.items ?? []} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); invalidate() }} />}
    </>
  )
}

function TaskForm({ task, leads, onClose, onSaved }: { task: Task | null; leads: Lead[]; onClose: () => void; onSaved: () => void }) {
  const { base } = useAgent()
  const [draft, setDraft] = useState<Draft>(task ? toDraft(task) : emptyDraft)

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        title: draft.title, description: draft.description || null, kind: draft.kind, priority: draft.priority,
        due_at: draft.due_at || null, recurrence: draft.recurrence || null, assignee: draft.assignee || null,
        lead_id: draft.lead_id ? Number(draft.lead_id) : null,
      }
      return task ? api<Task>(`${base}/crm/tasks/${task.id}`, { method: 'PATCH', json: payload }) : api<Task>(`${base}/crm/tasks`, { method: 'POST', json: payload })
    },
    onSuccess: () => { toast.success(task ? 'Task updated' : 'Task created'); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Sheet open onClose={onClose} title={task ? `Edit "${task.title}"` : 'New task'}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={save.isPending} disabled={!draft.title.trim()} onClick={() => save.mutate()}>
          {task ? 'Save changes' : 'Create task'}
        </Button>
      </>}>
      <div className="grid gap-4">
        <Field label="Title"><Input value={draft.title} onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))} placeholder="e.g. Call back about pricing" /></Field>
        <Field label="Description"><Textarea rows={3} value={draft.description} onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Kind">
            <Select value={draft.kind} onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value as Task['kind'] }))}>
              {(['todo', 'call', 'email', 'meeting', 'follow_up'] as const).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </Select>
          </Field>
          <Field label="Priority">
            <Select value={draft.priority} onChange={(e) => setDraft((d) => ({ ...d, priority: e.target.value as Task['priority'] }))}>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </Select>
          </Field>
        </div>
        <Field label="Due (IST)" hint="YYYY-MM-DD HH:MM"><Input value={draft.due_at} onChange={(e) => setDraft((d) => ({ ...d, due_at: e.target.value }))} placeholder="2026-11-10 15:30" /></Field>
        <Field label="Recurrence" hint="Repeats this task after it is marked done">
          <Select value={draft.recurrence ?? ''} onChange={(e) => setDraft((d) => ({ ...d, recurrence: e.target.value as Draft['recurrence'] }))}>
            <option value="">None</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </Select>
        </Field>
        <Field label="Assignee"><Input value={draft.assignee} onChange={(e) => setDraft((d) => ({ ...d, assignee: e.target.value }))} placeholder="e.g. Priya" /></Field>
        <Field label="Related lead" hint="Optional">
          <Select value={draft.lead_id} onChange={(e) => setDraft((d) => ({ ...d, lead_id: e.target.value }))}>
            <option value="">None</option>
            {leads.map((l) => <option key={l.id} value={l.id}>{l.name ?? l.phone}</option>)}
          </Select>
        </Field>
      </div>
    </Sheet>
  )
}
