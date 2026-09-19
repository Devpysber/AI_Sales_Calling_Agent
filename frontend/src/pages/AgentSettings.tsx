import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Bot, CalendarClock, Pause, Play, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { AgentMark } from '@/components/AppShell'
import { Button, Card, CardHeader, Field, Input, PageHeader, Skeleton, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { Stagger } from '@/lib/motion'
import { forgetAgent, useAgent } from '@/lib/agent'
import type { AgentSummary, AgentProfile } from '@/lib/types'
import { formatDate } from '@/lib/utils'

type ProfileResponse = { profile: AgentProfile & { agent_password?: string | null } }
type AgentRow = Pick<AgentSummary, 'id' | 'name' | 'description' | 'color' | 'phone_number' | 'status' | 'created_at'>

const links = [
  { Icon: Bot, title: 'Persona & voice', to: '/agent' },
  { Icon: BookOpen, title: 'Knowledge base', to: '/knowledge' },
  { Icon: CalendarClock, title: 'Automation', to: '/automation' },
] as const

export default function AgentSettings() {
  const { id, agent, base, path } = useAgent()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const confirm = useConfirm()
  const [form, setForm] = useState({ name: '', description: '', color: '', phone_number: '' })
  const [confirmName, setConfirmName] = useState('')

  // Deleting an agent is admin-only on the backend; team members never see the card.
  const meQ = useQuery({ queryKey: ['me'], queryFn: () => api<{ user: string | null; role?: string }>('/api/auth/me'), staleTime: 0 })
  // /api/auth/me marks team sessions with user === 'team' (role is a display label); stay hidden until loaded.
  const isAdmin = meQ.data ? meQ.data.user !== 'team' : false

  const profileQ = useQuery({ queryKey: ['agent-profile', id], queryFn: () => api<ProfileResponse>(`${base}/profile`) })
  const savedPassword = profileQ.data?.profile?.agent_password ?? ''
  const [vaultPassword, setVaultPassword] = useState('')
  // Key on the saved value, not the response object: background refetches must not clobber unsaved typing.
  useEffect(() => { if (profileQ.isSuccess) setVaultPassword(savedPassword) }, [profileQ.isSuccess, savedPassword])

  const saveProfile = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`${base}/profile`, { method: 'PUT', json: body }),
    onSuccess: () => {
      toast.success('Vault password updated')
      // Other pages read the same endpoint under the shared ['agent'] key.
      qc.invalidateQueries({ queryKey: ['agent-profile', id] })
      qc.invalidateQueries({ queryKey: ['agent'] })
      window.dispatchEvent(new CustomEvent('agents:changed'))
    },
    onError: (e) => toast.error(e.message),
  })

  // The agent list polls every few seconds and its stats change while calls run; only
  // reseed the form when the identity fields themselves change, never over unsaved typing.
  const agentName = agent?.name, agentDescription = agent?.description ?? '', agentColor = agent?.color, agentPhone = agent?.phone_number ?? ''
  useEffect(() => {
    if (id == null || agentName == null || agentColor == null) return
    setForm({ name: agentName, description: agentDescription, color: agentColor, phone_number: agentPhone })
  }, [id, agentName, agentDescription, agentColor, agentPhone])

  // The shell reads the agent list from the app-wide cache (a different QueryClient); the event refreshes it there.
  const refresh = () => window.dispatchEvent(new CustomEvent('agents:changed'))

  const save = useMutation({
    // PATCH returns the bare agent row (id, name, description, color, phone_number, status, created_at), not the full summary.
    mutationFn: (body: Record<string, unknown>) => api<AgentRow>(base, { method: 'PATCH', json: body }),
    onSuccess: (saved) => {
      if (saved && typeof saved.name === 'string') setForm({ name: saved.name, description: saved.description ?? '', color: saved.color ?? form.color, phone_number: saved.phone_number ?? '' })
      toast.success('Agent updated'); refresh()
    },
    onError: (e) => toast.error(e.message),
  })
  const setStatus = useMutation({
    mutationFn: (status: 'active' | 'paused') => api(base, { method: 'PATCH', json: { status } }),
    onSuccess: (_d, status) => { toast.success(status === 'paused' ? 'Agent paused' : 'Agent resumed'); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: () => api(base, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success(`${agent?.name ?? 'Agent'} deleted`)
      forgetAgent(id)
      window.dispatchEvent(new CustomEvent('agents:changed'))
      navigate('/', { replace: true })
    },
    onError: (e) => toast.error(e.message),
  })

  if (!agent) {
    return (
      <>
        <PageHeader eyebrow="Workspace" title="Agent settings" description="Identity, phone number and lifecycle of this agent's workspace." />
        <div className="mx-auto max-w-3xl space-y-6">
          <Skeleton className="h-80" /><Skeleton className="h-28" /><Skeleton className="h-32" />
        </div>
      </>
    )
  }

  const busy = save.isPending || setStatus.isPending || remove.isPending
  const dirty = form.name !== agent.name || form.description !== (agent.description ?? '') || form.color !== agent.color || form.phone_number !== (agent.phone_number ?? '')
  const s = agent.stats ?? { leads: 0, documents: 0 }
  const resetForm = () => setForm({ name: agent.name, description: agent.description ?? '', color: agent.color, phone_number: agent.phone_number ?? '' })
  const linkSub: Record<string, string> = { '/agent': 'Script, greeting, guardrails', '/knowledge': `${s.documents} document${s.documents === 1 ? '' : 's'}`, '/automation': 'Auto-dial, retries, hours' }

  return (
    <>
    <PageHeader eyebrow={<span className="break-words">{agent.name} · Workspace</span>} title="Agent settings" description="Identity, phone number and lifecycle of this agent's workspace." />
    <Stagger className="mx-auto max-w-3xl space-y-6" step={60}>

      <Card>
        <CardHeader title="Identity" description="How this agent appears to your team." />
        <form className="space-y-5 p-4 sm:p-5" onSubmit={(e) => { e.preventDefault(); if (dirty && form.name.trim() && !busy) save.mutate({ ...form, name: form.name.trim(), description: form.description.trim() || null, phone_number: form.phone_number.trim() || null }) }}>
          <div className="flex min-w-0 items-center gap-4 rounded-xl border border-border bg-surface-2/50 p-4">
            <AgentMark agent={{ name: form.name || agent.name, color: form.color || agent.color }} className="size-12 shrink-0 text-base" />
            <div className="min-w-0">
              <div className="truncate font-semibold">{form.name || agent.name}</div>
              <div className="text-xs break-words text-muted">{agent.created_at ? `Created ${formatDate(agent.created_at)} · ` : ''}workspace #{agent.id}</div>
            </div>
          </div>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} maxLength={255} disabled={busy} required /></Field>
          <Field label="Description"><Textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} disabled={busy} /></Field>
          <Field label="Phone number" hint="Plivo number used as caller ID. Inbound calls to this number are answered by this agent. Leave blank for the default PLIVO_PHONE_NUMBER.">
            <Input value={form.phone_number} onChange={(e) => setForm({ ...form, phone_number: e.target.value })} inputMode="tel" autoComplete="off" placeholder="Using the default Plivo number (leave empty)" disabled={busy} />
          </Field>
          <div className="-mx-4 flex flex-wrap justify-end gap-2 border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
            <Button type="button" disabled={!dirty || busy} onClick={resetForm}>Reset</Button>
            <Button type="submit" variant="primary" disabled={!dirty || !form.name.trim() || setStatus.isPending || remove.isPending} loading={save.isPending}>Save changes</Button>
          </div>
        </form>
      </Card>

      <div className="grid gap-3 sm:grid-cols-3">
        {links.map(({ Icon, title, to }) => (
          <Link key={to} to={path(to)} className="group glint relative block min-w-0 overflow-hidden rounded-[var(--radius-card)] shadow-card transition duration-300 hover:-translate-y-0.5 hover:shadow-pop">
            <Card className="h-full min-h-20 p-4 shadow-none border-border transition-colors duration-300 group-hover:border-border-strong">
              <Icon className="size-4" />
              <div className="mt-2 text-sm font-bold break-words">{title}</div>
              <div className="text-xs break-words text-muted">{linkSub[to]}</div>
            </Card>
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader title={agent.status === 'active' ? 'Pause agent' : 'Resume agent'}
          description={agent.status === 'active'
            ? 'Paused agents place no calls: manual calls, auto-dial and retries stop. Data stays untouched.'
            : 'This agent is paused. Resume to allow calls and scheduled jobs again.'} />
        <div className="flex flex-wrap justify-end px-4 py-3 sm:px-5">
          {agent.status === 'active'
            ? <Button disabled={save.isPending || remove.isPending} loading={setStatus.isPending} onClick={() => setStatus.mutate('paused')}><Pause />Pause agent</Button>
            : <Button variant="primary" disabled={save.isPending || remove.isPending} loading={setStatus.isPending} onClick={() => setStatus.mutate('active')}><Play />Resume agent</Button>}
        </div>
      </Card>

      <Card>
        <CardHeader title="Vault password" description="Require team members to enter this password to open this workspace's CRM. Leave empty for open access." />
        <form className="flex flex-wrap items-end gap-3 px-4 py-4 sm:px-5" onSubmit={(e) => {
          e.preventDefault()
          if (!profileQ.data || saveProfile.isPending || vaultPassword === savedPassword) return
          // The backend merges partial updates; sending only the password never overwrites a persona edited elsewhere.
          saveProfile.mutate({ agent_password: vaultPassword })
        }}>
          <Field label="Password" className="min-w-0 flex-1 basis-full sm:basis-auto sm:min-w-60" error={profileQ.isError ? profileQ.error.message : undefined}>
            <Input type="password" autoComplete="new-password" value={vaultPassword} onChange={(e) => setVaultPassword(e.target.value)} placeholder={profileQ.isPending ? 'Loading…' : 'No password required'} disabled={profileQ.isPending || profileQ.isError || saveProfile.isPending} />
          </Field>
          {profileQ.isError
            ? <Button type="button" onClick={() => profileQ.refetch()} loading={profileQ.isFetching}>Retry</Button>
            : <Button type="submit" variant="primary" disabled={!profileQ.data || vaultPassword === savedPassword} loading={saveProfile.isPending}>Save password</Button>}
        </form>
      </Card>

      {isAdmin && <Card className="border-danger/30">
        <CardHeader title={<span className="text-danger">Delete agent</span>}
          description={`Permanently deletes ${agent.name} with its ${s.leads} lead(s), all calls and transcripts, ${s.documents} knowledge document(s) and history. This cannot be undone.`} />
        <form className="flex flex-wrap items-end gap-3 px-4 py-4 sm:px-5" onSubmit={async (e) => {
          e.preventDefault()
          if (confirmName.trim() !== agent.name || busy) return
          if (await confirm({ title: `Delete ${agent.name}?`, description: "All of this agent's data is removed permanently.", confirmLabel: 'Delete forever', danger: true })) remove.mutate()
        }}>
          <Field label={`Type “${agent.name}” to confirm`} className="min-w-0 flex-1 basis-full break-words sm:basis-auto sm:min-w-60">
            <Input value={confirmName} onChange={(e) => setConfirmName(e.target.value)} autoComplete="off" disabled={remove.isPending} />
          </Field>
          <Button type="submit" variant="danger" disabled={confirmName.trim() !== agent.name || save.isPending || setStatus.isPending} loading={remove.isPending}><Trash2 />Delete agent</Button>
        </form>
      </Card>}
    </Stagger>
    </>
  )
}
