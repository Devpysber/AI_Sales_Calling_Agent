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
type EmbedConfig = { app_id: string; config_id: string; configured: boolean }

declare global {
  interface Window {
    FB?: any
    fbAsyncInit?: () => void
  }
}

// Meta's JS SDK must be injected at most once per page, even across component remounts. The script
// loads asynchronously, so a click before it (and FB.init) finishes must wait rather than silently
// no-op through FB?.login's optional chaining -- the very first "Connect with Meta" click otherwise
// did nothing, with no error, until clicked again.
let fbSdkLoadStarted = false
let fbSdkReady = false
const fbReadyCallbacks: (() => void)[] = []

function loadFacebookSdk(appId: string, onReady: () => void) {
  if (fbSdkReady) { onReady(); return }
  fbReadyCallbacks.push(onReady)
  if (fbSdkLoadStarted) return
  fbSdkLoadStarted = true
  window.fbAsyncInit = () => {
    window.FB?.init({ appId, autoLogAppEvents: true, xfbml: false, version: "v19.0" })
    fbSdkReady = true
    fbReadyCallbacks.splice(0).forEach((cb) => cb())
  }
  const script = document.createElement("script")
  script.src = "https://connect.facebook.net/en_US/sdk.js"
  script.async = true
  script.defer = true
  document.body.appendChild(script)
}

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

  const [wabaId, setWabaId] = useState("")
  const savedWabaId = String(profileQ.data?.profile?.whatsapp_business_account_id ?? "")
  useEffect(() => { if (profileQ.isSuccess) setWabaId(savedWabaId) }, [profileQ.isSuccess, savedWabaId])
  const saveWaba = useMutation({
    mutationFn: () => api(`${base}/profile`, { method: "PUT", json: { whatsapp_business_account_id: wabaId.trim() } }),
    onSuccess: () => { toast.success("WhatsApp Business Account ID saved"); qc.invalidateQueries({ queryKey: ["agent-profile", id] }) },
    onError: (e) => toast.error(e.message),
  })

  const [catalogId, setCatalogId] = useState("")
  const savedCatalogId = String(profileQ.data?.profile?.whatsapp_catalog_id ?? "")
  useEffect(() => { if (profileQ.isSuccess) setCatalogId(savedCatalogId) }, [profileQ.isSuccess, savedCatalogId])
  const saveCatalog = useMutation({
    mutationFn: () => api(`${base}/profile`, { method: "PUT", json: { whatsapp_catalog_id: catalogId.trim() } }),
    onSuccess: () => { toast.success("Product catalog ID saved"); qc.invalidateQueries({ queryKey: ["agent-profile", id] }) },
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

  const [igToken, setIgToken] = useState("")
  const igTokenSet = Boolean(profileQ.data?.profile?.instagram_business_token_set)
  const saveIgToken = useMutation({
    mutationFn: () => api(`${base}/profile`, { method: "PUT", json: { instagram_business_token: igToken } }),
    onSuccess: () => { toast.success("Instagram access token saved"); setIgToken(""); qc.invalidateQueries({ queryKey: ["agent-profile", id] }) },
    onError: (e) => toast.error(e.message),
  })

  const [iceBreakers, setIceBreakers] = useState<string[]>([])
  const savedIceBreakers = (profileQ.data?.profile?.instagram_ice_breakers as string[] | undefined) ?? []
  useEffect(() => { if (profileQ.isSuccess) setIceBreakers(savedIceBreakers) }, [profileQ.isSuccess, JSON.stringify(savedIceBreakers)])
  const iceBreakersDirty = JSON.stringify(iceBreakers.map((q) => q.trim()).filter(Boolean)) !== JSON.stringify(savedIceBreakers)

  const [igGreeting, setIgGreeting] = useState("")
  const savedIgGreeting = String(profileQ.data?.profile?.instagram_greeting ?? "")
  useEffect(() => { if (profileQ.isSuccess) setIgGreeting(savedIgGreeting) }, [profileQ.isSuccess, savedIgGreeting])

  const saveIgProfile = useMutation({
    mutationFn: () => api(`${base}/profile`, { method: "PUT", json: {
      instagram_ice_breakers: iceBreakers.map((q) => q.trim()).filter(Boolean),
      instagram_greeting: igGreeting.trim(),
    } }),
    onSuccess: () => { toast.success("Instagram ice breakers & greeting saved"); qc.invalidateQueries({ queryKey: ["agent-profile", id] }) },
    onError: (e) => toast.error(e.message),
  })

  const syncMessengerProfile = useMutation({
    mutationFn: () => api(`${base}/instagram/messenger-profile/sync`, { method: "POST" }),
    onSuccess: () => toast.success("Synced ice breakers & greeting to Instagram"),
    onError: (e) => toast.error(e.message),
  })

  const origin = window.location.origin
  const waLastAt = useLastMessageAt(base, "whatsapp")
  const igLastAt = useLastMessageAt(base, "instagram")

  // ---------------- WhatsApp Embedded Signup ("Connect with Meta") ----------------
  const embedConfigQ = useQuery({
    queryKey: ["whatsapp-embed-config"],
    queryFn: () => api<EmbedConfig>("/api/system/whatsapp-embed-config"),
  })
  const embedConfig = embedConfigQ.data

  // The popup's postMessage event and FB.login's own callback can arrive in either order; the
  // exchange only fires once both the auth code and the chosen phone_number_id/waba_id are in.
  const [embedCode, setEmbedCode] = useState<string | null>(null)
  const [embedIds, setEmbedIds] = useState<{ phoneNumberId: string; wabaId: string } | null>(null)

  const embeddedSignup = useMutation({
    mutationFn: (vars: { code: string; phone_number_id: string; waba_id: string }) =>
      api(`${base}/whatsapp/embedded-signup`, { method: "POST", json: vars }),
    onSuccess: () => {
      toast.success("Connected with Meta")
      qc.invalidateQueries({ queryKey: ["agent-profile", id] })
      setEmbedCode(null)
      setEmbedIds(null)
    },
    onError: (e) => {
      toast.error(e.message)
      setEmbedCode(null)
      setEmbedIds(null)
    },
  })

  useEffect(() => {
    if (embedCode && embedIds && !embeddedSignup.isPending) {
      embeddedSignup.mutate({ code: embedCode, phone_number_id: embedIds.phoneNumberId, waba_id: embedIds.wabaId })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedCode, embedIds])

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      const data = event.data
      if (data?.type === "WA_EMBEDDED_SIGNUP" && data?.event === "FINISH") {
        const { phone_number_id, waba_id } = data.data || {}
        if (phone_number_id && waba_id) setEmbedIds({ phoneNumberId: phone_number_id, wabaId: waba_id })
      }
    }
    window.addEventListener("message", onMessage)
    return () => window.removeEventListener("message", onMessage)
  }, [])

  function connectWithMeta() {
    if (!embedConfig?.configured) return
    loadFacebookSdk(embedConfig.app_id, () => {
      window.FB?.login(
        (response: any) => {
          const code = response?.authResponse?.code
          if (code) setEmbedCode(code)
          // No code (user cancelled the popup): do nothing, no error toast.
        },
        {
          config_id: embedConfig.config_id,
          response_type: "code",
          override_default_response_type: true,
          extras: { feature: "whatsapp_embedded_signup", sessionInfoVersion: "3" },
        },
      )
    })
  }

  return (
    <>
      <PageHeader eyebrow="Setup" title="Channels" description="Connect WhatsApp and Instagram so customers can message this agent." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="WhatsApp" action={<Badge tone={savedWaId ? "success" : "neutral"}>{savedWaId ? "Configured" : "Not configured"}</Badge>} />
          <p className="px-4 pt-3 text-xs text-muted sm:px-5">Last message received: {waLastAt ? timeAgo(waLastAt) : "Never"}</p>
          <div className="px-4 pt-3 sm:px-5">
            {embedConfig && !embedConfig.configured ? (
              <p className="text-xs text-muted">
                Embedded Signup needs a Meta App ID, App Secret and Configuration ID set under Settings → Secrets first.
              </p>
            ) : (
              <Button
                type="button"
                variant="secondary"
                disabled={!embedConfig?.configured || embeddedSignup.isPending}
                loading={embeddedSignup.isPending}
                onClick={connectWithMeta}
              >
                Connect with Meta
              </Button>
            )}
          </div>
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
          <form className="space-y-5 border-t border-border p-4 sm:p-5" onSubmit={(e) => { e.preventDefault(); if (wabaId.trim() !== savedWabaId && !saveWaba.isPending) saveWaba.mutate() }}>
            <Field label="WhatsApp Business Account ID" hint="From Meta WhatsApp Manager → account overview. Needed to create and submit message templates for approval.">
              <Input value={wabaId} onChange={(e) => setWabaId(e.target.value)} inputMode="numeric" autoComplete="off" disabled={saveWaba.isPending} />
            </Field>
            <div className="-mx-4 flex justify-end border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
              <Button type="submit" variant="primary" disabled={wabaId.trim() === savedWabaId} loading={saveWaba.isPending}>Save</Button>
            </div>
          </form>
          <form className="space-y-5 border-t border-border p-4 sm:p-5" onSubmit={(e) => { e.preventDefault(); if (catalogId.trim() !== savedCatalogId && !saveCatalog.isPending) saveCatalog.mutate() }}>
            <Field label="Product catalog ID" hint="From Meta Commerce Manager. Lets the agent show products, a product list, or the full catalog in chat (show_product / browse_catalog tools). Leave empty if this business doesn't sell via a catalog.">
              <Input value={catalogId} onChange={(e) => setCatalogId(e.target.value)} inputMode="numeric" autoComplete="off" disabled={saveCatalog.isPending} />
            </Field>
            <div className="-mx-4 flex justify-end border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
              <Button type="submit" variant="primary" disabled={catalogId.trim() === savedCatalogId} loading={saveCatalog.isPending}>Save</Button>
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
          <form
            className="space-y-5 border-t border-border p-4 sm:p-5"
            onSubmit={(e) => { e.preventDefault(); if (igToken.trim() && !saveIgToken.isPending) saveIgToken.mutate() }}
          >
            <Field
              label="Instagram access token (optional)"
              hint={<>A long-lived Instagram/Facebook Page access token for this account, from Meta's Graph API Explorer or App Dashboard. Leave empty to use the server's default token. {igTokenSet && <Badge tone="success">Saved</Badge>}</>}
            >
              <Input
                type="password"
                value={igToken}
                onChange={(e) => setIgToken(e.target.value)}
                autoComplete="off"
                disabled={saveIgToken.isPending}
                placeholder={igTokenSet ? "••••••••••••" : ""}
              />
            </Field>
            <div className="-mx-4 flex justify-end border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
              <Button type="submit" variant="primary" disabled={!igToken.trim()} loading={saveIgToken.isPending}>Save</Button>
            </div>
          </form>
          <form
            className="space-y-4 border-t border-border p-4 sm:p-5"
            onSubmit={(e) => { e.preventDefault(); if (iceBreakersDirty || igGreeting.trim() !== savedIgGreeting) saveIgProfile.mutate() }}
          >
            <Field label="Ice breakers" hint="Up to 4 clickable starter prompts shown to a first-time Instagram visitor, before they've sent any message.">
              <div className="space-y-2">
                {iceBreakers.map((q, i) => (
                  <div key={i} className="flex gap-2">
                    <Input
                      value={q}
                      maxLength={80}
                      placeholder="e.g. What are your hours?"
                      onChange={(e) => setIceBreakers((prev) => prev.map((v, j) => (j === i ? e.target.value : v)))}
                      disabled={saveIgProfile.isPending}
                    />
                    <Button type="button" variant="ghost" size="icon" aria-label="Remove ice breaker"
                      onClick={() => setIceBreakers((prev) => prev.filter((_, j) => j !== i))}>&times;</Button>
                  </div>
                ))}
                {iceBreakers.length < 4 && (
                  <Button type="button" variant="secondary" size="sm" onClick={() => setIceBreakers((prev) => [...prev, ""])}>
                    Add ice breaker
                  </Button>
                )}
              </div>
            </Field>
            <Field label="Greeting message" hint="Shown to first-time visitors before they message you. Meta supports the literal {{user_first_name}} token, which it substitutes automatically.">
              <Input value={igGreeting} onChange={(e) => setIgGreeting(e.target.value)} maxLength={150} disabled={saveIgProfile.isPending} />
            </Field>
            <div className="-mx-4 flex justify-end gap-2 border-t border-border px-4 pt-3 sm:-mx-5 sm:px-5">
              <Button type="button" variant="secondary" loading={syncMessengerProfile.isPending} onClick={() => syncMessengerProfile.mutate()}>
                Sync to Instagram
              </Button>
              <Button type="submit" variant="primary" disabled={!iceBreakersDirty && igGreeting.trim() === savedIgGreeting} loading={saveIgProfile.isPending}>
                Save
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </>
  )
}
