import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Building2, Pencil, Plus, RefreshCw, Trash2, Users } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, EmptyState, Field, Input, PageHeader, Select, Sheet, Skeleton, Tabs, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { Lead, Page } from '@/lib/types'

/* ---------------- types ---------------- */

type Company = {
  id: number; agent_id: number; name: string; industry: string | null; website: string | null
  phone: string | null; city: string | null; owner: string | null; notes: string | null
  created_at: string; updated_at: string
}
type Contact = {
  id: number; agent_id: number; company_id: number | null; lead_id: number | null; name: string
  email: string | null; phone: string | null; title: string | null; tags: string | null
  preferred_channel: 'call' | 'email' | 'whatsapp' | null; owner: string | null; notes: string | null
  created_at: string; updated_at: string
}
// Server-enforced list (app/models/crm.py DEAL_STAGES) -- must match exactly.
const DEAL_STAGES = ['Prospecting', 'Qualified', 'Proposal', 'Negotiation', 'Won', 'Lost'] as const
type DealStage = typeof DEAL_STAGES[number]
// Mirrors crm_records.py's update()'s own stage -> probability mapping, which only applies when a PATCH
// omits probability (e.g. the Kanban drag/drop). The form always sends probability, so it has to apply
// this itself on a stage change, or dragging a card to "Won" sets 100% but editing it to "Won" doesn't.
const STAGE_PROBABILITY: Record<DealStage, number> = { Prospecting: 10, Qualified: 30, Proposal: 50, Negotiation: 75, Won: 100, Lost: 0 }
type Deal = {
  id: number; agent_id: number; title: string; stage: string; value: number; probability: number
  lead_id: number | null; contact_id: number | null; company_id: number | null; owner: string | null
  expected_close: string | null; lost_reason: string | null; notes: string | null
  created_at: string; updated_at: string
}

