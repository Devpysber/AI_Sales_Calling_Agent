import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CalendarClock, Copy, Globe, HeartHandshake, Mail, PhoneForwarded, Play, RefreshCw, RotateCcw, Save, ShieldCheck, Zap } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, EmptyState, Field, Input, PageHeader, Select, Skeleton, Switch, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { Stagger } from '@/lib/motion'
import type { AutomationSettings } from '@/lib/types'
import { cn, DAYS, timeAgo } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

type Resp = { settings: AutomationSettings; within_calling_hours: boolean; jobs: Record<string, { label: string; at?: string; result?: string }> }
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hourLabel = (h: number) => `${((h + 11) % 12) + 1}:00 ${h < 12 ? 'AM' : 'PM'}`
/** The form draft: number fields may sit empty while the user is mid-edit (Backspace must be able to clear them). */
type Draft = { [K in keyof AutomationSettings]: AutomationSettings[K] extends number ? number | '' : AutomationSettings[K] }
type NumKey = { [K in keyof AutomationSettings]: AutomationSettings[K] extends number ? K : never }[keyof AutomationSettings]
/** Number inputs: keep '' while empty, otherwise a finite number; garbage keeps the previous value. */
const num = (v: string, fallback: number | ''): number | '' => { if (v.trim() === '') return ''; const n = Number(v); return Number.isFinite(n) ? n : fallback }
const minutes = (seconds: number | '') => (seconds === '' ? '?' : Math.round(seconds / 60 * 10) / 10)
const fmt = (v: number | '') => (v === '' ? '?' : String(v))

/** Integer ranges the scheduler expects; mirrors the min/max on the inputs, which browsers do not enforce while typing. */
const LIMITS: Record<NumKey, [label: string, min: number, max: number]> = {
  auto_dial_interval_minutes: ['Check every (minutes)', 1, 1440],
  max_calls_per_run: ['Calls per run', 1, 50],
  retry_interval_minutes: ['Check every (minutes)', 1, 1440],
  retry_min_gap_minutes: ['Wait between attempts', 5, 1440],
  max_retries: ['Max attempts', 1, 10],
  calling_hours_start: ['Calling window start', 0, 23],
  calling_hours_end: ['Calling window end', 0, 23],
  max_concurrent_calls: ['Max simultaneous calls', 1, 100],
  meeting_reminder_hour: ['Reminder hour', 0, 23],
  daily_report_hour: ['Report hour', 0, 23],
  speed_to_lead_min_seconds: ['Speed to lead "at least"', 0, 14400],
  speed_to_lead_max_seconds: ['Speed to lead "at most"', 0, 14400],
  nurture_after_days: ['Call again after (days)', 1, 60],
  nurture_max_attempts: ['Max follow-ups per lead', 1, 10],
}

/** Things Save should refuse rather than send to the scheduler. */
function validate(f: Draft): string | null {
  for (const k of Object.keys(LIMITS) as NumKey[]) {
    const [label, min, max] = LIMITS[k]
    const v = f[k]
    if (v === '' || !Number.isInteger(v)) return `${label} must be a whole number`
    if (v < min || v > max) return `${label} must be between ${min} and ${max}`
  }
  if (Number(f.calling_hours_start) >= Number(f.calling_hours_end)) return 'Calling window must end after it starts'
  if (!f.calling_days?.length) return 'Pick at least one calling day'
  if (Number(f.speed_to_lead_min_seconds) > Number(f.speed_to_lead_max_seconds)) return 'Speed to lead: "at least" must not exceed "at most"'
  if (f.daily_report_email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.daily_report_email)) return 'Daily report recipient is not a valid email'
  return null
}
/** Only call after validate() returned null: every number field is then a real number. */
const finalize = (f: Draft) => f as AutomationSettings

