import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, Trash2, Users } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button, Card, CardHeader, Dialog, EmptyState, Input, Select, Skeleton, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'

type TeamMember = { id: string; email: string; name: string; phone?: string | null; role?: string | null; notes?: string | null
  max_agents?: number | null; created_agents?: number | null }

const ROLES = ['Sales', 'Support', 'Manager', 'Operations']
const DEFAULT_AGENT_LIMIT = 1
const KEY = ['team-members']

const label = 'mb-1.5 block text-sm font-semibold'

export default function TeamMembers() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: KEY, queryFn: () => api<{ members: TeamMember[] }>('/api/system/team-members') })
  const members = data?.members ?? []

  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [phone, setPhone] = useState('')
  const [role, setRole] = useState('Sales')
  const [notes, setNotes] = useState('')
  // Kept as a string so the field can be cleared while typing; clamped on blur and on submit.
  const [maxAgents, setMaxAgents] = useState(String(DEFAULT_AGENT_LIMIT))
  const [changePwId, setChangePwId] = useState<string | null>(null)
  const [newPassword, setNewPassword] = useState('')
  // Bumped whenever an in-place limit edit is rejected, so the uncontrolled input remounts back to the server value
  // even though that value did not change.
  const [limitReset, setLimitReset] = useState(0)

  const resetForm = () => { setName(''); setEmail(''); setPassword(''); setPhone(''); setRole('Sales'); setNotes(''); setMaxAgents(String(DEFAULT_AGENT_LIMIT)) }
  const clampLimit = (raw: string, fallback: number) => {
    const n = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(n)) return fallback
    return Math.min(100, Math.max(0, Math.floor(n)))
  }

  const add = useMutation({
    mutationFn: () => api('/api/system/team-members', {
      method: 'POST',
      json: { email: email.trim(), name: name.trim(), password, phone: phone.trim(), role, notes: notes.trim(), max_agents: clampLimit(maxAgents, DEFAULT_AGENT_LIMIT) },
    }),
    onSuccess: () => {
      toast.success('Team member added')
      setOpen(false)
      resetForm()
      qc.invalidateQueries({ queryKey: KEY })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const changePwd = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      api(`/api/system/team-members/${id}/password`, { method: 'PUT', json: { password } }),
    onSuccess: () => {
      toast.success('Password updated')
      setChangePwId(null)
      setNewPassword('')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // The limit is edited in place: a whole dialog for one number is more friction than the change deserves.
  const setLimit = useMutation({
    mutationFn: ({ id, max_agents }: { id: string; max_agents: number }) =>
      api(`/api/system/team-members/${id}`, { method: 'PUT', json: { max_agents } }),
    onSuccess: () => {
      toast.success('Agent limit updated')
      qc.invalidateQueries({ queryKey: KEY })
    },
    // On failure the server value is unchanged, so an invalidate alone would not remount the input; bump the reset token.
    onError: (e: Error) => { toast.error(e.message); setLimitReset(n => n + 1); qc.invalidateQueries({ queryKey: KEY }) },
  })

  const remove = useMutation({
    mutationFn: (id: string) => api('/api/system/team-members/' + id, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success('Team member removed')
      qc.invalidateQueries({ queryKey: KEY })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const closeAdd = () => { if (!add.isPending) setOpen(false) }
  const closePw = () => { if (changePwd.isPending) return; setChangePwId(null); setNewPassword('') }

  const onRemove = async (m: TeamMember) => {
    const ok = await confirm({ title: `Remove ${m.name}?`, description: 'Their login stops working immediately. Agents they created stay.', confirmLabel: 'Remove', danger: true })
    if (ok) remove.mutate(m.id)
  }

  const commitLimit = (m: TeamMember, raw: string) => {
    const current = m.max_agents ?? 0
    const parsed = Number(raw)
    if (raw.trim() === '' || !Number.isFinite(parsed)) {
      toast.error('Enter a number between 0 and 100')
      setLimitReset(n => n + 1)
      return
    }
    const next = clampLimit(raw, current)
    if (next !== parsed) toast.message(`Limit clamped to ${next} (allowed range 0–100)`)
    if (next === current) { if (next !== parsed) setLimitReset(n => n + 1); return }
    setLimit.mutate({ id: m.id, max_agents: next })
  }

  const pwMember = members.find(m => m.id === changePwId)

  const limitInput = (m: TeamMember, className: string) => (
    <Input key={`${m.id}-${m.max_agents ?? 0}-${limitReset}`} type="number" min={0} max={100} defaultValue={m.max_agents ?? 0}
      disabled={setLimit.isPending && setLimit.variables?.id === m.id}
      onBlur={e => commitLimit(m, e.target.value)}
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur() } }}
      className={className} aria-label={`Agent limit for ${m.name}`} />
  )

  return (
    <Card className="mt-4 h-fit">
      <CardHeader title="Sales Team Accounts" description="Manage logins for individual sales team members. They cannot delete agents."
        action={<Button size="sm" onClick={() => setOpen(true)}><Plus className="size-4" />Add member</Button>} />

      {isPending ? (
        <div className="space-y-3 p-5">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : isError ? (
        <EmptyState title="Could not load team members" description={(error as Error)?.message}
          action={<Button size="sm" onClick={() => refetch()}>Retry</Button>} />
      ) : members.length === 0 ? (
        <EmptyState icon={<Users className="size-5" />} title="No team members yet" description="Add a login for each sales person who should use this workspace."
          action={<Button size="sm" onClick={() => setOpen(true)}><Plus className="size-4" />Add member</Button>} />
      ) : (
        <>
          {/* Mobile: one card per member */}
          <ul className="divide-y divide-border border-t border-border md:hidden">
            {members.map(m => (
              <li key={m.id} className="space-y-3 p-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="min-w-0 font-medium break-words">{m.name}</span>
                    <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">{m.role || 'Sales'}</span>
                  </div>
                  {m.notes ? <p className="mt-0.5 text-xs text-muted break-words">{m.notes}</p> : null}
                  <p className="mt-1 text-sm text-muted break-all">{m.email}</p>
                  {m.phone ? <a href={`tel:${m.phone}`} className="mt-0.5 inline-flex min-h-10 items-center text-sm text-muted hover:underline">{m.phone}</a> : null}
                </div>
                <div className="flex items-center gap-2">
                  {limitInput(m, 'h-10 w-20 text-sm')}
                  <span className="text-xs whitespace-nowrap text-muted">agents · {m.created_agents ?? 0} used</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="ghost" size="sm" className="min-h-10" onClick={() => setChangePwId(m.id)}>
                    <KeyRound className="size-4" /> Password
                  </Button>
                  <Button variant="ghost" size="sm" className="min-h-10 text-danger hover:bg-danger-soft" loading={remove.isPending && remove.variables === m.id}
                    disabled={remove.isPending} onClick={() => onRemove(m)}>
                    <Trash2 className="size-4" /> Remove
                  </Button>
                </div>
              </li>
            ))}
          </ul>

          {/* Desktop: table */}
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full text-left text-sm">
              <thead className="border-y border-border bg-surface-2 text-xs uppercase text-muted">
                <tr>
                  <th className="px-5 py-3 font-semibold">Name</th>
                  <th className="px-5 py-3 font-semibold">Role</th>
                  <th className="px-5 py-3 font-semibold">Phone</th>
                  <th className="px-5 py-3 font-semibold">Email</th>
                  <th className="px-5 py-3 font-semibold">Agents</th>
                  <th className="px-5 py-3 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {members.map(m => (
                  <tr key={m.id} className="transition hover:bg-surface-2/50">
                    <td className="max-w-[16rem] px-5 py-3 font-medium break-words">{m.name}{m.notes ? <span className="block text-xs font-normal text-muted">{m.notes}</span> : null}</td>
                    <td className="px-5 py-3 text-muted whitespace-nowrap">{m.role || 'Sales'}</td>
                    <td className="px-5 py-3 text-muted whitespace-nowrap">{m.phone ? <a href={`tel:${m.phone}`} className="hover:underline">{m.phone}</a> : '—'}</td>
                    <td className="max-w-[16rem] px-5 py-3 text-muted break-all">{m.email}</td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {limitInput(m, 'h-8 w-20 text-sm')}
                        <span className="text-xs whitespace-nowrap text-muted">{m.created_agents ?? 0} used</span>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex justify-end gap-2 whitespace-nowrap">
                        <Button variant="ghost" size="sm" onClick={() => setChangePwId(m.id)}>
                          Change password
                        </Button>
                        <Button variant="ghost" size="sm" className="text-danger hover:bg-danger-soft" loading={remove.isPending && remove.variables === m.id}
                          disabled={remove.isPending} onClick={() => onRemove(m)}>
                          <Trash2 className="size-4" /> Remove
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <Dialog open={open} onClose={closeAdd} title="Add team member" description="They sign in with this email and password.">
        <form className="space-y-4 pb-1" onSubmit={e => { e.preventDefault(); if (!add.isPending) add.mutate() }}>
          <fieldset disabled={add.isPending} className="min-w-0 space-y-4">
            <div>
              <label className={label} htmlFor="tm-name">Name</label>
              <Input id="tm-name" required value={name} onChange={e => setName(e.target.value)} placeholder="Ashish" autoComplete="off" />
            </div>
            <div>
              <label className={label} htmlFor="tm-email">Email</label>
              <Input id="tm-email" required type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="ashish@example.com" autoComplete="off" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="min-w-0">
                <label className={label} htmlFor="tm-phone">Phone</label>
                <Input id="tm-phone" value={phone} onChange={e => setPhone(e.target.value)} inputMode="tel" placeholder="+91 98765 43210" />
                <p className="mt-1 text-xs text-muted">Rings when a call is transferred to them.</p>
              </div>
              <div className="min-w-0">
                <label className={label} htmlFor="tm-role">Role</label>
                <Select id="tm-role" value={role} onChange={e => setRole(e.target.value)}>
                  {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                </Select>
              </div>
            </div>
            <div>
              <label className={label} htmlFor="tm-max">Agents they can create</label>
              <Input id="tm-max" type="number" min={0} max={100} inputMode="numeric" value={maxAgents}
                onChange={e => setMaxAgents(e.target.value)}
                onBlur={() => setMaxAgents(String(clampLimit(maxAgents, DEFAULT_AGENT_LIMIT)))} />
              <p className="mt-1 text-xs text-muted">One by default. Set 0 to stop them creating any.</p>
            </div>
            <div>
              <label className={label} htmlFor="tm-notes">What they handle</label>
              <Input id="tm-notes" value={notes} onChange={e => setNotes(e.target.value)} placeholder="Bhopal showroom, used cars" />
            </div>
            <div>
              <label className={label} htmlFor="tm-pw">Password</label>
              <Input id="tm-pw" required type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Minimum 8 characters" minLength={8} autoComplete="new-password" />
            </div>
          </fieldset>
          <div className="flex flex-wrap justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={closeAdd} disabled={add.isPending}>Cancel</Button>
            <Button type="submit" variant="primary" loading={add.isPending}>Add member</Button>
          </div>
        </form>
      </Dialog>

      <Dialog open={!!changePwId} onClose={closePw} title="Change password" description={pwMember ? `For ${pwMember.name} (${pwMember.email})` : undefined}>
        <form className="space-y-4 pb-1" onSubmit={e => { e.preventDefault(); if (changePwId && !changePwd.isPending) changePwd.mutate({ id: changePwId, password: newPassword }) }}>
          <fieldset disabled={changePwd.isPending}>
            <label className={label} htmlFor="tm-newpw">New password</label>
            <Input id="tm-newpw" required type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="Minimum 8 characters" minLength={8} autoComplete="new-password" autoFocus />
          </fieldset>
          <div className="flex flex-wrap justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={closePw} disabled={changePwd.isPending}>Cancel</Button>
            <Button type="submit" variant="primary" loading={changePwd.isPending}>Save changes</Button>
          </div>
        </form>
      </Dialog>
    </Card>
  )
}
