import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { AlertTriangle, Users } from 'lucide-react'
import { useState } from 'react'
import { Button, Card, CardHeader, EmptyState, PageHeader, Skeleton, Tabs } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import type { RepLeaderboard } from '@/lib/types'

const RANGES = [{ value: '7', label: '7d' }, { value: '14', label: '14d' }, { value: '30', label: '30d' }, { value: '90', label: '90d' }] as const

const pct = (n: number | null | undefined) => (n == null ? '—' : `${n.toFixed(1)}%`)

export default function Team() {
  const { agent, base } = useAgent()
  const [days, setDays] = useState<'7' | '14' | '30' | '90'>('30')
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery<RepLeaderboard>({
    queryKey: ['team-leaderboard', days],
    queryFn: () => api<RepLeaderboard>(`${base}/analytics/leaderboard`, { params: { days } }),
    placeholderData: keepPreviousData,
  })

  const reps = data?.reps ?? []

  return (
    <>
      <PageHeader
        eyebrow={<><Users className="size-3.5" />{agent?.name} · Team</>}
        title="Team performance"
        description="How each rep's leads are converting: calls, connects, meetings and lead quality."
        actions={<Tabs value={days} onChange={setDays} items={RANGES.map((r) => ({ ...r }))} />} />

      {isError ? (
        <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load team performance" description={error instanceof Error ? error.message : 'Something went wrong while fetching the report.'}
          action={<Button onClick={() => refetch()} loading={isFetching}>Try again</Button>} /></Card>
      ) : isLoading || !data ? (
        <div className="grid gap-4"><Skeleton className="h-64" /></div>
      ) : (
        <Card className="overflow-hidden">
          <CardHeader title="Reps" description="Sorted by meetings booked, then connected calls" />
          <div className="w-full min-w-0 overflow-x-auto overscroll-x-contain">
            <table className="rows-in w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-y border-border bg-surface-2/50 text-left text-[11px] font-bold tracking-wider text-muted uppercase">
                  <th className="px-5 py-2.5">Rep</th>
                  <th className="px-3 py-2.5 text-right">Leads owned</th>
                  <th className="px-3 py-2.5 text-right">Calls</th>
                  <th className="px-3 py-2.5 text-right">Connect rate</th>
                  <th className="px-3 py-2.5 text-right">Meetings booked</th>
                  <th className="px-3 py-2.5 text-right">Hot leads</th>
                  <th className="px-5 py-2.5 text-right">Avg lead score</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {reps.map((r) => (
                  <tr key={r.user_id}>
                    <td className="px-5 py-2.5">
                      <div className="font-semibold">{r.name || '—'}</div>
                      <div className="text-xs text-muted">{r.email}</div>
                    </td>
                    <td className="px-3 text-right tabular-nums">{r.leads_owned}</td>
                    <td className="px-3 text-right tabular-nums">{r.calls}</td>
                    <td className="px-3 text-right tabular-nums">{pct(r.connect_rate)}</td>
                    <td className="px-3 text-right tabular-nums">{r.meetings}</td>
                    <td className="px-3 text-right tabular-nums">{r.hot_leads}</td>
                    <td className="px-5 text-right tabular-nums">{r.avg_lead_score ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!reps.length && <EmptyState icon={<Users />} title="No team activity yet" description="Assign leads to reps and place calls to see performance here." />}
        </Card>
      )}
    </>
  )
}