export default function Automation() {
  const { agent, base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data, isError, error, refetch, isFetching } = useQuery({ queryKey: ['automation'], queryFn: () => api<Resp>(`${base}/automation`), refetchInterval: 10000 })
  const [form, setForm] = useState<Draft | null>(null)
  // The settings the form was last synced from. While the user has no unsaved edits, a background
  // refetch (another tab, a toggle elsewhere) flows into the form; once they edit, their draft wins.
  const synced = useRef<string | null>(null)
  useEffect(() => {
    if (!data) return
    const incoming = JSON.stringify(data.settings)
    if (incoming === synced.current) return
    // Capture before the updater runs: React may defer it to render time, after synced.current moves on.
    const prevSynced = synced.current
    synced.current = incoming
    setForm((prev) => (!prev || JSON.stringify(prev) === prevSynced ? data.settings : prev))
  }, [data])

  const save = useMutation({
    mutationFn: (s: AutomationSettings) => api<AutomationSettings>(`${base}/automation`, { method: 'PUT', json: s }),
    // Per-call callbacks decide what the form becomes (full save replaces it; a toggle keeps the draft).
    onSuccess: (s) => {
      synced.current = JSON.stringify(s); toast.success('Automation saved')
      qc.invalidateQueries({ queryKey: ['automation'] }); qc.invalidateQueries({ queryKey: ['intake'] })
      // The sidebar's 'Automation On/Off' badge lives on the root QueryClient; this event is how pages reach it.
      window.dispatchEvent(new CustomEvent('agents:changed'))
    },
    onError: (e) => toast.error(e.message),
  })
  const run = useMutation({
    mutationFn: (job: string) => api<{ result: string }>(`${base}/automation/run/${job}`, { method: 'POST' }),
    onSuccess: (r) => { toast.success(r.result); qc.invalidateQueries() },
    onError: (e) => toast.error(e.message),
  })

  if (isError && !data) {
    return <><PageHeader title="Automation" />
      <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load automation" description={error?.message}
        action={<Button loading={isFetching} onClick={() => refetch()}><RefreshCw />Try again</Button>} /></Card></>
  }
  if (!data || !form) return <><PageHeader title="Automation" /><div className="grid gap-4 xl:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-56" />)}</div></>

  const dirty = JSON.stringify(form) !== JSON.stringify(data.settings)
  const problem = dirty ? validate(form) : null
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))
  const toggleNow = (k: 'auto_dial_enabled' | 'retry_enabled' | 'meeting_reminder_enabled' | 'daily_report_enabled' | 'speed_to_lead_enabled' | 'nurture_enabled', v: boolean) => {
    if (save.isPending) return
    // Persist only the switch on top of the saved settings; the (possibly invalid) draft stays local until Save.
    setForm((f) => (f ? { ...f, [k]: v } : f))
    save.mutate({ ...data.settings, [k]: v }, {
      onSuccess: (s) => setForm((f) => (f ? { ...f, [k]: s[k] } : s)),
      onError: () => setForm((f) => (f ? { ...f, [k]: !v } : f)),
    })
  }
  const saveAll = () => {
    const p = validate(form)
    if (p) { toast.error(p); return }
    save.mutate(finalize(form), { onSuccess: (s) => setForm(s) })
  }
  const runJob = async (job: string, calls: boolean) => {
    if (run.isPending) return
    if (calls && !(await confirm({ title: 'Run now?', description: 'This places real phone calls immediately, even outside calling hours.', confirmLabel: 'Run now' }))) return
    run.mutate(job)
  }
  const last = (job: string) => {
    const j = data.jobs?.[job]
    return <span className="min-w-0 break-words text-xs text-muted">{j?.at ? <>Last run {timeAgo(j.at)}{j.result ? <> · {j.result}</> : null}</> : 'Never run'}</span>
  }
  const runBtn = (job: string, calls: boolean, label = 'Run now') => (
    <Button size="sm" loading={run.isPending && run.variables === job} disabled={run.isPending} onClick={() => runJob(job, calls)}><Play />{label}</Button>
  )

  return (
    <>
      <PageHeader eyebrow={<><CalendarClock className="size-3.5" />{agent?.name} · Autopilot</>} title="Automation"
        description="Let this agent work through its own pipeline on a schedule, inside the hours you allow. Other agents keep their own schedules."
        actions={<>
          {dirty && <Button disabled={save.isPending} onClick={() => setForm(data.settings)}><RotateCcw />Discard</Button>}
          <Button variant="primary" disabled={!dirty || !!problem} loading={save.isPending} title={problem ?? undefined} onClick={saveAll}><Save />Save changes</Button>
        </>}>
        {problem && <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-warning"><AlertTriangle className="size-3.5 shrink-0" />{problem}</p>}
        <Stagger className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 md:grid-cols-3 xl:grid-cols-6" step={60}>
          {([
            ['Auto-dial', form.auto_dial_enabled, form.auto_dial_enabled ? `Every ${fmt(form.auto_dial_interval_minutes)} min · ${fmt(form.max_calls_per_run)}/run` : 'Off', 'auto_dial'],
            ['Retries', form.retry_enabled, form.retry_enabled ? `Up to ${fmt(form.max_retries)} tries · ${fmt(form.retry_min_gap_minutes)} min apart` : 'Off', 'retry_calls'],
            ['Reminders', form.meeting_reminder_enabled, form.meeting_reminder_enabled ? `Daily at ${hourLabel(Number(form.meeting_reminder_hour))}` : 'Off', 'meeting_reminder'],
            ['Daily report', form.daily_report_enabled, form.daily_report_enabled ? `At ${hourLabel(Number(form.daily_report_hour))}` : 'Off', 'daily_report'],
            ['Speed to lead', form.speed_to_lead_enabled, form.speed_to_lead_enabled ? `Calls ${minutes(form.speed_to_lead_min_seconds)}–${minutes(form.speed_to_lead_max_seconds)} min after the form` : 'Off', 'callbacks'],
            ['Follow-ups', form.nurture_enabled, form.nurture_enabled ? `After ${fmt(form.nurture_after_days)} days · ${fmt(form.nurture_max_attempts)}×` : 'Off', 'nurture'],
          ] as const).map(([label, on, detail, job]) => (
            <div key={label} className={cn('glint relative overflow-hidden rounded-2xl border bg-surface p-4 shadow-card transition duration-300 hover:-translate-y-0.5 hover:shadow-pop', on ? 'border-success/30' : 'border-border')}>
              {/* A running job has a light travelling along its top edge; an idle one is still. */}
              {on && <span className="job-track absolute inset-x-0 top-0 h-0.5" />}
              <div className="flex items-center justify-between gap-2"><span className="min-w-0 truncate text-[13px] font-bold">{label}</span><Badge tone={on ? 'success' : 'neutral'} dot={on} pulse={on}>{on ? 'On' : 'Off'}</Badge></div>
              <div className="mt-1.5 truncate text-xs text-muted">{detail}</div>
              <div className="mt-1 truncate text-[11px] text-muted">{data.jobs?.[job]?.at ? `Last run ${timeAgo(data.jobs[job].at)}` : 'Never run'}</div>
            </div>
          ))}
        </Stagger>
      </PageHeader>

      <div className="grid gap-4 xl:grid-cols-2">
        <JobCard icon={<PhoneForwarded />} title="Auto-dial new leads" description="Calls leads that are New (never called) or queued as Pending."
          enabled={form.auto_dial_enabled} onToggle={(v) => toggleNow('auto_dial_enabled', v)}
          busy={save.isPending} footer={<>{last('auto_dial')}{runBtn('auto_dial', true)}</>}>
          <Field label="Check every (minutes)"><Input type="number" min={1} value={form.auto_dial_interval_minutes} onChange={(e) => set('auto_dial_interval_minutes', num(e.target.value, form.auto_dial_interval_minutes))} /></Field>
          <Field label="Calls per run"><Input type="number" min={1} max={50} value={form.max_calls_per_run} onChange={(e) => set('max_calls_per_run', num(e.target.value, form.max_calls_per_run))} /></Field>
        </JobCard>

        <JobCard icon={<Zap />} title="Speed to lead" description="Calls a website enquiry shortly after the form is sent, while interest is highest."
          enabled={form.speed_to_lead_enabled} onToggle={(v) => toggleNow('speed_to_lead_enabled', v)}
          busy={save.isPending} footer={<span className="min-w-0 break-words text-xs text-muted">A random delay in this range keeps it natural. Outside calling hours the lead waits for auto-dial.</span>}>
          <Field label="Call after at least (seconds, 3600 = 1 hour)"><Input type="number" min={0} max={14400} value={form.speed_to_lead_min_seconds} onChange={(e) => set('speed_to_lead_min_seconds', num(e.target.value, form.speed_to_lead_min_seconds))} /></Field>
          <Field label="and at most (seconds, 7200 = 2 hours)"><Input type="number" min={form.speed_to_lead_min_seconds === '' ? 0 : form.speed_to_lead_min_seconds} max={14400} value={form.speed_to_lead_max_seconds} onChange={(e) => set('speed_to_lead_max_seconds', num(e.target.value, form.speed_to_lead_max_seconds))} /></Field>
        </JobCard>

        <JobCard icon={<HeartHandshake />} title="Follow up warm leads" description="Calls Interested and Follow Up leads nobody has spoken to recently, continuing from the last conversation."
          enabled={form.nurture_enabled} onToggle={(v) => toggleNow('nurture_enabled', v)}
          busy={save.isPending} footer={<>{last('nurture')}{runBtn('nurture', true)}</>}>
          <Field label="Call again after (days)"><Input type="number" min={1} max={60} value={form.nurture_after_days} onChange={(e) => set('nurture_after_days', num(e.target.value, form.nurture_after_days))} /></Field>
          <Field label="Max follow-ups per lead"><Input type="number" min={1} max={10} value={form.nurture_max_attempts} onChange={(e) => set('nurture_max_attempts', num(e.target.value, form.nurture_max_attempts))} /></Field>
        </JobCard>

        <JobCard icon={<RefreshCw />} title="Retry unanswered calls" description="Re-dials leads whose last call was Busy, No Answer or Failed."
          enabled={form.retry_enabled} onToggle={(v) => toggleNow('retry_enabled', v)}
          busy={save.isPending} footer={<>{last('retry_calls')}{runBtn('retry_calls', true)}</>}>
          <Field label="Check every (minutes)"><Input type="number" min={1} max={1440} value={form.retry_interval_minutes} onChange={(e) => set('retry_interval_minutes', num(e.target.value, form.retry_interval_minutes))} /></Field>
          <Field label="Wait between attempts (minutes)"><Input type="number" min={5} value={form.retry_min_gap_minutes} onChange={(e) => set('retry_min_gap_minutes', num(e.target.value, form.retry_min_gap_minutes))} /></Field>
          <Field label="Max attempts"><Input type="number" min={1} max={10} value={form.max_retries} onChange={(e) => set('max_retries', num(e.target.value, form.max_retries))} /></Field>
        </JobCard>

        <Card className="xl:col-span-2">
          <div className="flex flex-col items-start gap-4 p-4 sm:flex-row sm:p-5">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-success-soft text-success"><ShieldCheck className="size-5" /></span>
            <fieldset disabled={save.isPending} className="min-w-0 w-full flex-1">
              <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">Calling window & limits</h3>
                {data.within_calling_hours ? <Badge tone="success" dot>Calling allowed now</Badge> : <Badge tone="warning" dot>Outside calling hours</Badge>}</div>
              <p className="text-sm break-words text-muted">Automated calls only go out inside this window (IST). TRAI permits promotional calls 9 AM – 9 PM.</p>
              <div className="mt-4 grid gap-4 sm:grid-cols-3">
                <Field label="From"><Select value={form.calling_hours_start} onChange={(e) => set('calling_hours_start', Number(e.target.value))}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
                <Field label="Until"><Select value={form.calling_hours_end} onChange={(e) => set('calling_hours_end', Number(e.target.value))}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
                <Field label="Max simultaneous calls" hint="Across all triggers"><Input type="number" min={1} max={100} value={form.max_concurrent_calls} onChange={(e) => set('max_concurrent_calls', num(e.target.value, form.max_concurrent_calls))} /></Field>
              </div>
              <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Calling days">
                {DAYS.map((d, i) => {
                  const days = form.calling_days ?? []
                  const on = days.includes(i)
                  return <button key={d} type="button" aria-pressed={on}
                    onClick={() => set('calling_days', on ? days.filter((x) => x !== i) : [...days, i].sort((a, b) => a - b))}
                    className={cn('h-10 min-w-12 rounded-xl border px-2 text-sm font-bold transition sm:h-9 sm:min-w-10 sm:max-w-14 sm:flex-1',
                      on ? 'border-fg bg-fg text-bg' : 'border-border text-muted hover:border-border-strong')}>{d}</button>
                })}
              </div>
            </fieldset>
          </div>
        </Card>

        <JobCard icon={<CalendarClock />} title="Meeting reminders" description="Emails leads the day before a booked meeting."
          enabled={form.meeting_reminder_enabled} onToggle={(v) => toggleNow('meeting_reminder_enabled', v)}
          busy={save.isPending} footer={<>{last('meeting_reminder')}{runBtn('meeting_reminder', false)}</>}>
          <Field label="Send at"><Select value={form.meeting_reminder_hour} onChange={(e) => set('meeting_reminder_hour', Number(e.target.value))}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
        </JobCard>

        <JobCard icon={<Mail />} title="Daily report" description="Emails calls, connects, meetings and pipeline every day."
          enabled={form.daily_report_enabled} onToggle={(v) => toggleNow('daily_report_enabled', v)}
          busy={save.isPending} footer={<>{last('daily_report')}{runBtn('daily_report', false, 'Send now')}</>}>
          <Field label="Send at"><Select value={form.daily_report_hour} onChange={(e) => set('daily_report_hour', Number(e.target.value))}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
          <Field label="Recipient" hint="Empty = your Admin profile email"><Input type="email" value={form.daily_report_email} onChange={(e) => set('daily_report_email', e.target.value)} placeholder="Your admin email" /></Field>
        </JobCard>

        <WebsiteIntake />
      </div>
      <p className="mt-4 flex items-start gap-1.5 text-xs break-words text-muted"><span className="mt-0.5 shrink-0"><SchedulerPulse /></span>The scheduler checks every 20 seconds. Toggles save instantly; field edits need Save.</p>
    </>
  )
}

