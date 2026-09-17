import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { Key } from 'lucide-react'
import { api } from '@/lib/api'
import { Button, Card, CardHeader, Input } from './ui'

export default function SecretsForm() {
  const queryClient = useQueryClient()
  const { data: secrets, isLoading } = useQuery({
    queryKey: ['system', 'secrets'],
    queryFn: () => api<Record<string, string>>('/api/system/secrets'),
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
    onError: (e) => toast.error(String(e))
  })

  if (isLoading) return null

  const getVal = (key: string) => (form[key] !== undefined ? form[key] : (secrets?.[key] || ''))
  const setVal = (key: string, val: string) => setForm((prev) => ({ ...prev, [key]: val }))

  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Key className="size-4" />Secrets & Costs</span>} description="Manage credentials securely in the database. Leave fields alone (or as ********) to keep existing values. Emptying a field removes the override. Update cost pricing or credits manually." />
      <div className="grid gap-6 p-5 sm:grid-cols-2">
        <div className="space-y-4">
          <h3 className="font-bold">Telephony & AI</h3>
          <div className="space-y-1.5"><label className="text-sm font-bold">Plivo Auth ID</label><Input value={getVal('plivo_auth_id')} onChange={(e) => setVal('plivo_auth_id', e.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Plivo Auth Token</label><Input type="password" value={getVal('plivo_auth_token')} onChange={(e) => setVal('plivo_auth_token', e.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Plivo Phone Number</label><Input value={getVal('plivo_phone_number')} onChange={(e) => setVal('plivo_phone_number', e.target.value)} placeholder="+1234567890" /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">OpenRouter API Key</label><Input type="password" value={getVal('openrouter_api_key')} onChange={(e) => setVal('openrouter_api_key', e.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Sarvam API Key</label><Input type="password" value={getVal('sarvam_api_key')} onChange={(e) => setVal('sarvam_api_key', e.target.value)} /></div>
        </div>
        <div className="space-y-4">
          <h3 className="font-bold">Email (Resend)</h3>
          <div className="space-y-1.5"><label className="text-sm font-bold">Resend API Key</label><Input type="password" value={getVal('resend_api_key')} onChange={(e) => setVal('resend_api_key', e.target.value)} /></div>
          <div className="space-y-1.5"><label className="text-sm font-bold">Email From</label><Input value={getVal('email_from')} onChange={(e) => setVal('email_from', e.target.value)} placeholder="Company <noreply@domain.com>" /></div>
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
