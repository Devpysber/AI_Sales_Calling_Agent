import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, LogOut, MessageSquareCode, ShieldCheck, Smartphone } from 'lucide-react'
import { toast } from 'sonner'
import { Badge, Button, Card, CardHeader, PageHeader, Switch, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { AutomationSettings } from '@/lib/types'
import { titleCase } from '@/lib/utils'

type WaStatus = { status?: string; phone?: string | null; pushname?: string | null }
type WaQr = { status?: string; qr?: string }

const TONE: Record<string, 'success' | 'warning' | 'neutral'> = { connected: 'success', qr_ready: 'warning', initializing: 'warning', authenticated: 'warning' }
// The bridge is loading WhatsApp Web or showing a QR: keep asking for the code until the phone scans it.
const WAITING = ['qr_ready', 'initializing', 'authenticated']

export default function WhatsApp() {
  const { agent, base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const enabled = !!agent?.id

  const statusQ = useQuery({
    queryKey: ['wa-status', agent?.id],
    queryFn: () => api<WaStatus>(`${base}/whatsapp/status`),
    enabled,
    refetchInterval: (q) => (q.state.data?.status === 'connected' ? 15000 : 3000),
  })
  const status = statusQ.data?.status ?? (statusQ.isError ? 'disconnected' : 'initializing')
  const isConnected = status === 'connected'
  // Asking for a QR starts a browser on the server (~450 MB), so it only happens once someone clicks Link.
  const [linking, setLinking] = useState(false)
  const waiting = linking && !isConnected || WAITING.includes(status)

  // Polls only while the bridge is waiting for a scan; react-query stops it on unmount.
  const qrQ = useQuery({
    queryKey: ['wa-qr', agent?.id],
    queryFn: () => api<WaQr>(`${base}/whatsapp/qr`),
    enabled: enabled && waiting,
    refetchInterval: waiting ? 3000 : false,
  })

  const info = { pushname: statusQ.data?.pushname }
  const number = (statusQ.data?.phone || '').replace(/@.*$/, '')

  const disconnect = useMutation({
    mutationFn: () => api(`${base}/whatsapp/disconnect`, { method: 'POST' }),
    onSuccess: () => { toast.success('WhatsApp disconnected'); qc.invalidateQueries({ queryKey: ['wa-status'] }) },
    onError: (e: Error) => toast.error(e.message),
  })

  const automation = useQuery({ queryKey: ['automation'], queryFn: () => api<{ settings: AutomationSettings }>(`${base}/automation`), enabled })
  const toggle = useMutation({
    mutationFn: (v: boolean) => api<AutomationSettings>(`${base}/automation`, { method: 'PUT', json: { ...automation.data!.settings, whatsapp_auto_reply: v } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['automation'] }); toast.success('Saved') },
    onError: (e: Error) => toast.error(e.message),
  })

  const { data: activity } = useQuery({
    queryKey: ['agent-whatsapp-events', agent?.id],
    queryFn: () => api<any[]>(`${base}/activity`, { params: { limit: 40 } }),
    enabled,
  })
  const list: any[] = Array.isArray(activity) ? activity : (activity as any)?.items || []
  const whatsappEvents = list.filter(
    (e: any) => e?.title?.toLowerCase?.().includes('whatsapp') || e?.detail?.toLowerCase?.().includes('whatsapp'),
  )

  const onDisconnect = async () => {
    if (await confirm({ title: 'Disconnect WhatsApp?', description: 'The agent stops replying on this number until you scan a new QR code.', confirmLabel: 'Disconnect', danger: true })) disconnect.mutate()
  }

  const autoReply = automation.data?.settings.whatsapp_auto_reply
  const qr = qrQ.data?.qr

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 sm:p-6 lg:p-8">
      <PageHeader title="WhatsApp" description={`Link a WhatsApp number so ${agent?.name || 'this agent'} can chat with leads.`} />

      <Card className="space-y-6 p-4 sm:p-6">
        <CardHeader title="Connection" action={<Badge tone={TONE[status] ?? 'neutral'}>{isConnected ? 'Connected' : status === 'qr_ready' ? 'Scan required' : status === 'not_linked' ? 'Not linked' : titleCase(status)}</Badge>} />
        <div className="grid items-center gap-8 md:grid-cols-2">
          <div className="flex flex-col items-center justify-center rounded-2xl border-2 border-dashed border-border bg-surface-2/40 p-6 text-center">
            {isConnected ? (
              <div className="space-y-4 py-6">
                <div className="mx-auto flex size-16 items-center justify-center rounded-full bg-success/20 text-success"><CheckCircle2 className="size-10" /></div>
                <div>
                  <h3 className="text-lg font-bold text-fg">Connected</h3>
                  <p className="text-sm text-muted">{info?.pushname || 'Linked account'}{number ? ` · +${number}` : ''}</p>
                </div>
                <Button variant="outline-danger" size="sm" onClick={onDisconnect} loading={disconnect.isPending}><LogOut className="size-4" />Disconnect</Button>
              </div>
            ) : qr ? (
              <div className="space-y-3">
                <div className="inline-block overflow-hidden rounded-xl bg-white p-3 shadow-md"><img src={qr} alt="Scan WhatsApp QR" className="size-64 object-contain" /></div>
                <p className="animate-pulse text-xs font-semibold text-muted">Scan with WhatsApp on your phone</p>
              </div>
            ) : (
              <div className="space-y-4 py-12">
                {waiting ? <div className="mx-auto size-12 animate-spin rounded-full border-4 border-brand border-t-transparent" /> : <Smartphone className="mx-auto size-10 text-muted" />}
                <p className="text-sm text-muted">{waiting ? 'Preparing the QR code...' : 'WhatsApp is not connected.'}</p>
                {!waiting && <Button size="sm" onClick={() => setLinking(true)}><Smartphone className="size-4" />Link a number</Button>}
              </div>
            )}
          </div>

          <div className="space-y-4">
            <h4 className="text-sm font-bold uppercase tracking-wider text-fg-2">How to link</h4>
            <ol className="space-y-3 text-sm text-muted">
              {['Open WhatsApp on your phone.', 'Go to Settings, then Linked devices.', 'Tap Link a device and scan the QR code.'].map((s, i) => (
                <li key={s} className="flex items-start gap-3">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-brand/20 text-xs font-bold text-brand">{i + 1}</span>
                  <span>{s}</span>
                </li>
              ))}
            </ol>
            <div className="flex items-start gap-2 rounded-xl border border-success/30 bg-success/10 p-4 text-xs text-success">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" />
              <p>Replies are automatic. Messages the agent starts are limited to 30 new chats a day, spaced 20–60 s apart, to keep the number safe.</p>
            </div>
          </div>
        </div>
      </Card>

      <Card className="flex items-center justify-between gap-4 p-4 sm:p-6">
        <div>
          <div className="text-sm font-semibold text-fg">Auto-reply</div>
          <p className="text-xs text-muted">The agent answers incoming chats on the linked number.</p>
        </div>
        <Switch label="Auto-reply" checked={!!autoReply} disabled={autoReply === undefined || toggle.isPending} onChange={(v) => toggle.mutate(v)} />
      </Card>

      <Card className="space-y-4 p-4 sm:p-6">
        <CardHeader title="Recent WhatsApp activity" description="Messages and actions recorded for this agent." />
        {whatsappEvents.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted">
            <MessageSquareCode className="mx-auto mb-2 size-8 text-muted/50" />
            No WhatsApp activity yet.
          </div>
        ) : (
          <div className="max-h-80 divide-y divide-border overflow-y-auto pr-2">
            {whatsappEvents.map((ev) => (
              <div key={ev.id} className="py-2.5 text-xs">
                <div className="flex items-center justify-between text-muted">
                  <span className="font-semibold text-fg">{ev.title}</span>
                  <span>{new Date(ev.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
                <p className="mt-1 truncate font-mono text-muted/90">{ev.detail}</p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
