import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button, Field, Input, Select, Sheet, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgents } from '@/lib/agent'
import type { AgentSummary } from '@/lib/types'
import { cn, LANGUAGES } from '@/lib/utils'

export const AGENT_COLORS = ['#5b4bf5', '#0e8a5e', '#d9480f', '#1f6feb', '#c2255c', '#7048e8', '#0b7285', '#b36b00']

const TEMPLATES = [
  { id: 'sales', label: 'Outbound sales', objective: 'Understand the prospect\'s business, explain how our services help, and book a discovery meeting with our team.', cta: 'Book a 30-minute discovery call with our solutions team.' },
  { id: 'support', label: 'Customer support', objective: 'Resolve the caller\'s question using the knowledge base, confirm the issue is solved, and log anything that needs a human follow-up.', cta: 'Create a follow-up for our support team if the issue is not solved on the call.' },
  { id: 'reminder', label: 'Appointments & reminders', objective: 'Confirm, reschedule or cancel the customer\'s upcoming appointment and answer simple questions about it.', cta: 'Confirm the appointment date and time.' },
  { id: 'website', label: 'Website enquiries', objective: 'Call people who filled a form on our website, understand what they need, answer from the knowledge base and book the next step.', cta: 'Book a callback or visit with our team.' },
  { id: 'realestate', label: 'Real estate site visits', objective: 'Qualify property enquiries on budget, location and timeline, share project details from the knowledge base, and book a site visit.', cta: 'Book a site visit this week.' },
  { id: 'collections', label: 'Payment reminders', objective: 'Politely remind the customer about a due payment, confirm when they will pay, and note any issue that needs our team.', cta: 'Get a promised payment date.' },
  { id: 'onboarding', label: 'Customer onboarding', objective: 'Welcome new customers, confirm their details, explain the next steps and answer setup questions from the knowledge base.', cta: 'Confirm the customer is ready for the next step.' },
  { id: 'survey', label: 'Feedback survey', objective: 'Collect short feedback about the customer\'s recent experience with a few friendly questions.', cta: 'Thank the customer and note their rating and comments.' },
]

export function ColorPicker({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {AGENT_COLORS.map((c) => (
        <button key={c} type="button" onClick={() => onChange(c)} aria-label={`Colour ${c}`}
          className={cn('grid size-8 place-items-center rounded-lg text-white ring-offset-2 ring-offset-surface transition', value === c && 'ring-2 ring-fg')}
          style={{ background: c }}>
          {value === c && <Check className="size-4" />}
        </button>
      ))}
    </div>
  )
}

