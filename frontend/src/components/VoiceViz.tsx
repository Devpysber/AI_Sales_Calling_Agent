/**
 * Voice visuals: the things on screen that show the agent is a voice, and whether it is talking.
 *
 * These run continuously on purpose (unlike entrances, which play once): an idle orb breathes
 * slowly, a live one spins and pulses faster, and waveform bars move only while there is
 * something to hear. The state is always the real one passed in — nothing here fakes activity.
 */

import { cn } from '@/lib/utils'

type OrbState = 'idle' | 'listening' | 'speaking' | 'live'

const ORB_SPEED: Record<OrbState, string> = {
  idle: '14s',
  listening: '7s',
  speaking: '3.2s',
  live: '4.5s',
}

/**
 * The agent itself: a sphere of turning light with rings leaving it.
 * idle = slow breath; live/speaking = faster spin, rings and a brighter core.
 */
export function VoiceOrb({ state = 'idle', size = 120, className }: { state?: OrbState; size?: number; className?: string }) {
  const active = state !== 'idle'
  return (
    <div className={cn('voice-orb relative grid shrink-0 place-items-center', active && 'is-active', className)}
      style={{ width: size, height: size, ['--orb-speed' as string]: ORB_SPEED[state] }} aria-hidden>
      {/* Rings travelling outwards: two while live, none while idle. */}
      {active && <>
        <span className="orb-ring absolute inset-0 rounded-full" />
        <span className="orb-ring absolute inset-0 rounded-full" style={{ animationDelay: '1.1s' }} />
      </>}
      <span className="orb-halo absolute inset-[-18%] rounded-full" />
      <span className="orb-body absolute inset-[8%] overflow-hidden rounded-full">
        <span className="orb-swirl absolute inset-[-40%]" />
        <span className="orb-swirl orb-swirl-2 absolute inset-[-40%]" />
        <span className="orb-gloss absolute inset-0 rounded-full" />
      </span>
      <Waveform bars={5} active={active} className="relative z-10 h-[26%] text-white/90" />
    </div>
  )
}

/**
 * Equaliser bars. Moving = there is audio right now; flat = silence. Each bar runs the same
 * keyframes at its own delay and duration, so the pattern never visibly repeats.
 */
export function Waveform({ bars = 5, active = true, className }: { bars?: number; active?: boolean; className?: string }) {
  return (
    <span className={cn('waveform inline-flex items-center gap-[12%]', active && 'is-active', className)} aria-hidden>
      {Array.from({ length: bars }, (_, i) => (
        <span key={i} className="wave-bar h-full w-[3px] rounded-full bg-current"
          style={{ animationDelay: `${(i * 137) % 600}ms`, animationDuration: `${820 + ((i * 211) % 480)}ms` }} />
      ))}
    </span>
  )
}

/** A soft moving light behind page headers. Colour stays in the brand's own greys plus the status accent. */
export function Aurora({ className }: { className?: string }) {
  return (
    <div className={cn('aurora pointer-events-none absolute inset-0 overflow-hidden', className)} aria-hidden>
      <span className="aurora-blob aurora-a" />
      <span className="aurora-blob aurora-b" />
      <span className="aurora-blob aurora-c" />
    </div>
  )
}
