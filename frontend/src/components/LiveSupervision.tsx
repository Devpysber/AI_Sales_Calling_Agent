import { useState, useEffect, useRef } from 'react'
import { Mic, MicOff, Send, MessageSquare, Hand, X } from 'lucide-react'
import { Button, Input } from '@/components/ui'
import { useAgent } from '@/lib/agent'

export default function LiveSupervision({ callId }: { callId: number }) {
  const { base } = useAgent()
  const [ws, setWs] = useState<WebSocket | null>(null)
  const [state, setState] = useState<any>(null)
  const [guideText, setGuideText] = useState('')
  const [dirText, setDirText] = useState('')
  const [sayText, setSayText] = useState('')
  const [recording, setRecording] = useState(false)
  const mediaRecorder = useRef<MediaRecorder | null>(null)

  useEffect(() => {
    // Construct WS URL
    const loc = window.location
    const protocol = loc.protocol === 'https:' ? 'wss:' : 'ws:'
    // base might be /api/agents/1
    const wsUrl = `${protocol}//${loc.host}${base}/calls/${callId}/monitor`
    
    const socket = new WebSocket(wsUrl)
    socket.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'state') setState(msg)
      if (msg.type === 'audio') {
        // play audio if we wanted to
      }
    }
    setWs(socket)
    return () => socket.close()
  }, [callId, base])

  const sendCommand = (action: string, text: string = '', now: boolean = false) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action, text, now }))
    }
  }

  const toggleRecording = async () => {
    if (recording) {
      mediaRecorder.current?.stop()
      setRecording(false)
      sendCommand('release')
    } else {
      sendCommand('takeover')
      setRecording(true)
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      // This is simplified, for true human_audio we would stream PCM data chunks to WS
      mr.start()
      mediaRecorder.current = mr
    }
  }

  if (!state) return <div className="p-3 text-sm text-muted">Connecting to live call...</div>

  return (
    <div className="rounded-xl border border-brand/30 bg-surface p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-brand flex items-center gap-2">
          <span className="relative flex h-3 w-3"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-75"></span><span className="relative inline-flex rounded-full h-3 w-3 bg-brand"></span></span>
          Live Supervision
        </h3>
        <div className="text-xs font-mono text-muted">
          Mode: <span className="text-fg">{state.mode}</span> | 
          AI: {state.agent_speaking ? 'Speaking' : 'Silent'}
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex gap-2">
          <Input value={guideText} onChange={(e) => setGuideText(e.target.value)} placeholder="One-time instruction for next reply..." className="flex-1" />
          <Button onClick={() => { sendCommand('guide', guideText, false); setGuideText('') }}><Send className="size-4"/> Guide</Button>
          <Button variant="secondary" onClick={() => { sendCommand('guide', guideText, true); setGuideText('') }}><Send className="size-4"/> Guide Now</Button>
        </div>
        
        <div className="flex gap-2">
          <Input value={dirText} onChange={(e) => setDirText(e.target.value)} placeholder="Standing direction (e.g. 'Push for a demo')..." className="flex-1" />
          <Button variant="secondary" onClick={() => sendCommand('direction', dirText)}><Send className="size-4"/> Set Dir</Button>
          {state.direction && <Button variant="ghost" onClick={() => sendCommand('direction', '')}><X className="size-4"/></Button>}
        </div>

        <div className="flex gap-2">
          <Input value={sayText} onChange={(e) => setSayText(e.target.value)} placeholder="Force AI to say exact text..." className="flex-1" />
          <Button variant="secondary" onClick={() => { sendCommand('say', sayText); setSayText('') }}><MessageSquare className="size-4"/> Say</Button>
        </div>
        
        <div className="flex items-center gap-2 pt-2 border-t border-border">
          <Button variant={recording ? 'danger' : 'secondary'} onClick={toggleRecording}>
            {recording ? <><MicOff className="size-4"/> Stop Speaking (Release)</> : <><Mic className="size-4"/> Take Over & Speak</>}
          </Button>
          <Button variant="secondary" onClick={() => sendCommand('stop_speaking')}><Hand className="size-4"/> Stop AI Audio</Button>
        </div>
      </div>
    </div>
  )
}