function JobCard({ icon, title, description, enabled, onToggle, children, footer, busy }: {
  icon: ReactNode; title: string; description: string; enabled: boolean; onToggle: (v: boolean) => void; children: ReactNode; footer: ReactNode
  /** A save is in flight: fields and the switch wait for it. */
  busy?: boolean
}) {
  return (
    <Card className={cn('flex min-w-0 flex-col transition duration-300', enabled && 'beam beam-on ring-1 ring-fg/40')}>
      <div className="flex items-start gap-3 p-4 sm:gap-4 sm:p-5">
        <span className="relative grid size-10 shrink-0 place-items-center">
          {enabled && <span className="job-spinner absolute -inset-1 rounded-[14px]" aria-hidden />}
          <span className={cn('relative grid size-10 place-items-center rounded-xl transition-colors duration-300 [&_svg]:size-5', enabled ? 'bg-brand text-brand-fg' : 'bg-surface-2 text-muted')}>{icon}</span>
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-bold break-words">{title}</h3>{enabled && <Badge tone="success" pulse>On</Badge>}</div>
          <p className="text-sm break-words text-muted">{description}</p>
        </div>
        <Switch checked={enabled} onChange={onToggle} label={title} disabled={busy} />
      </div>
      <fieldset disabled={busy} className="grid min-w-0 flex-1 gap-4 px-4 pb-4 sm:grid-cols-2 sm:px-5">{children}</fieldset>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-border px-4 py-3 sm:px-5">{footer}</div>
    </Card>
  )
}

