import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { toast } from 'sonner'
import { Check, Key, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Card, CardHeader, Input, Skeleton } from './ui'

const MASK = '********'

type Secrets = Record<string, string> & { _hints?: Record<string, string> }

/**
 * A stored credential is never sent back, so the field shows the mask and a hint of what is saved —
 * without the hint a wrong value (an email address in the Plivo Auth ID) is invisible from here.
 * Removal happens only through the bin button; focusing/blurring a saved field never changes it.
 */
function Credential({ label, value, hint, secret, placeholder, help, disabled, onChange, onBlur, onRemove }: {
  label: string
  value: string
  hint?: string
  secret?: boolean
  placeholder?: string
  help?: string
  disabled?: boolean
  onChange: (v: string) => void
  onBlur: () => void
  onRemove: () => void
}) {
  const id = useId()
  const saved = value === MASK

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="min-w-0 truncate text-sm font-bold">{label}</label>
        {saved && <span className="flex shrink-0 items-center gap-1 text-[11px] font-semibold text-success"><Check className="size-3" />Saved</span>}
      </div>
      <div className="flex items-center gap-1.5">
        {/* Wrapper carries the flex sizing: in password mode Input applies className to the inner <input>, not its wrapper. */}
        <div className="min-w-0 flex-1">
          <Input
            id={id}
            className="w-full"
            // Keep the type stable per field: Input renders password fields inside a wrapper, so flipping
            // text -> password on the first keystroke would remount the <input> and drop focus.
            type={secret ? 'password' : 'text'}
            value={value}
            disabled={disabled}
            autoComplete="off"
            placeholder={placeholder}
            onChange={(e) => onChange(e.target.value)}
            onFocus={(e) => { if (saved) e.target.select() }}   // typing over the selected mask replaces it, not appends
            onBlur={onBlur}
          />
        </div>
        {saved && (
          <button type="button" onClick={onRemove} title="Remove this value" aria-label="Remove this value" disabled={disabled}
            className="grid size-10 shrink-0 place-items-center rounded-lg border border-border text-fg-2 transition hover:border-danger hover:text-danger">
            <Trash2 className="size-4" />
          </button>
        )}
      </div>
      {saved && hint && <p className="break-words text-[11.5px] text-muted">Currently saved: <span className="font-mono break-all">{hint}</span></p>}
      {help && !saved && <p className="text-[11.5px] text-muted">{help}</p>}
    </div>
  )
}

function NumberField({ label, value, placeholder, disabled, onChange }: {
  label: string; value: string; placeholder: string; disabled?: boolean; onChange: (v: string) => void
}) {
  const id = useId()
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-bold">{label}</label>
      <Input id={id} type="number" step="0.01" min="0" inputMode="decimal" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  )
}

