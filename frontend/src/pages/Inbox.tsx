import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, Camera as InstagramIcon, Inbox as InboxIcon, MessageCircle } from "lucide-react"
import { useNavigate } from "react-router-dom"
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton } from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import { cn, timeAgo } from "@/lib/utils"

type Chat = {
  id: number; lead_id: number | null; lead_name: string | null; lead_phone: string | null; status: string
  last_text: string; last_at: string | null; message_count: number; ai_paused: boolean; awaiting_reply: boolean
}
type Channel = "whatsapp" | "instagram"
type Row = Chat & { channel: Channel }

export default function Inbox() {
  const { base, path } = useAgent()
  const navigate = useNavigate()

  const wa = useQuery({ queryKey: ["inbox-wa-chats", base], queryFn: () => api<{ items: Chat[] }>(`${base}/whatsapp/chats`), refetchInterval: 10000 })
  const ig = useQuery({ queryKey: ["inbox-ig-chats", base], queryFn: () => api<{ items: Chat[] }>(`${base}/instagram/chats`), refetchInterval: 10000 })

  const isLoading = wa.isLoading || ig.isLoading
  const isError = wa.isError || ig.isError
  const isFetching = wa.isFetching || ig.isFetching
  const error = (wa.error ?? ig.error) as Error | undefined

  const rows: Row[] = [
    ...(wa.data?.items ?? []).map((c) => ({ ...c, channel: "whatsapp" as const })),
    ...(ig.data?.items ?? []).map((c) => ({ ...c, channel: "instagram" as const })),
  ].sort((a, b) => {
    if (a.awaiting_reply !== b.awaiting_reply) return a.awaiting_reply ? -1 : 1
    return (b.last_at ? new Date(b.last_at).getTime() : 0) - (a.last_at ? new Date(a.last_at).getTime() : 0)
  })

  const open = (row: Row) => navigate(path(row.channel === "whatsapp" ? `/whatsapp-cloud?lead=${row.lead_id}` : `/instagram?lead=${row.lead_id}`))

  return (
    <>
      <PageHeader eyebrow="Channels" title="Inbox" description="Every WhatsApp and Instagram conversation that needs attention, in one place." />
      {isError ? (
        <Card><EmptyState icon={<AlertTriangle />} title="Couldn't load the inbox" description={error?.message ?? "Something went wrong while fetching conversations."}
          action={<Button onClick={() => { wa.refetch(); ig.refetch() }} loading={isFetching}>Try again</Button>} /></Card>
      ) : isLoading ? (
        <Skeleton className="h-96" />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState icon={<InboxIcon />} title="No conversations yet" description="WhatsApp and Instagram chats will show up here once customers start messaging." />
        </Card>
      ) : (
        <Card className="divide-y divide-border overflow-hidden">
          {rows.map((row) => (
            <button key={`${row.channel}-${row.id}`} type="button" onClick={() => open(row)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-surface-2/70">
              <span className="grid size-9 shrink-0 place-items-center rounded-full bg-surface-2 text-muted">
                {row.channel === "whatsapp" ? <MessageCircle className="size-4 text-[#25d366]" /> : <InstagramIcon className="size-4 text-[#e1306c]" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className={cn("truncate text-sm", row.awaiting_reply ? "font-semibold" : "font-medium")}>{row.lead_name || row.lead_phone || "Unknown"}</span>
                  <span className="shrink-0 text-xs text-muted">{timeAgo(row.last_at)}</span>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2">
                  <span className="truncate text-xs text-muted">{row.last_text}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {row.awaiting_reply && <Badge tone="success" dot>Awaiting reply</Badge>}
                    {row.ai_paused && <Badge tone="neutral">AI paused</Badge>}
                  </span>
                </div>
              </div>
            </button>
          ))}
        </Card>
      )}
    </>
  )
}
