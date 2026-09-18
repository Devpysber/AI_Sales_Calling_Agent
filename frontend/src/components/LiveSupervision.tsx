import { Ear, EarOff, Hand, Mic, MicOff, PhoneForwarded, Radio, Send, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Input, useConfirm } from '@/components/ui'
import { useAgent } from '@/lib/agent'
import { cn } from '@/lib/utils'
import { VoiceOrb, Waveform } from '@/components/VoiceViz'

type LiveState = {
  type: 'state'; mode: 'ai' | 'human'; agent_speaking: boolean; caller_speaking: boolean; thinking: boolean
  direction: string | null; guidance: string | null; can_transfer?: boolean; transfer_number?: string | null
}

const RATE = 8000

/** Base64 PCM16 (8 kHz mono) -> Float32 samples for Web Audio. */
function decodePcm(b64: string): Float32Array<ArrayBuffer> {
  const bin = atob(b64)
  const out = new Float32Array(new ArrayBuffer((bin.length >> 1) * 4))
  for (let i = 0; i < out.length; i++) {
    let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8)
    if (v >= 0x8000) v -= 0x10000
    out[i] = v / 32768
  }
  return out
}

/** Float32 at the mic's rate -> base64 PCM16 at 8 kHz (simple decimation, fine for speech on a phone line). */
function encodePcm(input: Float32Array, fromRate: number): string {
  const step = fromRate / RATE
  const n = Math.floor(input.length / step)
  const bytes = new Uint8Array(n * 2)
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, input[Math.floor(i * step)]!))
    const v = s < 0 ? s * 0x8000 : s * 0x7fff
    bytes[2 * i] = v & 0xff
    bytes[2 * i + 1] = (v >> 8) & 0xff
  }
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]!)
  return btoa(bin)
}

