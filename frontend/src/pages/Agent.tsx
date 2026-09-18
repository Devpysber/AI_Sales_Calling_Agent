import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BookOpen, Bot, Check, CircleDashed, Mic, MicOff, Play, RotateCcw, Save, SendHorizontal,
  Sparkles, Square, Target, UserRound, Volume2,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { QualificationBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, Field, Input, PageHeader, Select, Skeleton, Switch, Tabs, Textarea } from '@/components/ui'
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

  const { data } = useQuery({ queryKey: ['agent'], queryFn: () => api<ProfileResponse>(`${base}/profile`) })
  const knowledge = useQuery({ queryKey: ['knowledge'], queryFn: () => api<KnowledgeResponse>(`${base}/knowledge`) })

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
    onSuccess: (profile) => {
      qc.setQueryData<ProfileResponse>(['agent'], (old) => old && { ...old, profile })
      setDraft(profile)
      window.dispatchEvent(new CustomEvent('agents:changed'))
      toast.success('Agent saved', { description: 'Changes apply to the next call and the playground.' })
    },
    onError: (e) => toast.error('Could not save', { description: e.message }),
  })

  if (!data || !draft) return <><PageHeader title="Agent" /><Skeleton className="h-[560px] rounded-xl" /></>

  const docs = knowledge.data?.stats.documents ?? 0
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
    <div className={cn(dirty && tab !== 'playground' && 'pb-20')}>
      <PageHeader eyebrow={<>{agent?.name} · Build</>} title="Persona & playground"
        description="Shape how this agent introduces itself, what it asks and how it handles pushback, then rehearse a call in the browser with its own voice and knowledge before it dials anyone."
        actions={<Tabs value={tab} onChange={setTab} items={[
          { value: 'playground', label: 'Playground' },
          { value: 'persona', label: <span className="flex items-center gap-1.5">Persona & voice{dirty && <span className="size-1.5 rounded-full bg-warning" />}</span> },
          { value: 'playbook', label: 'Call playbook' },
        ]} />} />

      <AgentSummary profile={draft} saved={data.profile} voices={data.voices} languages={data.languages} docs={docs} checks={checks} onGo={setTab} />

      {tab === 'playground'
        ? <Playground profile={data.profile} unsaved={dirty} onSave={() => save.mutate(draft)} saving={save.isPending} />
        : <ProfileEditor section={tab} draft={draft} setDraft={setDraft} data={data} />}

      {dirty && tab !== 'playground' && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 backdrop-blur-md lg:left-64">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-4 py-3 sm:px-6 lg:px-8">
            <AlertTriangle className="size-4 text-warning" />
            <span className="mr-auto text-sm font-medium">
              {invalid.length ? <span className="text-danger">Unknown placeholder {invalid.map((p) => `{${p}}`).join(', ')} — use {'{name}'}, {'{agent}'} or {'{company}'}</span> : 'You have unsaved changes'}
            </span>
            <Button onClick={() => setDraft(data.profile)}><RotateCcw />Discard</Button>
            <Button variant="primary" disabled={invalid.length > 0} loading={save.isPending} onClick={() => save.mutate(draft)}><Save />Save changes</Button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ============================== Summary ============================== */

function AgentSummary({ profile, saved, voices, languages, docs, checks, onGo }: {
  profile: AgentProfile; saved: AgentProfile; voices: string[]; languages: Record<string, string>; docs: number
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
    ['Knowledge', docs ? `${docs} document${docs > 1 ? 's' : ''}` : <span key="none" className="text-warning">None</span>],
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
        <dl className="flex flex-1 flex-wrap gap-x-8 gap-y-3">
          {facts.map(([k, v]) => (
            <div key={k}><dt className="text-xs text-muted">{k}</dt><dd className="text-sm font-medium">{v}</dd></div>
          ))}
        </dl>
        <button type="button" onClick={() => setOpen(!open)} className="flex items-center gap-3 rounded-lg px-2 py-1 text-left hover:bg-surface-2" aria-expanded={open}>
          <Ring pct={pct} />
          <div><div className="text-sm font-medium">{pct === 100 ? 'Ready to call' : 'Setup'}</div><div className="text-xs text-muted">{done}/{checks.length} complete</div></div>
        </button>
      </div>
      {open && (
        <ul className="grid gap-x-6 gap-y-1 border-t border-border bg-surface-2/50 px-5 py-3 sm:grid-cols-2 lg:grid-cols-3">
          {checks.map((c) => (
            <li key={c.label}>
              {c.label.includes('knowledge') && !c.done
                ? <Link to={path('/knowledge')} className="flex items-center gap-2 py-1 text-sm text-fg-2 hover:text-brand"><CircleDashed className="size-4 text-muted" />{c.label}</Link>
                : <button type="button" onClick={() => onGo(c.tab)} className={cn('flex items-center gap-2 py-1 text-left text-sm', c.done ? 'text-muted' : 'text-fg-2 hover:text-brand')}>
                    {c.done ? <Check className="size-4 text-success" /> : <CircleDashed className="size-4 text-muted" />}{c.label}
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
  useEffect(() => () => audio.current?.pause(), [])

  // What this agent calls the person on the line: patient, guest, student, customer…
  const caller = (draft.customer_noun || 'customer').trim()

  const fill = (t: string) => t.replace(/\{(\w+)\}/g, (m, k: string) => ({ name: 'Rahul', agent: draft.agent_name, company: draft.company_name })[k] ?? m)

  const preview = async (key: string, text: string, language: string) => {
    if (playing === key) { audio.current?.pause(); setPlaying(null); return }
    audio.current?.pause()
    setPlaying(key)
    try {
      const blob = await api<Blob>(`${base}/voice-preview`, { method: 'POST', json: { text: text.slice(0, 600), language, speaker: draft.voice_speaker } })
      const a = new Audio(URL.createObjectURL(blob))
      audio.current = a
      a.onended = () => setPlaying(null)
      await a.play()
    } catch (e) { toast.error('Voice preview failed', { description: (e as Error).message }); setPlaying(null) }
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
            <Field label="Max call length" hint="Plivo hangs up after this"><div className="relative"><Input type="number" min={1} max={30} value={draft.max_call_minutes} onChange={(e) => set('max_call_minutes', Math.min(30, Math.max(1, Number(e.target.value) || 1)))} className="pr-12" /><span className="pointer-events-none absolute top-2 right-3 text-sm text-muted">min</span></div></Field>
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
                    {PLACEHOLDERS.map((p) => <button key={p} type="button" onClick={() => insert(key, p)} className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[11px] text-muted hover:border-brand hover:text-brand">{`{${p}}`}</button>)}
                  </div>
                  <Textarea rows={2} value={draft[key]} maxLength={400} onChange={(e) => set(key, e.target.value)} className={cn(bad.length && 'border-danger focus:border-danger focus:ring-danger/15')} />
                  <div className="flex items-start gap-3 rounded-lg bg-surface-2 px-3 py-2.5">
                    <Button size="icon" variant="ghost" className="-my-1 -ml-1 size-8" onClick={() => preview(key, fill(draft[key]), lang)} disabled={!draft[key].trim()} aria-label={`Play ${label} greeting`}>{playing === key ? <Square /> : <Play />}</Button>
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
          <div><div className="font-medium">Record calls</div><div className="text-sm text-muted">Save recordings and play them from call history. Tell people the call is recorded where the law requires it.</div></div>
          <Switch checked={draft.record_calls} onChange={(v) => set('record_calls', v)} label="Record calls" />
        </Card>
        <Card className="flex items-center justify-between gap-4 p-5">
          <div><div className="font-medium">Voicemail detection</div><div className="text-sm text-muted">Hang up automatically when an answering machine picks up. Can misfire on Indian caller tunes, so keep it off unless you see voicemail calls.</div></div>
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

function Playground({ profile, unsaved, onSave, saving }: { profile: AgentProfile; unsaved: boolean; onSave: () => void; saving: boolean }) {
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
  const [time, setTime] = useState(new Date())

  // Real-time clock update
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  const [selected, setSelected] = useState<number | null>(null)
  const [ended, setEnded] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const mouthTimer = useRef<number>(0)
  // Voice loudness sampled from the playing audio, read by the avatar every frame to move the mouth in time.
  const level = useRef(0)
  const analyser = useRef<{ ctx: AudioContext; raf: number } | null>(null)
  const meter = (a: HTMLAudioElement) => {
    try {
      const ctx = analyser.current?.ctx ?? new AudioContext()
      const node = ctx.createAnalyser(); node.fftSize = 512
      ctx.createMediaElementSource(a).connect(node); node.connect(ctx.destination)
      void ctx.resume()
      const buf = new Uint8Array(node.fftSize)
      if (analyser.current) cancelAnimationFrame(analyser.current.raf)
      const tick = () => {
        node.getByteTimeDomainData(buf)
        let sum = 0
        for (const v of buf) { const d = (v - 128) / 128; sum += d * d }
        level.current = Math.sqrt(sum / buf.length)
        analyser.current!.raf = requestAnimationFrame(tick)
      }
      analyser.current = { ctx, raf: requestAnimationFrame(tick) }
    } catch { level.current = 0 }
  }
  const bottom = useRef<HTMLDivElement>(null)
  const recog = useRef<SpeechRecognitionLike | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const historyRef = useRef(history)
  useEffect(() => { historyRef.current = history }, [history])

  const leads = useQuery({ queryKey: ['leads', 'playground'], queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 100, sort: 'name', order: 'asc' } }) })
  const lead = leads.data?.items.find((l) => l.id === leadId)

  const greeting = useQuery({
    queryKey: ['agent', 'greeting', lang, leadId, direction, profile],
    queryFn: () => api<{ text: string }>(`${base}/greeting`, { params: { language: lang, lead_id: leadId || undefined, purpose: inbound ? 'inbound' : undefined } }),
  })
  useEffect(() => { if (greeting.data && history.length === 0) setHistory([{ role: 'assistant', text: greeting.data.text }]) }, [greeting.data, history.length])
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [history])
  useEffect(() => () => { audio.current?.pause(); recog.current?.stop(); window.clearTimeout(mouthTimer.current); if (analyser.current) { cancelAnimationFrame(analyser.current.raf); void analyser.current.ctx.close() } }, [])

  const play = (url: string) => { 
    audio.current?.pause(); 
    window.clearTimeout(mouthTimer.current)
    audio.current = new Audio(url); 
    audio.current.onplay = () => setIsSpeaking(true);
    audio.current.crossOrigin = 'anonymous'
    meter(audio.current)
    audio.current.onended = () => { setIsSpeaking(false); level.current = 0 };
    audio.current.onerror = () => setIsSpeaking(false);
    audio.current.onpause = () => setIsSpeaking(false);
    void audio.current.play() 
  }

  const send = useMutation({
    mutationFn: ({ message, prior }: { message: string; prior: ChatTurn[] }) => api<AgentTurnResult>(`${base}/playground`, {
      method: 'POST', json: { message, speak, lead_id: leadId || undefined, purpose: inbound ? 'inbound' : undefined, history: prior.map(({ role, text }) => ({ role, text })) },
    }),
    onSuccess: (res) => {
      setHistory((h) => { setSelected(h.length); return [...h, { role: 'assistant', text: res.reply, meta: res }] })
      if (res.audio_url) play(res.audio_url)
      else {
        // No voice (TTS outage): still mouth the reply for roughly as long as it would take to say it.
        setIsSpeaking(true)
        window.clearTimeout(mouthTimer.current)
        mouthTimer.current = window.setTimeout(() => setIsSpeaking(false), Math.min(12000, 600 + res.reply.length * 55))
      }
      if (res.audio_error) toast.warning('Voice unavailable', { description: res.audio_error })
      if (res.end_call) setEnded(true)
    },
    onError: (e) => {
      toast.error('Agent error', { description: e.message })
      setHistory((h) => h.slice(0, -1))
    },
  })

  // Reads history through a ref so voice input (which fires later) never sends a stale conversation.
  const submit = useCallback((message: string) => {
    const m = message.trim()
    if (!m || send.isPending) return
    const prior = historyRef.current
    setHistory([...prior, { role: 'customer', text: m }])
    setText('')
    send.mutate({ message: m, prior })
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
    r.start()
    setListening(true)
  }

  const reset = () => { audio.current?.pause(); window.clearTimeout(mouthTimer.current); setIsSpeaking(false); setHistory([]); setSelected(null); setEnded(false); void greeting.refetch() }

  const turns = history.filter((t) => t.meta)
  const inspected = (selected !== null ? history[selected]?.meta : undefined) ?? turns.at(-1)?.meta
  const avgMs = turns.length ? Math.round(turns.reduce((a, t) => a + t.meta!.total_ms, 0) / turns.length) : null

  return (
    <div className="grid gap-4 grid-cols-1">
      <Card className="relative flex h-[calc(100vh-290px)] min-h-[540px] flex-col overflow-hidden">
        
        {/* 3D Spline Avatar Background */}
        <div className="absolute inset-0 z-0 bg-surface-1" aria-hidden>
          <AgentAvatar zoomOut={true} isSpeaking={isSpeaking || send.isPending} isListening={listening} level={level} />
        </div>

        <div className="relative z-10 flex flex-wrap items-center gap-2 border-b border-border/50 bg-elevated/40 px-4 py-3 backdrop-blur-xl">
          <div className="mr-auto flex min-w-0 items-center gap-2.5">
            {/* The agent's orb: calm while it waits for you, spinning up while it thinks of a reply. */}
            <VoiceOrb state={send.isPending ? 'speaking' : ended ? 'idle' : 'listening'} size={40} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold">{profile.agent_name} · {profile.company_name}</div>
              <div className="truncate text-xs text-muted">{inbound ? 'Rehearsing an inbound call' : 'Rehearsing an outbound call'} · voice {titleCase(profile.voice_speaker)} · nothing is saved to the CRM</div>
            </div>
          </div>
          <Tabs value={direction} onChange={(v) => { setDirection(v); setHistory([]); setSelected(null); setEnded(false) }}
            items={[{ value: 'outbound', label: 'Outbound' }, { value: 'inbound', label: 'Inbound' }]} />
          <Select value={leadId} onChange={(e) => { setLeadId(e.target.value ? Number(e.target.value) : ''); reset() }} className="h-8 w-auto max-w-44 text-[13px]" aria-label="Prospect">
            <option value="">Sample {caller}</option>
            {leads.data?.items.map((l) => <option key={l.id} value={l.id}>{l.name || l.phone}</option>)}
          </Select>
          <Select value={lang} onChange={(e) => { setLang(e.target.value); setHistory([]); setSelected(null); setEnded(false) }} className="h-8 w-auto text-[13px]" aria-label="Greeting language">
            {Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </Select>
          <label className="flex h-8 items-center gap-2 rounded-lg border border-border px-2 text-[13px] text-muted"><Volume2 className="size-3.5" />Voice<Switch checked={speak} onChange={setSpeak} label="Speak replies" /></label>
          <Button size="sm" variant="ghost" onClick={reset}><RotateCcw />Restart</Button>
          {/* IST clock lives in the toolbar so it can wrap with the other controls instead of floating over them. */}
          <div className="flex h-8 items-center gap-2 rounded-full border border-border bg-black/30 px-3 text-[13px] font-medium tabular-nums">
            <div className="size-2 rounded-full bg-green-500 animate-pulse" />
            {time.toLocaleTimeString('en-US', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit' })} IST
          </div>
        </div>

        {unsaved && (
          <div className="relative z-10 flex flex-wrap items-center gap-2 border-b border-warning/30 bg-warning-soft/80 px-4 py-2 text-[13px] text-warning backdrop-blur-md">
            <AlertTriangle className="size-4" /><span className="mr-auto">The playground uses your saved agent. Save your edits to test them.</span>
            <Button size="sm" variant="primary" loading={saving} onClick={onSave}><Save />Save</Button>
          </div>
        )}

        {/* Bottom Right UI Controls (Moved to avoid overlap) */}
        <div className="absolute bottom-6 right-6 z-20 flex flex-col items-end pointer-events-auto gap-3">
          
          {listening && (
            <div className="bg-red-500 text-white text-xs font-bold px-3 py-1 rounded-full animate-pulse shadow-lg mr-2">
              Listening...
            </div>
          )}

          <div className="flex items-center gap-3">
            <form onSubmit={(e) => { e.preventDefault(); submit(text) }} className="flex w-64 items-center rounded-full bg-white/10 p-1.5 backdrop-blur-md border border-white/20 transition-all focus-within:w-80 focus-within:bg-white/20">
              <Input value={text} onChange={(e) => setText(e.target.value)} disabled={ended} maxLength={1000} placeholder={listening ? 'Listening...' : 'Type reply...'} className="h-9 flex-1 bg-transparent border-none text-[13px] text-white shadow-none focus-visible:ring-0 placeholder:text-gray-300" />
              <Button type="submit" variant="primary" size="sm" className="rounded-full px-3" disabled={!text.trim() || ended} loading={send.isPending}><SendHorizontal className="size-3.5" /></Button>
            </form>

            <button type="button" onClick={toggleMic} disabled={ended} aria-label={listening ? 'Stop listening' : 'Speak'}
              className={cn("flex size-14 shrink-0 items-center justify-center rounded-full text-white shadow-2xl transition-all active:scale-95", listening ? 'bg-red-500 animate-pulse shadow-red-500/50' : 'bg-gray-800 shadow-black/50 hover:bg-gray-700')}>
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
            <div className="mt-1 font-medium">{lead.name}</div>
            <div className="text-muted">{[lead.company, lead.city].filter(Boolean).join(' · ') || lead.phone}</div>
            {lead.summary && <p className="mt-2 line-clamp-3 text-fg-2">{lead.summary}</p>}
          </Card>
        )}
      </div>
    </div>
  )
}

function Inspector({ turn }: { turn: AgentTurnResult }) {
  const { path } = useAgent()
  const crm = Object.entries(turn.crm_update).filter(([, v]) => v)
  return (
    <div className="space-y-5 p-5 text-sm">
      <dl className="grid grid-cols-2 gap-2">
        {([['Intent', titleCase(turn.intent)], ['Temperature', <QualificationBadge key="q" value={turn.qualification} />], ['Sentiment', turn.sentiment ?? '—'], ['Language', turn.language ?? '—']] as [string, ReactNode][]).map(([k, v]) => (
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
            {crm.map(([k, v]) => <div key={k} className="flex gap-3 px-3 py-2"><dt className="w-24 shrink-0 text-muted capitalize">{k.replace(/_/g, ' ')}</dt><dd className="min-w-0 break-words">{v}</dd></div>)}
          </dl>
        ) : <p className="text-muted">Nothing new to record.</p>}
      </div>

      <div>
        <div className="mb-1.5 text-xs font-medium text-muted uppercase">Knowledge used</div>
        {turn.knowledge.length ? turn.knowledge.map((k, i) => (
          <div key={i} className="mb-1.5 flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs"><BookOpen className="size-3.5 shrink-0 text-muted" /><span className="truncate font-medium">{k.title}</span></div>
        )) : <p className="text-muted">No documents matched. <Link to={path('/knowledge')} className="text-brand">Add knowledge</Link> so the agent can answer specifics.</p>}
      </div>
    </div>
  )
}
