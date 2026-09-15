import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Clock, Copy, Globe, HeartHandshake, Mail, PhoneForwarded, Play, RefreshCw, RotateCcw, Save, ShieldCheck, Zap } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, Field, Input, PageHeader, Select, Skeleton, Switch, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import type { AutomationSettings } from '@/lib/types'
import { cn, DAYS, timeAgo } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

type Resp = { settings: AutomationSettings; within_calling_hours: boolean; jobs: Record<string, { label: string; at?: string; result?: string }> }
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hourLabel = (h: number) => `${((h + 11) % 12) + 1}:00 ${h < 12 ? 'AM' : 'PM'}`

export default function Automation() {
  const { agent, base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data } = useQuery({ queryKey: ['automation'], queryFn: () => api<Resp>(`${base}/automation`), refetchInterval: 10000 })
  const [form, setForm] = useState<AutomationSettings | null>(null)
  useEffect(() => { if (data && !form) setForm(data.settings) }, [data, form])

  const save = useMutation({
    mutationFn: (s: AutomationSettings) => api<AutomationSettings>(`${base}/automation`, { method: 'PUT', json: s }),
    onSuccess: (s) => { setForm(s); toast.success('Automation saved'); qc.invalidateQueries({ queryKey: ['automation'] }) },
    onError: (e) => toast.error(e.message),
  })
  const run = useMutation({
    mutationFn: (job: string) => api<{ result: string }>(`${base}/automation/run/${job}`, { method: 'POST' }),
    onSuccess: (r) => { toast.success(r.result); qc.invalidateQueries() },
    onError: (e) => toast.error(e.message),
  })

  if (!data || !form) return <><PageHeader title="Automation" /><Skeleton className="h-[600px]" /></>

  const dirty = JSON.stringify(form) !== JSON.stringify(data.settings)
  const set = <K extends keyof AutomationSettings>(k: K, v: AutomationSettings[K]) => setForm({ ...form, [k]: v })
  const toggleNow = (k: 'auto_dial_enabled' | 'retry_enabled' | 'meeting_reminder_enabled' | 'daily_report_enabled' | 'speed_to_lead_enabled' | 'nurture_enabled', v: boolean) => {
    const next = { ...form, [k]: v }
    setForm(next)
    save.mutate(next)
  }
  const runJob = async (job: string, calls: boolean) => {
    if (calls && !(await confirm({ title: 'Run now?', description: 'This places real phone calls immediately, even outside calling hours.', confirmLabel: 'Run now' }))) return
    run.mutate(job)
  }
  const Last = ({ job }: { job: string }) => {
    const j = data.jobs[job]
    return <span className="text-xs text-muted">{j?.at ? <>Last run {timeAgo(j.at)} · {j.result}</> : 'Never run'}</span>
  }

  return (
    <>
      <PageHeader eyebrow={<><CalendarClock className="size-3.5" />{agent?.name} · Autopilot</>} title="Automation"
        description="Let this agent work through its own pipeline on a schedule, inside the hours you allow. Other agents keep their own schedules."
        actions={<>
          {dirty && <Button onClick={() => setForm(data.settings)}><RotateCcw />Discard</Button>}
          <Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(form)}><Save />Save changes</Button>
        </>}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          {([
            ['Auto-dial', form.auto_dial_enabled, form.auto_dial_enabled ? `Every ${form.auto_dial_interval_minutes} min · ${form.max_calls_per_run}/run` : 'Off', 'auto_dial'],
            ['Retries', form.retry_enabled, form.retry_enabled ? `Up to ${form.max_retries} tries · ${form.retry_min_gap_minutes} min apart` : 'Off', 'retry_calls'],
            ['Reminders', form.meeting_reminder_enabled, form.meeting_reminder_enabled ? `Daily at ${hourLabel(form.meeting_reminder_hour)}` : 'Off', 'meeting_reminder'],
            ['Daily report', form.daily_report_enabled, form.daily_report_enabled ? `At ${hourLabel(form.daily_report_hour)}` : 'Off', 'daily_report'],
            ['Speed to lead', form.speed_to_lead_enabled, form.speed_to_lead_enabled ? `Calls ${Math.round(form.speed_to_lead_min_seconds / 60 * 10) / 10}–${Math.round(form.speed_to_lead_max_seconds / 60 * 10) / 10} min after the form` : 'Off', 'callbacks'],
            ['Follow-ups', form.nurture_enabled, form.nurture_enabled ? `After ${form.nurture_after_days} days · ${form.nurture_max_attempts}×` : 'Off', 'nurture'],
          ] as const).map(([label, on, detail, job]) => (
            <div key={label} className="rounded-2xl border border-border bg-surface p-4 shadow-card">
              <div className="flex items-center justify-between"><span className="text-[13px] font-bold">{label}</span><Badge tone={on ? 'success' : 'neutral'} dot={on}>{on ? 'On' : 'Off'}</Badge></div>
              <div className="mt-1.5 truncate text-xs text-muted">{detail}</div>
              <div className="mt-1 truncate text-[11px] text-muted">{data.jobs[job]?.at ? `Last run ${timeAgo(data.jobs[job]!.at!)}` : 'Never run'}</div>
            </div>
          ))}
        </div>
      </PageHeader>

      <div className="grid gap-4 xl:grid-cols-2">
        <JobCard icon={<PhoneForwarded />} title="Auto-dial new leads" description="Calls leads that are New (never called) or queued as Pending."
          enabled={form.auto_dial_enabled} onToggle={(v) => toggleNow('auto_dial_enabled', v)}
          footer={<><Last job="auto_dial" /><Button size="sm" loading={run.isPending && run.variables === 'auto_dial'} onClick={() => runJob('auto_dial', true)}><Play />Run now</Button></>}>
          <Field label="Check every (minutes)"><Input type="number" min={1} value={form.auto_dial_interval_minutes} onChange={(e) => set('auto_dial_interval_minutes', +e.target.value)} /></Field>
          <Field label="Calls per run"><Input type="number" min={1} max={50} value={form.max_calls_per_run} onChange={(e) => set('max_calls_per_run', +e.target.value)} /></Field>
        </JobCard>

        <JobCard icon={<Zap />} title="Speed to lead" description="Calls a website enquiry shortly after the form is sent, while interest is highest."
          enabled={form.speed_to_lead_enabled} onToggle={(v) => toggleNow('speed_to_lead_enabled', v)}
          footer={<span className="text-xs text-muted">A random delay in this range keeps it natural. Outside calling hours the lead waits for auto-dial.</span>}>
          <Field label="Call after at least (seconds)"><Input type="number" min={0} max={3600} value={form.speed_to_lead_min_seconds} onChange={(e) => set('speed_to_lead_min_seconds', +e.target.value)} /></Field>
          <Field label="and at most (seconds)"><Input type="number" min={0} max={3600} value={form.speed_to_lead_max_seconds} onChange={(e) => set('speed_to_lead_max_seconds', +e.target.value)} /></Field>
        </JobCard>

        <JobCard icon={<HeartHandshake />} title="Follow up warm leads" description="Calls Interested and Follow Up leads nobody has spoken to recently, continuing from the last conversation."
          enabled={form.nurture_enabled} onToggle={(v) => toggleNow('nurture_enabled', v)}
          footer={<><Last job="nurture" /><Button size="sm" loading={run.isPending && run.variables === 'nurture'} onClick={() => runJob('nurture', true)}><Play />Run now</Button></>}>
          <Field label="Call again after (days)"><Input type="number" min={1} max={60} value={form.nurture_after_days} onChange={(e) => set('nurture_after_days', +e.target.value)} /></Field>
          <Field label="Max follow-ups per lead"><Input type="number" min={1} max={10} value={form.nurture_max_attempts} onChange={(e) => set('nurture_max_attempts', +e.target.value)} /></Field>
        </JobCard>

        <JobCard icon={<RefreshCw />} title="Retry unanswered calls" description="Re-dials leads whose last call was Busy, No Answer or Failed."
          enabled={form.retry_enabled} onToggle={(v) => toggleNow('retry_enabled', v)}
          footer={<><Last job="retry_calls" /><Button size="sm" loading={run.isPending && run.variables === 'retry_calls'} onClick={() => runJob('retry_calls', true)}><Play />Run now</Button></>}>
          <Field label="Wait between attempts (minutes)"><Input type="number" min={5} value={form.retry_min_gap_minutes} onChange={(e) => set('retry_min_gap_minutes', +e.target.value)} /></Field>
          <Field label="Max attempts"><Input type="number" min={1} max={10} value={form.max_retries} onChange={(e) => set('max_retries', +e.target.value)} /></Field>
        </JobCard>

        <Card className="xl:col-span-2">
          <div className="flex flex-wrap items-start gap-4 p-5">
            <span className="grid size-10 place-items-center rounded-xl bg-success-soft text-success"><ShieldCheck className="size-5" /></span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">Calling window & limits</h3>
                {data.within_calling_hours ? <Badge tone="success" dot>Calling allowed now</Badge> : <Badge tone="warning" dot>Outside calling hours</Badge>}</div>
              <p className="text-sm text-muted">Automated calls only go out inside this window (IST). TRAI permits promotional calls 9 AM – 9 PM.</p>
              <div className="mt-4 grid gap-4 sm:grid-cols-3">
                <Field label="From"><Select value={form.calling_hours_start} onChange={(e) => set('calling_hours_start', +e.target.value)}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
                <Field label="Until"><Select value={form.calling_hours_end} onChange={(e) => set('calling_hours_end', +e.target.value)}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
                <Field label="Max simultaneous calls" hint="Across all triggers"><Input type="number" min={1} max={100} value={form.max_concurrent_calls} onChange={(e) => set('max_concurrent_calls', +e.target.value)} /></Field>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {DAYS.map((d, i) => {
                  const on = form.calling_days.includes(i)
                  return <button key={d} type="button" onClick={() => set('calling_days', on ? form.calling_days.filter((x) => x !== i) : [...form.calling_days, i].sort())}
                    className={cn('h-9 w-14 rounded-xl border text-sm font-bold transition', on ? 'border-fg bg-fg text-bg' : 'border-border text-muted hover:border-border-strong')}>{d}</button>
                })}
              </div>
            </div>
          </div>
        </Card>

        <JobCard icon={<CalendarClock />} title="Meeting reminders" description="Emails leads the day before a booked meeting."
          enabled={form.meeting_reminder_enabled} onToggle={(v) => toggleNow('meeting_reminder_enabled', v)}
          footer={<><Last job="meeting_reminder" /><Button size="sm" onClick={() => runJob('meeting_reminder', false)}><Play />Run now</Button></>}>
          <Field label="Send at"><Select value={form.meeting_reminder_hour} onChange={(e) => set('meeting_reminder_hour', +e.target.value)}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
        </JobCard>

        <JobCard icon={<Mail />} title="Daily report" description="Emails calls, connects, meetings and pipeline every day."
          enabled={form.daily_report_enabled} onToggle={(v) => toggleNow('daily_report_enabled', v)}
          footer={<><Last job="daily_report" /><Button size="sm" onClick={() => runJob('daily_report', false)}><Play />Send now</Button></>}>
          <Field label="Send at"><Select value={form.daily_report_hour} onChange={(e) => set('daily_report_hour', +e.target.value)}>{HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}</Select></Field>
          <Field label="Recipient" hint="Empty = your Admin profile email"><Input type="email" value={form.daily_report_email} onChange={(e) => set('daily_report_email', e.target.value)} placeholder="Your admin email" /></Field>
        </JobCard>

        <WebsiteIntake />
      </div>
      <p className="mt-4 flex items-center gap-1.5 text-xs text-muted"><Clock className="size-3.5" />The scheduler checks every 20 seconds. Toggles save instantly; field edits need Save.</p>
    </>
  )
}