type Intake = { url: string; token: string; speed_to_lead: boolean; auto_dial: boolean
  speed_to_lead_min_seconds: number; speed_to_lead_max_seconds: number }

/** What actually happens to a form enquiry, given which automations are switched on. */
function intakeFate(d: Intake): { tone: 'success' | 'warning' | 'neutral'; label: string } {
  if (d.speed_to_lead) {
    return { tone: 'success', label: `Calls ${minutes(d.speed_to_lead_min_seconds)}–${minutes(d.speed_to_lead_max_seconds)} min after the form` }
  }
  if (d.auto_dial) return { tone: 'success', label: 'Queued for auto-dial' }
  return { tone: 'warning', label: 'Saved only — switch on an automation to call' }
}

function WebsiteIntake() {
  const { base, agent } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data, isError, error, refetch, isFetching } = useQuery({ queryKey: ['intake'], queryFn: () => api<Intake>(`${base}/intake`) })
  const [tab, setTab] = useState<'html' | 'js' | 'curl'>('html')
  const rotate = useMutation({
    mutationFn: () => api<Intake>(`${base}/intake/rotate`, { method: 'POST' }),
    onSuccess: (d) => { qc.setQueryData(['intake'], d); qc.invalidateQueries({ queryKey: ['intake'] }); toast.success('New form link created', { description: 'Update it on your website.' }) },
    onError: (e) => toast.error(e.message),
  })
  if (isError && !data) {
    return <Card className="xl:col-span-2"><EmptyState icon={<Globe />} title="Couldn't load the website form link" description={error?.message}
      action={<Button loading={isFetching} onClick={() => refetch()}><RefreshCw />Try again</Button>} /></Card>
  }
  if (!data) return <Skeleton className="h-64 xl:col-span-2" />
  const snippets = {
    html: `<form accept-charset="UTF-8" action="${data.url}" method="POST">\n  <input name="name" placeholder="Your name" required>\n  <input name="phone" placeholder="Phone" required>\n  <input name="email" placeholder="Email">\n  <textarea name="message" placeholder="How can we help?"></textarea>\n  <input name="website" style="display:none" tabindex="-1" autocomplete="off">\n  <button>Request a call</button>\n</form>`,
    js: `await fetch("${data.url}", {\n  method: "POST",\n  headers: { "Content-Type": "application/json" },\n  body: JSON.stringify({ name, phone, email, message, source: "landing-page" }),\n})`,
    curl: `curl -X POST "${data.url}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"name":"Rahul","phone":"9876543210","message":"Interested"}'`,
  }
  const copy = async (t: string) => {
    try { await navigator.clipboard.writeText(t); toast.success('Copied') }
    catch { toast.error('Copy failed', { description: 'Select the text and copy it manually.' }) }
  }
  const fate = intakeFate(data)
  return (
    <Card className="xl:col-span-2">
      <div className="flex flex-col items-start gap-4 p-4 sm:flex-row sm:p-5">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><Globe className="size-5" /></span>
        <div className="min-w-0 flex-1 w-full">
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-bold break-words">Website form → {agent?.name}</h3>
            <Badge tone={fate.tone} dot className="max-w-full whitespace-normal">{fate.label}</Badge></div>
          <p className="mt-1 text-sm break-words text-muted">Send enquiries from any website, landing page, WordPress/Webflow form, Zapier or your backend straight into this agent's leads. Use a separate agent per website to keep each site's leads, script and reports apart.</p>
          <div className="mt-3 flex min-w-0 items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-2">
            <code className="min-w-0 flex-1 truncate font-mono text-[12.5px]" title={data.url}>{data.url}</code>
            <Button size="icon" variant="ghost" onClick={() => copy(data.url)} aria-label="Copy form link"><Copy /></Button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {(['html', 'js', 'curl'] as const).map((t) => (
              <button key={t} type="button" aria-pressed={tab === t} onClick={() => setTab(t)} className={cn('h-10 rounded-lg px-2.5 text-xs font-semibold transition sm:h-7', tab === t ? 'bg-fg text-bg' : 'text-muted hover:bg-surface-2')}>
                {{ html: 'HTML form', js: 'JavaScript', curl: 'cURL / Zapier' }[t]}</button>
            ))}
            <Button size="sm" variant="ghost" className="sm:ml-auto" onClick={() => copy(snippets[tab])}><Copy />Copy snippet</Button>
          </div>
          <pre className="mt-2 max-h-56 min-w-0 overflow-auto rounded-xl bg-ink p-3 font-mono text-[12px] leading-relaxed break-all whitespace-pre-wrap text-ink-fg">{snippets[tab]}</pre>
          <p className="mt-2 text-xs break-words text-muted">Fields: name, phone (required), email, company, city, message, source, language (e.g. hi-IN). Repeat enquiries update the same lead. Bots filling the hidden “website” field are ignored.</p>
        </div>
        <Button size="sm" variant="ghost" className="w-full sm:w-auto" loading={rotate.isPending} onClick={async () => {
          if (rotate.isPending) return
          if (await confirm({ title: 'Create a new form link?', description: 'Forms using the current link stop working until you update them.', confirmLabel: 'Regenerate', danger: true })) rotate.mutate()
        }}><RefreshCw />New link</Button>
      </div>
    </Card>
  )
}

/** A ring that fills once every 20 s: the cadence the scheduler runs at, drawn rather than stated. */
function SchedulerPulse() {
  return (
    <svg viewBox="0 0 16 16" className="size-3.5 -rotate-90" aria-hidden>
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--border-strong)" strokeWidth="2" />
      <circle cx="8" cy="8" r="6" fill="none" stroke="var(--success)" strokeWidth="2" strokeLinecap="round"
        className="scheduler-ring" pathLength={100} strokeDasharray="100" />
    </svg>
  )
}
