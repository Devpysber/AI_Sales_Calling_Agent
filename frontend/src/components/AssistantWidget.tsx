import { useMutation } from '@tanstack/react-query'
import { Fragment, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { Button, Sheet, Spinner, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import { useAgent } from '@/lib/agent'
import { cn } from '@/lib/utils'

type AssistantAction = { type: string; label: string; lead_id: number | null; params: Record<string, unknown> }
type Turn = { role: 'user' | 'assistant'; content: string; actions?: AssistantAction[] }
type ChatResponse = { answer: string; actions: AssistantAction[]; model?: string; error?: boolean }

// Same small plain-text markup renderer used for WhatsApp/Instagram transcripts: *bold*, **bold**, _italic_, ~strike~
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

function ActionCard({ action, onDone }: { action: AssistantAction; onDone: () => void }) {
  const { base } = useAgent()
  const { mutate, isPending } = useMutation({
    mutationFn: () => api<{ ok: boolean }>(`${base}/crm/assistant/execute`, { method: 'POST', json: action }),
    onSuccess: () => { toast.success('Done: ' + action.label); onDone() },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <div className="mt-2 rounded-xl border border-border bg-surface-2 p-2.5 text-[13px]">
      <div className="font-medium text-fg">{action.label}</div>
      <div className="mt-2 flex gap-2">
        <Button size="sm" variant="primary" loading={isPending} onClick={() => mutate()}>Confirm</Button>
        <Button size="sm" variant="ghost" onClick={onDone} disabled={isPending}>Dismiss</Button>
      </div>
    </div>
  )
}

function Bubble({ turn, onDismissAction }: { turn: Turn; onDismissAction: (i: number) => void }) {
  const isUser = turn.role === 'user'
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={cn('max-w-[85%] rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed whitespace-pre-wrap',
        isUser ? 'bg-brand text-brand-fg' : 'bg-surface-2 text-fg')}>
        {renderFormatted(turn.content)}
        {turn.actions?.map((a, i) => <ActionCard key={i} action={a} onDone={() => onDismissAction(i)} />)}
      </div>
    </div>
  )
}

export default function AssistantWidget() {
  const { base } = useAgent()
  const [open, setOpen] = useState(false)
  const [turns, setTurns] = useState<Turn[]>([])
  const [message, setMessage] = useState('')
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns])

  const { mutate, isPending } = useMutation({
    mutationFn: (text: string) => {
      const history = turns.slice(-8).map((t) => ({ role: t.role, content: t.content }))
      return api<ChatResponse>(`${base}/crm/assistant/chat`, { method: 'POST', json: { message: text, history } })
    },
    onSuccess: (res) => {
      setTurns((t) => [...t, { role: 'assistant', content: res.answer, actions: res.actions }])
      if (res.error) toast.error('The assistant is temporarily unavailable.')
    },
    onError: (e: Error) => {
      setTurns((t) => [...t, { role: 'assistant', content: e.message }])
      toast.error(e.message)
    },
  })

  const send = () => {
    const text = message.trim()
    if (!text || isPending) return
    setTurns((t) => [...t, { role: 'user', content: text }])
    setMessage('')
    mutate(text)
  }

  const dismissAction = (turnIndex: number, actionIndex: number) => {
    setTurns((t) => t.map((turn, i) => i !== turnIndex || !turn.actions ? turn : { ...turn, actions: turn.actions.filter((_, ai) => ai !== actionIndex) }))
  }

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label="Open AI assistant"
        className="fixed bottom-6 right-6 z-40 grid size-14 place-items-center rounded-full bg-brand text-brand-fg shadow-glow transition hover:brightness-110 active:scale-95">
        <Sparkles className="size-5" />
      </button>

      <Sheet open={open} onClose={() => setOpen(false)} title="CRM Assistant" description="Ask about leads, tasks and deals in this workspace." width="max-w-md"
        footer={
          <form className="flex w-full gap-2" onSubmit={(e) => { e.preventDefault(); send() }}>
            <Textarea rows={1} value={message} placeholder="Ask a question or tell me to do something…"
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              className="min-h-10 resize-none py-2" />
            <Button type="submit" loading={isPending} disabled={!message.trim()}>Send</Button>
          </form>
        }>
        <div ref={listRef} className="flex h-full flex-col gap-3 overflow-y-auto">
          {turns.length === 0 && <div className="text-sm text-muted">Ask things like "which leads have no follow-up?" or "create a task to call Aditi tomorrow".</div>}
          {turns.map((turn, i) => (
            <Bubble key={i} turn={turn} onDismissAction={(ai) => dismissAction(i, ai)} />
          ))}
          {isPending && <div className="flex justify-start"><div className="rounded-2xl bg-surface-2 px-3.5 py-2.5"><Spinner /></div></div>}
        </div>
      </Sheet>
    </>
  )
}
