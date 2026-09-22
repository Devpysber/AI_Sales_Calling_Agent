import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, CircleAlert, Copy, ExternalLink, Eye, EyeOff, HeartPulse, RefreshCw, Wrench } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, CardHeader, EmptyState, Input, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

type IssueStatus = 'open' | 'escalated' | 'healed' | 'dismissed'

type Issue = {
  id: string
  kind: string
  title: string
  detail: string
  agent_id?: number | null
  call_id?: number | null
  status: IssueStatus
  count: number
  first_at: string
  last_at: string
  healable: boolean
  remedy: string | null
  result: string | null
  fix_pr?: string | null
}

type IssuesResponse = { issues: Issue[]; escalation: string }
type HealResponse = { results: { id: string; kind: string; title: string; ok: boolean; note: string }[]; issues: Issue[]; escalation: string }

const KEY = ['system', 'issues']

const STATUS_TONE: Record<IssueStatus, 'warning' | 'danger' | 'success' | 'neutral'> = {
  open: 'warning', escalated: 'danger', healed: 'success', dismissed: 'neutral',
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60_000) return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  return `${Math.floor(diff / 86_400_000)}d ago`
}

function IssueRow({ issue, onHeal, onDismiss, healingId, dismissingId }: {
  issue: Issue
  onHeal: (id: string) => void
  onDismiss: (id: string) => void
  healingId: string | null
  dismissingId: string | null
}) {
  const [expanded, setExpanded] = useState(false)
  const canHeal = issue.healable && (issue.status === 'open' || issue.status === 'escalated') && !issue.fix_pr
  return (
    <div className="flex min-w-0 flex-wrap items-start gap-x-4 gap-y-2 px-4 py-4 transition-colors hover:bg-surface-2/50 sm:flex-nowrap sm:px-5">
      <div className="min-w-0 flex-1 basis-40">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONE[issue.status]}>{issue.status}</Badge>
          {issue.fix_pr && (
            <a href={issue.fix_pr} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-semibold text-brand hover:underline">
              Fix PR <ExternalLink className="size-3" />
            </a>
          )}
          <span className="font-bold break-words">{issue.title}</span>
          {issue.count > 1 && <span className="text-xs font-semibold text-muted">x{issue.count}</span>}
          {issue.agent_id != null && <Badge tone="neutral">agent #{issue.agent_id}</Badge>}
          {issue.call_id != null && <Badge tone="neutral">call #{issue.call_id}</Badge>}
          <span className="text-xs text-muted">{timeAgo(issue.last_at)}</span>
        </div>
        <button type="button" onClick={() => setExpanded((v) => !v)} className="mt-1 block text-left text-sm text-muted">
          <span className={cn('whitespace-pre-wrap', !expanded && 'line-clamp-2')}>{issue.detail}</span>
        </button>
        {issue.remedy && <p className="mt-1 text-xs text-muted">Remedy: {issue.remedy}</p>}
        {issue.result && <p className="mt-1 text-xs font-medium text-fg-2">{issue.result}</p>}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {canHeal && (
          <Button size="sm" onClick={() => onHeal(issue.id)} loading={healingId === issue.id} disabled={dismissingId === issue.id}>
            Heal
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => onDismiss(issue.id)} loading={dismissingId === issue.id} disabled={healingId === issue.id}>
          Dismiss
        </Button>
      </div>
    </div>
  )
}

export default function HealPanel() {
  const qc = useQueryClient()
  const [healingAll, setHealingAll] = useState(false)
  const [healingId, setHealingId] = useState<string | null>(null)
  const [dismissingId, setDismissingId] = useState<string | null>(null)
  const [devOpen, setDevOpen] = useState(false)
  const [tokenVisible, setTokenVisible] = useState(false)
  const [rotating, setRotating] = useState(false)

  const { data, isFetching, isError, error, refetch } = useQuery({
    queryKey: KEY,
    queryFn: () => api<IssuesResponse>('/api/system/issues', { params: { detect: true } }),
    staleTime: 15_000,
    refetchInterval: 60_000,
  })

  const { data: tokenData, refetch: refetchToken } = useQuery({
    queryKey: ['system', 'heal-token'],
    queryFn: () => api<{ token: string; export_url: string }>('/api/system/issues/token'),
    staleTime: 60_000,
  })

  const issues = data?.issues ?? []
  const openCount = issues.filter((i) => i.status === 'open').length
  const escalatedCount = issues.filter((i) => i.status === 'escalated').length
  const lastCheck = issues.length ? issues.reduce((m, i) => (i.last_at > m ? i.last_at : m), issues[0].last_at) : null

  const applyResults = (res: HealResponse) => {
    for (const r of res.results) {
      if (r.ok) toast.success(`${r.title}: ${r.note}`)
      else toast.error(`${r.title}: ${r.note}`)
    }
    qc.setQueryData(KEY, { issues: res.issues, escalation: res.escalation })
  }

  const healAll = async () => {
    setHealingAll(true)
    try {
      const res = await api<HealResponse>('/api/system/issues/heal', { method: 'POST', json: {} })
      applyResults(res)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Heal failed')
    } finally {
      setHealingAll(false)
    }
  }

  const healOne = async (id: string) => {
    setHealingId(id)
    try {
      const res = await api<HealResponse>('/api/system/issues/heal', { method: 'POST', json: { id } })
      applyResults(res)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Heal failed')
    } finally {
      setHealingId(null)
    }
  }

  const dismiss = async (id: string) => {
    setDismissingId(id)
    try {
      await api(`/api/system/issues/${id}/dismiss`, { method: 'POST' })
      qc.setQueryData<IssuesResponse | undefined>(KEY, (prev) => prev && { ...prev, issues: prev.issues.filter((i) => i.id !== id) })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Dismiss failed')
    } finally {
      setDismissingId(null)
    }
  }

  const copyEscalation = async () => {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(data?.escalation ?? '')
      toast.success('Copied')
    } catch {
      toast.error('Could not copy — select the text and copy it manually')
    }
  }

  const copyText = async (text: string) => {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(text)
      toast.success('Copied')
    } catch {
      toast.error('Could not copy — select the text and copy it manually')
    }
  }

  const rotateToken = async () => {
    setRotating(true)
    try {
      await api<{ token: string; export_url: string }>('/api/system/issues/token', { params: { rotate: true } })
      await refetchToken()
      toast.success('Token rotated — update the cloud routine')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Rotate failed')
    } finally {
      setRotating(false)
    }
  }

  const canHealAll = issues.some((i) => (i.status === 'open' || i.status === 'escalated') && i.healable)

  return (
    <Card className="mb-6">
      <CardHeader
        title="Health & heal"
        description="Problems the app hit at run time. Heal re-runs what failed; anything without a remedy is listed for a developer."
        action={<>
          <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
            <RefreshCw className={cn(isFetching && 'animate-spin')} />Refresh
          </Button>
          <Button size="sm" onClick={healAll} loading={healingAll} disabled={!canHealAll || healingAll}>
            <Wrench />Heal all
          </Button>
        </>}
      />
      <div className="px-4 pb-2 text-sm text-muted sm:px-5">
        {openCount} open · {escalatedCount} escalated{lastCheck ? ` · last check ${timeAgo(lastCheck)}` : ''}
      </div>
      {isError ? (
        <EmptyState
          icon={<CircleAlert />}
          title="Could not check health"
          description={(error as Error).message}
          action={<Button size="sm" onClick={() => refetch()}>Try again</Button>}
        />
      ) : issues.length === 0 ? (
        <EmptyState icon={<HeartPulse />} title="All clear" description="Nothing failed recently." />
      ) : (
        <div className="divide-y divide-border">
          {issues.map((issue) => (
            <IssueRow key={issue.id} issue={issue} onHeal={healOne} onDismiss={dismiss} healingId={healingId} dismissingId={dismissingId} />
          ))}
        </div>
      )}
      {data?.escalation && (
        <div className="border-t border-border px-4 py-3 sm:px-5">
          <button type="button" onClick={() => setDevOpen((v) => !v)} className="flex items-center gap-1.5 text-sm font-semibold text-fg-2">
            <ChevronDown className={cn('size-4 transition-transform', devOpen && 'rotate-180')} />
            For the developer
          </button>
          {devOpen && (
            <div className="mt-2 space-y-2">
              <Textarea readOnly rows={6} value={data.escalation} className="font-mono text-xs" />
              <Button size="sm" variant="ghost" onClick={copyEscalation}><Copy />Copy</Button>
            </div>
          )}
        </div>
      )}
      <div className="border-t border-border px-4 py-3 sm:px-5">
        <div className="text-sm font-semibold text-fg-2">Fix agent</div>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1 basis-48">
            <label className="mb-1 block text-xs text-muted">Export URL</label>
            <div className="flex items-center gap-1.5">
              <Input readOnly value={tokenData?.export_url ?? ''} className="font-mono text-xs" />
              <Button size="sm" variant="ghost" onClick={() => copyText(tokenData?.export_url ?? '')} disabled={!tokenData}>
                <Copy />
              </Button>
            </div>
          </div>
          <div className="min-w-0 flex-1 basis-48">
            <label className="mb-1 block text-xs text-muted">Token</label>
            <div className="flex items-center gap-1.5">
              <Input readOnly value={tokenVisible ? (tokenData?.token ?? '') : '••••••••'} className="font-mono text-xs" />
              <Button size="sm" variant="ghost" onClick={() => setTokenVisible((v) => !v)} disabled={!tokenData}>
                {tokenVisible ? <EyeOff /> : <Eye />}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => copyText(tokenData?.token ?? '')} disabled={!tokenData}>
                <Copy />
              </Button>
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={rotateToken} loading={rotating}>
            <RefreshCw />Rotate token
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted">
          The daily cloud fix agent reads escalated issues from this URL with this token and links its PR back here.
        </p>
      </div>
    </Card>
  )
}
