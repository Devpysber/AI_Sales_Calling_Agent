import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button, Card, CardHeader, Input } from '@/components/ui'
import { api } from '@/lib/api'

type TeamMember = { id: string; email: string; name: string; phone?: string; role?: string; notes?: string
  max_agents: number; created_agents: number }

const ROLES = ['Sales', 'Support', 'Manager', 'Operations']
const DEFAULT_AGENT_LIMIT = 1

export default function TeamMembers() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['team-members'], queryFn: () => api<{ members: TeamMember[] }>('/api/system/team-members') })
  const members = data?.members ?? []
  
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [phone, setPhone] = useState('')
  const [role, setRole] = useState('Sales')
  const [notes, setNotes] = useState('')
  const [maxAgents, setMaxAgents] = useState(DEFAULT_AGENT_LIMIT)
  const [changePwId, setChangePwId] = useState<string | null>(null)
  const [newPassword, setNewPassword] = useState('')
  
  const add = useMutation({
    mutationFn: () => api('/api/system/team-members', { method: 'POST', json: { email, name, password, phone, role, notes, max_agents: maxAgents } }),
    onSuccess: () => {
      toast.success('Team member added')
      setOpen(false)
      setName(''); setEmail(''); setPassword(''); setPhone(''); setRole('Sales'); setNotes(''); setMaxAgents(DEFAULT_AGENT_LIMIT)
      qc.invalidateQueries({ queryKey: ['team-members'] })
    },
    onError: (e: Error) => toast.error(e.message)
  })

  const changePwd = useMutation({
    mutationFn: () => api(`/api/system/team-members/${changePwId}/password`, { method: 'PUT', json: { password: newPassword } }),
    onSuccess: () => {
      toast.success('Password updated')
      setChangePwId(null)
      setNewPassword('')
    },
    onError: (e: Error) => toast.error(e.message)
  })
  
  // The limit is edited in place: a whole dialog for one number is more friction than the change deserves.
  const setLimit = useMutation({
    mutationFn: ({ id, max_agents }: { id: string; max_agents: number }) =>
      api(`/api/system/team-members/${id}`, { method: 'PUT', json: { max_agents } }),
    onSuccess: () => {
      toast.success('Agent limit updated')
      qc.invalidateQueries({ queryKey: ['team-members'] })
    },
    onError: (e: Error) => toast.error(e.message)
  })

  const remove = useMutation({
    mutationFn: (id: string) => api('/api/system/team-members/' + id, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success('Team member removed')
      qc.invalidateQueries({ queryKey: ['team-members'] })
    },
    onError: (e: Error) => toast.error(e.message)
  })

  return (
    <Card className="mt-4 h-fit">
      <CardHeader title="Sales Team Accounts" description="Manage logins for individual sales team members. They cannot delete agents." 
        action={<Button size="sm" onClick={() => setOpen(true)}><Plus className="size-4" />Add member</Button>} />
      
      {members.length > 0 ? (
        <div className="overflow-x-auto">
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
                  <td className="px-5 py-3 font-medium">{m.name}{m.notes ? <span className="block text-xs font-normal text-muted">{m.notes}</span> : null}</td>
                  <td className="px-5 py-3 text-muted">{m.role || 'Sales'}</td>
                  <td className="px-5 py-3 text-muted">{m.phone ? <a href={`tel:${m.phone}`} className="hover:underline">{m.phone}</a> : '—'}</td>
                  <td className="px-5 py-3 text-muted break-all">{m.email}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <Input type="number" min={0} max={100} defaultValue={m.max_agents}
                        onBlur={(e) => {
                          const next = Number(e.target.value)
                          if (Number.isFinite(next) && next >= 0 && next !== m.max_agents) setLimit.mutate({ id: m.id, max_agents: next })
                        }}
                        className="h-8 w-16 text-sm" aria-label={`Agent limit for ${m.name}`} />
                      <span className="text-xs whitespace-nowrap text-muted">{m.created_agents} used</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" size="sm" onClick={() => setChangePwId(m.id)}>
                        Change Password
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { if (confirm('Remove this member?')) remove.mutate(m.id) }} className="text-danger hover:bg-danger-soft">
                        <Trash2 className="size-4 mr-1.5" /> Remove
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-5 text-sm text-muted">No team members added yet.</div>
      )}

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <Card className="max-h-[90vh] w-full max-w-md overflow-y-auto">
            <CardHeader title="Add team member" />
            <form className="p-5 pt-0 space-y-4" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
              <div>
                <label className="mb-1.5 block text-sm font-semibold">Name</label>
                <Input required value={name} onChange={e => setName(e.target.value)} placeholder="Ashish" />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-semibold">Email</label>
                <Input required type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="ashish@example.com" />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-semibold">Phone</label>
                  <Input value={phone} onChange={e => setPhone(e.target.value)} inputMode="tel" placeholder="+91 98765 43210" />
                  <p className="mt-1 text-xs text-muted">Rings when a call is transferred to them.</p>
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-semibold">Role</label>
                  <select value={role} onChange={e => setRole(e.target.value)}
                    className="h-9 w-full rounded-xl border border-border bg-surface px-3 text-sm outline-none focus:border-border-strong">
                    {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-semibold">Agents they can create</label>
                <Input type="number" min={0} max={100} value={maxAgents}
                  onChange={e => setMaxAgents(Math.max(0, Number(e.target.value) || 0))} />
                <p className="mt-1 text-xs text-muted">One by default. Set 0 to stop them creating any.</p>
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-semibold">What they handle</label>
                <Input value={notes} onChange={e => setNotes(e.target.value)} placeholder="Bhopal showroom, used cars" />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-semibold">Password</label>
                <Input required type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Minimum 8 characters" minLength={8} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button type="submit" variant="primary" loading={add.isPending}>Add member</Button>
              </div>
            </form>
          </Card>
        </div>
      )}

      {changePwId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <Card className="w-full max-w-sm">
            <CardHeader title="Change Password" />
            <form className="p-5 pt-0 space-y-4" onSubmit={(e) => { e.preventDefault(); changePwd.mutate() }}>
              <div>
                <label className="mb-1.5 block text-sm font-semibold">New Password</label>
                <Input required type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="Minimum 8 characters" minLength={8} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button type="button" variant="ghost" onClick={() => { setChangePwId(null); setNewPassword('') }}>Cancel</Button>
                <Button type="submit" variant="primary" loading={changePwd.isPending}>Save changes</Button>
              </div>
            </form>
          </Card>
        </div>
      )}
    </Card>
  )
}
