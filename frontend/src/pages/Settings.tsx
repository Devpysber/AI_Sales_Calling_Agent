import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleAlert, Copy, PlugZap, RefreshCw, Server } from 'lucide-react'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { toast } from 'sonner'
import InboundSetup from '@/components/InboundSetup'
import SecretsForm from '@/components/SecretsForm'
import TeamMembers from '@/components/TeamMembers'
import { Badge, Button, Card, CardHeader, PageHeader, Skeleton } from '@/components/ui'
import { api } from '@/lib/api'
import { AnimatedNumber, Stagger } from '@/lib/motion'
import { cn } from '@/lib/utils'

type Check = { ok: boolean; detail?: string | null; [k: string]: unknown }
type Status = {
  plivo: Check; openrouter: Check; sarvam: Check; public_url: Check; email: Check
  llm_providers: string[]; signature_validation: boolean
  infrastructure: Record<string, string | boolean>
}

function Row({ ok, title, children, okLabel = 'Connected', checking, className, style }: {
  ok: boolean; title: string; children: ReactNode; okLabel?: string; checking?: boolean; className?: string; style?: CSSProperties
}) {
  return (
    // While a re-check runs, light sweeps across each row: the page is working, not frozen.
    <div className={cn('group flex items-start gap-4 px-5 py-4 transition-colors hover:bg-surface-2/50', checking && 'scan-line', className)} style={style}>
      <span className="relative mt-0.5 grid size-5 shrink-0 place-items-center">
        {/* A live connection keeps a slow ring leaving it; a broken one pulses in warning. */}
        <span className={cn('absolute inset-0 rounded-full', ok ? 'animate-live-ring bg-success/40' : 'animate-pulse-dot bg-warning/30')} />
        {ok ? <CheckCircle2 className="relative size-5 text-success" /> : <CircleAlert className="relative size-5 text-warning" />}
      </span>
      <div className="min-w-0 flex-1"><div className="font-bold">{title}</div><div className="mt-0.5 text-sm [overflow-wrap:anywhere] text-muted">{children}</div></div>
      <Badge tone={ok ? 'success' : 'warning'} dot pulse={!ok} className="shrink-0">{ok ? okLabel : 'Action needed'}</Badge>
    </div>
  )
}

const copy = (t: string) => { void navigator.clipboard.writeText(t); toast.success('Copied') }

