import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { toast } from "sonner"
import { Badge, Button, Card, CardHeader, Field, Input, PageHeader } from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import { timeAgo } from "@/lib/utils"
import type { AgentProfile } from "@/lib/types"

type ProfileResponse = { profile: AgentProfile & Record<string, unknown> }
type ChatSummary = { last_at: string | null }

// Derives "most recent message" from the existing chats list rather than adding a new endpoint.
function useLastMessageAt(base: string, channel: "whatsapp" | "instagram") {
  const q = useQuery({
    queryKey: ["channel-last-msg", base, channel],
    queryFn: () => api<{ items: ChatSummary[] }>(`${base}/${channel}/chats`),
    refetchInterval: 30000,
  })
  const items = q.data?.items ?? []
  const latest = items.reduce<string | null>((max, it) => (it.last_at && (!max || it.last_at > max) ? it.last_at : max), null)
  return latest
}

// No status-check endpoint exists against Meta today (only the signed webhook handlers in
// app/api/whatsapp.py / app/api/instagram.py). "Connected" here just means the per-agent id is set.
// No outbound test-send endpoint exists either (whatsapp_service.send / _send require an existing
// chat_id + lead); building one is out of scope for this frontend-only day, so that mini-form is skipped.
export default function Channels() {
  const { base, id } = useAgent()
  const qc = useQueryClient()
  const profileQ = useQuery({ queryKey: ["agent-profile", id], queryFn: () => api<ProfileResponse>(`${base}/profile`) })

  const [waId, setWaId] = useState("")
  const savedWaId = String(profileQ.data?.profile?.whatsapp_phone_number_id ?? "")
  useEffect(() => { if (profileQ.isSuccess) setWaId(savedWaId) }, [profileQ.isSuccess, savedWaId])
  const saveWa = useMutation({
    mutationFn: () => api(`${base}/profile`, { method: "PUT", json: { whatsapp_phone_number_id: waId.trim() } }),
    onSuccess: () => { toast.success("WhatsApp number saved"); qc.invalidateQueries({ queryKey: ["agent-profile", id] }) },
    onError: (e) => toast.error(e.message),
  })

  const [igId, setIgId] = useState("")
  const savedIgId = String(profileQ.data?.profile?.instagram_account_id ?? "")
  useEffect(() => { if (profileQ.isSuccess) setIgId(savedIgId) }, [profileQ.isSuccess, savedIgId])
  const saveIg = useMutation({
    mutationFn: () => api(`${base}/profile`, { method: "PUT", json: { instagram_account_id: igId.trim() } }),
    onSuccess: () => { toast.success("Instagram account ID saved"); qc.invalidateQueries({ queryKey: ["agent-profile", id] }) },
    onError: (e) => toast.error(e.message),
  })

  const origin = window.location.origin
  const waLastAt = useLastMessageAt(base, "whatsapp")
  const igLastAt = useLastMessageAt(base, "instagram")

  return (
    <>
      <PageHeader eyebrow="Setup" title="Channels" description="Connect WhatsApp and Instagram so customers can message this agent." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="WhatsApp" action={<Badge tone={savedWaId ? "success" : "neutral"}>{savedWaId ? "Configured" : "Not configured"}</Badge>} />
          <p className="px-4 pt-3 text-xs text-muted sm:px-5">Last message received: {waLastAt ? timeAgo(waLastAt) : "Never"}</p>
          <form className="space-y-5 p-4 sm:p-5" onSubmit={(e) => { e.preventDefault(); if (waId.trim() !== savedWaId && !saveWa.isPending) saveWa.mutate() }}>
            <Field label="WhatsApp phone number ID" hint="From Meta WhatsApp Manager → API setup. Leave empty to use the server default.">
              <Input value={waId} onChange={(e) => setWaId(e.target.value)} inputMode="numeric" autoComplete="off" disabled={saveWa.isPending} />
            </Field>
            <Field label="Webhook URL" hint="Paste into Meta's WhatsApp webhook configuration.">
              <Input readOnly value={`${origin}/api/whatsapp/webhook`} onFocus={(e) => e.currentTarget.select()} />
            </Field>
            <div className="-mx-4 flex justify-end border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
              <Button type="submit" variant="primary" disabled={waId.trim() === savedWaId} loading={saveWa.isPending}>Save</Button>
            </div>
          </form>
        </Card>

        <Card>
          <CardHeader title="Instagram" action={<Badge tone={savedIgId ? "success" : "neutral"}>{savedIgId ? "Configured" : "Not configured"}</Badge>} />
          <p className="px-4 pt-3 text-xs text-muted sm:px-5">Last message received: {igLastAt ? timeAgo(igLastAt) : "Never"}</p>
          <form className="space-y-5 p-4 sm:p-5" onSubmit={(e) => { e.preventDefault(); if (igId.trim() !== savedIgId && !saveIg.isPending) saveIg.mutate() }}>
            <Field label="Instagram account ID" hint="The IG-connected Page/account ID for this agent. Incoming messages are routed to this agent by matching it against the sender's Instagram account.">
              <Input value={igId} onChange={(e) => setIgId(e.target.value)} inputMode="numeric" autoComplete="off" disabled={saveIg.isPending} />
            </Field>
            <Field label="Webhook URL" hint="Paste into Meta's Instagram webhook configuration.">
              <Input readOnly value={`${origin}/api/instagram/webhook`} onFocus={(e) => e.currentTarget.select()} />
            </Field>
            <div className="-mx-4 flex justify-end border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
              <Button type="submit" variant="primary" disabled={igId.trim() === savedIgId} loading={saveIg.isPending}>Save</Button>
            </div>
          </form>
        </Card>
      </div>
    </>
  )
}
