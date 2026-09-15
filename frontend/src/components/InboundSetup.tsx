import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleAlert, PlugZap } from 'lucide-react'
import { toast } from 'sonner'
import { Badge, Button, Skeleton, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'

type Inbound = { number: string; voice_enabled: boolean | null; app_id: string | null; app_name: string | null; answer_url: string
  connected: boolean; expected_answer_url: string; previous_app: { app_id: string; app_name: string | null } | null }

/** Whether the Plivo number sends incoming calls to this app, with connect / restore actions. */
export default function InboundSetup() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data: s, isLoading, error } = useQuery({ queryKey: ['system', 'inbound'], queryFn: () => api<Inbound>('/api/system/inbound'), staleTime: 0, refetchOnMount: 'always', refetchInterval: 30_000, retry: false })
  const act = useMutation({
    mutationFn: (action: 'connect' | 'restore') => api<Inbound>(`/api/system/inbound/${action}`, { method: 'POST' }),
    onSuccess: (data, action) => {
      qc.setQueryData(['system', 'inbound'], data)
      toast.success(action === 'connect' ? `Inbound calls on ${data.number} now reach your agent` : `Restored ${data.app_name ?? 'the previous application'}`)
    },
    onError: (e) => toast.error('Plivo update failed', { description: e.message }),
  })
  const connect = async () => {
    if (!s) return
    const ok = await confirm({
      title: `Route inbound calls on ${s.number} to your agent?`,
      description: s.app_name ? `Calls to this number are handled by “${s.app_name}” today. It is saved and can be restored with one click.` : 'Plivo will send incoming calls on this number to this app.',
      confirmLabel: 'Connect inbound',
    })
    if (ok) act.mutate('connect')
  }
  if (isLoading) return <Skeleton className="h-16" />
  if (error || !s) return <p className="rounded-xl bg-warning-soft px-3 py-2 text-warning">Could not read the Plivo number: {(error as Error)?.message}</p>
  const stale = !s.connected && s.app_name === 'psyber-voice-inbound'
  return (
    <div className="rounded-xl border border-border p-4">
      <div className="flex flex-wrap items-center gap-3">
        {s.connected ? <CheckCircle2 className="size-5 text-success" /> : <CircleAlert className="size-5 text-warning" />}
        <div className="min-w-0 flex-1">
          <div className="font-bold">{s.number} · {s.connected ? 'inbound and outbound on this app' : 'outbound only'}</div>
          <div className="truncate text-xs text-muted">
            {s.connected ? 'Incoming calls are answered by your agent in real time.'
              : stale ? 'Connected earlier, but the public URL changed (ngrok restart). Reconnect to update it.'
              : `Incoming calls go to “${s.app_name ?? 'no application'}”.`}
          </div>
        </div>
        {!s.connected && <Button variant="primary" loading={act.isPending && act.variables === 'connect'} onClick={connect}><PlugZap />{stale ? 'Reconnect' : 'Connect inbound'}</Button>}
        {s.connected && <Badge tone="success" dot>Live</Badge>}
        {s.previous_app && <Button loading={act.isPending && act.variables === 'restore'} onClick={() => act.mutate('restore')}>Restore “{s.previous_app.app_name ?? s.previous_app.app_id}”</Button>}
      </div>
      {s.voice_enabled === false && <p className="mt-2 text-xs text-danger">Voice is disabled on this number in Plivo.</p>}
    </div>
  )
}
