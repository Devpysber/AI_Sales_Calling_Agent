import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, MessageCircle, Phone, Send, Sparkles, User } from "lucide-react"
import { Fragment, useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton, Textarea } from "@/components/ui"
import { useStartCall } from "@/components/LeadSheets"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import { cn } from "@/lib/utils"
import { StatusTicks, type MessageStatus } from "@/lib/messageStatus"

type Turn = { role: "customer" | "assistant" | "human"; text: string; at?: string; by?: string; status?: MessageStatus; error?: string }

// Very small WhatsApp-style plain-text markup renderer: *bold*, **bold**, _italic_, ~strike~
function renderFormatted(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_|~[^~]+~)/g)
  return parts.map((part, i) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) return <strong key={i}>{part.slice(2, -2)}</strong>
    if (/^\*[^*]+\*$/.test(part)) return <strong key={i}>{part.slice(1, -1)}</strong>
    if (/^_[^_]+_$/.test(part)) return <em key={i}>{part.slice(1, -1)}</em>
    if (/^~[^~]+~$/.test(part)) return <s key={i}>{part.slice(1, -1)}</s>
    return <Fragment key={i}>{part}</Fragment>
  })
}
type Chat = {
  id: number; lead_id: number | null; lead_name: string | null; lead_phone: string | null; status: string
  last_text: string; last_at: string | null; message_count: number; ai_paused: boolean; awaiting_reply: boolean
}
type ChatDetail = Chat & { transcript: Turn[]; summary?: string | null }

const time = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "")

