import { AlertTriangle, Check, CheckCheck } from "lucide-react"

// Mirrors the backend Message.status column (app/models/channel.py): received | queued | sent |
// delivered | read | failed. Only the outbound-relevant values are ever surfaced on a Turn.
export type MessageStatus = "sent" | "delivered" | "read" | "failed" | "queued" | "received"

/** WhatsApp-style outbound tick, driven by the real per-message status instead of an assumed "read". */
export function StatusTicks({ status, error }: { status?: MessageStatus; error?: string }) {
  if (status === "failed")
    return (
      <span title={error || "Failed to send"}>
        <AlertTriangle className="size-[13px] text-red-500" aria-label={error || "Failed to send"} />
      </span>
    )
  if (status === "read") return <CheckCheck className="size-[14px] text-[#53bdeb]" />
  if (status === "delivered") return <CheckCheck className="size-[14px] text-[#667781]" />
  // "sent", or no status known yet (e.g. older rows / in-flight sends): single grey check.
  return <Check className="size-[14px] text-[#667781]" />
}