export default function NewAgentSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data } = useAgents()
  const [color, setColor] = useState(AGENT_COLORS[0]!)
  const [template, setTemplate] = useState('sales')
  const [copyFrom, setCopyFrom] = useState('')

  useEffect(() => {
    if (open) {
      setColor(AGENT_COLORS[(data?.agents.length ?? 0) % AGENT_COLORS.length]!)
      setTemplate('sales')
      setCopyFrom('')
    }
  }, [open, data?.agents.length])

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<AgentSummary>('/api/agents', { method: 'POST', json: body }),
    onSuccess: (agent) => {
      toast.success(`${agent.name} is ready`, { description: 'Add knowledge and leads to start calling.' })
      qc.invalidateQueries({ queryKey: ['agents'] })
      onClose()
      navigate(`/a/${agent.id}/agent`)
    },
    onError: (e) => toast.error(e.message),
  })

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>
    const t = TEMPLATES.find((x) => x.id === template)!
    // Only send what was filled in: blanks keep the copied agent's (or the default) values.
    const profile: Record<string, string> = Object.fromEntries(
      (['agent_name', 'company_name', 'company_tagline', 'website_url', 'voice_speaker', 'default_language', 'objective', 'call_to_action', 'instructions'] as const)
        .map((k) => [k, (f[k] ?? '').trim()]).filter(([, v]) => v))
    if (!copyFrom) {
      profile.objective ??= t.objective
      profile.call_to_action ??= t.cta
    }
    create.mutate({
      name: f.name, description: f.description || undefined, phone_number: f.phone_number || undefined, color,
      copy_from: copyFrom ? Number(copyFrom) : undefined, profile,
    })
  }

  const voices = data?.voices ?? []
  return (
    <Sheet open={open} onClose={onClose} title="New agent" width="max-w-xl"
      description="Each agent is its own workspace: persona, voice, number, knowledge base, leads, calls and history stay separate."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" type="submit" form="new-agent" loading={create.isPending}>Create agent</Button></>}>
      <form id="new-agent" onSubmit={submit} className="space-y-6">
        <section className="space-y-4">
          <h3 className="text-sm font-semibold">Workspace</h3>
          <Field label="Agent name *" hint="What your team calls this agent, e.g. “Real estate leads” or “Clinic reminders”.">
            <Input name="name" required autoFocus placeholder="Real estate outbound" maxLength={255} />
          </Field>
          <Field label="What is it for?"><Textarea name="description" rows={2} placeholder="Calls website enquiries for the Pune project and books site visits." /></Field>
          <Field label="Website" hint="The site this agent handles leads for. Each agent gets its own website form link on its Automation page.">
            <Input name="website_url" type="url" placeholder="https://www.yourwebsite.com" maxLength={200} />
          </Field>
          <Field label="Phone number" hint="Plivo number for caller ID and inbound calls. Leave blank to use the default number."><Input name="phone_number" inputMode="tel" placeholder="+91 80 1234 5678" /></Field>
        </section>

        <section className="space-y-4 border-t border-border pt-5">
          <h3 className="text-sm font-semibold">Starting point</h3>
          {!!data?.agents.length && (
            <Field label="Copy persona & automation from" hint="Knowledge, leads and calls are never copied.">
              <Select value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
                <option value="">Start fresh</option>
                {data.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </Field>
          )}
          {!copyFrom && (
            <div className="grid gap-2 sm:grid-cols-2">
              {TEMPLATES.map((t) => (
                <button key={t.id} type="button" onClick={() => setTemplate(t.id)}
                  className={cn('rounded-xl border p-3 text-left transition', template === t.id ? 'border-fg bg-surface-2 ring-1 ring-fg' : 'border-border hover:border-border-strong')}>
                  <div className="text-sm font-medium">{t.label}</div>
                  <div className="mt-1 line-clamp-2 text-xs text-muted">{t.objective}</div>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-4 border-t border-border pt-5">
          <h3 className="text-sm font-semibold">Persona</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={copyFrom ? 'Speaks as' : 'Speaks as *'} hint="First name the agent introduces itself with."><Input name="agent_name" required={!copyFrom} placeholder="e.g. Neha" maxLength={60} /></Field>
            <Field label={copyFrom ? 'Company' : 'Company *'} hint="Said in the greeting: “calling from …”."><Input name="company_name" required={!copyFrom} placeholder="e.g. Skyline Realty" maxLength={120} /></Field>
            <Field label="Voice">
              <Select name="voice_speaker" defaultValue="">
                <option value="">{copyFrom ? 'Same as copied agent' : 'Default (rahul)'}</option>
                {voices.map((v) => <option key={v} value={v} className="capitalize">{v}</option>)}
              </Select>
            </Field>
            <Field label="Default language">
              <Select name="default_language" defaultValue="">
                <option value="">{copyFrom ? 'Same as copied agent' : 'English'}</option>
                {Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
          </div>
          <Field label={copyFrom ? 'What the company does' : 'What the company does *'} hint="One or two lines the agent can say. Details belong in the knowledge base.">
            <Textarea name="company_tagline" rows={2} required={!copyFrom} maxLength={400} placeholder="e.g. 2 & 3 BHK apartments in Hinjewadi, Pune, ready to move in, from ₹65 lakh." />
          </Field>
        </section>

        <section className="space-y-4 border-t border-border pt-5">
          <h3 className="text-sm font-semibold">Call playbook</h3>
          <Field label="Objective" hint="What a successful call achieves. Pre-filled from the template.">
            <Textarea name="objective" rows={2} key={`o-${template}-${copyFrom}`} placeholder={copyFrom ? 'Keep the copied objective' : undefined}
              defaultValue={copyFrom ? '' : TEMPLATES.find((t) => t.id === template)!.objective} />
          </Field>
          <Field label="Call to action" hint="The one concrete next step the agent asks for.">
            <Input name="call_to_action" key={`c-${template}-${copyFrom}`} placeholder={copyFrom ? 'Keep the copied call to action' : undefined}
              defaultValue={copyFrom ? '' : TEMPLATES.find((t) => t.id === template)!.cta} />
          </Field>
          <Field label="How should it talk?" hint="Optional: tone, questions to ask, what to avoid. You can refine the full playbook after creating.">
            <Textarea name="instructions" rows={3} placeholder="e.g. Friendly and brief. Ask budget, preferred location and move-in timeline. Offer a Saturday site visit." />
          </Field>
        </section>
      </form>
    </Sheet>
  )
}
