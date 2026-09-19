/**
 * App-wide incoming-call alert.
 *
 * Polls the light /api/agents/live feed on every page. A new inbound call slides in bottom-right as a
 * ringing card: rings travelling out of the caller's avatar while it rings, the agent's orb and a
 * live waveform once someone answers, and who has it (the AI or the team). After a few seconds, or
 * on Dismiss, it folds into a pill that stays for as long as any call is live. Outbound calls
 * (auto-dial, retries) only update the pill: popping a card for each would drown out the inbound
 * ones people actually need to notice.
 */

import { useQuery } from '@tanstack/react-query'
import { Bot, Headphones, PhoneForwarded, PhoneIncoming, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CallTimer } from '@/components/Live'
import { VoiceOrb, Waveform } from '@/components/VoiceViz'
import { api } from '@/lib/api'
import type { Call } from '@/lib/types'
import { callParty, cn } from '@/lib/utils'

type LiveCall = Call & { agent_name: string | null }

const CARD_MS = 14_000          // a card folds into the pill after this long
const FRESH_MS = 90_000         // on page load, only calls younger than this pop a card

export const PREVIEW_EVENT = 'incoming:preview'

/**
 * A local demo call for the Ctrl K "Preview incoming call" command: rings for 4 s, is answered
 * by the AI, ends after 25 s. Never sent to the server and never stored — it only exercises
 * this banner, which otherwise needs a real call (and a working tunnel) to be seen at all.
 */
function previewCall(agentId: number, agentName: string | null, phase: 'ringing' | 'live'): LiveCall {
  const now = new Date().toISOString()
  return {
    id: -1, agent_id: agentId, agent_name: agentName, direction: 'inbound', trigger: 'inbound',
    status: phase === 'ringing' ? 'Ringing' : 'In Progress', from_number: '+91 98xxx xxx21 (preview)',
    to_number: null, lead_name: 'Preview caller', created_at: now, answered_at: phase === 'live' ? now : null,
  } as unknown as LiveCall
}

const ringing = (c: LiveCall) => c.status === 'Queued' || c.status === 'Ringing'
const toTeam = (c: LiveCall) => c.trigger === 'forwarded' || !!c.transferred_to