const money = (v: number) => `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

export default function Accounts() {
  const { base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [tab, setTab] = useState<'companies' | 'contacts' | 'deals'>('companies')

  const companiesQ = useQuery({ queryKey: ['accounts', 'companies', base], queryFn: () => api<Company[]>(`${base}/crm/companies`) })
  const contactsQ = useQuery({ queryKey: ['accounts', 'contacts', base], queryFn: () => api<Contact[]>(`${base}/crm/contacts`) })
  const dealsQ = useQuery({ queryKey: ['accounts', 'deals', base], queryFn: () => api<Deal[]>(`${base}/crm/deals`) })
  const leadsQ = useQuery({ queryKey: ['accounts', 'leads', base], queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 200 } }) })

  const invalidate = () => { qc.invalidateQueries({ queryKey: ['accounts', 'companies'] }); qc.invalidateQueries({ queryKey: ['accounts', 'contacts'] }); qc.invalidateQueries({ queryKey: ['accounts', 'deals'] }) }

  const companyById = useMemo(() => new Map((companiesQ.data ?? []).map((c) => [c.id, c])), [companiesQ.data])
  // Companies tab needs linked-record counts per company; Contacts tab needs the resolved company name.
  const contactCountByCompany = useMemo(() => {
    const m = new Map<number, number>()
    for (const c of contactsQ.data ?? []) if (c.company_id) m.set(c.company_id, (m.get(c.company_id) ?? 0) + 1)
    return m
  }, [contactsQ.data])
  const dealCountByCompany = useMemo(() => {
    const m = new Map<number, number>()
    for (const d of dealsQ.data ?? []) if (d.company_id) m.set(d.company_id, (m.get(d.company_id) ?? 0) + 1)
    return m
  }, [dealsQ.data])

  const anyError = companiesQ.isError || contactsQ.isError || dealsQ.isError
  const anyLoading = !companiesQ.data || !contactsQ.data || !dealsQ.data
  const refetchAll = () => { companiesQ.refetch(); contactsQ.refetch(); dealsQ.refetch(); leadsQ.refetch() }

  return (
    <>
      <PageHeader eyebrow={<><Building2 className="size-3.5" />CRM</>} title="Accounts"
        description="Companies, contacts and the deal pipeline — the B2B side of this agent's CRM."
        actions={<Tabs value={tab} onChange={setTab} items={[{ value: 'companies', label: 'Companies' }, { value: 'contacts', label: 'Contacts' }, { value: 'deals', label: 'Deals' }]} />} />

      {anyError && !companiesQ.data && !contactsQ.data && !dealsQ.data ? (
        <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load accounts" description={(companiesQ.error ?? contactsQ.error ?? dealsQ.error)?.message}
          action={<Button onClick={refetchAll}><RefreshCw />Try again</Button>} /></Card>
      ) : anyLoading ? (
        <div className="grid gap-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24" />)}</div>
      ) : tab === 'companies' ? (
        <CompaniesTab companies={companiesQ.data!} contactCountByCompany={contactCountByCompany} dealCountByCompany={dealCountByCompany} confirm={confirm} invalidate={invalidate} />
      ) : tab === 'contacts' ? (
        <ContactsTab contacts={contactsQ.data!} companies={companiesQ.data!} leads={leadsQ.data?.items ?? []} confirm={confirm} invalidate={invalidate} />
      ) : (
        <DealsTab deals={dealsQ.data!} companies={companiesQ.data!} contacts={contactsQ.data!} leads={leadsQ.data?.items ?? []} companyById={companyById} confirm={confirm} invalidate={invalidate} />
      )}
    </>
  )
}

type Confirm = ReturnType<typeof useConfirm>

/* ---------------- Companies ---------------- */

function CompaniesTab({ companies, contactCountByCompany, dealCountByCompany, confirm, invalidate }: {
  companies: Company[]; contactCountByCompany: Map<number, number>; dealCountByCompany: Map<number, number>
  confirm: Confirm; invalidate: () => void
}) {
  const { base } = useAgent()
  const [editing, setEditing] = useState<Company | 'new' | null>(null)
  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/companies/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Company deleted'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })

  return (
    <>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setEditing('new')}><Plus />New company</Button></div>
      {companies.length === 0 ? (
        <Card><EmptyState icon={<Building2 />} title="No companies yet" description="Add the businesses you sell to."
          action={<Button variant="primary" onClick={() => setEditing('new')}><Plus />New company</Button>} /></Card>
      ) : (
        <div className="grid gap-3">
          {companies.map((c) => (
            <Card key={c.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-bold break-words">{c.name}</h3>
                  {c.industry && <Badge tone="neutral">{c.industry}</Badge>}
                  {(contactCountByCompany.get(c.id) ?? 0) > 0 && <Badge tone="brand">{contactCountByCompany.get(c.id)} contact(s)</Badge>}
                  {(dealCountByCompany.get(c.id) ?? 0) > 0 && <Badge tone="success">{dealCountByCompany.get(c.id)} deal(s)</Badge>}
                </div>
                <p className="mt-1 text-xs break-words text-muted">
                  {[c.city, c.phone, c.website, c.owner ? `Owner: ${c.owner}` : null].filter(Boolean).join(' · ') || 'No details yet'}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setEditing(c)}><Pencil />Edit</Button>
                <Button size="sm" variant="ghost" onClick={async () => {
                  if (await confirm({ title: 'Delete this company?', description: `"${c.name}" will be removed.`, confirmLabel: 'Delete', danger: true })) remove.mutate(c.id)
                }}><Trash2 />Delete</Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {editing && <CompanyForm company={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); invalidate() }} />}
    </>
  )
}

function CompanyForm({ company, onClose, onSaved }: { company: Company | null; onClose: () => void; onSaved: () => void }) {
  const { base } = useAgent()
  const [draft, setDraft] = useState(() => ({
    name: company?.name ?? '', industry: company?.industry ?? '', website: company?.website ?? '',
    phone: company?.phone ?? '', city: company?.city ?? '', owner: company?.owner ?? '', notes: company?.notes ?? '',
  }))
  const save = useMutation({
    mutationFn: () => company
      ? api<Company>(`${base}/crm/companies/${company.id}`, { method: 'PATCH', json: draft })
      : api<Company>(`${base}/crm/companies`, { method: 'POST', json: draft }),
    onSuccess: () => { toast.success(company ? 'Company updated' : 'Company created'); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Sheet open onClose={onClose} title={company ? `Edit "${company.name}"` : 'New company'}
      footer={<><Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={save.isPending} disabled={!draft.name.trim()} onClick={() => save.mutate()}>{company ? 'Save changes' : 'Create company'}</Button></>}>
      <div className="grid gap-4">
        <Field label="Name"><Input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} placeholder="Acme Corp" /></Field>
        <Field label="Industry"><Input value={draft.industry} onChange={(e) => setDraft((d) => ({ ...d, industry: e.target.value }))} /></Field>
        <Field label="Website"><Input value={draft.website} onChange={(e) => setDraft((d) => ({ ...d, website: e.target.value }))} placeholder="https://" /></Field>
        <Field label="Phone"><Input value={draft.phone} onChange={(e) => setDraft((d) => ({ ...d, phone: e.target.value }))} /></Field>
        <Field label="City"><Input value={draft.city} onChange={(e) => setDraft((d) => ({ ...d, city: e.target.value }))} /></Field>
        <Field label="Owner"><Input value={draft.owner} onChange={(e) => setDraft((d) => ({ ...d, owner: e.target.value }))} placeholder="Who manages this account" /></Field>
        <Field label="Notes"><Textarea rows={3} value={draft.notes} onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))} /></Field>
      </div>
    </Sheet>
  )
}

/* ---------------- Contacts ---------------- */

function ContactsTab({ contacts, companies, leads, confirm, invalidate }: {
  contacts: Contact[]; companies: Company[]; leads: Lead[]; confirm: Confirm; invalidate: () => void
}) {
  const { base } = useAgent()
  const [editing, setEditing] = useState<Contact | 'new' | null>(null)
  const companyById = useMemo(() => new Map(companies.map((c) => [c.id, c])), [companies])
  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/contacts/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Contact deleted'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })

  return (
    <>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setEditing('new')}><Plus />New contact</Button></div>
      {contacts.length === 0 ? (
        <Card><EmptyState icon={<Users />} title="No contacts yet" description="Add the people you deal with at your customer companies."
          action={<Button variant="primary" onClick={() => setEditing('new')}><Plus />New contact</Button>} /></Card>
      ) : (
        <div className="grid gap-3">
          {contacts.map((c) => (
            <Card key={c.id} className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-bold break-words">{c.name}</h3>
                  {c.title && <span className="text-xs text-muted">{c.title}</span>}
                  {c.company_id && companyById.get(c.company_id) && <Badge tone="neutral">{companyById.get(c.company_id)!.name}</Badge>}
                  {c.preferred_channel && <Badge tone="brand">{c.preferred_channel}</Badge>}
                </div>
                <p className="mt-1 text-xs break-words text-muted">{[c.email, c.phone, c.owner ? `Owner: ${c.owner}` : null].filter(Boolean).join(' · ') || 'No details yet'}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" onClick={() => setEditing(c)}><Pencil />Edit</Button>
                <Button size="sm" variant="ghost" onClick={async () => {
                  if (await confirm({ title: 'Delete this contact?', description: `"${c.name}" will be removed.`, confirmLabel: 'Delete', danger: true })) remove.mutate(c.id)
                }}><Trash2 />Delete</Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {editing && <ContactForm contact={editing === 'new' ? null : editing} companies={companies} leads={leads}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); invalidate() }} />}
    </>
  )
}

function ContactForm({ contact, companies, leads, onClose, onSaved }: {
  contact: Contact | null; companies: Company[]; leads: Lead[]; onClose: () => void; onSaved: () => void
}) {
  const { base } = useAgent()
  const [draft, setDraft] = useState(() => ({
    name: contact?.name ?? '', email: contact?.email ?? '', phone: contact?.phone ?? '', title: contact?.title ?? '',
    tags: contact?.tags ?? '', preferred_channel: contact?.preferred_channel ?? '', owner: contact?.owner ?? '', notes: contact?.notes ?? '',
    company_id: contact?.company_id ?? ('' as number | ''), lead_id: contact?.lead_id ?? ('' as number | ''),
  }))
  const save = useMutation({
    mutationFn: () => {
      const payload = { ...draft, company_id: draft.company_id || null, lead_id: draft.lead_id || null, preferred_channel: draft.preferred_channel || null }
      return contact
        ? api<Contact>(`${base}/crm/contacts/${contact.id}`, { method: 'PATCH', json: payload })
        : api<Contact>(`${base}/crm/contacts`, { method: 'POST', json: payload })
    },
    onSuccess: () => { toast.success(contact ? 'Contact updated' : 'Contact created'); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Sheet open onClose={onClose} title={contact ? `Edit "${contact.name}"` : 'New contact'}
      footer={<><Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={save.isPending} disabled={!draft.name.trim()} onClick={() => save.mutate()}>{contact ? 'Save changes' : 'Create contact'}</Button></>}>
      <div className="grid gap-4">
        <Field label="Name"><Input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} /></Field>
        <Field label="Company"><Select value={draft.company_id} onChange={(e) => setDraft((d) => ({ ...d, company_id: e.target.value ? Number(e.target.value) : '' }))}>
          <option value="">No company</option>
          {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select></Field>
        <Field label="Converted from lead" hint="The lead this customer converted from, if any">
          <Select value={draft.lead_id} onChange={(e) => setDraft((d) => ({ ...d, lead_id: e.target.value ? Number(e.target.value) : '' }))}>
            <option value="">No linked lead</option>
            {leads.map((l) => <option key={l.id} value={l.id}>{l.name || 'Unnamed'} · {l.phone}</option>)}
          </Select>
        </Field>
        <Field label="Title"><Input value={draft.title} onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))} placeholder="e.g. Procurement Manager" /></Field>
        <Field label="Email"><Input type="email" value={draft.email} onChange={(e) => setDraft((d) => ({ ...d, email: e.target.value }))} /></Field>
        <Field label="Phone"><Input value={draft.phone} onChange={(e) => setDraft((d) => ({ ...d, phone: e.target.value }))} /></Field>
        <Field label="Preferred channel"><Select value={draft.preferred_channel} onChange={(e) => setDraft((d) => ({ ...d, preferred_channel: e.target.value }))}>
          <option value="">Unspecified</option>
          <option value="call">Call</option>
          <option value="email">Email</option>
          <option value="whatsapp">WhatsApp</option>
        </Select></Field>
        <Field label="Tags" hint="Comma-separated"><Input value={draft.tags} onChange={(e) => setDraft((d) => ({ ...d, tags: e.target.value }))} /></Field>
        <Field label="Owner"><Input value={draft.owner} onChange={(e) => setDraft((d) => ({ ...d, owner: e.target.value }))} /></Field>
        <Field label="Notes"><Textarea rows={3} value={draft.notes} onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))} /></Field>
      </div>
    </Sheet>
  )
}

/* ---------------- Deals (Kanban by stage) ---------------- */

function DealsTab({ deals, companies, contacts, leads, companyById, confirm, invalidate }: {
  deals: Deal[]; companies: Company[]; contacts: Contact[]; leads: Lead[]; companyById: Map<number, Company>
  confirm: Confirm; invalidate: () => void
}) {
  const { base } = useAgent()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Deal | 'new' | null>(null)
  const [dragging, setDragging] = useState<{ id: number; from: string } | null>(null)
  const [over, setOver] = useState<string | null>(null)

  const move = useMutation({
    mutationFn: ({ id, stage }: { id: number; stage: string }) => api<Deal>(`${base}/crm/deals/${id}`, { method: 'PATCH', json: { stage } }),
    onSuccess: () => { toast.success('Deal stage updated'); invalidate() },
    onError: (e: Error) => toast.error(e.message),
    onSettled: () => qc.invalidateQueries({ queryKey: ['accounts', 'deals'] }),
  })
  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/crm/deals/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Deal deleted'); invalidate() }, onError: (e: Error) => toast.error(e.message),
  })

  const byStage = useMemo(() => {
    const m = new Map<string, Deal[]>()
    for (const s of DEAL_STAGES) m.set(s, [])
    for (const d of deals) { if (!m.has(d.stage)) m.set(d.stage, []); m.get(d.stage)!.push(d) }
    return m
  }, [deals])
  const stages = useMemo(() => [...DEAL_STAGES, ...[...byStage.keys()].filter((s) => !DEAL_STAGES.includes(s as DealStage))], [byStage])

  return (
    <>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setEditing('new')}><Plus />New deal</Button></div>
      {deals.length === 0 ? (
        <Card><EmptyState icon={<Building2 />} title="No deals yet" description="Track pipeline revenue by stage."
          action={<Button variant="primary" onClick={() => setEditing('new')}><Plus />New deal</Button>} /></Card>
      ) : (
        <div className="-mx-4 overflow-x-auto px-4 pb-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
          <div className="flex flex-col gap-4 sm:w-max sm:flex-row">
            {stages.map((stage) => {
              const items = byStage.get(stage) ?? []
              const total = items.reduce((n, d) => n + (d.value || 0), 0)
              const isOver = over === stage && dragging?.from !== stage
              return (
                <div key={stage}
                  onDragOver={(e) => { if (dragging) { e.preventDefault(); setOver(stage) } }}
                  onDragLeave={(e) => { if (e.currentTarget.contains(e.relatedTarget as Node | null)) return; setOver((o) => (o === stage ? null : o)) }}
                  onDrop={(e) => { e.preventDefault(); if (dragging && dragging.from !== stage) move.mutate({ id: dragging.id, stage }); setDragging(null); setOver(null) }}
                  className={`flex w-full flex-col rounded-[var(--radius-card)] border bg-surface-2/60 transition sm:w-80 sm:shrink-0 ${isOver ? 'border-fg bg-surface-2' : 'border-transparent'}`}>
                  <div className="flex items-center gap-2 px-3.5 pt-3.5 pb-2">
                    <h3 className="flex-1 text-[13px] font-extrabold">{stage}</h3>
                    <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] font-bold tabular-nums ring-1 ring-border">{items.length}</span>
                  </div>
                  <div className="px-3.5 pb-2 text-xs font-semibold text-muted">{money(total)}</div>
                  <div className="flex max-h-[70vh] min-h-24 flex-col gap-2.5 overflow-y-auto px-2.5 pb-3 sm:min-h-40">
                    {items.length ? items.map((d) => (
                      <div key={d.id} draggable
                        onDragStart={(e) => { e.dataTransfer.setData('text/plain', String(d.id)); e.dataTransfer.effectAllowed = 'move'; setDragging({ id: d.id, from: stage }) }}
                        onDragEnd={() => { setDragging(null); setOver(null) }}
                        className="cursor-grab rounded-2xl border border-border bg-surface p-3.5 shadow-card transition active:cursor-grabbing"
                        onClick={() => setEditing(d)}>
                        <div className="flex items-start justify-between gap-2">
                          <h4 className="min-w-0 break-words text-[13px] font-bold">{d.title}</h4>
                          <Button size="icon" variant="ghost" aria-label="Delete deal" onClick={async (e) => {
                            e.stopPropagation()
                            if (await confirm({ title: 'Delete this deal?', description: `"${d.title}" will be removed.`, confirmLabel: 'Delete', danger: true })) remove.mutate(d.id)
                          }}><Trash2 className="size-3.5" /></Button>
                        </div>
                        <div className="mt-1 text-sm font-bold text-success">{money(d.value)}</div>
                        <div className="mt-1 text-[11.5px] text-muted">{d.probability}% probability{d.expected_close ? ` · closes ${d.expected_close}` : ''}</div>
                        {d.company_id && companyById.get(d.company_id) && <div className="mt-1.5 text-[11.5px] text-fg-2">{companyById.get(d.company_id)!.name}</div>}
                        <select aria-label="Move to stage" value={d.stage} disabled={move.isPending} onClick={(e) => e.stopPropagation()}
                          onChange={(e) => move.mutate({ id: d.id, stage: e.target.value })}
                          className="mt-2 h-9 w-full rounded-lg border border-border bg-surface px-1 text-[11px] text-fg-2">
                          {stages.map((s) => <option key={s} value={s}>{s}</option>)}
                        </select>
                      </div>
                    )) : (
                      <div className={`grid flex-1 place-items-center rounded-2xl border-2 border-dashed py-10 text-center text-xs font-semibold text-muted ${isOver ? 'border-fg' : 'border-border'}`}>
                        {dragging ? 'Drop here' : 'No deals'}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
      {editing && <DealForm deal={editing === 'new' ? null : editing} companies={companies} contacts={contacts} leads={leads}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); invalidate() }} />}
    </>
  )
}

function DealForm({ deal, companies, contacts, leads, onClose, onSaved }: {
  deal: Deal | null; companies: Company[]; contacts: Contact[]; leads: Lead[]; onClose: () => void; onSaved: () => void
}) {
  const { base } = useAgent()
  const [draft, setDraft] = useState(() => ({
    title: deal?.title ?? '', stage: deal?.stage ?? DEAL_STAGES[0], value: deal?.value ?? 0, probability: deal?.probability ?? 10,
    company_id: deal?.company_id ?? ('' as number | ''), contact_id: deal?.contact_id ?? ('' as number | ''), lead_id: deal?.lead_id ?? ('' as number | ''),
    expected_close: deal?.expected_close ?? '', lost_reason: deal?.lost_reason ?? '', notes: deal?.notes ?? '',
  }))
  const [customStage, setCustomStage] = useState(!DEAL_STAGES.includes(draft.stage as DealStage))
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        ...draft,
        company_id: draft.company_id || null, contact_id: draft.contact_id || null, lead_id: draft.lead_id || null,
        expected_close: draft.expected_close || null, lost_reason: draft.stage === 'Lost' ? draft.lost_reason || null : null,
      }
      return deal
        ? api<Deal>(`${base}/crm/deals/${deal.id}`, { method: 'PATCH', json: payload })
        : api<Deal>(`${base}/crm/deals`, { method: 'POST', json: payload })
    },
    onSuccess: () => { toast.success(deal ? 'Deal updated' : 'Deal created'); onSaved() },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Sheet open onClose={onClose} title={deal ? `Edit "${deal.title}"` : 'New deal'}
      footer={<><Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" loading={save.isPending} disabled={!draft.title.trim()} onClick={() => save.mutate()}>{deal ? 'Save changes' : 'Create deal'}</Button></>}>
      <div className="grid gap-4">
        <Field label="Title"><Input value={draft.title} onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))} /></Field>
        <Field label="Stage">
          {customStage ? (
            <Input value={draft.stage} onChange={(e) => setDraft((d) => ({ ...d, stage: e.target.value }))} placeholder="Custom stage name" />
          ) : (
            <Select value={draft.stage} onChange={(e) => {
              const stage = e.target.value as DealStage
              setDraft((d) => ({ ...d, stage, probability: STAGE_PROBABILITY[stage] }))
            }}>
              {DEAL_STAGES.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          )}
          <button type="button" className="mt-1 text-left text-xs text-muted underline" onClick={() => setCustomStage((v) => !v)}>
            {customStage ? 'Use a standard stage instead' : 'Use a custom stage instead'}
          </button>
        </Field>
        <Field label="Value (₹)"><Input type="number" min={0} value={draft.value} onChange={(e) => setDraft((d) => ({ ...d, value: Number(e.target.value) || 0 }))} /></Field>
        <Field label="Probability (%)"><Input type="number" min={0} max={100} value={draft.probability} onChange={(e) => setDraft((d) => ({ ...d, probability: Number(e.target.value) || 0 }))} /></Field>
        <Field label="Company"><Select value={draft.company_id} onChange={(e) => setDraft((d) => ({ ...d, company_id: e.target.value ? Number(e.target.value) : '' }))}>
          <option value="">No company</option>
          {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select></Field>
        <Field label="Contact"><Select value={draft.contact_id} onChange={(e) => setDraft((d) => ({ ...d, contact_id: e.target.value ? Number(e.target.value) : '' }))}>
          <option value="">No contact</option>
          {contacts.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select></Field>
        <Field label="Lead"><Select value={draft.lead_id} onChange={(e) => setDraft((d) => ({ ...d, lead_id: e.target.value ? Number(e.target.value) : '' }))}>
          <option value="">No linked lead</option>
          {leads.map((l) => <option key={l.id} value={l.id}>{l.name || 'Unnamed'} · {l.phone}</option>)}
        </Select></Field>
        <Field label="Expected close" hint="YYYY-MM-DD"><Input type="date" value={draft.expected_close} onChange={(e) => setDraft((d) => ({ ...d, expected_close: e.target.value }))} /></Field>
        {draft.stage === 'Lost' && <Field label="Lost reason"><Input value={draft.lost_reason} onChange={(e) => setDraft((d) => ({ ...d, lost_reason: e.target.value }))} /></Field>}
        <Field label="Notes"><Textarea rows={3} value={draft.notes} onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))} /></Field>
      </div>
    </Sheet>
  )
}
