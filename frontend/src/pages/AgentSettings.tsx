import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Bot, CalendarClock, Pause, Play, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { AgentMark } from '@/components/AppShell'
import { Button, Card, CardHeader, Field, Input, PageHeader, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { forgetAgent, useAgent } from '@/lib/agent'
import { formatDate } from '@/lib/utils'

export default function AgentSettings() {
  const { id, agent, base, path } = useAgent()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const [form, setForm] = useState({ name: '', description: '', color: '', phone_number: '' })
  const [confirmName, setConfirmName] = useState('')

  useEffect(() => {
    if (agent) setForm({ name: agent.name, description: agent.description ?? '', color: agent.color, phone_number: agent.phone_number ?? '' })
  }, [agent])

  // The shell reads the agent list from the app-wide cache, so refresh it there too.
  const refresh = () => { qc.invalidateQueries({ queryKey: ['agents'] }); window.dispatchEvent(new CustomEvent('agents:changed')) }

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(base, { method: 'PATCH', json: body }),
    onSuccess: () => { toast.success('Agent updated'); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: () => api(base, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success(`${agent?.name} deleted`)
      forgetAgent(id)
      window.dispatchEvent(new CustomEvent('agents:changed'))
      navigate('/', { replace: true })
    },
    onError: (e) => toast.error(e.message),
  })

  if (!agent) return null
  const dirty = form.name !== agent.name || form.description !== (agent.description ?? '') || form.color !== agent.color || form.phone_number !== (agent.phone_number ?? '')
  const s = agent.stats

  return (
    <>
    <PageHeader eyebrow={<>{agent.name} · Workspace</>} title="Agent settings" description="Identity, phone number and lifecycle of this agent's workspace." />
    <div className="mx-auto max-w-3xl space-y-6">

      <Card>
        <CardHeader title="Identity" description="How this agent appears to your team." />
        <div className="space-y-5 p-5">
          <div className="flex items-center gap-4 rounded-xl border border-border bg-surface-2/50 p-4">
            <AgentMark agent={{ name: form.name || agent.name, color: form.color || agent.color }} className="size-12 text-base" />
            <div className="min-w-0">
              <div className="truncate font-semibold">{form.name || agent.name}</div>
              <div className="text-xs text-muted">Created {formatDate(agent.created_at)} · workspace #{agent.id}</div>
            </div>
          </div>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={255} /></Field>
          <Field label="Description"><Textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Field label="Phone number" hint="Plivo number used as caller ID. Inbound calls to this number are answered by this agent. Leave blank for the default PLIVO_PHONE_NUMBER.">
            <Input value={form.phone_number} onChange={(e) => setForm({ ...form, phone_number: e.target.value })} inputMode="tel" placeholder="+91 80 1234 5678" />
          </Field>
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
          <Button disabled={!dirty} onClick={() => setForm({ name: agent.name, description: agent.description ?? '', color: agent.color, phone_number: agent.phone_number ?? '' })}>Reset</Button>
          <Button variant="primary" disabled={!dirty || !form.name.trim()} loading={save.isPending}
            onClick={() => save.mutate({ ...form, description: form.description || null, phone_number: form.phone_number || null })}>Save changes</Button>
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-3">
        {[[Bot, 'Persona & voice', 'Script, greeting, guardrails', '/agent'], [BookOpen, 'Knowledge base', `${s.documents} document${s.documents === 1 ? '' : 's'}`, '/knowledge'],
          [CalendarClock, 'Automation', 'Auto-dial, retries, hours', '/automation']].map(([Icon, t, d, to]) => {
          const I = Icon as typeof Bot
          return (
            <Link key={to as string} to={path(to as string)}>
              <Card className="h-full p-4 transition hover:border-border-strong"><I className="size-4" /><div className="mt-2 text-sm font-bold">{t as string}</div><div className="text-xs text-muted">{d as string}</div></Card>
            </Link>
          )
        })}
      </div>

      <Card>
        <CardHeader title={agent.status === 'active' ? 'Pause agent' : 'Resume agent'}
          description={agent.status === 'active'
            ? 'Paused agents place no calls: manual calls, auto-dial and retries stop. Data stays untouched.'
            : 'This agent is paused. Resume to allow calls and scheduled jobs again.'} />
        <div className="flex justify-end px-5 py-3">
          {agent.status === 'active'
            ? <Button loading={save.isPending} onClick={() => save.mutate({ status: 'paused' })}><Pause />Pause agent</Button>
            : <Button variant="primary" loading={save.isPending} onClick={() => save.mutate({ status: 'active' })}><Play />Resume agent</Button>}
        </div>
      </Card>

      <Card className="border-danger/30">
        <CardHeader title={<span className="text-danger">Delete agent</span>}
          description={`Permanently deletes ${agent.name} with its ${s.leads} lead(s), all calls and transcripts, ${s.documents} knowledge document(s) and history. This cannot be undone.`} />
        <div className="flex flex-wrap items-end gap-3 px-5 py-4">
          <Field label={`Type “${agent.name}” to confirm`} className="min-w-60 flex-1">
            <Input value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
          </Field>
          <Button variant="danger" disabled={confirmName !== agent.name} loading={remove.isPending} onClick={async () => {
            if (await confirm({ title: `Delete ${agent.name}?`, description: 'All of this agent\'s data is removed permanently.', confirmLabel: 'Delete forever', danger: true })) remove.mutate()
          }}><Trash2 />Delete agent</Button>
        </div>
      </Card>
    </div>
    </>
  )
}
