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
  const { data: s, isLoading, error, refetch, isFetching } = useQuery({ queryKey: ['system', 'inbound'], queryFn: () => api<Inbound>('/api/system/inbound'), staleTime: 0, refetchOnMount: 'always', refetchInterval: 30_000, retry: false })
  const act = useMutation({
    mutationFn: (action: 'connect' | 'restore') => api<Inbound>(`/api/system/inbound/${action}`, { method: 'POST' }),
    onSuccess: (data, action) => {
      qc.setQueryData(['system', 'inbound'], data)
      toast.success(action === 'connect' ? `Inbound calls on ${data.number} now reach your agent` : `Restored ${data.app_name ?? 'the previous application'}`)
    },
    onError: (e) => toast.error('Plivo update failed', { description: e.message }),
  })
  const stale = !!s && !s.connected && s.app_name === 'psyber-voice-inbound'
  const connect = async () => {
    if (!s) return
    const ok = await confirm({
      title: `Route inbound calls on ${s.number} to your agent?`,
      description: stale ? 'Plivo will be updated to this app’s current public URL.'
        : s.app_name ? `Calls to this number are handled by “${s.app_name}” today. It is saved and can be restored with one click.`
        : 'Plivo will send incoming calls on this number to this app.',
      confirmLabel: 'Connect inbound',
    })
    if (ok) act.mutate('connect')
  }
  if (isLoading) return <Skeleton className="h-16" />
  if (!s) return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl bg-warning-soft px-3 py-2 text-warning">
      <span className="min-w-0 flex-1 break-words">Could not read the Plivo number{error ? `: ${(error as Error).message}` : ''}</span>
      <Button size="sm" loading={isFetching} onClick={() => void refetch()}>Retry</Button>
    </div>
  )
  return (
    <div className="rounded-xl border border-border p-4">
      {error && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-warning-soft px-3 py-1.5 text-xs text-warning">
          <span className="min-w-0 flex-1 break-words">Showing the last known status; refresh failed: {(error as Error).message}</span>
          <Button size="sm" loading={isFetching} onClick={() => void refetch()}>Retry</Button>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {s.connected ? <CheckCircle2 className="size-5 shrink-0 text-success" /> : <CircleAlert className="size-5 shrink-0 text-warning" />}
        <div className="min-w-0 flex-1 basis-48">
          <div className="break-words font-bold">{s.number ?? 'No number'} · {s.connected ? 'inbound and outbound on this app' : 'outbound only'}</div>
          <div className="break-words text-xs text-muted">
            {s.connected ? 'Incoming calls are answered by your agent in real time.'
              : stale ? 'Connected earlier, but the public URL changed (ngrok restart). Reconnect to update it.'
              : `Incoming calls go to “${s.app_name ?? 'no application'}”.`}
          </div>
        </div>
        <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">
          {!s.connected && <Button variant="primary" className="w-full sm:w-auto" loading={act.isPending && act.variables === 'connect'} disabled={act.isPending} onClick={() => void connect()}><PlugZap />{stale ? 'Reconnect' : 'Connect inbound'}</Button>}
          {s.connected && <Badge tone="success" dot>Live</Badge>}
          {s.previous_app && <Button className="w-full min-w-0 sm:w-auto sm:max-w-full" loading={act.isPending && act.variables === 'restore'} disabled={act.isPending} onClick={() => act.mutate('restore')}><span className="truncate">Restore “{s.previous_app.app_name ?? s.previous_app.app_id}”</span></Button>}
        </div>
      </div>
      {s.voice_enabled === false && <p className="mt-2 text-xs text-danger">Voice is disabled on this number in Plivo.</p>}
    </div>
  )
}