function JobCard({ icon, title, description, enabled, onToggle, children, footer }: {
  icon: ReactNode; title: string; description: string; enabled: boolean; onToggle: (v: boolean) => void; children: ReactNode; footer: ReactNode
}) {
  return (
    <Card className={cn('flex flex-col transition', enabled && 'ring-1 ring-fg/40')}>
      <div className="flex items-start gap-4 p-5">
        <span className={cn('grid size-10 shrink-0 place-items-center rounded-xl [&_svg]:size-5', enabled ? 'bg-brand text-brand-fg' : 'bg-surface-2 text-muted')}>{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2"><h3 className="font-bold">{title}</h3>{enabled && <Badge tone="success" pulse>On</Badge>}</div>
          <p className="text-sm text-muted">{description}</p>
        </div>
        <Switch checked={enabled} onChange={onToggle} label={title} />
      </div>
      <div className="grid flex-1 gap-4 px-5 pb-4 sm:grid-cols-2">{children}</div>
      <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">{footer}</div>
    </Card>
  )
}

type Intake = { url: string; token: string; speed_to_lead: boolean }

function WebsiteIntake() {
  const { base, agent } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data } = useQuery({ queryKey: ['intake'], queryFn: () => api<Intake>(`${base}/intake`) })
  const [tab, setTab] = useState<'html' | 'js' | 'curl'>('html')
  const rotate = useMutation({
    mutationFn: () => api<Intake>(`${base}/intake/rotate`, { method: 'POST' }),
    onSuccess: (d) => { qc.setQueryData(['intake'], d); toast.success('New form link created', { description: 'Update it on your website.' }) },
  })
  if (!data) return <Skeleton className="h-64 xl:col-span-2" />
  const snippets = {
    html: `<form action="${data.url}" method="POST">\n  <input name="name" placeholder="Your name" required>\n  <input name="phone" placeholder="Phone" required>\n  <input name="email" placeholder="Email">\n  <textarea name="message" placeholder="How can we help?"></textarea>\n  <input name="website" style="display:none" tabindex="-1" autocomplete="off">\n  <button>Request a call</button>\n</form>`,
    js: `await fetch("${data.url}", {\n  method: "POST",\n  headers: { "Content-Type": "application/json" },\n  body: JSON.stringify({ name, phone, email, message, source: "landing-page" }),\n})`,
    curl: `curl -X POST "${data.url}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"name":"Rahul","phone":"9876543210","message":"Interested"}'`,
  }
  const copy = (t: string) => { void navigator.clipboard.writeText(t); toast.success('Copied') }
  return (
    <Card className="xl:col-span-2">
      <div className="flex flex-wrap items-start gap-4 p-5">
        <span className="grid size-10 place-items-center rounded-xl bg-brand-soft text-brand"><Globe className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-bold">Website form → {agent?.name}</h3>
            <Badge tone={data.speed_to_lead ? 'success' : 'neutral'} dot>{data.speed_to_lead ? 'Calls 1–2 min after the form' : 'Queued for auto-dial'}</Badge></div>
          <p className="text-sm text-muted">Send enquiries from any website, landing page, WordPress/Webflow form, Zapier or your backend straight into this agent's leads. Use a separate agent per website to keep each site's leads, script and reports apart.</p>
          <div className="mt-3 flex min-w-0 items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-2">
            <code className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{data.url}</code>
            <Button size="icon" variant="ghost" onClick={() => copy(data.url)} aria-label="Copy form link"><Copy /></Button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {(['html', 'js', 'curl'] as const).map((t) => (
              <button key={t} type="button" onClick={() => setTab(t)} className={cn('rounded-lg px-2.5 py-1 text-xs font-semibold', tab === t ? 'bg-fg text-bg' : 'text-muted hover:bg-surface-2')}>
                {{ html: 'HTML form', js: 'JavaScript', curl: 'cURL / Zapier' }[t]}</button>
            ))}
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => copy(snippets[tab])}><Copy />Copy snippet</Button>
          </div>
          <pre className="mt-2 max-h-56 overflow-auto rounded-xl bg-ink p-3 font-mono text-[12px] leading-relaxed text-ink-fg">{snippets[tab]}</pre>
          <p className="mt-2 text-xs text-muted">Fields: name, phone (required), email, company, city, message, source, language (e.g. hi-IN). Repeat enquiries update the same lead. Bots filling the hidden “website” field are ignored.</p>
        </div>
        <Button size="sm" variant="ghost" loading={rotate.isPending} onClick={async () => {
          if (await confirm({ title: 'Create a new form link?', description: 'Forms using the current link stop working until you update them.', confirmLabel: 'Regenerate', danger: true })) rotate.mutate()
        }}><RefreshCw />New link</Button>
      </div>
    </Card>
  )
}
