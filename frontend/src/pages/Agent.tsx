import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BookOpen, Bot, Check, CircleDashed, Lightbulb, MessageSquareText, Mic, MicOff, Play, RotateCcw, Save, SendHorizontal,
  ShieldAlert, Sparkles, Square, Target, UserRound, Volume2,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { QualificationBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Skeleton, Switch, Tabs, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import type { AgentProfile, AgentTurnResult, KnowledgeDoc, Lead, Page, PlaygroundUsage, Turn } from '@/lib/types'
import { cn, LANGUAGES, titleCase } from '@/lib/utils'
import { Stagger } from '@/lib/motion'
import { VoiceOrb, Waveform } from '@/components/VoiceViz'
import { AgentAvatar } from '@/components/AgentAvatar'
import { useAgent } from '@/lib/agent'

type ProfileResponse = { profile: AgentProfile; voices: string[]; languages: Record<string, string> }
type KnowledgeResponse = { documents: KnowledgeDoc[]; stats: { chunks: number; documents: number; semantic: boolean } }
type Section = 'playground' | 'persona' | 'playbook'

const PLACEHOLDERS = ['name', 'agent', 'company']

const unknownPlaceholders = (t: string) =>
  [...t.matchAll(/\{(\w*)\}/g)].map((m) => m[1]!).filter((p) => !PLACEHOLDERS.includes(p))

/* ============================== Page ============================== */

export default function Agent() {
  const { agent, base } = useAgent()
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const meQ = useQuery({ queryKey: ['me'], queryFn: () => api<{ user: string | null }>('/api/auth/me'), staleTime: 60_000 })
  const isAdmin = meQ.data ? meQ.data.user !== 'team' : false
  const requested = params.get('tab') ?? ''
  const tab = (['playground', 'persona', 'playbook'].includes(requested) && (isAdmin || requested === 'playground') ? requested : 'playground') as Section
  const setTab = (t: Section) => setParams((p) => { p.set('tab', t); return p }, { replace: true })

  const { data, isError, error, refetch, isFetching } = useQuery({ queryKey: ['agent'], queryFn: () => api<ProfileResponse>(`${base}/profile`) })
  const knowledge = useQuery({ queryKey: ['knowledge'], queryFn: () => api<KnowledgeResponse>(`${base}/knowledge`) })
  useEffect(() => { if (knowledge.isError) toast.error('Could not load the knowledge base', { description: knowledge.error.message }) }, [knowledge.isError, knowledge.error])

  // One draft shared by every tab, so switching tabs never loses edits.
  const [draft, setDraft] = useState<AgentProfile | null>(null)
  useEffect(() => { if (data && !draft) setDraft(data.profile) }, [data, draft])
  const dirty = !!data && !!draft && JSON.stringify(draft) !== JSON.stringify(data.profile)

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const invalid = draft ? [...unknownPlaceholders(draft.greeting_en), ...unknownPlaceholders(draft.greeting_hi)] : []
  const save = useMutation({
    mutationFn: (p: AgentProfile) => api<AgentProfile>(`${base}/profile`, { method: 'PUT', json: p }),
    onSuccess: (profile, vars) => {
      qc.setQueryData<ProfileResponse>(['agent'], (old) => old && { ...old, profile })
      // Keep edits typed while the PUT was in flight; only adopt the server copy if the draft is unchanged.
      setDraft((d) => (d && JSON.stringify(d) !== JSON.stringify(vars) ? d : profile))
      window.dispatchEvent(new CustomEvent('agents:changed'))
      toast.success('Agent saved', { description: 'Changes apply to the next call and the playground.' })
    },
    onError: (e) => toast.error('Could not save', { description: e.message }),
  })

  if (isError && !data) return (
    <>
      <PageHeader title="Agent" />
      <Card>
        <EmptyState icon={<AlertTriangle className="size-6 text-danger" />} title="Could not load this agent"
          description={<span className="break-words">{error.message}</span>}
          action={<Button variant="primary" loading={isFetching} onClick={() => void refetch()}><RotateCcw />Try again</Button>} />
      </Card>
    </>
  )
  if (!data || !draft) return <><PageHeader title="Agent" /><Skeleton className="h-[560px] rounded-xl" /></>

  const docs = knowledge.data?.stats?.documents ?? knowledge.data?.documents?.length ?? 0
  const checks: { label: string; done: boolean; tab: Section }[] = [
    { label: 'Agent and company name', done: !!draft.agent_name.trim() && !!draft.company_name.trim(), tab: 'persona' },
    { label: 'Company tagline', done: !!draft.company_tagline.trim(), tab: 'persona' },
    { label: 'English and Hindi opening lines', done: !!draft.greeting_en.trim() && !!draft.greeting_hi.trim() && !invalid.length, tab: 'persona' },
    { label: 'Call objective and call to action', done: !!draft.objective.trim() && !!draft.call_to_action.trim(), tab: 'playbook' },
    { label: 'Objection handling', done: draft.objection_handling.trim().length > 20, tab: 'playbook' },
    { label: 'Qualification criteria', done: draft.qualification_criteria.trim().length > 20, tab: 'playbook' },
    { label: 'At least one knowledge document', done: docs > 0, tab: 'playground' },
  ]

  return (
    <div>
      <PageHeader eyebrow={<>{agent?.name} · Build</>} title="Persona & playground"
        description="Shape how this agent introduces itself, what it asks and how it handles pushback, then rehearse a call in the browser with its own voice and knowledge before it dials anyone."
        actions={isAdmin && <Tabs value={tab} onChange={setTab} items={[
          { value: 'playground', label: 'Playground' },
          { value: 'persona', label: <span className="flex items-center gap-1.5"><span className="sm:hidden">Persona</span><span className="hidden sm:inline">Persona & voice</span>{dirty && <span className="size-1.5 rounded-full bg-warning" />}</span> },
          { value: 'playbook', label: <><span className="sm:hidden">Playbook</span><span className="hidden sm:inline">Call playbook</span></> },
        ]} />} />

      <AgentSummary profile={draft} saved={data.profile} voices={data.voices} languages={data.languages} docs={docs} knowledgeError={knowledge.isError} checks={checks} onGo={setTab} />

      {tab === 'playground'
        ? <Playground profile={data.profile} unsaved={dirty} invalid={invalid} onSave={() => { if (!invalid.length) save.mutate(draft) }} saving={save.isPending} />
        : <fieldset disabled={save.isPending} className="min-w-0 disabled:opacity-70"><ProfileEditor section={tab} draft={draft} setDraft={setDraft} data={data} /></fieldset>}

      {dirty && tab !== 'playground' && (
        <div className="sticky bottom-0 z-30 -mx-4 mt-4 border-t border-border bg-surface/95 backdrop-blur-md sm:-mx-6 lg:-mx-8">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6 lg:px-8">
            <AlertTriangle className="size-4 shrink-0 text-warning" />
            <span className="min-w-0 flex-1 basis-40 text-sm font-medium break-words">
              {invalid.length ? <span className="text-danger">Unknown placeholder {invalid.map((p) => `{${p}}`).join(', ')} — use {'{name}'}, {'{agent}'} or {'{company}'}</span> : 'You have unsaved changes'}
            </span>
            <div className="flex gap-2">
              <Button disabled={save.isPending} onClick={() => setDraft(data.profile)}><RotateCcw />Discard</Button>
              <Button variant="primary" disabled={invalid.length > 0} loading={save.isPending} onClick={() => save.mutate(draft)}><Save />Save changes</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ============================== Summary ============================== */

function AgentSummary({ profile, saved, voices, languages, docs, knowledgeError, checks, onGo }: {
  profile: AgentProfile; saved: AgentProfile; voices: string[]; languages: Record<string, string>; docs: number; knowledgeError?: boolean
  checks: { label: string; done: boolean; tab: Section }[]; onGo: (t: Section) => void
}) {
  const { path } = useAgent()
  const done = checks.filter((c) => c.done).length
  const pct = Math.round((done / checks.length) * 100)
  const [open, setOpen] = useState(false)
  const changed = (k: keyof AgentProfile, value: ReactNode) => profile[k] === saved[k] ? value
    : <span key={k} className="inline-flex items-center gap-1.5">{value}<span className="rounded-full bg-warning-soft px-1.5 text-[10px] font-bold text-warning">unsaved</span></span>
  const facts: [string, ReactNode][] = [
    ['Voice', changed('voice_speaker', voices.includes(profile.voice_speaker) ? titleCase(profile.voice_speaker) : profile.voice_speaker)],
    ['Language', changed('default_language', languages[profile.default_language] ?? profile.default_language)],
    ['Max length', changed('max_call_minutes', `${profile.max_call_minutes} min`)],
    ['Knowledge', knowledgeError ? <span key="err" className="text-danger">Failed to load</span> : docs ? `${docs} document${docs > 1 ? 's' : ''}` : <span key="none" className="text-warning">None</span>],
    ['Recording', profile.record_calls ? 'On' : 'Off'],
  ]

  return (
    <Card className="glint beam mb-4 overflow-hidden reveal reveal-in reveal-up">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4 p-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="relative grid size-11 shrink-0 place-items-center rounded-xl bg-brand text-brand-fg shadow-sm">
            <span className="absolute inset-0 rounded-xl bg-brand opacity-40 blur-md" aria-hidden />
            <Bot className="relative size-5" />
          </span>
          <div className="min-w-0">
            <div className="truncate font-semibold">{profile.agent_name || 'Unnamed agent'}</div>
            <div className="truncate text-sm text-muted">{profile.company_name}{profile.company_tagline && ` · ${profile.company_tagline}`}</div>
          </div>
        </div>
        <Stagger className="flex min-w-0 flex-1 basis-full flex-wrap gap-x-8 gap-y-3 sm:basis-auto" delay={120} step={60}>
          {facts.map(([k, v]) => (
            <div key={k} className="min-w-0"><dt className="text-xs text-muted">{k}</dt><dd className="truncate text-sm font-medium">{v}</dd></div>
          ))}
        </Stagger>
        <button type="button" onClick={() => setOpen(!open)} className="flex min-h-10 items-center gap-3 rounded-lg px-2 py-1 text-left hover:bg-surface-2" aria-expanded={open}>
          <Ring pct={pct} />
          <div>
            <div className="flex items-center gap-1.5 text-sm font-medium">
              {pct === 100 && <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-live-ring rounded-full bg-success" /><span className="relative inline-flex size-2 rounded-full bg-success" /></span>}
              {pct === 100 ? 'Ready to call' : 'Setup'}
            </div>
            <div className="text-xs text-muted">{done}/{checks.length} complete</div>
          </div>
        </button>
      </div>
      {open && (
        <ul className="grid gap-x-6 gap-y-1 border-t border-border bg-surface-2/50 px-5 py-3 sm:grid-cols-2 lg:grid-cols-3">
          {checks.map((c) => (
            <li key={c.label}>
              {c.label.includes('knowledge') && !c.done
                ? <Link to={path('/knowledge')} className="flex min-h-10 items-center gap-2 py-1 text-sm text-fg-2 hover:text-brand"><CircleDashed className="size-4 shrink-0 text-muted" />{c.label}</Link>
                : <button type="button" onClick={() => onGo(c.tab)} className={cn('flex min-h-10 items-center gap-2 py-1 text-left text-sm', c.done ? 'text-muted' : 'text-fg-2 hover:text-brand')}>
                    {c.done ? <Check className="size-4 shrink-0 text-success" /> : <CircleDashed className="size-4 shrink-0 text-muted" />}{c.label}
                  </button>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Ring({ pct }: { pct: number }) {
  const r = 16, c = 2 * Math.PI * r
  // Starts empty and sweeps to the real value on the first paint, so the progress reads as progress.
  const [shown, setShown] = useState(0)
  useEffect(() => { const id = requestAnimationFrame(() => setShown(pct)); return () => cancelAnimationFrame(id) }, [pct])
  return (
    <svg viewBox="0 0 40 40" className="size-10 -rotate-90" aria-hidden>
      <circle cx="20" cy="20" r={r} fill="none" strokeWidth="4" className="stroke-surface-2" />
      <circle cx="20" cy="20" r={r} fill="none" strokeWidth="4" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - shown / 100)}
        className={cn('transition-[stroke-dashoffset,stroke] duration-1000 ease-out', pct === 100 ? 'stroke-success' : 'stroke-brand')} />
    </svg>
  )
}

/* ============================== Editor ============================== */

function Section({ title, description, children, aside }: { title: string; description?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} description={description} action={aside} />
      <div className="p-5">{children}</div>
    </Card>
  )
}

function Counted({ value, max, ...rest }: Parameters<typeof Textarea>[0] & { value: string; max: number }) {
  return (
    <div className="relative">
      <Textarea value={value} maxLength={max} {...rest} className="pb-6" />
      <span className={cn('pointer-events-none absolute right-3 bottom-1.5 text-[11px] tabular-nums', value.length > max * 0.9 ? 'text-warning' : 'text-muted')}>{value.length}/{max}</span>
    </div>
  )
}

function ProfileEditor({ section, draft, setDraft, data }: {
  section: 'persona' | 'playbook'; draft: AgentProfile; setDraft: (f: (p: AgentProfile | null) => AgentProfile | null) => void; data: ProfileResponse
}) {
  const { base, path } = useAgent()
  const set = <K extends keyof AgentProfile>(k: K, v: AgentProfile[K]) => setDraft((p) => p && { ...p, [k]: v })
  const [playing, setPlaying] = useState<string | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  // Bumped on every preview start/stop so a request that resolves after Stop (or after a newer request) is ignored.
  const previewToken = useRef(0)
  useEffect(() => () => { previewToken.current++; audio.current?.pause() }, [])

  // What this agent calls the person on the line: patient, guest, student, customer…
  const caller = (draft.customer_noun || 'customer').trim()

  const fill = (t: string) => t.replace(/\{(\w+)\}/g, (m, k: string) => ({ name: 'Rahul', agent: draft.agent_name, company: draft.company_name })[k] ?? m)

  const preview = async (key: string, text: string, language: string) => {
    const token = ++previewToken.current
    if (playing === key) { audio.current?.pause(); setPlaying(null); return }
    audio.current?.pause()
    setPlaying(key)
    try {
      const blob = await api<Blob>(`${base}/voice-preview`, { method: 'POST', json: { text: text.slice(0, 600), language, speaker: draft.voice_speaker } })
      if (token !== previewToken.current) return // stopped or superseded while fetching
      if (!(blob instanceof Blob) || !blob.size) throw new Error('The voice service returned no audio')
      const url = URL.createObjectURL(blob)
      const a = new Audio(url)
      audio.current = a
      const done = () => { URL.revokeObjectURL(url); setPlaying((p) => (p === key ? null : p)) }
      a.onended = done
      a.onpause = done
      a.onerror = () => { done(); toast.error('Voice preview failed', { description: 'The browser could not play the audio.' }) }
      await a.play()
    } catch (e) {
      if (token !== previewToken.current) return
      toast.error('Voice preview failed', { description: (e as Error).message }); setPlaying(null)
    }
  }

  // Max call length is edited as free text so backspacing to empty does not snap to 1; it is clamped when the field loses focus.
  const [minutes, setMinutes] = useState(String(draft.max_call_minutes))
  useEffect(() => { setMinutes(String(draft.max_call_minutes)) }, [draft.max_call_minutes])
  const commitMinutes = () => {
    const n = Math.min(30, Math.max(1, Math.round(Number(minutes)) || 1))
    setMinutes(String(n))
    if (n !== draft.max_call_minutes) set('max_call_minutes', n)
  }

  const insert = (key: 'greeting_en' | 'greeting_hi', token: string) => set(key, `${draft[key]}${draft[key].endsWith(' ') || !draft[key] ? '' : ' '}{${token}}`)

  const aside = (
    <Card className="p-5 text-sm reveal reveal-in reveal-right" style={{ animationDelay: '200ms' }}>
      <div className="flex items-center gap-2 font-medium"><BookOpen className="size-4 text-brand" />Playbook vs knowledge</div>
      <p className="mt-1.5 text-muted">Put <b className="font-medium text-fg-2">how</b> to sell here — tone, process, objection handling. Put <b className="font-medium text-fg-2">what</b> you sell — services, prices, FAQs — in the Knowledge Base, so the agent quotes facts instead of inventing them.</p>
      <Link to={path('/knowledge')} className="mt-3 inline-block font-medium text-brand">Open Knowledge Base →</Link>
    </Card>
  )

  if (section === 'playbook') return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Stagger className="space-y-4" step={60}>
        <Section title="Goal" description="What a successful call achieves. The agent steers every conversation toward this.">
          <div className="grid gap-4">
            <Field label="Call objective"><Counted rows={2} max={600} value={draft.objective} onChange={(e) => set('objective', e.target.value)} placeholder="Qualify the prospect's need for software development and book a discovery meeting." /></Field>
            <Field label="Call to action" hint="The single next step the agent asks for"><Input value={draft.call_to_action} maxLength={200} onChange={(e) => set('call_to_action', e.target.value)} placeholder="Book a 20-minute video call with a solutions consultant" /></Field>
          </div>
        </Section>
        <Section title="Conversation" description="Plain-language instructions, written as you'd brief someone new on their first day.">
          <div className="grid gap-4">
            <Field label="Style & process"><Counted rows={6} max={3000} value={draft.instructions} onChange={(e) => set('instructions', e.target.value)} placeholder={'1. Confirm you are speaking to the decision maker.\n2. Ask about their current challenge.\n3. …'} /></Field>
            <Field label="Objection handling" hint="One objection per line, with how to respond"><Counted rows={6} max={3000} value={draft.objection_handling} onChange={(e) => set('objection_handling', e.target.value)} placeholder={'Too expensive → explain phased delivery and ask about budget range.\nAlready have a vendor → ask what they would improve.'} /></Field>
          </div>
        </Section>
        <Section title="Qualification & guardrails">
          <div className="grid gap-4">
            <Field label="Hot / Warm / Cold criteria"><Counted rows={4} max={1500} value={draft.qualification_criteria} onChange={(e) => set('qualification_criteria', e.target.value)} placeholder={'Hot: clear need, budget and timeline within 3 months.\nWarm: interested, no timeline.\nCold: no need.'} /></Field>
            <Field label="Never say or promise" hint="Enforced on every reply"><Counted rows={3} max={1500} value={draft.forbidden_topics} onChange={(e) => set('forbidden_topics', e.target.value)} placeholder="Discounts, guaranteed delivery dates, competitor criticism" /></Field>
          </div>
        </Section>
      </Stagger>
      <div className="space-y-4 xl:sticky xl:top-20 xl:h-fit">{aside}</div>
    </div>
  )

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
      <Stagger className="space-y-4" step={60}>
        <Section title="Identity" description="How the agent introduces itself and your business.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent name"><Input value={draft.agent_name} maxLength={60} onChange={(e) => set('agent_name', e.target.value)} /></Field>
            <Field label="Company name"><Input value={draft.company_name} maxLength={120} onChange={(e) => set('company_name', e.target.value)} /></Field>
            <Field label="Company tagline" className="sm:col-span-2" hint={`One line on what you do — used when the ${caller} asks “who are you?”`}>
              <Input value={draft.company_tagline} maxLength={200} onChange={(e) => set('company_tagline', e.target.value)} placeholder="AI and software development partner for growing businesses" />
            </Field>
            <Field label="Its job" hint="How it describes itself: clinic front desk, order support, admissions counsellor…">
              <Input value={draft.agent_role ?? ''} maxLength={60} onChange={(e) => set('agent_role', e.target.value)} placeholder="senior sales consultant" />
            </Field>
            <Field label="Calls the person a" hint="What the caller is to you: customer, patient, guest, student, prospect.">
              <Input value={draft.customer_noun ?? ''} maxLength={40} onChange={(e) => set('customer_noun', e.target.value)} placeholder="customer" />
            </Field>
            <Field label="Website" className="sm:col-span-2" hint="The site this agent handles. The agent can mention it, and its website form link is on the Automation page.">
              <Input type="url" value={draft.website_url ?? ''} maxLength={200} onChange={(e) => set('website_url', e.target.value)} placeholder="Not set: e.g. https://www.carsindias.com" />
            </Field>
          </div>
        </Section>

        <Section title="Voice" description="Sarvam Bulbul text-to-speech."
          aside={<Button size="sm" onClick={() => preview('voice', fill(draft.greeting_en || `Hello, this is ${draft.agent_name}.`), 'en-IN')}>{playing === 'voice' ? <Square /> : <Volume2 />}{playing === 'voice' ? 'Stop' : 'Hear voice'}</Button>}>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Speaker"><Select value={draft.voice_speaker} onChange={(e) => set('voice_speaker', e.target.value)}>{data.voices.map((v) => <option key={v} value={v}>{titleCase(v)}</option>)}</Select></Field>
            <Field label="Default language" hint="Used when a lead has none"><Select value={draft.default_language} onChange={(e) => set('default_language', e.target.value)}>{Object.entries(data.languages).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
            <Field label="Max call length" hint="Plivo hangs up after this"><div className="relative"><Input type="number" min={1} max={30} value={minutes} onChange={(e) => setMinutes(e.target.value)} onBlur={commitMinutes} className="pr-12" /><span className="pointer-events-none absolute top-2 right-3 text-sm text-muted">min</span></div></Field>
          </div>
        </Section>

        <Section title="Opening line" description={`The first thing the ${caller} hears. Keep it under 20 words and end with a question.`}>
          <div className="space-y-5">
            {([['greeting_en', 'English', 'en-IN'], ['greeting_hi', 'Hindi', 'hi-IN']] as const).map(([key, label, lang]) => {
              const bad = unknownPlaceholders(draft[key])
              const words = draft[key].trim().split(/\s+/).filter(Boolean).length
              return (
                <div key={key} className="grid gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="mr-auto text-[13px] font-medium text-fg-2">{label}</span>
                    {PLACEHOLDERS.map((p) => <button key={p} type="button" onClick={() => insert(key, p)} className="min-h-10 rounded-md border border-border px-2.5 py-0.5 font-mono text-[11px] text-muted hover:border-brand hover:text-brand sm:min-h-0 sm:px-1.5">{`{${p}}`}</button>)}
                  </div>
                  <Textarea rows={2} value={draft[key]} maxLength={200} onChange={(e) => set(key, e.target.value)} className={cn(bad.length && 'border-danger focus:border-danger focus:ring-danger/15')} />
                  <div className="flex items-start gap-3 rounded-lg bg-surface-2 px-3 py-2.5">
                    <Button size="icon" variant="ghost" className="-my-2 -ml-1 size-10 sm:-my-1 sm:size-8" onClick={() => preview(key, fill(draft[key]), lang)} disabled={!draft[key].trim()} aria-label={`Play ${label} greeting`}>{playing === key ? <Square /> : <Play />}</Button>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-fg-2">{fill(draft[key]) || <span className="text-muted">Empty</span>}</p>
                      <p className={cn('mt-0.5 text-xs', bad.length ? 'text-danger' : words > 18 ? 'text-warning' : 'text-muted')}>
                        {bad.length ? `Unknown placeholder ${bad.map((b) => `{${b}}`).join(', ')}` : `Preview for a lead named Rahul · ${words} words · ${draft[key].length}/200 chars${words > 18 ? ' — shorter is cheaper (TTS is billed per character)' : ''}`}
                      </p>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </Section>

        <Card className="flex items-center justify-between gap-4 p-5">
          <div className="min-w-0"><div className="font-medium">Record calls</div><div className="text-sm text-muted">Save recordings and play them from call history. Tell people the call is recorded where the law requires it.</div></div>
          <Switch checked={draft.record_calls} onChange={(v) => set('record_calls', v)} label="Record calls" />
        </Card>
        <Card className="flex items-center justify-between gap-4 p-5">
          <div className="min-w-0"><div className="font-medium">Voicemail detection</div><div className="text-sm text-muted">Hang up automatically when an answering machine picks up. Can misfire on Indian caller tunes, so keep it off unless you see voicemail calls.</div></div>
          <Switch checked={draft.detect_voicemail} onChange={(v) => set('detect_voicemail', v)} label="Voicemail detection" />
        </Card>
      </Stagger>
      <div className="space-y-4 xl:sticky xl:top-20 xl:h-fit">{aside}</div>
    </div>
  )
}

/* ============================== Playground ============================== */

interface SpeechRecognitionLike { lang: string; interimResults: boolean; onresult: (e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void; onend: () => void; onerror: (e: { error: string }) => void; start: () => void; stop: () => void }
type ChatTurn = Turn & { meta?: AgentTurnResult }
const VISIBLE_TURNS = 4  // turns shown when the transcript is collapsed

/** Turns a raw provider error dump into one line a person can act on. */
function humanLlmError(detail: string) {
  const d = detail.toLowerCase()
  if (/no credits|insufficient_quota|exceed your available credits|\b402\b|payment/.test(d)) return 'the AI provider is out of credits. Top up the account, then retry.'
  if (/prompt tokens limit|context length|too many tokens/.test(d)) return 'the conversation plus knowledge is too long for the free-tier model. Restart the rehearsal or trim the knowledge base.'
  if (/\b429\b|rate limit|rate-limit|in-flight/.test(d)) return 'the AI provider is rate-limited right now. Wait a few seconds and retry.'
  if (/\b401\b|\b403\b|api key|unauthori/.test(d)) return 'the AI provider rejected the API key. Check the keys in Settings.'
  if (/timeout|timed out|network|failed to fetch|econn/.test(d)) return 'the AI provider did not answer in time. Retry in a moment.'
  return 'the AI provider returned an error. Retry, or check the technical detail below.'
}

function Playground({ profile, unsaved, invalid, onSave, saving }: { profile: AgentProfile; unsaved: boolean; invalid: string[]; onSave: () => void; saving: boolean }) {
  const { base } = useAgent()
  const qc = useQueryClient()
  // Rehearsing a call should use the agent's own word for the person on the line.
  const caller = (profile.customer_noun || 'customer').trim()
  const [history, setHistory] = useState<ChatTurn[]>([])
  const [text, setText] = useState('')
  const [lang, setLang] = useState(profile.default_language || 'en-IN')
  const [direction, setDirection] = useState<'outbound' | 'inbound'>('outbound')
  const inbound = direction === 'inbound'
  const [leadId, setLeadId] = useState<number | ''>('')
  const [speak, setSpeak] = useState(true) // Voice is now primary
  const [listening, setListening] = useState(false)

  const [selected, setSelected] = useState<number | null>(null)
  const [ended, setEnded] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  // 0..1 through the current reply's audio: the latest bubble lights its words up as they are spoken.
  const [spokenFrac, setSpokenFrac] = useState(0)
  const mouthTimer = useRef<number>(0)
  // Voice loudness sampled from the playing audio, read by the avatar every frame to move the mouth in time.
  const level = useRef(0)
  const lastFrac = useRef(-1)
  const analyser = useRef<{ ctx: AudioContext; node?: AnalyserNode; source?: MediaElementAudioSourceNode; raf: number } | null>(null)
  const meter = (a: HTMLAudioElement) => {
    try {
      const ctx = analyser.current?.ctx ?? new AudioContext()
      // Tear down the previous graph first: one source node per reply would otherwise pile up on the shared context.
      if (analyser.current) { cancelAnimationFrame(analyser.current.raf); analyser.current.source?.disconnect(); analyser.current.node?.disconnect() }
      const node = ctx.createAnalyser(); node.fftSize = 512
      const source = ctx.createMediaElementSource(a)
      source.connect(node); node.connect(ctx.destination)
      void ctx.resume()
      const buf = new Uint8Array(node.fftSize)
      const tick = () => {
        node.getByteTimeDomainData(buf)
        let sum = 0
        for (const v of buf) { const d = (v - 128) / 128; sum += d * d }
        level.current = Math.sqrt(sum / buf.length)
        // The mouth reads `level` straight from the ref every frame; the word highlight only needs ~12 steps a second.
        // Setting state 60x a second re-rendered this whole page per frame and made the orb stutter on long replies.
        if (a.duration > 0 && !a.paused) {
          const frac = Math.min(1, Math.round((a.currentTime / a.duration) * 40) / 40)
          if (frac !== lastFrac.current) { lastFrac.current = frac; setSpokenFrac(frac) }
        }
        analyser.current!.raf = requestAnimationFrame(tick)
      }
      analyser.current = { ctx, node, source, raf: requestAnimationFrame(tick) }
    } catch { level.current = 0 }
  }
  const bottom = useRef<HTMLDivElement>(null)
  const recog = useRef<SpeechRecognitionLike | null>(null)
  // stop() still finalises captured audio and fires onresult; drop the handlers first so speech captured
  // before a Restart / language switch cannot land in the new session.
  const stopMic = () => {
    const r = recog.current
    if (!r) return
    recog.current = null
    r.onresult = () => {}
    r.onerror = () => {}
    r.onend = () => {}
    try { r.stop() } catch { /* already stopped */ }
    setListening(false)
  }
  const audio = useRef<HTMLAudioElement | null>(null)
  const historyRef = useRef(history)
  useEffect(() => { historyRef.current = history }, [history])
  // Bumped by clear(): a reply that lands after Restart / a mode switch belongs to the old rehearsal and is dropped.
  const session = useRef(0)
  // Read by submit() so a late voice result cannot fire a second turn while one is in flight.
  const pending = useRef(false)
  // The last customer message the agent could not answer, kept in the transcript with a Retry.
  const [failed, setFailed] = useState<{ message: string; detail: string } | null>(null)
  const [showAll, setShowAll] = useState(false)
  // Phones: the transcript covers the avatar, so it stays hidden until asked for. Always shown from sm up.
  const [chatOpen, setChatOpen] = useState(false)

  // Monthly rehearsal allowance (team members): shown as a line under the header and locks the input when spent.
  const usage = useQuery({ queryKey: ['playground-usage', base], queryFn: () => api<PlaygroundUsage>(`${base}/playground/usage`), staleTime: 30_000 })
  const leads = useQuery({ queryKey: ['leads', 'playground'], queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 100, sort: 'name', order: 'asc' } }) })
  useEffect(() => { if (leads.isError) toast.error('Could not load leads for the prospect list', { description: leads.error.message }) }, [leads.isError, leads.error])
  const lead = leads.data?.items.find((l) => l.id === leadId)

  const greeting = useQuery({
    queryKey: ['agent', 'greeting', lang, leadId, direction, profile],
    queryFn: () => api<{ text: string }>(`${base}/greeting`, { params: { language: lang, lead_id: leadId || undefined, purpose: inbound ? 'inbound' : undefined } }),
  })
  // Seed the opening line at index 0; if a customer turn somehow landed first, the greeting still goes in front of it.
  useEffect(() => {
    if (!greeting.data) return
    setHistory((h) => (h[0]?.role === 'assistant' ? h : [{ role: 'assistant', text: greeting.data.text }, ...h]))
  }, [greeting.data])
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [history])
  useEffect(() => () => { audio.current?.pause(); stopMic(); window.clearTimeout(mouthTimer.current); if (analyser.current) { cancelAnimationFrame(analyser.current.raf); analyser.current.source?.disconnect(); analyser.current.node?.disconnect(); void analyser.current.ctx.close() } }, [])

  const play = (url: string) => {
    audio.current?.pause();
    window.clearTimeout(mouthTimer.current)
    // The backend builds audio URLs from PUBLIC_BASE_URL (often an ngrok host). Play the same-origin path instead so the
    // request goes through the dev proxy / this origin: a cross-origin element routed through an AnalyserNode is silent.
    let src = url
    try { const u = new URL(url, location.origin); src = u.origin === location.origin ? u.href : u.pathname + u.search } catch { /* keep as given */ }
    audio.current = new Audio(src);
    // 'play' fires as soon as play() is called, seconds before a deferred TTS file has downloaded, so the
    // mouth used to move in silence. 'playing' means audio is actually coming out; 'waiting' means it stalled.
    audio.current.onplaying = () => setIsSpeaking(true);
    setSpokenFrac(0); lastFrac.current = 0
    audio.current.onwaiting = () => setIsSpeaking(false);
    audio.current.crossOrigin = 'anonymous'
    meter(audio.current)
    audio.current.onended = () => { setIsSpeaking(false); level.current = 0; setSpokenFrac(1) };
    audio.current.onerror = () => setIsSpeaking(false);
    audio.current.onpause = () => setIsSpeaking(false);
    // pause() from the next reply / Restart, or blocked autoplay, rejects play(): swallow it and reset the speaking state.
    audio.current.play().catch(() => { setIsSpeaking(false); level.current = 0 })
  }

  const send = useMutation({
    mutationFn: ({ message, prior }: { message: string; prior: ChatTurn[]; session: number }) => api<AgentTurnResult>(`${base}/playground`, {
      method: 'POST', json: { message, lead_id: leadId || undefined, purpose: inbound ? 'inbound' : undefined, history: prior.map(({ role, text }) => ({ role, text })) },
    }),
    onSettled: () => { pending.current = false },
    onSuccess: (res, vars) => {
      if (vars.session !== session.current) return // the rehearsal was restarted or switched while this reply was in flight
      if (res.usage) qc.setQueryData(['playground-usage', base], res.usage)
      setHistory((h) => { setSelected(h.length); return [...h, { role: 'assistant', text: res.reply, meta: res }] })
      if (speak && res.audio_url) play(res.audio_url)
      else {
        // Voice off or TTS fallback active: mouth the reply for roughly as long as it would take.
        // Detach the old element's handlers before pausing: its 'pause' event lands a task later and
        // would switch the speaking state straight back off.
        if (audio.current) { audio.current.onpause = null; audio.current.onended = null; audio.current.onerror = null; audio.current.onwaiting = null; audio.current.pause() }
        setIsSpeaking(true)
        window.clearTimeout(mouthTimer.current)
        const dur = Math.min(12000, 600 + res.reply.length * 55)
        const t0 = performance.now()
        setSpokenFrac(0)
        const step = () => {
          const f = Math.min(1, (performance.now() - t0) / dur)
          setSpokenFrac(f)
          if (f < 1) mouthTimer.current = window.setTimeout(step, 80)
          else setIsSpeaking(false)
        }
        mouthTimer.current = window.setTimeout(step, 80)
      }
      // audio_error is suppressed: Edge TTS fallback handles it silently on the server side.
      if (res.end_call) setEnded(true)
    },
    onError: (e, { message, session: s }) => {
      if (s !== session.current) return
      // Drop the unanswered bubble and keep a persistent, human explanation with a Retry instead of a raw provider dump.
      setHistory((h) => (h.at(-1)?.role === 'customer' && h.at(-1)?.text === message ? h.slice(0, -1) : h))
      if (/PLAYGROUND_LIMIT/.test(e.message)) { void usage.refetch(); toast.error('Monthly rehearsal limit reached', { description: e.message.replace(/^.*PLAYGROUND_LIMIT:\s*/, '') }); return }
      setFailed({ message, detail: e.message })
      toast.error('The agent could not reply', { description: humanLlmError(e.message) })
    },
  })

  // Reads history through a ref so voice input (which fires later) never sends a stale conversation.
  const submit = useCallback((message: string) => {
    const m = message.trim()
    if (!m || pending.current) return
    pending.current = true
    const prior = historyRef.current
    setHistory([...prior, { role: 'customer', text: m }])
    setText('')
    setFailed(null)
    send.mutate({ message: m, prior, session: session.current })
  }, [send])

  const toggleMic = () => {
    const W = window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike; webkitSpeechRecognition?: new () => SpeechRecognitionLike }
    const Ctor = W.SpeechRecognition ?? W.webkitSpeechRecognition
    if (!Ctor) return void toast.error('Voice input needs Chrome or Edge')
    if (listening) { stopMic(); return }
    const r = new Ctor()
    r.lang = lang
    r.interimResults = false
    r.onresult = (e) => { if (recog.current === r) submit(e.results[0]![0]!.transcript) }
    r.onend = () => { if (recog.current === r) { recog.current = null; setListening(false) } }
    r.onerror = (e: { error: string }) => {
      setListening(false);
      if (e.error === 'no-speech') {
        // Fail silently on timeout, they just didn't speak
      } else if (e.error === 'audio-capture') {
        toast.error('No microphone found, or another app is using it.')
      } else if (e.error === 'not-allowed') {
        toast.error('Microphone access denied by browser.')
      } else {
        toast.error('Browser speech error: ' + e.error)
      }
    }
    recog.current = r
    try { r.start() } catch (e) { toast.error('Could not start listening', { description: (e as Error).message }); return }
    setListening(true)
  }

  const clear = () => {
    session.current++; pending.current = false
    audio.current?.pause(); stopMic(); window.clearTimeout(mouthTimer.current); setIsSpeaking(false); level.current = 0
    setHistory([]); setSelected(null); setEnded(false); setFailed(null); setText(''); setShowAll(false)
  }
  // Error states carry their only Retry inside the transcript; on phones it must not stay display:none.
  useEffect(() => { if (failed || greeting.isError) setChatOpen(true) }, [failed, greeting.isError])
  useEffect(() => { if (greeting.isError) toast.error('Could not load the opening line', { description: greeting.error.message }) }, [greeting.isError, greeting.error])
  const waitingForGreeting = greeting.isPending && history.length === 0
  const u = usage.data
  // A new rehearsal (no customer line yet) is blocked once the month's allowance is spent; a running one may finish.
  const limitReached = !!u && !u.exempt && u.remaining === 0 && !history.some((t) => t.role === 'customer')
  const inputLocked = ended || waitingForGreeting || limitReached
  // Restart: refetch may hand back the same (structurally shared) greeting object, so the seeding effect would not re-run — seed directly.
  const reset = async () => {
    clear()
    const s = session.current
    const g = await greeting.refetch()
    if (s !== session.current || !g.data) return
    setHistory((h) => (h[0]?.role === 'assistant' ? h : [{ role: 'assistant', text: g.data!.text }, ...h]))
  }

  const turns = history.filter((t) => t.meta)
  const inspected = (selected !== null ? history[selected]?.meta : undefined) ?? turns.at(-1)?.meta
  const avgMs = turns.length ? Math.round(turns.reduce((a, t) => a + t.meta!.total_ms, 0) / turns.length) : null

  return (
    <div className="grid gap-4 grid-cols-1">
      {/* ===== Main playground card ===== */}
      <Card className="dark relative flex h-[calc(100svh-180px)] min-h-[520px] flex-col overflow-hidden border-white/10 bg-[#080810] sm:h-[calc(100dvh-280px)] sm:min-h-[680px]">

        {/* ── Toolbar ──────────────────────────────────────────── */}
        <div className="relative z-20 flex flex-wrap items-center gap-2 border-b border-white/10 bg-black/60 px-3 py-2 backdrop-blur-xl shrink-0 sm:px-4 sm:py-3">
          <div className="mr-auto flex w-full min-w-0 items-center gap-2.5 sm:w-auto">
            <VoiceOrb state={send.isPending ? 'speaking' : ended ? 'idle' : 'listening'} size={36} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-white">{profile.agent_name} · {profile.company_name}</div>
              <div className="flex min-w-0 items-center gap-2 text-xs text-white/50">
                <span className={cn('inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-px text-[10.5px] font-semibold uppercase tracking-wider transition-colors',
                  send.isPending ? 'border-brand/40 bg-brand/15 text-brand-fg' : isSpeaking ? 'border-success/40 bg-success/15 text-success' : listening ? 'border-danger/40 bg-danger/15 text-danger' : ended ? 'border-white/10 bg-white/5 text-white/50' : 'border-white/10 bg-white/5 text-white/70')}>
                  {send.isPending ? <><span className="size-1.5 animate-pulse rounded-full bg-brand" />Thinking</>
                    : isSpeaking ? <><Waveform bars={5} className="h-2.5" />Speaking</>
                    : listening ? <><span className="size-1.5 animate-pulse rounded-full bg-danger" />Listening</>
                    : ended ? 'Ended' : <><span className="size-1.5 rounded-full bg-success" />Live</>}
                </span>
                <span className="truncate">{inbound ? 'Rehearsing an inbound call' : 'Rehearsing an outbound call'} · voice {titleCase(profile.voice_speaker)} · nothing is saved to the CRM</span>
              </div>
              {(() => {
                const last = [...history].reverse().find((t) => t.meta?.char_budget)?.meta
                if (!last?.char_budget) return null
                const pct = Math.min(100, (100 * (last.spoken_chars ?? 0)) / last.char_budget)
                return (
                  <div className="mt-1.5 flex items-center gap-2 text-[11px] text-white/70" title="Same TTS character budget the live call is steered by">
                    <span className="h-1.5 w-28 shrink-0 overflow-hidden rounded-full bg-white/15"><span className={cn('block h-full rounded-full transition-[width] duration-500', pct >= 100 ? 'bg-danger' : pct >= 75 ? 'bg-warning' : 'bg-success')} style={{ width: `${pct}%` }} /></span>
                    <span className="truncate">{last.spoken_chars} / {last.char_budget} chars spoken{last.steer === 'budget' ? ' · budget spent: closing' : last.steer === 'steer' ? ' · steering to next step' : ''}</span>
                  </div>
                )
              })()}
              {u && (u.exempt ? (
                <div className="mt-1.5 truncate text-[11px] text-white/60">Administrator · unlimited rehearsals{u.limit > 0 ? ` · team members get ${u.limit} per month` : ''}</div>
              ) : (
                <div className="mt-1.5 flex items-center gap-2 text-[11px] text-white/70" aria-live="polite">
                  <span className="h-1.5 w-28 shrink-0 overflow-hidden rounded-full bg-white/15">
                    <span key={u.used} className={cn('grow-x block h-full rounded-full transition-[width] duration-500', u.remaining === 0 ? 'bg-danger' : u.remaining === 1 ? 'bg-warning' : 'bg-success')}
                      style={{ width: `${Math.min(100, (100 * u.used) / Math.max(1, u.limit))}%` }} />
                  </span>
                  <span className={cn('truncate', u.remaining === 0 ? 'font-semibold text-danger' : u.remaining === 1 && 'text-warning')}>
                    {u.remaining === 0 ? `All ${u.limit} rehearsals used this month` : `${u.used} of ${u.limit} rehearsals used this month · ${u.remaining} left`} · resets {new Date(u.resets_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <Tabs value={direction} onChange={(v) => { setDirection(v); clear() }}
            items={[{ value: 'outbound', label: 'Outbound' }, { value: 'inbound', label: 'Inbound' }]} />
          <Select value={leadId} onChange={(e) => { setLeadId(e.target.value ? Number(e.target.value) : ''); clear() }} className="h-9 w-auto max-w-56 text-[13px]" aria-label="Prospect">
            <option value="">Sample {caller}</option>
            {leads.isPending && <option value="" disabled>Loading leads…</option>}
            {leads.data?.items.map((l) => <option key={l.id} value={l.id}>{l.name || l.phone}</option>)}
          </Select>
          <Select value={lang} onChange={(e) => { setLang(e.target.value); clear() }} className="h-9 w-auto text-[13px]" aria-label="Greeting language">
            {Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
          <label className="flex h-9 items-center gap-2 rounded-lg border border-white/20 bg-white/5 px-2 text-[13px] text-white/70"><Volume2 className="size-3.5" />Voice<Switch checked={speak} onChange={setSpeak} label="Speak replies" /></label>
          <Button size="sm" variant="ghost" onClick={() => void reset()}><RotateCcw />Restart</Button>
          <Clock />
        </div>

        {unsaved && (
          <div className="relative z-20 flex flex-wrap items-center gap-2 border-b border-warning/30 bg-warning-soft/80 px-4 py-2 text-[13px] text-warning backdrop-blur-md shrink-0">
            <AlertTriangle className="size-4 shrink-0" />
            <span className="min-w-0 flex-1 basis-48 break-words">
              {invalid.length ? `Fix the unknown placeholder ${invalid.map((p) => `{${p}}`).join(', ')} in Persona & voice before saving.` : 'The playground uses your saved agent. Save your edits to test them.'}
            </span>
            <Button size="sm" variant="primary" disabled={invalid.length > 0} loading={saving} onClick={onSave}><Save />Save</Button>
          </div>
        )}

        {/* ── Content: avatar fills entire background ── */}
        <div className="relative flex-1 min-h-0">

          {/* Avatar: full card background */}
          <div className="absolute inset-0 bg-[#080810]">
            <AgentAvatar
              zoomOut={false}
              isSpeaking={isSpeaking}
              isListening={listening}
              level={level}
              className="absolute inset-x-0 top-0 bottom-16 sm:inset-0"
            />
          </div>

          {/* Gradient at bottom so bubbles stay readable over the body */}
          <div className="absolute inset-x-0 bottom-0 h-48 bg-gradient-to-t from-black/80 to-transparent pointer-events-none z-[1]" />

          {/* ── Floating Overlay: Chat on left, Input on right ── */}
          <div className="absolute inset-x-3 bottom-3 z-20 flex flex-col items-stretch gap-3 pointer-events-none sm:inset-x-6 sm:bottom-6 sm:flex-row sm:items-end sm:justify-between sm:gap-6">
            
            {/* Chat column: toggle above, bubbles below. The toggle lives outside the clipped/scrolling
                box so it is always reachable, on phones too, whichever mode the transcript is in. */}
            <div className={cn('w-full min-w-0 flex-col gap-2 sm:flex sm:w-auto sm:max-w-[40%] lg:max-w-[36%] sm:min-w-[300px]', chatOpen ? 'flex' : 'hidden')}>
            {history.length > VISIBLE_TURNS && (
              <div className="pointer-events-auto self-start">
                <button type="button" onClick={() => setShowAll((v) => !v)}
                  className="min-h-8 rounded-full border border-white/10 bg-black/60 px-4 py-1.5 text-[11px] font-semibold text-white/85 transition hover:bg-white/15">
                  {showAll ? `Show last ${VISIBLE_TURNS} turns` : `Show full transcript (${history.length} turns)`}
                </button>
              </div>
            )}
            <div
              className={cn(
                'flex w-full min-w-0 flex-col gap-2',
                showAll
                  // Solid panel: a backdrop blur here smeared the avatar into a grey slab behind the text.
                  ? 'pointer-events-auto max-h-[50vh] overflow-y-auto rounded-2xl border border-white/10 bg-[#0b0c12]/92 p-3 sm:max-h-[60vh] sm:p-5 sm:rounded-3xl'
                  : 'pointer-events-none max-h-[38vh] justify-end overflow-hidden [scrollbar-width:none] sm:max-h-[42vh] pt-2 [mask-image:linear-gradient(to_bottom,transparent,black_6%)]',
              )}
            >
              {greeting.isError && history.length === 0 && (
                <div className="pointer-events-auto flex flex-wrap items-center gap-2 self-start rounded-2xl border border-danger/40 bg-danger-soft/90 px-4 py-2.5 text-[13px] text-danger backdrop-blur-md shadow-xl">
                  <AlertTriangle className="size-4 shrink-0" /><span className="min-w-0 break-words">Could not load the opening line: {greeting.error.message}</span>
                  <Button size="sm" variant="ghost" onClick={() => void greeting.refetch()}><RotateCcw />Retry</Button>
                </div>
              )}
              {greeting.isPending && history.length === 0 && (
                <div className="self-start rounded-2xl bg-black/60 px-4 py-2.5 text-[13px] text-white/70 backdrop-blur-md border border-white/10 shadow-xl">Preparing the opening line…</div>
              )}
              {(showAll ? history : history.slice(-VISIBLE_TURNS)).map((t, i, arr) => (
                <div key={history.length - arr.length + i}
                  className={cn('reveal reveal-in reveal-up pointer-events-auto flex max-w-[88%] flex-col gap-1 sm:max-w-[80%]',
                    t.role === 'assistant' ? 'self-start items-start' : 'self-end items-end')}>
                  <div className={cn('flex items-center gap-1.5 px-1 text-[10.5px] font-semibold uppercase tracking-wider text-white/45',
                    t.role === 'customer' && 'flex-row-reverse')}>
                    {t.role === 'assistant'
                      ? <span className="flex size-4 items-center justify-center rounded-full bg-gradient-to-br from-brand to-info text-[9px] text-white"><Sparkles className="size-2.5" /></span>
                      : <span className="flex size-4 items-center justify-center rounded-full bg-white/20 text-white/80"><UserRound className="size-2.5" /></span>}
                    <span>{t.role === 'assistant' ? profile.agent_name : caller}</span>
                    {t.meta && <span className="rounded-full bg-white/10 px-1.5 py-px font-mono text-[9.5px] normal-case tracking-normal text-white/60">{(t.meta.total_ms / 1000).toFixed(1)}s</span>}
                    {t.meta?.qualification && (
                      <span className={cn('rounded-full px-1.5 py-px text-[9.5px] normal-case tracking-normal',
                        t.meta.qualification === 'Hot' ? 'bg-danger/25 text-danger' : t.meta.qualification === 'Warm' ? 'bg-warning/25 text-warning' : 'bg-info/25 text-info')}>
                        {t.meta.qualification}
                      </span>
                    )}
                  </div>
                  <div className={cn(
                    'relative rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed break-words',
                    t.role === 'assistant'
                      ? 'rounded-tl-md border border-white/10 bg-[#20222b] text-white'
                      : 'rounded-tr-md bg-white font-medium text-black',
                    i === arr.length - 1 && t.role === 'assistant' && 'border-brand/50',
                  )}>
                    {i === arr.length - 1 && t.role === 'assistant' && isSpeaking
                      ? <SpokenText text={t.text} frac={spokenFrac} />
                      : t.text}
                  </div>
                </div>
              ))}
              {send.isPending && (
                <div className="reveal reveal-in reveal-up self-start flex items-center gap-2 rounded-2xl rounded-tl-md border border-white/12 bg-gradient-to-br from-white/14 to-white/6 px-5 py-3.5 text-[13px] text-white/60 shadow-xl backdrop-blur-xl">
                  <span className="flex gap-1.5">
                    <span className="size-1.5 rounded-full bg-white/60 animate-bounce [animation-delay:0ms]" />
                    <span className="size-1.5 rounded-full bg-white/60 animate-bounce [animation-delay:150ms]" />
                    <span className="size-1.5 rounded-full bg-white/60 animate-bounce [animation-delay:300ms]" />
                  </span>
                </div>
              )}
              {failed && !send.isPending && (
                <div className="pointer-events-auto flex max-w-[85%] flex-col gap-1.5 self-start rounded-2xl border border-danger/40 bg-danger-soft/90 px-4 py-2.5 text-[13px] text-danger backdrop-blur-md shadow-xl">
                  <div className="flex flex-wrap items-center gap-2">
                    <AlertTriangle className="size-4 shrink-0" />
                    <span className="min-w-0 flex-1 basis-40 break-words">{profile.agent_name || 'The agent'} could not reply — {humanLlmError(failed.detail)}</span>
                    <Button size="sm" variant="ghost" onClick={() => submit(failed.message)}><RotateCcw />Retry</Button>
                  </div>
                  <details className="text-xs opacity-80"><summary className="cursor-pointer">Technical detail</summary><p className="mt-1 break-words font-mono" title={failed.detail}>{failed.detail}</p></details>
                </div>
              )}
              {ended && (
                <div className="reveal reveal-in reveal-up pointer-events-auto flex flex-wrap items-center gap-3 self-start rounded-2xl border border-white/10 bg-[#1a1c24] px-4 py-2.5 text-[13px] text-white/80 shadow-xl">
                  <span className="flex size-6 items-center justify-center rounded-full bg-white/10"><Square className="size-3" /></span>
                  <span className="font-medium">Call ended</span><span className="text-white/45">by the agent</span>
                  <Button size="sm" variant="ghost" className="ml-auto text-white/90" onClick={() => void reset()}><RotateCcw />Start again</Button>
                </div>
              )}
              <div ref={bottom} />
            </div>
            </div>

            {/* Input area */}
            <div className="pointer-events-auto relative flex w-full shrink-0 items-center gap-3 sm:w-auto sm:self-end">
              <Button type="button" size="sm" variant="ghost" onClick={() => setChatOpen((v) => !v)}
                className="h-11 shrink-0 rounded-full border border-white/10 bg-black/50 px-3 text-white/80 hover:bg-black/70 sm:hidden"
                aria-pressed={chatOpen} aria-label={chatOpen ? 'Hide conversation' : `Show conversation (${history.length} turns)`}>
                <MessageSquareText className="size-4" />{history.length > 0 && <span className="text-[11px] font-semibold tabular-nums">{history.length}</span>}
              </Button>
              {listening && (
                <div className="absolute -top-10 right-4 flex items-center gap-2 bg-black/40 px-3 py-1 rounded-full backdrop-blur-md border border-white/10">
                  <div className="size-2 animate-pulse rounded-full bg-red-500" />
                  <span className="text-[11px] font-medium text-red-400 tracking-wide">Listening…</span>
                </div>
              )}
              <form onSubmit={(e) => { e.preventDefault(); submit(text) }}
                className="flex w-full sm:w-[380px] items-center rounded-full border border-white/10 bg-black/50 px-5 py-2.5 transition-all focus-within:bg-black/70 focus-within:border-white/20 backdrop-blur-xl shadow-2xl">
                <Input
                  value={text} onChange={(e) => setText(e.target.value)}
                  disabled={inputLocked} maxLength={1000}
                  placeholder={listening ? 'Listening...' : limitReached ? 'Monthly limit reached' : ended ? 'Call ended' : waitingForGreeting ? 'Preparing…' : 'Type reply…'}
                  aria-label="Your reply"
                  className="h-9 min-w-0 flex-1 border-none bg-transparent text-[13px] text-white shadow-none focus-visible:ring-0 placeholder:text-white/40 px-0"
                />
                <Button type="submit" variant="primary" size="sm"
                  className="ml-2 rounded-full px-4 shrink-0 h-8 bg-white/15 hover:bg-white/25 text-white border-0"
                  disabled={!text.trim() || inputLocked || send.isPending}
                  loading={send.isPending} aria-label="Send">
                  <SendHorizontal className="size-3.5" />
                </Button>
              </form>
              <button type="button" onClick={toggleMic} disabled={inputLocked || send.isPending}
                aria-label={listening ? 'Stop listening' : 'Speak'}
                className={cn(
                  'flex size-14 shrink-0 items-center justify-center rounded-full text-white shadow-2xl transition-all active:scale-95 disabled:opacity-40',
                  listening
                    ? 'bg-red-500 shadow-red-500/30 animate-pulse'
                    : 'bg-[#2a2a35] hover:bg-[#353542] border border-white/5',
                )}>
                {listening ? <MicOff className="size-5" /> : <Mic className="size-5" />}
              </button>
            </div>
          </div>
        </div>
      </Card>


      <div className="space-y-4">
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-brand" />Turn inspector</span>}
            description={inspected ? 'What the agent understood on its latest reply' : undefined}
            action={avgMs !== null && <Badge tone={avgMs < 2500 ? 'success' : avgMs < 4500 ? 'warning' : 'danger'}>avg {(avgMs / 1000).toFixed(1)}s</Badge>} />
          {inspected ? <Inspector key={selected ?? history.length} turn={inspected} /> : (
            <div className="space-y-3 p-5 text-sm text-muted">
              <p>Reply as a {caller} to see what the agent understood on each turn:</p>
              <ul className="space-y-2">
                {[[Target, 'Intent and lead temperature'], [UserRound, 'CRM fields it would update'], [BookOpen, 'Knowledge passages behind the answer']].map(([Icon, l]) => {
                  const I = Icon as typeof Target
                  return <li key={l as string} className="flex items-center gap-2"><I className="size-4 text-brand" />{l as string}</li>
                })}
              </ul>
            </div>
          )}
        </Card>
        {lead && (
          <div className="space-y-4">
            <Card className="p-5 text-sm">
              <div className="text-xs font-medium text-muted uppercase">Testing as</div>
              <div className="mt-1 font-medium">{lead.name || lead.phone}</div>
              <div className="text-muted">{[lead.company, lead.city].filter(Boolean).join(' · ') || lead.phone}</div>
            </Card>
            
            {(lead.summary || lead.requirements || lead.objections) && (
              <Card>
                <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4" />AI analysis</span>} description="Context for this lead from previous calls" />
                <div className="space-y-3 px-4 pb-4 sm:px-5 sm:pb-5">
                  <div className="rounded-2xl border border-border bg-surface-2/40 p-4">
                    <div className="mb-1.5 flex items-center gap-2 text-[13px] font-bold"><Bot className="size-4" />Summary</div>
                    <div className="text-sm leading-relaxed break-words text-fg-2">{lead.summary || <span className="text-muted">No summary yet.</span>}</div>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-2xl border border-border bg-surface-2/40 p-4">
                      <div className="mb-1.5 flex items-center gap-2 text-[13px] font-bold"><Lightbulb className="size-4" />Requirements</div>
                      <div className="text-sm leading-relaxed break-words text-fg-2">{lead.requirements || <span className="text-muted">No requirements stated.</span>}</div>
                    </div>
                    <div className="rounded-2xl border border-border bg-surface-2/40 p-4">
                      <div className="mb-1.5 flex items-center gap-2 text-[13px] font-bold"><ShieldAlert className="size-4" />Objections</div>
                      <div className="text-sm leading-relaxed break-words text-fg-2">{lead.objections || <span className="text-muted">No objections raised.</span>}</div>
                    </div>
                  </div>
                  <Stagger className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                    {[
                      ['Temperature', <QualificationBadge value={lead.qualification ?? null} />], 
                      ['Language', LANGUAGES[lead.language] ?? lead.language]
                    ].map(([k, v]) => (
                      <div key={k as string} className="min-w-0 rounded-xl border border-border px-3 py-2.5">
                        <div className="text-[11px] font-bold tracking-wider text-muted uppercase">{k as string}</div>
                        <div className="mt-1 font-bold break-words">{v as ReactNode}</div>
                      </div>
                    ))}
                  </Stagger>
                </div>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/** The reply as it is being spoken: words already said are bright, the rest wait dimmed. */
function SpokenText({ text, frac }: { text: string; frac: number }) {
  const words = text.split(/(\s+)/)
  const said = Math.round(words.filter((w) => w.trim()).length * frac)
  let n = 0
  return (
    <>
      {words.map((w, i) => {
        if (!w.trim()) return w
        n += 1
        return <span key={i} className={cn('transition-opacity duration-150', n <= said ? 'opacity-100' : 'opacity-35')}>{w}</span>
      })}
    </>
  )
}

/** IST wall clock. Its own component so the tick re-renders this pill only, not the 3D avatar. */
function Clock() {
  const [time, setTime] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setTime(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <div className="hidden h-8 items-center gap-2 rounded-full border border-border bg-black/30 px-3 text-[13px] font-medium tabular-nums sm:flex">
      <div className="size-2 animate-pulse rounded-full bg-green-500" />
      {time.toLocaleTimeString('en-US', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit' })} IST
    </div>
  )
}

function Inspector({ turn }: { turn: AgentTurnResult }) {
  const { path } = useAgent()
  const crm = Object.entries(turn.crm_update ?? {}).filter(([, v]) => v)
  return (
    <div className="space-y-5 p-5 text-sm">
      <Stagger className="grid grid-cols-2 gap-2" step={70}>
        {([['Intent', titleCase(turn.intent || 'unknown')], ['Temperature', <QualificationBadge key="q" value={turn.qualification} />], ['Sentiment', turn.sentiment ?? '—'], ['Language', turn.language ?? '—']] as [string, ReactNode][]).map(([k, v]) => (
          <div key={k} className="glint rounded-lg border border-border/60 bg-surface-2 p-2.5 transition hover:border-border-strong"><dt className="text-xs text-muted">{k}</dt><dd className="mt-0.5 font-medium capitalize">{v}</dd></div>
        ))}
      </Stagger>

      <div className="reveal reveal-in reveal-up" style={{ animationDelay: '280ms' }}>
        <div className="mb-1.5 flex justify-between text-xs text-muted"><span>Response time</span><span className="tabular-nums">{(turn.total_ms / 1000).toFixed(2)}s total</span></div>
        {/* 0-6s scale: green under 2.5s (feels instant on a call), amber to 4.5s, red beyond. */}
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div className={cn('grow-x h-full rounded-full', turn.total_ms < 2500 ? 'bg-success' : turn.total_ms < 4500 ? 'bg-warning' : 'bg-danger')}
            style={{ width: `${Math.min(100, (turn.total_ms / 6000) * 100)}%`, animationDelay: '320ms' }} />
        </div>
        {turn.llm_ms != null && <div className="mt-1 text-[11px] text-muted tabular-nums">model {(turn.llm_ms / 1000).toFixed(2)}s · voice {Math.max(0, (turn.total_ms - turn.llm_ms) / 1000).toFixed(2)}s</div>}
      </div>

      {turn.end_call && <div className="flex items-center gap-2 rounded-lg bg-warning-soft px-3 py-2 text-xs font-medium text-warning"><AlertTriangle className="size-3.5" />Agent decided to end the call</div>}

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted uppercase">CRM updates</div>
        {crm.length ? (
          <Stagger className="divide-y divide-border rounded-lg border border-border" from="left" delay={360} step={60}>
            {crm.map(([k, v]) => <div key={k} className="flex flex-wrap gap-x-3 gap-y-0.5 px-3 py-2 sm:flex-nowrap"><dt className="w-full shrink-0 text-muted capitalize sm:w-24">{k.replace(/_/g, ' ')}</dt><dd className="min-w-0 break-words">{String(v)}</dd></div>)}
          </Stagger>
        ) : <p className="text-muted">Nothing new to record.</p>}
      </div>

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted uppercase">Knowledge used</div>
        {turn.knowledge?.length ? <Stagger className="space-y-1.5" from="left" delay={420} step={60}>{turn.knowledge.map((k, i) => (
          <div key={i} className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs transition hover:border-border-strong"><BookOpen className="size-3.5 shrink-0 text-brand" /><span className="truncate font-medium">{k.title}</span></div>
        ))}</Stagger> : <p className="text-muted">No documents matched. <Link to={path('/knowledge')} className="text-brand">Add knowledge</Link> so the agent can answer specifics.</p>}
      </div>
    </div>
  )
}
