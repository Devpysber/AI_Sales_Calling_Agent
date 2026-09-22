import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button, Field, Input, Select, Sheet, Skeleton, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgentOptional, useAgents } from '@/lib/agent'
import type { AgentSummary, AgentsResponse } from '@/lib/types'
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
    talk: 'Let them finish describing the problem before answering. Repeat the issue back in one line so they know you understood. Give the fix in plain steps. If you cannot solve it, say so honestly, tell them someone will call back, and take down anything the team will need. Never sell anything on a support call.',
    objections: 'If they push back on being told to wait: acknowledge the frustration, confirm what is being escalated, and give a realistic timeframe if you have one.',
    criteria: 'Hot: issue needs a human follow-up today. Warm: solved, wants a callback. Cold: solved, nothing pending.' },
  { id: 'reminder', label: 'Appointments & reminders', role: 'appointments coordinator', caller: 'customer',
    objective: 'Confirm, reschedule or cancel the customer\'s upcoming appointment and answer simple questions about it.', cta: 'Confirm the appointment date and time.',
    talk: 'Keep it under a minute. Say the day and time you are calling about, ask if it still works, and accept the answer. If they want to move it, agree a new time and repeat it back. If they want to cancel, accept it politely without persuading. Do not pitch anything.',
    objections: 'If they hesitate on the time: offer to reschedule instead of pressing them to keep it.',
    criteria: 'Hot: wants to reschedule now. Warm: confirmed, no action needed. Cold: wants to cancel.' },
  { id: 'website', label: 'Website enquiries', role: 'customer advisor', caller: 'enquirer',
    objective: 'Call people who filled a form on our website, understand what they need, answer from the knowledge base and book the next step.', cta: 'Book a callback or visit with our team.',
    talk: 'Mention they enquired on our website so the call is not a surprise. Ask what they were looking for, answer their question first, then offer the next step. They reached out to us, so stay helpful rather than pushy.',
    objections: 'If they say they were just browsing: ask what caught their interest and answer that before offering a next step.',
    criteria: 'Hot: wants a callback or visit. Warm: interested, needs more info first. Cold: not interested.' },
  { id: 'realestate', label: 'Real estate site visits', role: 'property consultant', caller: 'prospect',
    objective: 'Qualify property enquiries on budget, location and timeline, share project details from the knowledge base, and book a site visit.', cta: 'Book a site visit this week.',
    talk: 'Ask budget, preferred location and when they want to move in, one question at a time. Share only details you actually have. Offer a weekend visit slot first, since most people prefer it. If the budget does not fit, say so plainly instead of pushing.',
    objections: 'Budget too low: ask if they would consider a smaller unit or different area before ruling it out. Just browsing: ask what stage they are at and offer information without pressing for a visit.',
    criteria: 'Hot: budget fits and wants a visit. Warm: interested, budget or timeline unclear. Cold: budget does not fit or not looking anymore.' },
  { id: 'clinic', label: 'Clinic & healthcare', role: 'clinic front-desk coordinator', caller: 'patient',
    objective: 'Answer questions about treatments, timings and charges, and book the patient in with the right doctor.', cta: 'Book a consultation at a time that suits the patient.',
    talk: 'Be calm, warm and unhurried: people calling a clinic are often worried. Ask what the problem is and how long it has been going on, then offer the soonest suitable slot. Never diagnose, never promise a result, and never discuss another patient. Anything clinical beyond the knowledge base goes to the doctor.',
    objections: 'Nervous about cost or the procedure: reassure calmly and offer to have the doctor explain more at the visit. Never diagnose or promise a result over the phone.',
    criteria: 'Hot: wants to book now. Warm: interested, deciding. Cold: not ready or wrong clinic.' },
  { id: 'education', label: 'Courses & admissions', role: 'admissions counsellor', caller: 'student',
    objective: 'Explain courses, fees, batches and eligibility, and book a counselling session or campus visit.', cta: 'Book a counselling session with an advisor.',
    talk: 'Ask what they have studied so far and what they want to do next before recommending anything. Give fees and batch dates plainly when you have them. If a parent is on the line, answer their questions too. Never guarantee a job, a score or an admission.',
    objections: 'Worried about fees or placement guarantees: give the honest facts you have and never promise a job, a score or an admission.',
    criteria: 'Hot: wants to book a session or visit. Warm: interested, comparing options. Cold: not eligible or not interested.' },
  { id: 'orders', label: 'Orders & deliveries', role: 'order support specialist', caller: 'customer',
    objective: 'Answer questions about an order, delivery, return or refund, and get the customer to the resolution.', cta: 'Confirm what happens next and by when.',
    talk: 'Take the order number first and repeat it back. Say what is happening in plain language and give a date when you have one. If it is late or the answer is bad news, say it straight away and apologise once. Never invent a tracking status. Anything you cannot see goes to the team with a promised callback.',
    objections: 'Frustrated about a delay: apologise once, give the honest status, and escalate rather than promise a date you do not have.',
    criteria: 'Hot: needs escalation to the team today. Warm: resolved, wants confirmation in writing. Cold: fully resolved on the call.' },
  { id: 'services', label: 'Home & field services', role: 'service booking coordinator', caller: 'customer',
    objective: 'Understand the job, share what it involves and roughly what it costs, and book a technician visit.', cta: 'Book a visit slot for the technician.',
    talk: 'Ask what the problem is, how old the equipment is, and the area they are in, one at a time. Give a price range only if the knowledge base has one, and say plainly that the final figure depends on the visit. Offer the earliest slot and confirm the address.',
    objections: 'Price concern: explain the final figure depends on the visit and give the range you have, never a firm quote over the phone.',
    criteria: 'Hot: wants to book a visit now. Warm: interested, deciding on timing. Cold: not needed right now.' },
  { id: 'hospitality', label: 'Bookings & reservations', role: 'reservations host', caller: 'guest',
    objective: 'Take or change a booking, answer questions about availability, timings and charges, and confirm the reservation.', cta: 'Confirm the booking with date, time and party size.',
    talk: 'Warm and quick, the way a good front desk sounds. Take the date, time and number of people, then repeat the whole booking back once. Mention anything they must know in advance. If the slot is full, offer the nearest alternative rather than saying no.',
    objections: 'If the preferred slot is full: offer the nearest alternative before accepting a no.',
    criteria: 'Hot: booking confirmed. Warm: interested, checking availability. Cold: not booking.' },
  { id: 'collections', label: 'Payment reminders', role: 'accounts coordinator', caller: 'customer',
    objective: 'Politely remind the customer about a due payment, confirm when they will pay, and note any issue that needs our team.', cta: 'Get a promised payment date.',
    talk: 'Be respectful and never threatening. State what is due and ask when they can pay. If they are facing a problem, listen, note it, and say the team will look at it. Accept whatever date they give and repeat it back. Never argue, never raise your voice, never imply consequences.',
    objections: 'If they say they cannot pay: listen, note the reason, and say the team will follow up, without pressuring further.',
    criteria: 'Hot: gave a firm payment date. Warm: says they will pay but no date. Cold: refuses or disputes the amount.' },
  { id: 'onboarding', label: 'Customer onboarding', role: 'onboarding specialist', caller: 'customer',
    objective: 'Welcome new customers, confirm their details, explain the next steps and answer setup questions from the knowledge base.', cta: 'Confirm the customer is ready for the next step.',
    talk: 'Welcome them warmly and thank them for choosing us. Confirm their details one at a time. Explain what happens next in two or three plain steps. Ask if anything is unclear and answer it before finishing.',
    objections: 'Confused about a step: explain it again in plain terms rather than rushing to the next one.',
    criteria: 'Hot: fully set up and confirmed. Warm: set up but has open questions. Cold: not started, needs a callback.' },
  { id: 'survey', label: 'Feedback survey', role: 'customer experience associate', caller: 'customer',
    objective: 'Collect short feedback about the customer\'s recent experience with a few friendly questions.', cta: 'Thank the customer and note their rating and comments.',
    talk: 'Say up front that it will take a minute. Ask two or three short questions and let them talk. Never argue with criticism or defend the company: thank them for it and note it. If they are unhappy, say someone will follow up. Do not sell anything.',
    objections: 'If they are reluctant to answer: reassure it is quick and optional, and move on without pressing.',
    criteria: 'Hot: detailed feedback given. Warm: short answers given. Cold: declined to answer.' },
]

