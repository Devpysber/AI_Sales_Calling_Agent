import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, CircleAlert, Copy, PlugZap, RefreshCw, Server } from 'lucide-react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'
import InboundSetup from '@/components/InboundSetup'
import { Badge, Button, Card, CardHeader, PageHeader, Skeleton } from '@/components/ui'
import { api } from '@/lib/api'

type Check = { ok: boolean; detail?: string | null; [k: string]: unknown }
type Status = {
  plivo: Check; openrouter: Check; sarvam: Check; public_url: Check; email: Check
  llm_providers: string[]; signature_validation: boolean
  infrastructure: Record<string, string | boolean>
}

function Row({ ok, title, children, okLabel = 'Connected' }: { ok: boolean; title: string; children: ReactNode; okLabel?: string }) {
  return (
    <div className="flex items-start gap-4 px-5 py-4">
      {ok ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 size-5 shrink-0 text-warning" />}
      <div className="min-w-0 flex-1"><div className="font-bold">{title}</div><div className="mt-0.5 text-sm [overflow-wrap:anywhere] text-muted">{children}</div></div>
      <Badge tone={ok ? 'success' : 'warning'} className="shrink-0">{ok ? okLabel : 'Action needed'}</Badge>
    </div>
  )
}

const copy = (t: string) => { void navigator.clipboard.writeText(t); toast.success('Copied') }

export default function Settings() {
  const { data: s, refetch, isFetching } = useQuery({ queryKey: ['system', 'status'], queryFn: () => api<Status>('/api/system/status'), staleTime: 30_000 })
  const base = (s?.public_url.url as string | undefined) ?? window.location.origin

  return (
    <>
      <PageHeader eyebrow={<><PlugZap className="size-3.5" />Workspace · Shared by every agent</>} title="Integrations & system"
        description="Telephony, AI providers and deployment used by all agents. Secrets are configured on the server and never exposed here."
        actions={<Button onClick={() => refetch()} loading={isFetching}><RefreshCw />Re-check</Button>}>
        {s && (() => {
          const checks = [s.plivo.ok, s.openrouter.ok, s.sarvam.ok, s.public_url.ok, s.signature_validation, s.email.ok]
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
            <Card className="divide-y divide-border">
              <CardHeader title="Integrations" className="border-0" />
              <Row ok={s.plivo.ok} title="Plivo · telephony">
                {s.plivo.ok ? <>{String(s.plivo.account)} · calling from <b className="text-fg">{String(s.plivo.number)}</b>{s.plivo.alias ? ` (${s.plivo.alias})` : ''} · balance {String(s.plivo.credits)}</> : s.plivo.detail}
              </Row>
              <Row ok={s.openrouter.ok} title="OpenRouter · conversation LLM">
                {s.openrouter.ok ? <>Key {String(s.openrouter.key)} · {s.openrouter.free_tier ? <span className="text-warning">free tier (rate limited — add credits for production)</span> : 'paid'} · models {(s.openrouter.models as string[]).join(' → ')}</> : s.openrouter.detail}
              </Row>
              <Row ok={s.sarvam.ok} title="Sarvam AI · voice (Bulbul) + fallback LLM">
                {s.sarvam.ok ? <>Key {String(s.sarvam.key)} · TTS {String(s.sarvam.tts_model)} · LLM {String(s.sarvam.llm_model)}</> : s.sarvam.detail}
              </Row>
              <Row ok={s.public_url.ok} title="Public webhook URL">{s.public_url.ok ? <code className="font-mono">{base}</code> : s.public_url.detail}</Row>
              <Row ok={s.signature_validation} okLabel="Enabled" title="Webhook signature verification">{s.signature_validation ? 'Every Plivo request is verified with X-Plivo-Signature-V3.' : 'Disabled — set PLIVO_VALIDATE_SIGNATURE=true.'}</Row>
              <Row ok={s.email.ok} title="Email · Resend / SMTP">{s.email.ok ? String(s.email.detail) : 'Not configured — reminders and reports are logged to Activity only.'}</Row>
            </Card>

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
              {Object.entries(s.infrastructure).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4 px-5 py-2.5"><dt className="text-muted">{({ environment: 'Environment', database: 'Database', store: 'Session store', scheduler_in_api: 'Scheduler in API process', version: 'Version' } as Record<string, string>)[k] ?? k}</dt><dd className="text-right font-medium">{String(v)}</dd></div>
              ))}
              <div className="flex justify-between gap-4 px-5 py-2.5"><dt className="text-muted">LLM order</dt><dd className="text-right font-medium">{s.llm_providers.join(' → ')}</dd></div>
            </dl>
            <div className="border-t border-border p-5 text-xs text-muted">API docs: <a className="font-bold text-fg underline" href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></div>
          </Card>
        </div>
      )}
    </>
  )
}