export default function WhatsApp() {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const [selected, setSelected] = useState<number | null>(null)
  const [text, setText] = useState("")
  const [search, setSearch] = useState("")
  const endRef = useRef<HTMLDivElement>(null)

  const list = useQuery({ queryKey: ["wa-chats", base], queryFn: () => api<{ items: Chat[] }>(`${base}/whatsapp/chats`), refetchInterval: 5000 })
  const items = list.data?.items ?? []
  const q = search.trim().toLowerCase()
  const filteredItems = q
    ? items.filter((c) => [c.lead_name, c.lead_phone, c.last_text].some((v) => (v || "").toLowerCase().includes(q)))
    : items
  // Arriving from a lead's "WhatsApp" quick action: jump straight to that lead's chat once it's loaded.
  useEffect(() => {
    const leadId = Number(params.get("lead"))
    if (!leadId) return
    const match = items.find((c) => c.lead_id === leadId)
    if (match) setSelected(match.id)
  }, [params, items])
  const activeId = selected ?? items[0]?.id ?? null
  const detail = useQuery({
    queryKey: ["wa-chat", base, activeId], enabled: activeId != null, refetchInterval: 5000,
    queryFn: () => api<ChatDetail>(`${base}/whatsapp/chats/${activeId}`),
  })
  const refresh = () => { qc.invalidateQueries({ queryKey: ["wa-chats", base] }); qc.invalidateQueries({ queryKey: ["wa-chat", base, activeId] }) }

  const send = useMutation({
    mutationFn: () => api(`${base}/whatsapp/chats/${activeId}/reply`, { method: "POST", json: { text: text.trim() } }),
    onSuccess: () => { setText(""); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const resume = useMutation({
    mutationFn: () => api(`${base}/whatsapp/chats/${activeId}/resume`, { method: "POST" }),
    onSuccess: () => { toast.success("AI resumed"); refresh() },
    onError: (e) => toast.error(e.message),
  })
  const startCall = useStartCall()

  const turns = detail.data?.transcript ?? []
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }) }, [turns.length, activeId])
  const chat = detail.data

  return (
    <>
      <PageHeader eyebrow="Channels" title="WhatsApp" description="Chats customers start with your WhatsApp Business number." />
      {list.isLoading ? <Skeleton className="h-96" /> : items.length === 0 ? (
        <Card>
          <EmptyState icon={<MessageCircle />} title="No WhatsApp chats yet"
            description={`Messages to your WhatsApp Business number appear here. Set the webhook URL to ${window.location.origin}/api/whatsapp/webhook in Meta.`} />
        </Card>
      ) : (
        <Card className="grid h-[70vh] min-h-96 overflow-hidden p-0 md:grid-cols-[18rem_1fr]">
          <div className="min-h-0 overflow-y-auto border-b border-border bg-white md:border-r md:border-b-0 dark:bg-[#111b21]">
            <div className="border-b border-border bg-[#f0f2f5] px-4 py-3 text-sm font-semibold text-[#41525d] dark:bg-[#202c33] dark:text-[#e9edef]">
              <span className="inline-flex items-center gap-2"><MessageCircle className="size-4 text-[#25d366]" />WhatsApp</span>
            </div>
            <div className="border-b border-border px-2 py-2">
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats"
                className="w-full rounded-full border border-border bg-[#f0f2f5] px-3 py-1.5 text-sm text-[#111b21] outline-none placeholder:text-[#667781] dark:bg-[#202c33] dark:text-[#e9edef] dark:placeholder:text-[#8696a0]" />
            </div>
            {filteredItems.map((c) => (
              <button key={c.id} onClick={() => setSelected(c.id)}
                className={cn("flex w-full items-center gap-3 border-b border-black/5 px-3 py-3 text-left hover:bg-[#f5f6f6] dark:border-white/5 dark:hover:bg-white/5",
                  c.id === activeId && "bg-[#f0f2f5] dark:bg-white/10")}>
                <span className="grid size-10 shrink-0 place-items-center rounded-full bg-[#dfe5e7] text-sm font-semibold text-[#54656f] dark:bg-[#2a3942] dark:text-[#aebac1]">
                  {(c.lead_name || c.lead_phone || "?").trim().slice(0, 1).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className={cn("truncate text-sm text-[#111b21] dark:text-[#e9edef]", c.awaiting_reply ? "font-semibold" : "font-medium")}>{c.lead_name || c.lead_phone}</span>
                    <span className="shrink-0 text-[11px] text-[#667781] dark:text-[#8696a0]">{time(c.last_at)}</span>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2">
                    <span className={cn("truncate text-xs text-[#667781] dark:text-[#8696a0]", c.awaiting_reply && "font-semibold text-[#111b21] dark:text-[#e9edef]")}>{c.last_text}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {c.awaiting_reply && <Badge tone="success" dot>Awaiting reply</Badge>}
                      {c.ai_paused && <Badge tone="warning">Paused</Badge>}
                    </span>
                  </div>
                </div>
              </button>
            ))}
          </div>
          <div className="flex min-h-0 flex-col">
            {chat && (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2 bg-[#f0f2f5] px-4 py-2.5 dark:bg-[#202c33]">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-full bg-[#dfe5e7] text-sm font-semibold text-[#54656f] dark:bg-[#2a3942] dark:text-[#aebac1]">
                      {(chat.lead_name || chat.lead_phone || "?").trim().slice(0, 1).toUpperCase()}
                    </span>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-[#111b21] dark:text-[#e9edef]">{chat.lead_name || chat.lead_phone}</div>
                      <div className="text-xs text-[#667781] dark:text-[#8696a0]">{chat.lead_phone} · {chat.message_count} messages</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    {chat.lead_id && chat.lead_phone && (
                      <button type="button" title="Call this lead" aria-label="Call this lead" disabled={startCall.isPending}
                        onClick={() => startCall.mutate(chat.lead_id!)}
                        className="grid size-8 place-items-center rounded-full text-[#54656f] transition hover:bg-black/5 disabled:opacity-50 dark:text-[#aebac1] dark:hover:bg-white/10">
                        <Phone className="size-[17px]" />
                      </button>
                    )}
                    {chat.ai_paused && <Button onClick={() => resume.mutate()} loading={resume.isPending}>Resume AI</Button>}
                    {chat.lead_id && <Link to={path(`/leads/${chat.lead_id}`)}><Button type="button"><ExternalLink className="size-4" />Lead</Button></Link>}
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-4"
                  style={{ backgroundColor: "#efeae2", backgroundImage: "radial-gradient(rgba(0,0,0,0.045) 1px, transparent 1px)", backgroundSize: "14px 14px" }}>
                  {turns.map((t, i) => (
                    <div key={i} className={cn("flex flex-col", t.role === "customer" ? "items-start" : "items-end")}>
                      {t.role !== "customer" && (
                        <div className="mb-0.5 flex items-center gap-1.5 text-[11px] font-semibold text-[#008069]">
                          <span className="grid size-3.5 place-items-center rounded-full bg-[#008069]/15 [&_svg]:size-2.5">
                            {t.role === "human" ? <User /> : <Sparkles />}
                          </span>
                          {t.role === "human" ? (t.by && t.by !== "admin" ? t.by : "You") : "AI"}
                        </div>
                      )}
                      <div className={cn("relative w-fit max-w-[min(80%,30rem)] rounded-lg px-2.5 py-[6px] text-[14.2px] leading-[19px] break-words whitespace-pre-wrap shadow-[0_1px_0.5px_rgba(0,0,0,0.13)]",
                        t.role === "customer" ? "bg-white text-[#111b21]" : "bg-[#d9fdd3] text-[#111b21]")}>
                        {renderFormatted(t.text)}
                        <span className="float-right ml-2 mt-1.5 flex items-center gap-0.5 text-[11px] text-[#667781]">
                          {time(t.at)}
                          {t.role !== "customer" && <StatusTicks status={t.status} error={t.error} />}
                        </span>
                      </div>
                    </div>
                  ))}
                  <div ref={endRef} />
                </div>
                {chat.status === "Chat" ? (
                  <form className="flex items-end gap-2 bg-[#f0f2f5] p-2.5 dark:bg-[#202c33]"
                    onSubmit={(e) => { e.preventDefault(); if (text.trim() && !send.isPending) send.mutate() }}>
                    <Textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply as a human (pauses the AI for this chat)"
                      className="rounded-full bg-white dark:bg-[#2a3942]"
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (text.trim() && !send.isPending) send.mutate() } }} />
                    <Button type="submit" variant="primary" loading={send.isPending} disabled={!text.trim()}
                      className="rounded-full bg-[#00a884] hover:bg-[#029372]"><Send className="size-4" /></Button>
                  </form>
                ) : <div className="bg-[#f0f2f5] p-3 text-center text-xs text-[#667781] dark:bg-[#202c33] dark:text-[#8696a0]">This chat is closed. A new message starts a new chat.</div>}
              </>
            )}
          </div>
        </Card>
      )}
    </>
  )
}