export default function SecretsForm() {
  const queryClient = useQueryClient()
  const currencyId = useId()
  const { data: secrets, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['system', 'secrets'],
    queryFn: () => api<Secrets>('/api/system/secrets'),
  })

  const [form, setForm] = useState<Record<string, string>>({})
  // Keys explicitly removed with the bin — the only way an empty value for a saved credential reaches the server.
  const [removed, setRemoved] = useState<Set<string>>(() => new Set())

  const reset = () => { setForm({}); setRemoved(new Set()) }

  const { mutate, isPending } = useMutation({
    // `json` is what sets Content-Type: application/json — a raw `body` string reaches FastAPI
    // without it and the request is rejected before the handler runs.
    mutationFn: (body: Record<string, string>) => api('/api/system/secrets', { method: 'POST', json: body }),
    // Refetch before clearing the form so a new credential doesn't flash empty (and an edited number old) in between.
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system'] })
      reset()
      toast.success('Secrets saved securely')
    },
    // The server rejects a malformed credential with the reason; show that, not a generic failure.
    onError: (e) => toast.error('Could not save', { description: (e as Error).message }),
  })

  if (isLoading) return <Skeleton className="mt-4 h-64 rounded-xl" />
  if (isError) {
    return (
      <Card className="mt-4">
        <CardHeader title={<span className="flex items-center gap-2"><Key className="size-4" />Secrets & Costs</span>} description={`Could not load secrets: ${(error as Error)?.message ?? 'unknown error'}`} action={<Button variant="secondary" onClick={() => refetch()}>Retry</Button>} />
      </Card>
    )
  }

  const hints = secrets?._hints ?? {}
  const getVal = (key: string) => (form[key] !== undefined ? form[key] : (secrets?.[key] || ''))
  const serverVal = (key: string) => secrets?.[key] || ''
  // A value typed back to what the server holds is not a change — drop it so Save/Discard reflect real edits only.
  const setVal = (key: string, val: string) => setForm((prev) => {
    if (val === serverVal(key) && !removed.has(key)) { const next = { ...prev }; delete next[key]; return next }
    return { ...prev, [key]: val }
  })
  const dropKey = (key: string) => setForm((prev) => { const next = { ...prev }; delete next[key]; return next })
  // Blurring an emptied saved credential (backspace over the mask, tabbing through) restores it — never a silent delete.
  const restoreIfEmptied = (key: string) => {
    if (form[key] === '' && secrets?.[key] === MASK && !removed.has(key)) dropKey(key)
  }
  const remove = (key: string) => { setRemoved((prev) => new Set(prev).add(key)); setVal(key, '') }
  const cred = (key: string, label: string, opts: { secret?: boolean; placeholder?: string; help?: string } = {}) => (
    <Credential label={label} value={getVal(key)} hint={hints[key]} disabled={isPending}
      onChange={(v) => setVal(key, v)} onBlur={() => restoreIfEmptied(key)} onRemove={() => remove(key)} {...opts} />
  )
  const num = (key: string, label: string, placeholder: string) => (
    <NumberField label={label} value={getVal(key)} placeholder={placeholder} disabled={isPending} onChange={(v) => setVal(key, v)} />
  )
  const dirty = Object.keys(form).some((k) => form[k] !== serverVal(k))

  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Key className="size-4" />Secrets & Costs</span>} description="Manage credentials securely in the database. A saved value shows as ******** with a hint of what is stored — click the field to replace it, or the bin to remove it. Update cost pricing or credits manually." />
      <div className="grid gap-6 p-4 sm:p-5 md:grid-cols-2">
        <div className="space-y-4">
          <h3 className="font-bold">Telephony & AI</h3>
          {cred('plivo_auth_id', 'Plivo Auth ID', { placeholder: 'MA…', help: '20 characters starting with MA or SA, from the Plivo console overview.' })}
          {cred('plivo_auth_token', 'Plivo Auth Token', { secret: true })}
          {cred('plivo_phone_number', 'Plivo Phone Number', { placeholder: '+919876543210', help: 'International format, including the country code.' })}
          {cred('openrouter_api_key', 'OpenRouter API Key', { secret: true, placeholder: 'sk-or-…' })}
          {cred('sarvam_api_key', 'Sarvam API Key', { secret: true, placeholder: 'sk_…' })}
        </div>
        <div className="space-y-4">
          <h3 className="font-bold">Email (Resend)</h3>
          {cred('resend_api_key', 'Resend API Key', { secret: true, placeholder: 're_…' })}
          {cred('email_from', 'Email From', { placeholder: 'Company <noreply@domain.com>', help: 'The domain must be verified in Resend.' })}
        </div>
      </div>
      <div className="grid gap-6 border-t border-border p-4 sm:p-5 md:grid-cols-2">
        <div className="space-y-4">
          <h3 className="font-bold">Provider Pricing</h3>
          <div className="space-y-1.5">
            <label htmlFor={currencyId} className="text-sm font-bold">Cost Currency</label>
            <Input id={currencyId} value={getVal('cost_currency')} disabled={isPending} onChange={(e) => setVal('cost_currency', e.target.value)} placeholder="₹" />
          </div>
          {num('cost_per_call_minute', 'Plivo Cost per Minute', '0.38')}
          {num('cost_per_10k_tts_chars', 'Sarvam TTS per 10k Chars', '30.00')}
          {num('cost_per_stt_hour', 'Sarvam STT per Hour', '30.00')}
          {num('cost_per_llm_request', 'Sarvam LLM per Request', '0.02')}
        </div>
        <div className="space-y-4">
          <h3 className="font-bold">Balances</h3>
          {num('sarvam_credits', 'Sarvam Credits Left', '51.00')}
        </div>
      </div>
      <div className="flex flex-col gap-2 border-t border-border p-4 sm:flex-row sm:items-center sm:justify-end sm:p-5">
        {dirty && !isPending && <Button variant="secondary" className="w-full sm:w-auto" onClick={reset}>Discard changes</Button>}
        <Button className="w-full sm:w-auto" onClick={() => mutate(form)} loading={isPending} disabled={!dirty || isPending}>Save Secrets</Button>
      </div>
    </Card>
  )
}