export default function IncomingCall() {
  const navigate = useNavigate()
  const { data } = useQuery({
    queryKey: ['live-calls'],
    queryFn: () => api<{ live_calls: LiveCall[] }>('/api/agents/live'),
    refetchInterval: 3000,
    refetchIntervalInBackground: true,
    retry: false,
  })
  const [preview, setPreview] = useState<LiveCall | null>(null)
  useEffect(() => {
    const timers: number[] = []
    const onPreview = (e: Event) => {
      const { agentId, agentName } = (e as CustomEvent<{ agentId: number; agentName: string | null }>).detail
      timers.splice(0).forEach(window.clearTimeout)
      setPreview(previewCall(agentId, agentName, 'ringing'))
      timers.push(window.setTimeout(() => setPreview(previewCall(agentId, agentName, 'live')), 4000))
      timers.push(window.setTimeout(() => setPreview(null), 25_000))
    }
    window.addEventListener(PREVIEW_EVENT, onPreview)
    return () => { window.removeEventListener(PREVIEW_EVENT, onPreview); timers.forEach(window.clearTimeout) }
  }, [])
  const calls = useMemo(() => [...(preview ? [preview] : []), ...(data?.live_calls ?? [])], [data, preview])

  const seen = useRef<Set<number> | null>(null)
  const [cards, setCards] = useState<number[]>([])      // call ids showing as full cards
  const [open, setOpen] = useState(false)                // pill expanded into a list

  // A call we have not seen before becomes a card, if it is inbound and still fresh.
  useEffect(() => {
    if (!data && !preview) return
    const known = seen.current
    const now = Date.now()
    const arrived = calls.filter((c) => c.direction === 'inbound' && !(known?.has(c.id))
      && (known !== null || ringing(c) || now - Date.parse(c.created_at) < FRESH_MS))
    seen.current = new Set([...(known ?? []), ...calls.map((c) => c.id)])
    if (arrived.length) setCards((ids) => Array.from(new Set([...arrived.map((c) => c.id), ...ids])).slice(0, 3))
  }, [data, preview, calls])

  // Cards for calls that have ended go away on their own.
  const liveIds = useMemo(() => new Set(calls.map((c) => c.id)), [calls])
  const shown = cards.filter((id) => liveIds.has(id))

  // The expanded list closes when the last call ends or a card takes over, so the pill never
  // reappears already open the next time a call goes live.
  useEffect(() => {
    if (calls.length === 0 || shown.length > 0) setOpen(false)
  }, [calls.length, shown.length])

  // The tab title rings too, so a call is noticed from another tab.
  const ringingNow = calls.some((c) => c.direction === 'inbound' && ringing(c))
  // AppShell also writes document.title (route change, agents refetch), so the base title is
  // re-read before every flip instead of captured once: whatever is there that is not our ring
  // is the current page title, and that is what gets restored when the ringing stops.
  useEffect(() => {
    if (!ringingNow) return
    const RING = '📞 Incoming call'
    let base = document.title === RING ? '' : document.title
    let flip = false
    const id = window.setInterval(() => {
      if (document.title !== RING) base = document.title
      flip = !flip
      document.title = flip ? RING : base || document.title
    }, 900)
    return () => {
      window.clearInterval(id)
      if (document.title === RING && base) document.title = base
    }
  }, [ringingNow])

  const dismiss = (id: number) => setCards((ids) => ids.filter((x) => x !== id))
  const listen = (c: LiveCall) => {
    dismiss(c.id)
    if (c.id === -1) return                              // the preview never reaches the server: nothing to listen to
    navigate(c.agent_id != null ? `/a/${c.agent_id}/calls?status=active` : '/')
  }
  // A preview is re-announced each time the command runs, even though its id is always -1. The id
  // stays in `seen` on purpose: the arrival effect above re-runs on every poll, and removing it
  // there would make a dismissed preview pop straight back on the next tick.
  useEffect(() => {
    if (preview?.status === 'Ringing') {
      seen.current = new Set([...(seen.current ?? []), -1])
      setCards((ids) => [-1, ...ids.filter((x) => x !== -1)])
    }
  }, [preview])

  // Sonner's toasts land in the same corner with a higher z-index; while one is up the banner
  // lifts out from under it so Listen/Later stay reachable.
  const toastUp = useToastUp()

  return (
    <div className={cn('pointer-events-none fixed right-4 z-[70] flex w-[min(380px,calc(100vw-2rem))] max-w-full flex-col items-end gap-3 transition-[bottom] duration-200',
      toastUp ? 'bottom-[max(6rem,calc(env(safe-area-inset-bottom)+5rem))]' : 'bottom-[max(1rem,env(safe-area-inset-bottom))]')}>
      {shown.map((id) => {
        const c = calls.find((x) => x.id === id)
        return c ? <CallCard key={id} call={c} onDismiss={() => dismiss(id)} onListen={() => listen(c)} /> : null
      })}

      {calls.length > 0 && shown.length === 0 && (
        <div className="pointer-events-auto flex w-full min-w-0 flex-col items-end gap-2">
          {open && (
            <div className="animate-pop-in w-full min-w-0 overflow-hidden rounded-2xl border border-border bg-elevated shadow-pop">
              {calls.slice(0, 6).map((c) => (
                <button key={c.id} type="button" onClick={() => listen(c)} aria-label={c.id === -1 ? `${callParty(c)} (preview)` : undefined}
                  className="flex min-h-11 w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-surface-2">
                  <VoiceOrb state={ringing(c) ? 'listening' : 'live'} size={28} />
                  <span className="min-w-0 flex-1 leading-tight">
                    <span className="block truncate text-[13px] font-bold">{callParty(c)}</span>
                    <span className="block truncate text-[11px] text-muted">{c.direction === 'inbound' ? 'Inbound' : 'Outbound'} · {c.agent_name ?? 'agent'}</span>
                  </span>
                  <span className="shrink-0 text-[11px] font-semibold text-muted tabular-nums">
                    {ringing(c) ? 'Ringing' : <CallTimer since={c.answered_at ?? c.created_at} />}
                  </span>
                </button>
              ))}
            </div>
          )}
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
            className="beam beam-on beam-live animate-pop-in inline-flex min-h-11 items-center gap-2.5 rounded-full border border-success/40 bg-elevated py-1.5 pr-4 pl-1.5 text-[13px] font-bold shadow-pop">
            <VoiceOrb state="live" size={30} />
            <span>{calls.length} live call{calls.length > 1 ? 's' : ''}</span>
            <Waveform bars={5} className="h-3.5 text-success" />
          </button>
        </div>
      )}
    </div>
  )
}

/** True while a sonner toast is on screen (sonner marks each one with data-sonner-toast). */
function useToastUp() {
  const [up, setUp] = useState(false)
  useEffect(() => {
    const check = () => setUp(document.querySelector('[data-sonner-toast]') !== null)
    check()
    const mo = new MutationObserver(check)
    mo.observe(document.body, { childList: true, subtree: true })
    return () => mo.disconnect()
  }, [])
  return up
}

