import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { Check, Eye, EyeOff, Key, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Card, CardHeader, Input } from './ui'

const MASK = '********'

type Secrets = Record<string, string> & { _hints?: Record<string, string> }

/**
 * A stored credential is never sent back, so the field shows the mask and a hint of what is saved —
 * without the hint a wrong value (an email address in the Plivo Auth ID) is invisible from here.
 */
function Credential({ label, value, hint, secret, placeholder, help, onChange }: {
  label: string
  value: string
  hint?: string
  secret?: boolean
  placeholder?: string
  help?: string
  onChange: (v: string) => void
}) {
  const [reveal, setReveal] = useState(false)
  const saved = value === MASK
  const editing = value !== MASK && value !== ''

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-sm font-bold">{label}</label>
        {saved && <span className="flex items-center gap-1 text-[11px] font-semibold text-success"><Check className="size-3" />Saved</span>}
      </div>
      <div className="flex items-center gap-1.5">
        <Input
          className="flex-1"
          type={secret && !reveal ? 'password' : 'text'}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => { if (saved) onChange('') }}   // typing over the mask should replace it, not append
        />
        {secret && editing && (
          <button type="button" onClick={() => setReveal((r) => !r)} title={reveal ? 'Hide' : 'Show'}
            className="grid size-9 shrink-0 place-items-center rounded-lg border border-border text-fg-2 transition hover:border-border-strong hover:text-fg">
            {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        )}
        {saved && (
          <button type="button" onClick={() => onChange('')} title="Remove this value"
            className="grid size-9 shrink-0 place-items-center rounded-lg border border-border text-fg-2 transition hover:border-danger hover:text-danger">
            <Trash2 className="size-4" />
          </button>
        )}
      </div>
      {saved && hint && <p className="text-[11.5px] text-muted">Currently saved: <span className="font-mono">{hint}</span></p>}
      {help && !saved && <p className="text-[11.5px] text-muted">{help}</p>}
    </div>
  )
}

export default function SecretsForm() {
  const queryClient = useQueryClient()
  const { data: secrets, isLoading } = useQuery({
    queryKey: ['system', 'secrets'],
    queryFn: () => api<Secrets>('/api/system/secrets'),
  })

  const [form, setForm] = useState<Record<string, string>>({})

  const { mutate, isPending } = useMutation({
    // `json` is what sets Content-Type: application/json — a raw `body` string reaches FastAPI
    // without it and the request is rejected before the handler runs.
    mutationFn: (body: Record<string, string>) => api('/api/system/secrets', { method: 'POST', json: body }),
    onSuccess: () => {
      toast.success('Secrets saved securely')
      setForm({})
      queryClient.invalidateQueries({ queryKey: ['system'] })
    },
    // The server rejects a malformed credential with the reason; show that, not a generic failure.
    onError: (e) => toast.error('Could not save', { description: (e as Error).message }),
  })

  if (isLoading) return null

  const hints = secrets?._hints ?? {}
  const getVal = (key: string) => (form[key] !== undefined ? form[key] : (secrets?.[key] || ''))
  const setVal = (key: string, val: string) => setForm((prev) => ({ ...prev, [key]: val }))
  const cred = (key: string, label: string, opts: { secret?: boolean; placeholder?: string; help?: string } = {}) => (
    <Credential label={label} value={getVal(key)} hint={hints[key]} onChange={(v) => setVal(key, v)} {...opts} />
  )

  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Key className="size-4" />Secrets & Costs</span>} description="Manage credentials securely in the database. A saved value shows as ******** with a hint of what is stored — click the field to replace it, or the bin to remove it. Update cost pricing or credits manually." />
      <div className="grid gap-6 p-5 sm:grid-cols-2">
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
      <div className="border-t border-border p-5 grid gap-6 sm:grid-cols-2">
        <div className="space-y-4">
          <h3 className="font-bold">Provider Pricing</h3>
          <div className="space-y-1.5"><label className="text-sm font-bold">Cost Currency</label><Input value={getVal('cost_currency')} onChange={(e) => setVal('cost_currency', e.target.value)} placeholder="₹" /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Plivo Cost per Minute</label><Input type="number" step="0.01" value={getVal('cost_per_call_minute')} onChange={(e) => setVal('cost_per_call_minute', e.target.value)} placeholder="0.38" /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Sarvam TTS per 10k Chars</label><Input type="number" step="0.01" value={getVal('cost_per_10k_tts_chars')} onChange={(e) => setVal('cost_per_10k_tts_chars', e.target.value)} placeholder="30.00" /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Sarvam STT per Hour</label><Input type="number" step="0.01" value={getVal('cost_per_stt_hour')} onChange={(e) => setVal('cost_per_stt_hour', e.target.value)} placeholder="30.00" /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Sarvam LLM per Request</label><Input type="number" step="0.01" value={getVal('cost_per_llm_request')} onChange={(e) => setVal('cost_per_llm_request', e.target.value)} placeholder="0.02" /></div>
        </div>
        <div className="space-y-4">
          <h3 className="font-bold">Balances</h3>
          <div className="space-y-1.5"><label className="text-sm font-bold">Sarvam Credits Left</label><Input type="number" step="0.01" value={getVal('sarvam_credits')} onChange={(e) => setVal('sarvam_credits', e.target.value)} placeholder="51.00" /></div>
        </div>
      </div>
      <div className="border-t border-border p-5 flex justify-end">
        <Button onClick={() => mutate(form)} loading={isPending} disabled={Object.keys(form).length === 0}>Save Secrets</Button>
      </div>
    </Card>
  )
}