export default function LiveSupervision({ callId }: { callId: number }) {
  const { base } = useAgent()
  const confirm = useConfirm()
  const socket = useRef<WebSocket | null>(null)
  const [state, setState] = useState<LiveState | null>(null)
  const [status, setStatus] = useState<'connecting' | 'live' | 'ended' | 'error'>('connecting')
  const [listen, setListen] = useState(false)
  const [talking, setTalking] = useState(false)
  const [guide, setGuide] = useState('')
  const [direction, setDirection] = useState('')
  const [say, setSay] = useState('')
  const [lastHeard, setLastHeard] = useState('')
  const playback = useRef<{ ctx: AudioContext; at: number } | null>(null)
  const mic = useRef<{ ctx: AudioContext; stream: MediaStream; node: ScriptProcessorNode } | null>(null)
  const listenRef = useRef(listen)
  useEffect(() => { listenRef.current = listen }, [listen])

  const send = useCallback((payload: Record<string, unknown>) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(payload))
  }, [])

  const play = (b64: string) => {
    if (!listenRef.current) return
    playback.current ??= { ctx: new AudioContext({ sampleRate: RATE }), at: 0 }
    const { ctx } = playback.current
    const samples = decodePcm(b64)
    const buffer = ctx.createBuffer(1, samples.length, RATE)
    buffer.copyToChannel(samples, 0)
    const src = ctx.createBufferSource()
    src.buffer = buffer
    src.connect(ctx.destination)
    // Queue chunks back to back; resync if we fell behind
    const start = Math.max(ctx.currentTime + 0.05, playback.current.at)
    src.start(start)
    playback.current.at = start + buffer.duration
  }

  const stopMic = useCallback(() => {
    mic.current?.node.disconnect()
    mic.current?.stream.getTracks().forEach((t) => t.stop())
    void mic.current?.ctx.close()
    mic.current = null
    setTalking(false)
  }, [])

  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}${base}/calls/${callId}/monitor`)
    socket.current = ws
    ws.onopen = () => setStatus('live')
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'state') setState((s) => ({ ...s, ...msg }))
      else if (msg.type === 'audio') play(msg.pcm)
      else if (msg.type === 'heard') setLastHeard(msg.text)
      else if (msg.type === 'error') toast.error(msg.message)
      else if (msg.type === 'ended') setStatus('ended')
    }
    ws.onclose = (e) => { setStatus((s) => (s === 'live' || s === 'ended' ? 'ended' : e.code === 4401 ? 'error' : 'ended')); stopMic() }
    ws.onerror = () => setStatus('error')
    return () => { ws.close(); stopMic(); void playback.current?.ctx.close(); playback.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callId, base])

  const toggleListen = () => {
    const next = !listen
    setListen(next)
    send({ action: 'listen', on: next })
    if (next) void playback.current?.ctx.resume()
  }

  const toggleTalk = async () => {
    if (talking) { stopMic(); send({ action: 'release' }); return }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      const ctx = new AudioContext()
      const source = ctx.createMediaStreamSource(stream)
      const node = ctx.createScriptProcessor(2048, 1, 1)
      node.onaudioprocess = (ev) => send({ action: 'human_audio', audio: encodePcm(ev.inputBuffer.getChannelData(0), ctx.sampleRate) })
      source.connect(node)
      node.connect(ctx.destination)
      mic.current = { ctx, stream, node }
      send({ action: 'takeover' })
      setTalking(true)
      if (!listen) toggleListen()
    } catch {
      toast.error('Microphone blocked', { description: 'Allow microphone access to speak to the caller.' })
    }
  }

  const transfer = async () => {
    if (!state?.can_transfer) return void toast.error('No transfer number', { description: 'Set one on the Inbound & transfer page.' })
    if (await confirm({ title: 'Transfer this caller?', description: `The agent says “connecting you to our team”, then ${state.transfer_number} rings.`, confirmLabel: 'Transfer' })) {
      send({ action: 'transfer' })
      toast.success('Transferring…')
    }
  }

  if (status === 'error') return <div className="rounded-xl border border-danger/30 bg-danger-soft p-3 text-sm text-danger">Could not open live supervision. Sign in again, or the call is handled by another server.</div>
  if (status === 'ended') return <div className="rounded-xl border border-border bg-surface-2 p-3 text-sm text-muted">Live supervision ended: the call finished or was transferred.</div>
  if (!state) return <div className="rounded-xl border border-border p-3 text-sm text-muted">Connecting to the live call…</div>

  const activity = talking ? 'You are speaking' : state.caller_speaking ? 'Caller speaking' : state.thinking ? 'AI thinking' : state.agent_speaking ? 'AI speaking' : 'Listening'

  return (
    <div className="space-y-4 rounded-2xl border border-success/30 bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="relative flex size-2.5"><span className="absolute inline-flex size-full animate-live-ring rounded-full bg-success opacity-60" /><span className="relative inline-flex size-2.5 rounded-full bg-success" /></span>
        <h3 className="text-sm font-bold">Live supervision</h3>
        <Badge tone={state.mode === 'human' ? 'warning' : 'success'}>{state.mode === 'human' ? 'You have the call' : 'AI has the call'}</Badge>
        <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted"><Radio className={cn('size-3.5', (state.caller_speaking || state.agent_speaking) && 'text-success')} />{activity}</span>
      </div>
      {/* Two speaker lanes driven by the stream's own VAD state: whoever is talking right now moves. */}
      <div className="grid grid-cols-2 gap-2">
        <SpeakerLane label="Caller" active={state.caller_speaking} tone="caller"
          detail={state.caller_speaking ? 'Speaking' : 'Quiet'} />
        <SpeakerLane label={state.mode === 'human' ? 'You' : 'AI agent'} active={state.agent_speaking || talking || state.thinking}
          tone="agent" thinking={state.thinking && !state.agent_speaking}
          detail={talking ? 'You are speaking' : state.thinking ? 'Thinking' : state.agent_speaking ? 'Speaking' : 'Listening'} />
      </div>
      {lastHeard && <p key={lastHeard} className="reveal reveal-in reveal-up truncate rounded-lg bg-surface-2 px-3 py-2 text-xs text-fg-2">Caller: “{lastHeard}”</p>}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Button variant={listen ? 'primary' : 'secondary'} onClick={toggleListen}>{listen ? <Ear /> : <EarOff />}{listen ? 'Listening' : 'Listen in'}</Button>
        <Button variant={talking ? 'danger' : 'secondary'} onClick={toggleTalk}>{talking ? <MicOff /> : <Mic />}{talking ? 'Give back to AI' : 'Take over'}</Button>
        <Button variant="secondary" onClick={() => send({ action: 'stop_speaking' })}><Hand />Stop AI</Button>
        <Button variant="secondary" onClick={transfer} disabled={!state.can_transfer} title={state.can_transfer ? `Transfer to ${state.transfer_number}` : 'Set a transfer number first'}><PhoneForwarded />Transfer</Button>
      </div>

      <div className="space-y-2">
        <div className="flex gap-2">
          <Input value={guide} onChange={(e) => setGuide(e.target.value)} placeholder="Whisper to the AI for its next reply, e.g. offer a Saturday visit" className="flex-1" />
          <Button disabled={!guide.trim()} onClick={() => { send({ action: 'guide', text: guide }); setGuide('') }}><Sparkles />Next reply</Button>
          <Button variant="primary" disabled={!guide.trim()} onClick={() => { send({ action: 'guide', text: guide, now: true }); setGuide('') }}><Send />Now</Button>
        </div>
        <div className="flex gap-2">
          <Input value={direction} onChange={(e) => setDirection(e.target.value)} placeholder={state.direction ? `Standing: ${state.direction}` : 'Standing direction for every reply, e.g. push for a demo'} className="flex-1" />
          <Button disabled={!direction.trim()} onClick={() => { send({ action: 'direction', text: direction }); setDirection('') }}>Set</Button>
          {state.direction && <Button variant="ghost" size="icon" onClick={() => send({ action: 'direction', text: '' })} aria-label="Clear direction"><X /></Button>}
        </div>
        <div className="flex gap-2">
          <Input value={say} onChange={(e) => setSay(e.target.value)} placeholder="Make the agent say exactly this…" className="flex-1" />
          <Button disabled={!say.trim()} onClick={() => { send({ action: 'say', text: say }); setSay('') }}>Say</Button>
        </div>
      </div>
    </div>
  )
}


function SpeakerLane({ label, detail, active, thinking, tone }: {
  label: string; detail: string; active: boolean; thinking?: boolean; tone: 'caller' | 'agent'
}) {
  return (
    <div className={cn('flex items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors duration-300',
      active ? (tone === 'agent' ? 'border-info/40 bg-info-soft' : 'border-success/40 bg-success-soft') : 'border-border bg-surface-2')}>
      {tone === 'agent'
        ? <VoiceOrb state={thinking ? 'speaking' : active ? 'live' : 'idle'} size={34} />
        : <span className={cn('grid size-[34px] place-items-center rounded-full', active ? 'bg-success text-white' : 'bg-surface text-muted ring-1 ring-border')}>
            <Waveform bars={4} active={active} className="h-3.5" />
          </span>}
      <div className="min-w-0 flex-1 leading-tight">
        <div className="text-xs font-bold">{label}</div>
        <div className="truncate text-[11px] text-muted">{detail}</div>
      </div>
      <Waveform bars={9} active={active && !thinking} className={cn('h-5', tone === 'agent' ? 'text-info' : 'text-success')} />
    </div>
  )
}
