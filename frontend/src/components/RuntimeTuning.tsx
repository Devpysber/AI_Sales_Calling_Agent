import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { toast } from 'sonner'
import { Settings2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Card, CardHeader, Input, Skeleton } from './ui'

type RuntimeRow = { label: string; help: string; value: string | number; source: 'panel' | 'server'; kind: 'number' | 'text'; min: number | null; max: number | null; secret: boolean }
type Runtime = Record<string, RuntimeRow> & { _server?: Record<string, string> }

const MASK = '********'

function RuntimeField({ row, value, disabled, onChange, onReset }: {
  row: RuntimeRow; value: string; disabled?: boolean; onChange: (v: string) => void; onReset?: () => void
}) {
  const id = useId()
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="min-w-0 truncate text-sm font-bold">{row.label}</label>
        {row.source === 'panel' && onReset && (
          <button type="button" onClick={onReset} disabled={disabled} className="shrink-0 text-[11px] font-semibold text-brand hover:underline">Reset to server</button>
        )}
      </div>
      <Input
        id={id}
        type={row.secret ? 'password' : row.kind === 'number' ? 'number' : 'text'}
        step={row.kind === 'number' ? 'any' : undefined}
        min={row.kind === 'number' && row.min != null ? row.min : undefined}
        max={row.kind === 'number' && row.max != null ? row.max : undefined}
        value={value}
        disabled={disabled}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
        onFocus={(e) => { if (row.secret && value === MASK) e.target.select() }}
      />
      {row.help && <p className="text-[11.5px] text-muted">{row.help}</p>}
      <p className="text-[11px] text-muted">Using: {String(row.value)} · from {row.source === 'panel' ? 'this panel' : 'server .env / default'}</p>
    </div>
  )
}

export default function RuntimeTuning() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['system', 'runtime'],
    queryFn: () => api<Runtime>('/api/system/runtime'),
  })

  const [form, setForm] = useState<Record<string, string>>({})

  const keys = data ? Object.keys(data).filter((k) => k !== '_server') : []
  const getVal = (key: string) => (form[key] !== undefined ? form[key] : String((data?.[key] as RuntimeRow)?.value ?? ''))
  const setVal = (key: string, val: string) => setForm((prev) => ({ ...prev, [key]: val }))
  const reset = () => setForm({})
  const dirty = Object.keys(form).length > 0

  const { mutate, isPending } = useMutation({
    mutationFn: (body: Record<string, string>) => api('/api/system/runtime', { method: 'POST', json: body }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system'] })
      reset()
      toast.success('Runtime settings applied — live from the next call')
    },
    onError: (e) => toast.error('Could not save', { description: (e as Error).message }),
  })

  if (isLoading) return <Skeleton className="mt-4 h-64 rounded-xl" />
  if (isError) {
    return (
      <Card className="mt-4">
        <CardHeader title={<span className="flex items-center gap-2"><Settings2 className="size-4" />Runtime tuning</span>} description={`Could not load runtime settings: ${(error as Error)?.message ?? 'unknown error'}`} action={<Button variant="secondary" onClick={() => refetch()}>Retry</Button>} />
      </Card>
    )
  }

  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Settings2 className="size-4" />Runtime tuning</span>} description="Live settings the admin can change here without touching the server or redeploying. Empty = use the server value." />
      <div className="grid gap-4 p-4 sm:p-5 md:grid-cols-2">
        {keys.map((key) => {
          const row = data![key] as RuntimeRow
          return (
            <RuntimeField
              key={key}
              row={row}
              value={getVal(key)}
              disabled={isPending}
              onChange={(v) => setVal(key, v)}
              onReset={row.source === 'panel' ? () => setVal(key, '') : undefined}
            />
          )
        })}
      </div>
      <div className="flex flex-col gap-2 border-t border-border p-4 sm:flex-row sm:items-center sm:justify-end sm:p-5">
        {dirty && !isPending && <Button variant="secondary" className="w-full sm:w-auto" onClick={reset}>Discard changes</Button>}
        <Button className="w-full sm:w-auto" onClick={() => mutate(form)} loading={isPending} disabled={!dirty || isPending}>Save</Button>
      </div>
    </Card>
  )
}