export default function Settings() {
  const { data: s, refetch, isFetching } = useQuery({ queryKey: ['system', 'status'], queryFn: () => api<Status>('/api/system/status'), staleTime: 30_000 })
  const base = (s?.public_url.url as string | undefined) ?? window.location.origin
  const checks = s ? [s.plivo.ok, s.openrouter.ok, s.sarvam.ok, s.public_url.ok, s.signature_validation, s.email.ok] : []

  return (
    <>
      <PageHeader eyebrow={<><PlugZap className="size-3.5" />Workspace · Shared by every agent</>} title="Integrations & system"
        description="Telephony, AI providers and deployment used by all agents. Secrets are configured on the server and never exposed here."
        visual={s ? <HealthRing ok={checks.filter(Boolean).length} total={checks.length} checking={isFetching} /> : undefined}
        actions={<Button onClick={() => refetch()} disabled={isFetching}><RefreshCw className={cn(isFetching && 'animate-spin')} />{isFetching ? 'Checking…' : 'Re-check'}</Button>}>
        {s && (() => {
          const ok = checks.filter(Boolean).length
          return (
            <div className="flex flex-wrap items-center gap-3">
              <span className={`rounded-full px-3 py-1.5 text-sm font-bold ${ok === checks.length ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>{ok}/{checks.length} checks passing</span>
              <span className="text-sm text-muted">Environment <b className="text-fg">{String(s.infrastructure.environment)}</b> · version <b className="text-fg">{String(s.infrastructure.version)}</b></span>
            </div>
          )
        })()}
      </PageHeader>
      {!s ? <Skeleton className="h-96 rounded-xl" /> : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <Stagger className="divide-y divide-border rounded-[var(--radius-card)] border border-border bg-surface shadow-card" step={60}>
              <CardHeader title="Integrations" className="border-0" />
              <Row ok={s.plivo.ok} checking={isFetching} title="Plivo · telephony">
                {s.plivo.ok ? <>{String(s.plivo.account)} · calling from <b className="text-fg">{String(s.plivo.number)}</b>{s.plivo.alias ? ` (${s.plivo.alias})` : ''} · balance {String(s.plivo.credits)}</> : s.plivo.detail}
              </Row>
              <Row ok={s.openrouter.ok} checking={isFetching} title="OpenRouter · conversation LLM">
                {s.openrouter.ok ? <>Key {String(s.openrouter.key)} · {s.openrouter.free_tier ? <span className="text-warning">free tier (rate limited — add credits for production)</span> : 'paid'} · models {(s.openrouter.models as string[]).join(' → ')}</> : s.openrouter.detail}
              </Row>
              <Row ok={s.sarvam.ok} checking={isFetching} title="Sarvam AI · voice (Bulbul) + fallback LLM">
                {s.sarvam.ok ? <>Key {String(s.sarvam.key)} · TTS {String(s.sarvam.tts_model)} · LLM {String(s.sarvam.llm_model)}</> : s.sarvam.detail}
              </Row>
              <Row ok={s.public_url.ok} checking={isFetching} title="Public webhook URL">{s.public_url.ok ? <code className="font-mono">{base}</code> : s.public_url.detail}</Row>
              <Row ok={s.signature_validation} checking={isFetching} okLabel="Enabled" title="Webhook signature verification">{s.signature_validation ? 'Every Plivo request is verified with X-Plivo-Signature-V3.' : 'Disabled — set PLIVO_VALIDATE_SIGNATURE=true.'}</Row>
              <Row ok={s.email.ok} checking={isFetching} title="Email · Resend / SMTP">{s.email.ok ? String(s.email.detail) : 'Not configured — reminders and reports are logged to Activity only.'}</Row>
            </Stagger>

            <Card>
              <CardHeader title="Inbound calls" description="Customers who call your Plivo number talk to the agent that owns that number (set under Agent settings); other numbers go to the first active agent." />
              <div className="space-y-3 p-5 text-sm">
                <InboundSetup />
                <p className="pt-2 text-xs text-muted">Manual setup: in Plivo (Voice → Applications) use these URLs and attach the application to your number.</p>
                {[['Answer URL', `${base}/api/plivo/answer`], ['Hangup URL', `${base}/api/plivo/hangup`]].map(([k, v]) => (
                  <div key={k} className="flex min-w-0 items-center gap-3 rounded-xl border border-border bg-surface-2 px-3 py-2">
                    <span className="hidden w-24 shrink-0 text-muted sm:block">{k}</span><code className="min-w-0 flex-1 truncate font-mono text-[13px]">{v}</code><span className="text-xs text-muted">POST</span>
                    <Button size="icon" variant="ghost" onClick={() => copy(v!)} aria-label={`Copy ${k}`}><Copy /></Button>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <Card className="h-fit">
            <CardHeader title={<span className="flex items-center gap-2"><Server className="size-4" />Deployment</span>} />
            <dl className="divide-y divide-border text-sm">
              {Object.entries(s.infrastructure).map(([k, v], i) => (
                <div key={k} style={{ animationDelay: `${120 + i * 50}ms` }} className="reveal reveal-in reveal-right flex justify-between gap-4 px-5 py-2.5"><dt className="text-muted">{({ environment: 'Environment', database: 'Database', store: 'Session store', scheduler_in_api: 'Scheduler in API process', version: 'Version' } as Record<string, string>)[k] ?? k}</dt><dd className="text-right font-medium">{String(v)}</dd></div>
              ))}
              <div className="flex justify-between gap-4 px-5 py-2.5"><dt className="text-muted">LLM order</dt><dd className="text-right font-medium">{s.llm_providers.join(' → ')}</dd></div>
            </dl>
            <div className="border-t border-border p-5 text-xs text-muted">API docs: <a className="font-bold text-fg underline" href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></div>
          </Card>
        </div>
      )}
      {s && <SecretsForm />}
      {s && <TeamMembers />}
    </>
  )
}

/** System health as a ring that sweeps to the share of passing checks, spinning while re-checking. */
function HealthRing({ ok, total, checking }: { ok: number; total: number; checking: boolean }) {
  const size = 76, stroke = 6, r = (size - stroke) / 2, c = 2 * Math.PI * r
  const [shown, setShown] = useState(0)
  useEffect(() => { const id = requestAnimationFrame(() => setShown(total ? ok / total : 0)); return () => cancelAnimationFrame(id) }, [ok, total])
  const allGood = ok === total
  return (
    <span className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className={cn('-rotate-90', checking && 'animate-spin [animation-duration:1.4s]')}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={allGood ? 'var(--success)' : 'var(--warning)'} strokeWidth={stroke}
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - shown)}
          className="transition-[stroke-dashoffset] duration-1000 ease-[var(--ease-entrance)]" />
      </svg>
      <span className="absolute text-center leading-none">
        <span className="block text-lg font-extrabold"><AnimatedNumber value={ok} />/{total}</span>
        <span className="text-[9.5px] font-bold tracking-wider text-muted uppercase">healthy</span>
      </span>
    </span>
  )
}
