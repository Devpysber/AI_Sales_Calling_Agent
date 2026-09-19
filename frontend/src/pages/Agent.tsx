import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BookOpen, Bot, Check, CircleDashed, Mic, MicOff, Play, RotateCcw, Save, SendHorizontal,
  Sparkles, Square, Target, UserRound, Volume2,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { QualificationBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Skeleton, Switch, Tabs, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import type { AgentProfile, AgentTurnResult, KnowledgeDoc, Lead, Page, Turn } from '@/lib/types'
import { cn, LANGUAGES, titleCase } from '@/lib/utils'
import { Stagger } from '@/lib/motion'
import { VoiceOrb } from '@/components/VoiceViz'
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
  const tab = (['playground', 'persona', 'playbook'].includes(params.get('tab') ?? '') ? params.get('tab') : 'playground') as Section
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
        actions={<Tabs value={tab} onChange={setTab} items={[
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
    <Card className="mb-4 overflow-hidden reveal reveal-in reveal-up">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4 p-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand text-brand-fg shadow-sm"><Bot className="size-5" /></span>
          <div className="min-w-0">
            <div className="truncate font-semibold">{profile.agent_name || 'Unnamed agent'}</div>
            <div className="truncate text-sm text-muted">{profile.company_name}{profile.company_tagline && ` · ${profile.company_tagline}`}</div>
          </div>
        </div>
        <dl className="flex min-w-0 flex-1 basis-full flex-wrap gap-x-8 gap-y-3 sm:basis-auto">
          {facts.map(([k, v]) => (
            <div key={k} className="min-w-0"><dt className="text-xs text-muted">{k}</dt><dd className="truncate text-sm font-medium">{v}</dd></div>
          ))}
        </dl>
        <button type="button" onClick={() => setOpen(!open)} className="flex min-h-10 items-center gap-3 rounded-lg px-2 py-1 text-left hover:bg-surface-2" aria-expanded={open}>
          <Ring pct={pct} />
          <div><div className="text-sm font-medium">{pct === 100 ? 'Ready to call' : 'Setup'}</div><div className="text-xs text-muted">{done}/{checks.length} complete</div></div>
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
  return (
    <svg viewBox="0 0 40 40" className="size-10 -rotate-90" aria-hidden>
      <circle cx="20" cy="20" r={r} fill="none" strokeWidth="4" className="stroke-surface-2" />
      <circle cx="20" cy="20" r={r} fill="none" strokeWidth="4" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)}
        className={cn('transition-all', pct === 100 ? 'stroke-success' : 'stroke-brand')} />
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
                  <Textarea rows={2} value={draft[key]} maxLength={400} onChange={(e) => set(key, e.target.value)} className={cn(bad.length && 'border-danger focus:border-danger focus:ring-danger/15')} />
                  <div className="flex items-start gap-3 rounded-lg bg-surface-2 px-3 py-2.5">
                    <Button size="icon" variant="ghost" className="-my-2 -ml-1 size-10 sm:-my-1 sm:size-8" onClick={() => preview(key, fill(draft[key]), lang)} disabled={!draft[key].trim()} aria-label={`Play ${label} greeting`}>{playing === key ? <Square /> : <Play />}</Button>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-fg-2">{fill(draft[key]) || <span className="text-muted">Empty</span>}</p>
                      <p className={cn('mt-0.5 text-xs', bad.length ? 'text-danger' : words > 25 ? 'text-warning' : 'text-muted')}>
                        {bad.length ? `Unknown placeholder ${bad.map((b) => `{${b}}`).join(', ')}` : `Preview for a lead named Rahul · ${words} words${words > 25 ? ' — consider shortening' : ''}`}
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
  const mouthTimer = useRef<number>(0)
  // Voice loudness sampled from the playing audio, read by the avatar every frame to move the mouth in time.
  const level = useRef(0)
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
        analyser.current!.raf = requestAnimationFrame(tick)
      }
      analyser.current = { ctx, node, source, raf: requestAnimationFrame(tick) }
    } catch { level.current = 0 }
  }
  const bottom = useRef<HTMLDivElement>(null)
  const recog = useRef<SpeechRecognitionLike | null>(null)
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
  useEffect(() => () => { audio.current?.pause(); recog.current?.stop(); window.clearTimeout(mouthTimer.current); if (analyser.current) { cancelAnimationFrame(analyser.current.raf); analyser.current.source?.disconnect(); analyser.current.node?.disconnect(); void analyser.current.ctx.close() } }, [])

  const play = (url: string) => {
    audio.current?.pause();
    window.clearTimeout(mouthTimer.current)
    // The backend builds audio URLs from PUBLIC_BASE_URL (often an ngrok host). Play the same-origin path instead so the
    // request goes through the dev proxy / this origin: a cross-origin element routed through an AnalyserNode is silent.
    let src = url
    try { const u = new URL(url, location.origin); src = u.origin === location.origin ? u.href : u.pathname + u.search } catch { /* keep as given */ }
    audio.current = new Audio(src);
    audio.current.onplay = () => setIsSpeaking(true);
    audio.current.crossOrigin = 'anonymous'
    meter(audio.current)
    audio.current.onended = () => { setIsSpeaking(false); level.current = 0 };
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
      setHistory((h) => { setSelected(h.length); return [...h, { role: 'assistant', text: res.reply, meta: res }] })
      if (speak && res.audio_url) play(res.audio_url)
      else {
        // Voice off, or no audio (TTS outage): still mouth the reply for roughly as long as it would take to say it.
        audio.current?.pause()
        setIsSpeaking(true)
        window.clearTimeout(mouthTimer.current)
        mouthTimer.current = window.setTimeout(() => setIsSpeaking(false), Math.min(12000, 600 + res.reply.length * 55))
      }
      if (speak && res.audio_error) toast.warning('Voice unavailable', { description: res.audio_error })
      if (res.end_call) setEnded(true)
    },
    onError: (e, { message, session: s }) => {
      if (s !== session.current) return
      // Drop the unanswered bubble and keep a persistent, human explanation with a Retry instead of a raw provider dump.
      setHistory((h) => (h.at(-1)?.role === 'customer' && h.at(-1)?.text === message ? h.slice(0, -1) : h))
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
    if (listening) { recog.current?.stop(); return }
    const r = new Ctor()
    r.lang = lang
    r.interimResults = false
    r.onresult = (e) => submit(e.results[0]![0]!.transcript)
    r.onend = () => setListening(false)
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
    audio.current?.pause(); recog.current?.stop(); window.clearTimeout(mouthTimer.current); setIsSpeaking(false); level.current = 0
    setHistory([]); setSelected(null); setEnded(false); setFailed(null); setText('')
  }
  const waitingForGreeting = greeting.isPending && history.length === 0
  const inputLocked = ended || waitingForGreeting
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
      <Card className="relative flex h-[calc(100dvh-290px)] min-h-[640px] flex-col overflow-hidden sm:min-h-[540px]">

        {/* 3D Avatar – anchored to bottom 60% of card so the face stays clear of chat bubbles */}
        <div className="absolute inset-x-0 bottom-0 top-[38%] z-0" aria-hidden>
          <AgentAvatar zoomOut={true} isSpeaking={isSpeaking || send.isPending} isListening={listening} level={level} />
        </div>
        {/* Dark background for the chat/text area at the top */}
        <div className="absolute inset-x-0 top-0 h-[38%] z-0 bg-surface" aria-hidden />
        {/* Subtle gradient fade between chat area and avatar */}
        <div className="absolute inset-x-0 top-[34%] z-[1] h-20 bg-gradient-to-b from-surface/80 to-transparent pointer-events-none" aria-hidden />

        <div className="relative z-10 flex flex-wrap items-center gap-2 border-b border-border/50 bg-elevated/40 px-4 py-3 backdrop-blur-xl">
          <div className="mr-auto flex min-w-0 items-center gap-2.5">
            {/* The agent's orb: calm while it waits for you, spinning up while it thinks of a reply. */}
            <VoiceOrb state={send.isPending ? 'speaking' : ended ? 'idle' : 'listening'} size={40} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">{profile.agent_name} · {profile.company_name}</div>
              <div className="truncate text-xs text-muted">{inbound ? 'Rehearsing an inbound call' : 'Rehearsing an outbound call'} · voice {titleCase(profile.voice_speaker)} · nothing is saved to the CRM</div>
            </div>
          </div>
          <Tabs value={direction} onChange={(v) => { setDirection(v); clear() }}
            items={[{ value: 'outbound', label: 'Outbound' }, { value: 'inbound', label: 'Inbound' }]} />
          <Select value={leadId} onChange={(e) => { setLeadId(e.target.value ? Number(e.target.value) : ''); clear() }} className="h-10 w-auto max-w-40 text-[13px] sm:h-8 sm:max-w-44" aria-label="Prospect">
            <option value="">Sample {caller}</option>
            {leads.isPending && <option value="" disabled>Loading leads…</option>}
            {leads.data?.items.map((l) => <option key={l.id} value={l.id}>{l.name || l.phone}</option>)}
          </Select>
          <Select value={lang} onChange={(e) => { setLang(e.target.value); clear() }} className="h-10 w-auto text-[13px] sm:h-8" aria-label="Greeting language">
            {Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
          <label className="flex h-10 items-center gap-2 rounded-lg border border-border px-2 text-[13px] text-muted sm:h-8"><Volume2 className="size-3.5" />Voice<Switch checked={speak} onChange={setSpeak} label="Speak replies" /></label>
          <Button size="sm" variant="ghost" onClick={() => void reset()}><RotateCcw />Restart</Button>
          {/* IST clock lives in the toolbar so it can wrap with the other controls instead of floating over them. */}
          <Clock />
        </div>

        {unsaved && (
          <div className="relative z-10 flex flex-wrap items-center gap-2 border-b border-warning/30 bg-warning-soft/80 px-4 py-2 text-[13px] text-warning backdrop-blur-md">
            <AlertTriangle className="size-4 shrink-0" />
            <span className="min-w-0 flex-1 basis-48 break-words">
              {invalid.length ? `Fix the unknown placeholder ${invalid.map((p) => `{${p}}`).join(', ')} in Persona & voice before saving.` : 'The playground uses your saved agent. Save your edits to test them.'}
            </span>
            <Button size="sm" variant="primary" disabled={invalid.length > 0} loading={saving} onClick={onSave}><Save />Save</Button>
          </div>
        )}

        {/* Transcript: stays in the upper zone so it never overlaps the face */}
        <div className={cn('relative z-10 mt-auto flex flex-col gap-2 overflow-y-auto px-4 pt-4 pb-20 sm:pb-16',
          showAll ? 'pointer-events-auto max-h-[55%] bg-black/25 backdrop-blur-sm' : 'pointer-events-none max-h-[38%] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden')}>
          {history.length > 4 && (
            <button type="button" onClick={() => setShowAll((v) => !v)}
              className="pointer-events-auto sticky top-0 z-10 mb-1 min-h-10 self-start rounded-full bg-black/45 px-3 py-1 text-xs font-medium text-white/85 backdrop-blur-md hover:bg-black/60">
              {showAll ? 'Show last 4 turns' : `Show full transcript (${history.length} turns)`}
            </button>
          )}
          {greeting.isError && history.length === 0 && (
            <div className="pointer-events-auto flex flex-wrap items-center gap-2 self-start rounded-2xl border border-danger/40 bg-danger-soft/80 px-3 py-2 text-[13px] text-danger backdrop-blur-md">
              <AlertTriangle className="size-4 shrink-0" /><span className="min-w-0 break-words">Could not load the opening line: {greeting.error.message}</span>
              <Button size="sm" variant="ghost" onClick={() => void greeting.refetch()}><RotateCcw />Retry</Button>
            </div>
          )}
          {greeting.isPending && history.length === 0 && (
            <div className="self-start rounded-2xl bg-black/40 px-3 py-2 text-[13px] text-white/80 backdrop-blur-md">Preparing the opening line…</div>
          )}
          {(showAll ? history : history.slice(-4)).map((t, i, arr) => (
            <div key={history.length - arr.length + i} className={cn('pointer-events-auto max-w-[92%] rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed break-words shadow-lg backdrop-blur-md sm:max-w-[70%]',
              t.role === 'assistant' ? 'self-start bg-black/45 text-white' : 'self-end bg-brand text-brand-fg')}>
              {t.text}
            </div>
          ))}
          {send.isPending && <div className="self-start rounded-2xl bg-black/40 px-3.5 py-2 text-[13px] text-white/70 backdrop-blur-md">{profile.agent_name || 'Agent'} is thinking…</div>}
          {failed && !send.isPending && (
            <div className="pointer-events-auto flex max-w-[92%] flex-col gap-1.5 self-start rounded-2xl border border-danger/40 bg-danger-soft/90 px-3 py-2 text-[13px] text-danger backdrop-blur-md sm:max-w-[70%]">
              <div className="flex flex-wrap items-center gap-2">
                <AlertTriangle className="size-4 shrink-0" />
                <span className="min-w-0 flex-1 basis-40 break-words">{profile.agent_name || 'The agent'} could not reply — {humanLlmError(failed.detail)}</span>
                <Button size="sm" variant="ghost" onClick={() => submit(failed.message)}><RotateCcw />Retry</Button>
              </div>
              <details className="text-xs opacity-80"><summary className="cursor-pointer">Technical detail</summary><p className="mt-1 break-words font-mono" title={failed.detail}>{failed.detail}</p></details>
            </div>
          )}
          {ended && (
            <div className="pointer-events-auto flex flex-wrap items-center gap-2 self-start rounded-2xl border border-warning/40 bg-warning-soft/90 px-3 py-2 text-[13px] text-warning backdrop-blur-md">
              <AlertTriangle className="size-4 shrink-0" />The agent ended the call.<Button size="sm" variant="ghost" onClick={() => void reset()}><RotateCcw />Start again</Button>
            </div>
          )}
          <div ref={bottom} />
        </div>

        {/* Bottom controls: full width on phones, docked bottom-right from sm up. */}
        <div className="pointer-events-auto absolute inset-x-3 bottom-3 z-20 flex flex-col items-end gap-3 sm:inset-x-auto sm:right-6 sm:bottom-6">
          {listening && (
            <div className="mr-2 animate-pulse rounded-full bg-red-500 px-3 py-1 text-xs font-bold text-white shadow-lg">
              Listening...
            </div>
          )}

          <div className="flex w-full items-center gap-3 sm:w-auto">
            <form onSubmit={(e) => { e.preventDefault(); submit(text) }} className="flex min-w-0 flex-1 items-center rounded-full border border-white/20 bg-white/10 p-1.5 backdrop-blur-md transition-all focus-within:bg-white/20 sm:w-64 sm:flex-none sm:focus-within:w-80">
              <Input value={text} onChange={(e) => setText(e.target.value)} disabled={inputLocked} maxLength={1000} placeholder={listening ? 'Listening...' : ended ? 'Call ended' : waitingForGreeting ? 'Preparing the opening line…' : 'Type reply...'} aria-label="Your reply" className="h-10 min-w-0 flex-1 border-none bg-transparent sm:h-9 text-[13px] text-white shadow-none focus-visible:ring-0 placeholder:text-gray-300" />
              <Button type="submit" variant="primary" size="sm" className="rounded-full px-3" disabled={!text.trim() || inputLocked || send.isPending} loading={send.isPending} aria-label="Send"><SendHorizontal className="size-3.5" /></Button>
            </form>

            <button type="button" onClick={toggleMic} disabled={inputLocked || send.isPending} aria-label={listening ? 'Stop listening' : 'Speak'}
              className={cn('flex size-12 shrink-0 items-center justify-center rounded-full text-white shadow-2xl transition-all active:scale-95 disabled:opacity-50 sm:size-14', listening ? 'bg-red-500 animate-pulse shadow-red-500/50' : 'bg-gray-800 shadow-black/50 hover:bg-gray-700')}>
              {listening ? <MicOff className="size-5" /> : <Mic className="size-5" />}
            </button>
          </div>
        </div>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-brand" />Turn inspector</span>}
            description={inspected ? 'What the agent understood on its latest reply' : undefined}
            action={avgMs !== null && <Badge tone={avgMs < 2500 ? 'success' : avgMs < 4500 ? 'warning' : 'danger'}>avg {(avgMs / 1000).toFixed(1)}s</Badge>} />
          {inspected ? <Inspector turn={inspected} /> : (
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
          <Card className="p-5 text-sm">
            <div className="text-xs font-medium text-muted uppercase">Testing as</div>
            <div className="mt-1 font-medium">{lead.name || lead.phone}</div>
            <div className="text-muted">{[lead.company, lead.city].filter(Boolean).join(' · ') || lead.phone}</div>
            {lead.summary && <p className="mt-2 line-clamp-3 text-fg-2">{lead.summary}</p>}
          </Card>
        )}
      </div>
    </div>
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
      <dl className="grid grid-cols-2 gap-2">
        {([['Intent', titleCase(turn.intent || 'unknown')], ['Temperature', <QualificationBadge key="q" value={turn.qualification} />], ['Sentiment', turn.sentiment ?? '—'], ['Language', turn.language ?? '—']] as [string, ReactNode][]).map(([k, v]) => (
          <div key={k} className="rounded-lg bg-surface-2 p-2.5"><dt className="text-xs text-muted">{k}</dt><dd className="mt-0.5 font-medium capitalize">{v}</dd></div>
        ))}
      </dl>

      <div>
        <div className="mb-1.5 flex justify-between text-xs text-muted"><span>Response time</span><span className="tabular-nums">{(turn.total_ms / 1000).toFixed(2)}s total</span></div>
      </div>

      {turn.end_call && <div className="flex items-center gap-2 rounded-lg bg-warning-soft px-3 py-2 text-xs font-medium text-warning"><AlertTriangle className="size-3.5" />Agent decided to end the call</div>}

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted uppercase">CRM updates</div>
        {crm.length ? (
          <dl className="divide-y divide-border rounded-lg border border-border">
            {crm.map(([k, v]) => <div key={k} className="flex flex-wrap gap-x-3 gap-y-0.5 px-3 py-2 sm:flex-nowrap"><dt className="w-full shrink-0 text-muted capitalize sm:w-24">{k.replace(/_/g, ' ')}</dt><dd className="min-w-0 break-words">{String(v)}</dd></div>)}
          </dl>
        ) : <p className="text-muted">Nothing new to record.</p>}
      </div>

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted uppercase">Knowledge used</div>
        {turn.knowledge?.length ? turn.knowledge.map((k, i) => (
          <div key={i} className="mb-1.5 flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs"><BookOpen className="size-3.5 shrink-0 text-muted" /><span className="truncate font-medium">{k.title}</span></div>
        )) : <p className="text-muted">No documents matched. <Link to={path('/knowledge')} className="text-brand">Add knowledge</Link> so the agent can answer specifics.</p>}
      </div>
    </div>
  )
}
