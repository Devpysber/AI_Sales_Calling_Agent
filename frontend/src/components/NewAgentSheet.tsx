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

// Each template carries how the call should actually be run, not just its goal: without `talk` every
// new agent started from the same outbound-sales script whatever job it was created for. `role` and
// `caller` reach the prompt, so a clinic agent introduces itself as front desk talking to a patient
// rather than a sales consultant talking to a prospect.
const TEMPLATES = [
  { id: 'sales', label: 'Outbound sales', role: 'senior sales consultant', caller: 'prospect',
    objective: 'Understand the prospect\'s business, explain how our services help, and book a discovery meeting with our team.', cta: 'Book a 30-minute discovery call with our solutions team.',
    talk: 'Open by checking it is a good time. Ask what they do and what problem they are trying to solve before explaining anything. Give one short, relevant example, not a feature list. Ask for the meeting once you know it fits; if they say no twice, thank them warmly and end the call.' },
  { id: 'support', label: 'Customer support', role: 'customer support specialist', caller: 'customer',
    objective: 'Resolve the caller\'s question using the knowledge base, confirm the issue is solved, and log anything that needs a human follow-up.', cta: 'Create a follow-up for our support team if the issue is not solved on the call.',
    talk: 'Let them finish describing the problem before answering. Repeat the issue back in one line so they know you understood. Give the fix in plain steps. If you cannot solve it, say so honestly, tell them someone will call back, and take down anything the team will need. Never sell anything on a support call.' },
  { id: 'reminder', label: 'Appointments & reminders', role: 'appointments coordinator', caller: 'customer',
    objective: 'Confirm, reschedule or cancel the customer\'s upcoming appointment and answer simple questions about it.', cta: 'Confirm the appointment date and time.',
    talk: 'Keep it under a minute. Say the day and time you are calling about, ask if it still works, and accept the answer. If they want to move it, agree a new time and repeat it back. If they want to cancel, accept it politely without persuading. Do not pitch anything.' },
  { id: 'website', label: 'Website enquiries', role: 'customer advisor', caller: 'enquirer',
    objective: 'Call people who filled a form on our website, understand what they need, answer from the knowledge base and book the next step.', cta: 'Book a callback or visit with our team.',
    talk: 'Mention they enquired on our website so the call is not a surprise. Ask what they were looking for, answer their question first, then offer the next step. They reached out to us, so stay helpful rather than pushy.' },
  { id: 'realestate', label: 'Real estate site visits', role: 'property consultant', caller: 'prospect',
    objective: 'Qualify property enquiries on budget, location and timeline, share project details from the knowledge base, and book a site visit.', cta: 'Book a site visit this week.',
    talk: 'Ask budget, preferred location and when they want to move in, one question at a time. Share only details you actually have. Offer a weekend visit slot first, since most people prefer it. If the budget does not fit, say so plainly instead of pushing.' },
  { id: 'clinic', label: 'Clinic & healthcare', role: 'clinic front-desk coordinator', caller: 'patient',
    objective: 'Answer questions about treatments, timings and charges, and book the patient in with the right doctor.', cta: 'Book a consultation at a time that suits the patient.',
    talk: 'Be calm, warm and unhurried: people calling a clinic are often worried. Ask what the problem is and how long it has been going on, then offer the soonest suitable slot. Never diagnose, never promise a result, and never discuss another patient. Anything clinical beyond the knowledge base goes to the doctor.' },
  { id: 'education', label: 'Courses & admissions', role: 'admissions counsellor', caller: 'student',
    objective: 'Explain courses, fees, batches and eligibility, and book a counselling session or campus visit.', cta: 'Book a counselling session with an advisor.',
    talk: 'Ask what they have studied so far and what they want to do next before recommending anything. Give fees and batch dates plainly when you have them. If a parent is on the line, answer their questions too. Never guarantee a job, a score or an admission.' },
  { id: 'orders', label: 'Orders & deliveries', role: 'order support specialist', caller: 'customer',
    objective: 'Answer questions about an order, delivery, return or refund, and get the customer to the resolution.', cta: 'Confirm what happens next and by when.',
    talk: 'Take the order number first and repeat it back. Say what is happening in plain language and give a date when you have one. If it is late or the answer is bad news, say it straight away and apologise once. Never invent a tracking status. Anything you cannot see goes to the team with a promised callback.' },
  { id: 'services', label: 'Home & field services', role: 'service booking coordinator', caller: 'customer',
    objective: 'Understand the job, share what it involves and roughly what it costs, and book a technician visit.', cta: 'Book a visit slot for the technician.',
    talk: 'Ask what the problem is, how old the equipment is, and the area they are in, one at a time. Give a price range only if the knowledge base has one, and say plainly that the final figure depends on the visit. Offer the earliest slot and confirm the address.' },
  { id: 'hospitality', label: 'Bookings & reservations', role: 'reservations host', caller: 'guest',
    objective: 'Take or change a booking, answer questions about availability, timings and charges, and confirm the reservation.', cta: 'Confirm the booking with date, time and party size.',
    talk: 'Warm and quick, the way a good front desk sounds. Take the date, time and number of people, then repeat the whole booking back once. Mention anything they must know in advance. If the slot is full, offer the nearest alternative rather than saying no.' },
  { id: 'collections', label: 'Payment reminders', role: 'accounts coordinator', caller: 'customer',
    objective: 'Politely remind the customer about a due payment, confirm when they will pay, and note any issue that needs our team.', cta: 'Get a promised payment date.',
    talk: 'Be respectful and never threatening. State what is due and ask when they can pay. If they are facing a problem, listen, note it, and say the team will look at it. Accept whatever date they give and repeat it back. Never argue, never raise your voice, never imply consequences.' },
  { id: 'onboarding', label: 'Customer onboarding', role: 'onboarding specialist', caller: 'customer',
    objective: 'Welcome new customers, confirm their details, explain the next steps and answer setup questions from the knowledge base.', cta: 'Confirm the customer is ready for the next step.',
    talk: 'Welcome them warmly and thank them for choosing us. Confirm their details one at a time. Explain what happens next in two or three plain steps. Ask if anything is unclear and answer it before finishing.' },
  { id: 'survey', label: 'Feedback survey', role: 'customer experience associate', caller: 'customer',
    objective: 'Collect short feedback about the customer\'s recent experience with a few friendly questions.', cta: 'Thank the customer and note their rating and comments.',
    talk: 'Say up front that it will take a minute. Ask two or three short questions and let them talk. Never argue with criticism or defend the company: thank them for it and note it. If they are unhappy, say someone will follow up. Do not sell anything.' },
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
      toast.success(`${agent.name} is ready`, { description: 'Next: add your knowledge base, then a number or leads.' })
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
      (['agent_name', 'company_name', 'company_tagline', 'website_url', 'voice_speaker', 'default_language', 'objective', 'call_to_action', 'instructions', 'agent_password'] as const)
        .map((k) => [k, (f[k] ?? '').trim()]).filter(([, v]) => v))
    if (!copyFrom) {
      profile.objective ??= t.objective
      profile.call_to_action ??= t.cta
      profile.instructions ??= t.talk
      // How the agent introduces itself and what it calls the person on the line.
      profile.agent_role = t.role
      profile.customer_noun = t.caller
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
        {/* Four starred fields are all it takes to make a working agent: without this people fill the whole sheet. */}
        <div className="rounded-xl border border-border bg-surface-2 p-3 text-xs text-muted">
          <span className="font-medium text-fg">Fields marked * are all you need now.</span> Everything else has a
          sensible default and can be changed later on the agent's own pages. After you create it: add your services
          and prices to the <span className="text-fg">Knowledge base</span> (until then the agent takes details and
          promises a callback instead of quoting), point a number at it on <span className="text-fg">Inbound &amp;
          transfer</span>, and add leads or switch on <span className="text-fg">Automation</span> to start calling.
        </div>
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
          <Field label="Agent passcode" hint="Require team members to enter this password to open this workspace's CRM. Leave empty for open access.">
            <Input name="agent_password" type="password" placeholder="No passcode required" />
          </Field>
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
              {TEMPLATES.map((t, i) => (
                <button key={t.id} type="button" onClick={() => setTemplate(t.id)} style={{ animationDelay: `${120 + i * 40}ms` }}
                  className={cn('reveal reveal-in reveal-up rounded-xl border p-3 text-left transition duration-200 hover:-translate-y-0.5 active:scale-[.98]',
                    template === t.id ? 'beam beam-on border-fg bg-surface-2 ring-1 ring-fg' : 'border-border hover:border-border-strong hover:shadow-card')}>
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
          <Field label="How should it talk?" hint="Pre-filled from the template. Edit it to match how your team speaks to customers.">
            <Textarea name="instructions" rows={4} key={`i-${template}-${copyFrom}`} placeholder={copyFrom ? 'Keep the copied playbook' : undefined}
              defaultValue={copyFrom ? '' : TEMPLATES.find((t) => t.id === template)!.talk} />
          </Field>
        </section>
      </form>
    </Sheet>
  )
}