function CallCard({ call, onDismiss, onListen }: { call: LiveCall; onDismiss: () => void; onListen: () => void }) {
  const isRinging = ringing(call)
  const team = toTeam(call)

  // Folds away on its own; hovering with a mouse holds it open. Touch is excluded: a tap fires an
  // emulated mouseenter with no mouseleave, which would pin the card open until the call ended.
  // onDismiss is a new function on every 3 s poll; keeping it in a ref stops each poll restarting
  // the countdown, which would keep the card up for as long as the call lasted.
  const [hover, setHover] = useState(false)
  const [round, setRound] = useState(0)                  // bumps when the countdown restarts
  const dismissRef = useRef(onDismiss)
  useEffect(() => { dismissRef.current = onDismiss })
  useEffect(() => {
    if (hover) return
    setRound((r) => r + 1)
    const id = window.setTimeout(() => dismissRef.current(), CARD_MS)
    return () => window.clearTimeout(id)
  }, [hover])

  // Live regions announce changes, not what they mount with: the text is filled in a tick after
  // mount so the first 'Incoming call from …' is read too, not only the later ringing -> answered flip.
  const announcement = (isRinging ? `Incoming call from ${callParty(call)}` : team ? `${callParty(call)} is with your team` : `AI answered ${callParty(call)}`)
    + (call.id === -1 ? ' (preview, nothing is saved)' : '')
  const [announced, setAnnounced] = useState('')
  useEffect(() => {
    const id = window.setTimeout(() => setAnnounced(announcement), 50)
    return () => window.clearTimeout(id)
  }, [announcement])

  return (
    <div onPointerEnter={(e) => { if (e.pointerType === 'mouse') setHover(true) }} onPointerLeave={() => setHover(false)}
      className={cn('incoming-card pointer-events-auto relative w-full min-w-0 overflow-hidden rounded-3xl border bg-elevated shadow-pop',
        'beam beam-on', isRinging ? 'border-info/40' : 'beam-live border-success/40')}>
      {/* Announced once per state change; the ticking timer below is aria-hidden so it does not re-read every second. */}
      <span role="status" aria-live="polite" className="sr-only">{announced}</span>
      {/* Colour wash behind the card: blue while ringing, green once answered. */}
      <span className={cn('pointer-events-none absolute -top-16 -right-10 size-48 rounded-full blur-3xl',
        isRinging ? 'bg-info/25' : 'bg-success/25')} aria-hidden />

      <div className="relative flex items-start gap-3.5 p-4">
        <div className="relative grid size-14 shrink-0 place-items-center">
          {isRinging ? <>
            <span className="ring-wave absolute inset-0 rounded-full border-2 border-info/60" />
            <span className="ring-wave absolute inset-0 rounded-full border-2 border-info/60" style={{ animationDelay: '.6s' }} />
            <span className="ring-wave absolute inset-0 rounded-full border-2 border-info/60" style={{ animationDelay: '1.2s' }} />
            <span className="phone-shake relative grid size-12 place-items-center rounded-full bg-info text-white shadow-lg">
              <PhoneIncoming className="size-5" />
            </span>
          </> : <VoiceOrb state="live" size={56} />}
        </div>

        <div className="min-w-0 flex-1">
          <div className={cn('flex items-center gap-1.5 text-[11px] font-extrabold tracking-wider uppercase', isRinging ? 'text-info' : 'text-success')}>
            <span className={cn('size-1.5 rounded-full animate-pulse-dot', isRinging ? 'bg-info' : 'bg-success')} />
            {isRinging ? 'Incoming call' : team ? 'With your team' : 'AI on the line'}
          </div>
          <div className="mt-0.5 truncate text-lg leading-tight font-extrabold">{callParty(call)}</div>
          <div className="truncate text-xs text-muted">{call.from_number ?? 'Unknown number'} → {call.agent_name ?? 'agent'}{call.id === -1 ? ' · preview' : ''}</div>
          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2 text-xs font-semibold text-fg-2">
            {team ? <PhoneForwarded className="size-3.5 shrink-0" /> : <Bot className="size-3.5 shrink-0" />}
            {isRinging ? (team ? 'Ringing your team' : 'The AI is picking up…') : (team ? 'Forwarded to your team' : 'Answered by the AI')}
            {!isRinging && <><span className="text-muted">·</span><span aria-hidden><CallTimer since={call.answered_at ?? call.created_at} className="tabular-nums" /></span></>}
          </div>
          {!isRinging && <span className="mt-2 block max-w-full overflow-hidden"><Waveform bars={9} className="h-5 text-success" /></span>}
        </div>

        <button type="button" onClick={onDismiss} aria-label="Dismiss"
          className="-mt-1.5 -mr-1.5 grid size-10 shrink-0 place-items-center rounded-lg text-muted transition hover:bg-surface-2 hover:text-fg"><X className="size-4" /></button>
      </div>

      <div className="relative flex gap-2 px-4 pb-4">
        <button type="button" onClick={onListen}
          className="inline-flex min-h-10 min-w-0 flex-1 items-center justify-center gap-2 rounded-xl bg-brand py-2 text-[13px] font-bold text-brand-fg transition hover:brightness-110 active:scale-[.98]">
          <Headphones className="size-4" />{call.id === -1 ? 'Got it' : 'Listen live'}
        </button>
        <button type="button" onClick={onDismiss}
          className="min-h-10 shrink-0 rounded-xl border border-border px-4 py-2 text-[13px] font-bold text-fg-2 transition hover:bg-surface-2">Later</button>
      </div>

      {/* How long until it folds into the pill. Pauses while hovered. */}
      <span key={round} className={cn('absolute bottom-0 left-0 h-0.5 bg-current', isRinging ? 'text-info' : 'text-success', hover ? 'incoming-timer paused' : 'incoming-timer')}
        style={{ animationDuration: `${CARD_MS}ms` }} aria-hidden />
    </div>
  )
}
