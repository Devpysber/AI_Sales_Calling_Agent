import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, MessageCircle, Send, Sparkles, User } from "lucide-react"
import { Fragment, useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton, Textarea } from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import { cn } from "@/lib/utils"

type Turn = { role: "customer" | "assistant" | "human"; text: string; at?: string; by?: string }

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
  last_text: string; last_at: string | null; message_count: number; ai_paused: boolean
}
type ChatDetail = Chat & { transcript: Turn[]; summary?: string | null }

const time = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "")

export default function WhatsApp() {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const [text, setText] = useState("")
  const endRef = useRef<HTMLDivElement>(null)

  const list = useQuery({ queryKey: ["wa-chats", base], queryFn: () => api<{ items: Chat[] }>(`${base}/whatsapp/chats`), refetchInterval: 5000 })
  const items = list.data?.items ?? []
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
        <Card className="grid h-[70vh] min-h-96 overflow-hidden md:grid-cols-[18rem_1fr]">
          <div className="min-h-0 overflow-y-auto border-b border-border md:border-r md:border-b-0">
            {items.map((c) => (
              <button key={c.id} onClick={() => setSelected(c.id)}
                className={cn("block w-full border-b border-border px-4 py-3 text-left hover:bg-surface-2/60", c.id === activeId && "bg-surface-2")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold">{c.lead_name || c.lead_phone}</span>
                  <span className="shrink-0 text-xs text-muted">{time(c.last_at)}</span>
                </div>
                <div className="mt-0.5 truncate text-xs text-muted">{c.last_text}</div>
                <div className="mt-1 flex gap-1.5">
                  <Badge tone={c.status === "Chat" ? "success" : "neutral"}>{c.status === "Chat" ? "Open" : "Closed"}</Badge>
                  {c.ai_paused && <Badge tone="warning">AI paused</Badge>}
                </div>
              </button>
            ))}
          </div>
          <div className="flex min-h-0 flex-col">
            {chat && (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold">{chat.lead_name || chat.lead_phone}</div>
                    <div className="text-xs text-muted">{chat.lead_phone} · {chat.message_count} messages</div>
                  </div>
                  <div className="flex gap-2">
                    {chat.ai_paused && <Button onClick={() => resume.mutate()} loading={resume.isPending}>Resume AI</Button>}
                    {chat.lead_id && <Link to={path(`/leads/${chat.lead_id}`)}><Button type="button"><ExternalLink className="size-4" />Lead</Button></Link>}
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4">
                  {turns.map((t, i) => (
                    <div key={i} className={cn("flex flex-col", t.role === "customer" ? "items-start" : "items-end")}>
                      {t.role !== "customer" && (
                        <div className={cn("mb-1 flex items-center gap-1.5 text-xs font-semibold",
                          t.role === "human" ? "text-success" : "text-brand")}>
                          <span className={cn("grid size-4 place-items-center rounded-full [&_svg]:size-2.5",
                            t.role === "human" ? "bg-success/15" : "bg-brand-soft")}>
                            {t.role === "human" ? <User /> : <Sparkles />}
                          </span>
                          {t.role === "human" ? (t.by && t.by !== "admin" ? t.by : "You") : "AI"}
                        </div>
                      )}
                      <div className={cn("w-fit max-w-[min(80%,32rem)] rounded-2xl px-3 py-2 text-sm break-words whitespace-pre-wrap",
                        t.role === "customer" ? "bg-surface-2" : t.role === "human" ? "bg-success/15 text-fg" : "bg-brand-soft text-fg")}>
                        {renderFormatted(t.text)}
                        <div className="mt-0.5 text-right text-[10px] text-muted">{time(t.at)}</div>
                      </div>
                    </div>
                  ))}
                  <div ref={endRef} />
                </div>
                {chat.status === "Chat" ? (
                  <form className="flex items-end gap-2 border-t border-border p-3"
                    onSubmit={(e) => { e.preventDefault(); if (text.trim() && !send.isPending) send.mutate() }}>
                    <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply as a human (pauses the AI for this chat)"
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (text.trim() && !send.isPending) send.mutate() } }} />
                    <Button type="submit" variant="primary" loading={send.isPending} disabled={!text.trim()}><Send className="size-4" /></Button>
                  </form>
                ) : <div className="border-t border-border p-3 text-center text-xs text-muted">This chat is closed. A new message starts a new chat.</div>}
              </>
            )}
          </div>
        </Card>
      )}
    </>
  )
}