export function ColorPicker({ value, onChange, labelledBy }: { value: string; onChange: (c: string) => void; labelledBy?: string }) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-labelledby={labelledBy} aria-label={labelledBy ? undefined : 'Colour'}>
      {AGENT_COLORS.map((c) => (
        <button key={c} type="button" onClick={() => onChange(c)} aria-label={`Colour ${c}`}
          aria-pressed={value === c}
          className={cn('grid size-10 shrink-0 place-items-center rounded-lg text-white ring-offset-2 ring-offset-surface transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fg', value === c && 'ring-2 ring-fg')}
          style={{ background: c }}>
          {value === c && <Check className="size-4" />}
        </button>
      ))}
    </div>
  )
}

// Inside a workspace the nearest QueryClient is that agent's own cache (AgentProvider), so
// invalidating ['agents'] there never reaches the root client AppShell resolves agents from.
function useInWorkspace() {
  return useAgentOptional() !== null
}

export default function NewAgentSheet({ open, onClose, onCreated }: {
  open: boolean
  onClose: () => void
  /** Called with the new agent instead of the default navigation, e.g. so a parent on the root client can refetch first. */
  onCreated?: (agent: AgentSummary) => void
}) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const inWorkspace = useInWorkspace()
  const { data, isPending: agentsLoading, isError: agentsFailed } = useAgents()
  const [color, setColor] = useState(AGENT_COLORS[0]!)
  const [template, setTemplate] = useState('sales')
  const [copyFrom, setCopyFrom] = useState('')

  const agentCount = data?.agents.length ?? 0
  useEffect(() => {
    if (!open) return
    setColor(AGENT_COLORS[agentCount % AGENT_COLORS.length]!)
    setTemplate('sales')
    setCopyFrom('')
    // Only reset when the sheet opens; a background refetch changing the count must not wipe the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<AgentSummary>('/api/agents', { method: 'POST', json: body }),
    onSuccess: (agent) => {
      toast.success(`${agent.name} is ready`, { description: 'Next: add your knowledge base, then a number or leads.' })
      // Seed the cache with the new agent before navigating: AppShell resolves the workspace from ['agents'] and
      // bounces to Home when the id is unknown, and the refetch below would still be in flight at navigate time.
      qc.setQueryData<AgentsResponse>(['agents'], (old) => old && { ...old, agents: [...old.agents.filter((a) => a.id !== agent.id), agent] })
      void qc.invalidateQueries({ queryKey: ['agents'] })
      onClose()
      const to = `/a/${agent.id}/agent`
      if (onCreated) { onCreated(agent); return }
      // From inside another agent's workspace the root ['agents'] cache is unreachable from here; a client-side
      // navigate would hit AppShell's "unknown agent" guard and bounce to Home, so do a full navigation instead.
      if (inWorkspace) { window.location.assign(to); return }
      navigate(to)
    },
    onError: (e) => toast.error(e.message),
  })

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (create.isPending) return
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>
    const name = (f.name ?? '').trim()
    if (!name) { toast.error('Give the agent a name'); return }
    const t = TEMPLATES.find((x) => x.id === template) ?? TEMPLATES[0]!
    // Only send what was filled in: blanks keep the copied agent's (or the default) values.
    const profile: Record<string, string> = Object.fromEntries(
      (['agent_name', 'company_name', 'company_tagline', 'website_url', 'voice_speaker', 'default_language', 'objective', 'call_to_action', 'instructions', 'agent_password'] as const)
        .map((k) => [k, (f[k] ?? '').trim()]).filter(([, v]) => v))
    if (!copyFrom) {
      profile.objective ??= t.objective
      profile.call_to_action ??= t.cta
      profile.instructions ??= t.talk
      // Non-sales templates must not inherit the sales-only objection/qualification defaults
      // (PROFILE_DEFAULTS in app/services/agents.py talks price, vendors and "wants a meeting").
      if (t.objections) profile.objection_handling ??= t.objections
      if (t.criteria) profile.qualification_criteria ??= t.criteria
      // How the agent introduces itself and what it calls the person on the line.
      profile.agent_role = t.role
      profile.customer_noun = t.caller
    }
    create.mutate({
      name, description: f.description?.trim() || undefined, phone_number: f.phone_number?.trim() || undefined, color,
      copy_from: copyFrom ? Number(copyFrom) : undefined, profile,
    })
  }

  const voices = data?.voices ?? []
  const close = () => { if (!create.isPending) onClose() }
  return (
    <Sheet open={open} onClose={close} title="New agent" width="max-w-xl"
      description="Each agent is its own workspace: persona, voice, number, knowledge base, leads, calls and history stay separate."
      footer={<><Button onClick={close} disabled={create.isPending}>Cancel</Button><Button variant="primary" type="submit" form="new-agent" loading={create.isPending}>Create agent</Button></>}>
      <form id="new-agent" onSubmit={submit} className="min-w-0">
      <fieldset disabled={create.isPending} className="min-w-0 space-y-6 disabled:opacity-70">
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
            <Input name="name" required autoFocus autoComplete="off" placeholder="Real estate outbound" maxLength={255} />
          </Field>
          <Field label="What is it for?"><Textarea name="description" rows={2} placeholder="Calls website enquiries for the Pune project and books site visits." /></Field>
          <Field label="Website" hint="The site this agent handles leads for. Each agent gets its own website form link on its Automation page.">
            <Input name="website_url" type="url" placeholder="https://www.yourwebsite.com" maxLength={200} />
          </Field>
          <Field label="Phone number" hint="Plivo number for caller ID and inbound calls. Leave blank to use the default number."><Input name="phone_number" inputMode="tel" placeholder="+91 80 1234 5678" /></Field>
          {/* Not a <Field>: that renders a <label>, which would forward clicks on the text to the first swatch. */}
          <div className="grid min-w-0 gap-1.5">
            <span id="new-agent-colour" className="text-[13px] font-semibold text-fg-2">Colour</span>
            <ColorPicker value={color} onChange={setColor} labelledBy="new-agent-colour" />
            <span className="text-xs break-words text-muted">Tells this agent apart in the switcher and on call cards.</span>
          </div>
          <Field label="Agent passcode" hint="Require team members to enter this password to open this workspace's CRM. Leave empty for open access.">
            <Input name="agent_password" type="password" autoComplete="new-password" placeholder="No passcode required" />
          </Field>
        </section>

        <section className="space-y-4 border-t border-border pt-5">
          <h3 className="text-sm font-semibold">Starting point</h3>
          {agentsLoading && <Skeleton className="h-10 w-full rounded-lg" aria-label="Loading agents" />}
          {!!data?.agents.length && (
            <Field label="Copy persona & automation from" hint="Knowledge, leads and calls are never copied.">
              <Select value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
                <option value="">Start fresh</option>
                {data.agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            </Field>
          )}
          {!copyFrom && (
            <div className="grid gap-2 sm:grid-cols-2" role="group" aria-label="Template">
              {TEMPLATES.map((t, i) => (
                <button key={t.id} type="button" onClick={() => setTemplate(t.id)} style={{ animationDelay: `${120 + i * 40}ms` }}
                  aria-pressed={template === t.id}
                  className={cn('reveal reveal-in reveal-up min-h-11 min-w-0 rounded-xl border p-3 text-left transition duration-200 hover:-translate-y-0.5 active:scale-[.98]',
                    template === t.id ? 'beam beam-on border-fg bg-surface-2 ring-1 ring-fg' : 'border-border hover:border-border-strong hover:shadow-card')}>
                  <div className="text-sm font-medium break-words">{t.label}</div>
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
              <Select name="voice_speaker" defaultValue="" disabled={agentsLoading}>
                <option value="">{agentsLoading ? 'Loading voices…' : copyFrom ? 'Same as copied agent' : agentsFailed ? 'Default (voices unavailable)' : 'Default (rahul)'}</option>
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
            <Textarea name="company_tagline" rows={2} required={!copyFrom} maxLength={200} placeholder="e.g. 2 & 3 BHK apartments in Hinjewadi, Pune, ready to move in, from ₹65 lakh." />
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
      </fieldset>
      </form>
    </Sheet>
  )
}
