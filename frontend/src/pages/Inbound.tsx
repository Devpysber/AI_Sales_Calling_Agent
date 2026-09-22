import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Clock, MoonStar, PhoneForwarded, PhoneIncoming, PhoneMissed, Save, UserRound, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import CallSheet from '@/components/CallSheet'
import InboundSetup from '@/components/InboundSetup'
import { CallStatusBadge } from '@/components/status'
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Skeleton, Switch, Textarea } from '@/components/ui'
import { api } from '@/lib/api'
import { AnimatedNumber, Stagger } from '@/lib/motion'
import { Waveform } from '@/components/VoiceViz'
import { useAgent } from '@/lib/agent'
import type { AgentProfile, AutomationSettings, Call, Page } from '@/lib/types'
import { callParty, cn, formatDuration, timeAgo } from '@/lib/utils'

type ProfileResponse = { profile: AgentProfile; transfer_contacts?: { phone: string; name: string | null }[] }
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hourLabel = (h: number) => `${((h + 11) % 12) + 1}:00 ${h < 12 ? 'AM' : 'PM'}`
type Routing = Pick<AgentProfile, 'transfer_number' | 'team_members' | 'inbound_mode' | 'transfer_on_request' | 'after_hours_mode' | 'after_hours_message' | 'forward_fallback' | 'notify_missed_calls' | 'inbound_collect'>
const KEYS: (keyof Routing)[] = ['transfer_number', 'team_members', 'inbound_mode', 'transfer_on_request', 'after_hours_mode', 'after_hours_message', 'forward_fallback', 'notify_missed_calls', 'inbound_collect']

export default function Inbound() {
  const { agent, base, path } = useAgent()
  const ownerQ = useQuery({ queryKey: ['inbound-owner', base], queryFn: () => api<{ number: string; owner_id: number | null; own_number?: boolean; sharing: { id: number; name: string }[] }>(`${base}/inbound-owner`) })
  const setOwner = useMutation({
    mutationFn: () => api(`${base}/inbound-owner`, { method: 'PUT' }),
    onSuccess: () => { toast.success(`${agent?.name} now answers inbound calls on this line`); void qc.invalidateQueries({ queryKey: ['inbound-owner'] }) },
    onError: (e) => toast.error('Could not change who answers', { description: e.message }),
  })
  const navigate = useNavigate()
  // Same cache as the app-wide incoming-call banner: no extra polling for this page.
  const liveFeed = useQuery({
    queryKey: ['live-calls'],
    queryFn: () => api<{ live_calls: (Call & { agent_name: string | null })[] }>('/api/agents/live'),
    refetchInterval: 3000,
  })
  const liveInbound = liveFeed.data?.live_calls.find((c) => c.agent_id === agent?.id && c.direction === 'inbound')
  const qc = useQueryClient()
  const profile = useQuery({ queryKey: ['agent'], queryFn: () => api<ProfileResponse>(`${base}/profile`) })
  const data = profile.data
  const automation = useQuery({ queryKey: ['automation'], queryFn: () => api<{ settings: AutomationSettings; within_calling_hours: boolean }>(`${base}/automation`) })
  const calls = useQuery({
    queryKey: ['calls', 'inbound'],
    queryFn: () => api<Page<Call>>(`${base}/calls`, { params: { direction: 'inbound', page_size: 25 } }),
    refetchInterval: (q) => (q.state.data?.items.some((c) => ['Ringing', 'In Progress'].includes(c.status)) ? 2000 : 6000),
  })

  const [form, setForm] = useState<Routing | null>(null)
  const [callId, setCallId] = useState<number | null>(null)
  useEffect(() => { if (data && !form) setForm(Object.fromEntries(KEYS.map((k) => [k, data.profile[k]])) as Routing) }, [data, form])
  // Stable keys for team member rows: with `key={i}` removing row N re-used row N+1's inputs (focus and IME state moved to the wrong row).
  const nextKey = useRef(0)
  const [rowKeys, setRowKeys] = useState<number[]>([])
  const memberCount = form?.team_members?.length || (form?.transfer_number || '').split(',').filter((n) => n.trim()).length
  useEffect(() => { setRowKeys((k) => (k.length >= memberCount ? k : [...k, ...Array.from({ length: memberCount - k.length }, () => nextKey.current++)])) }, [memberCount])

  const save = useMutation({
    mutationFn: (values: Routing) => api<AgentProfile>(`${base}/profile`, { method: 'PUT', json: values }),
    onSuccess: (profile) => {
      qc.setQueryData<ProfileResponse>(['agent'], (old) => (old ? { ...old, profile } : old))
      // transfer_contacts (names for the saved numbers) is computed server-side: refetch so it matches the new numbers.
      qc.invalidateQueries({ queryKey: ['agent'] })
      setForm(Object.fromEntries(KEYS.map((k) => [k, profile[k]])) as Routing)
      toast.success('Call routing saved', { description: 'Applies to the next incoming call.' })
    },
    onError: (e) => toast.error('Not saved', { description: e.message }),
  })

  const saveHours = useMutation({
    mutationFn: (values: { calling_hours_start: number, calling_hours_end: number }) => {
      if (!automation.data) return Promise.reject(new Error('Calling hours are still loading'))
      return api<AutomationSettings>(`${base}/automation`, { method: 'PUT', json: { ...automation.data.settings, ...values } })
    },
    // The selects are controlled from the cache: patch it optimistically so they don't snap back to the old hour while the PUT is in flight.
    onMutate: async (values) => {
      await qc.cancelQueries({ queryKey: ['automation'] })
      const previous = qc.getQueryData<{ settings: AutomationSettings; within_calling_hours: boolean }>(['automation'])
      qc.setQueryData<{ settings: AutomationSettings; within_calling_hours: boolean }>(['automation'], (old) => (old ? { ...old, settings: { ...old.settings, ...values } } : old))
      return { previous }
    },
    onSuccess: (res) => {
      qc.setQueryData<{ settings: AutomationSettings; within_calling_hours: boolean }>(['automation'], (old) => (old ? { ...old, settings: res } : old))
      qc.invalidateQueries({ queryKey: ['automation'] })
      // The sidebar's "After hours" badge lives on the root client's ['agents'] query, which this per-agent
      // client cannot invalidate (see NewAgentSheet); it catches up on its own refetch interval.
      toast.success('Calling hours updated')
    },
    onError: (e, _values, ctx) => {
      if (ctx?.previous) qc.setQueryData(['automation'], ctx.previous)
      toast.error('Not saved', { description: e.message })
    },
  })

  const items = useMemo(() => calls.data?.items ?? [], [calls.data])
  const stats = useMemo(() => {
    // Team check-in calls are internal: count only what customers did, so "Answered" can't go negative.
    // Every figure comes from the same fetched page (the server-wide total would not add up with them).
    const external = items.filter((c) => c.trigger !== 'internal')
    const answered = external.filter((c) => c.status === 'Completed' || (c.status === 'Failed' && (c.duration ?? 0) > 0)).length
    const forwarded = external.filter((c) => c.trigger === 'forwarded').length
    const missed = external.filter((c) => ['No Answer', 'Busy'].includes(c.status) || (c.status === 'Failed' && !(c.duration ?? 0))).length
    return { total: external.length, answered, forwarded, missed }
  }, [items])

  if (profile.isError) {
    return <><PageHeader title="Inbound & transfer" /><Card><EmptyState icon={<PhoneMissed />} title="Couldn't load call routing" description={profile.error.message}
      action={<Button onClick={() => profile.refetch()} loading={profile.isFetching}>Try again</Button>} /></Card></>
  }
  if (!form || !data) return <><PageHeader title="Inbound & transfer" /><div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-80" /><Skeleton className="h-80" /></div></>
  const busy = save.isPending

  const set = <K extends keyof Routing>(k: K, v: Routing[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))
  // A half-filled team member row must not hide a transfer number that is still saved on the agent: the
  // backend forwards on `transfer_number`, so the badge here has to describe what will actually happen.
  const memberNums = (form.team_members ?? []).map((m) => (m?.phone ?? '').trim()).filter((n) => n)
  // Once rows exist, they are what gets saved (the server recomputes transfer_number from them), so the
  // legacy comma list only counts when there are no rows at all: blanking every phone must read as "Not set".
  const legacyNums = (form.transfer_number || '').split(',').map((n) => n.trim()).filter((n) => n)
  const tNums = form.team_members?.length ? memberNums : legacyNums
  const hasNumber = tNums.length > 0 && tNums.every(n => { const d = n.replace(/\D/g, ''); return d.length >= 11 && d.length <= 15 })
  const ringsLabel = tNums.join(', ')
  // The server drops any row without a phone, so a name or email typed without one would vanish on save.
  const blankPhoneRow = (form.team_members ?? []).some((m) => !(m?.phone ?? '').trim() && ((m?.name ?? '').trim() || (m?.email ?? '').trim()))
  const numberError = tNums.length > 0 && !hasNumber
    ? 'All numbers must include the country code and be valid length, e.g. +91 98765 43210.'
    : blankPhoneRow
      ? 'Every team member needs a phone number, or remove the row.'
      : undefined

  // Forwarding without a number always fails server-side (400), so block it here with a reason instead of a toast.
  const wantsForward = form.inbound_mode === 'forward' || form.after_hours_mode === 'forward'
  const needsNumber = (tNums.length > 0 && !hasNumber) || (!hasNumber && wantsForward) || blankPhoneRow
  const dirty = KEYS.some((k) => JSON.stringify(form[k]) !== JSON.stringify(data.profile[k]))
  const cfg = automation.data?.settings
  const hours = cfg ? `${hourLabel(cfg.calling_hours_start)} – ${hourLabel(cfg.calling_hours_end)} IST` : '…'
  const digits = (s: string | null | undefined) => (s ?? '').replace(/\D/g, '')
  const callerName = (c: Call) => {
    const d = digits(c.from_number)
    if (!d) return null
    return data.profile.team_members?.find((m) => digits(m?.phone) === d)?.name || data.transfer_contacts?.find((t) => digits(t.phone) === d)?.name || null
  }

  const routeNow = automation.data?.within_calling_hours === false ? form.after_hours_mode : form.inbound_mode
  const flow: Record<string, { icon: ReactNode; label: string; detail: string }> = {
    ai: { icon: <Bot />, label: `${data.profile.agent_name} (AI) answers`, detail: form.transfer_on_request && hasNumber ? `Hands over to ${ringsLabel} when asked` : 'Answers from the knowledge base' },
    forward: { icon: <PhoneForwarded />, label: 'Your team answers', detail: hasNumber ? `Rings ${ringsLabel}` : 'Needs a transfer number' },
    message: { icon: <MoonStar />, label: 'Closed message', detail: 'Plays a message, then hangs up' },
  }
  const option = (group: 'inbound_mode' | 'after_hours_mode', value: string, icon: ReactNode, title: string, detail: string, disabled = false) => {
    const active = form[group] === value
    return (
      <button key={value} type="button" role="radio" aria-checked={active} disabled={disabled || busy} onClick={() => set(group, value as never)}
        className={cn('flex min-h-11 w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition disabled:cursor-not-allowed disabled:opacity-45',
          active ? 'border-fg bg-surface-2' : 'border-border hover:border-border-strong')}>
        <span className={cn('grid size-4 shrink-0 place-items-center rounded-full border-2', active ? 'border-fg' : 'border-border-strong')}>{active && <span className="size-2 rounded-full bg-fg" />}</span>
        <span className="text-fg-2 [&_svg]:size-4">{icon}</span>
        <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{title}</span><span className="block truncate text-xs text-muted">{detail}</span></span>
      </button>
    )
  }
  const memberRows = ((form.team_members && form.team_members.length > 0)
    ? form.team_members
    : (form.transfer_number || '').split(',').map((n) => ({ name: '', phone: n.trim(), email: '' }))).filter((m) => form.team_members?.length || m.phone)
  const teamDetail = hasNumber ? `Rings ${ringsLabel}` : 'Set a transfer number first'

  return (
    <>
      <PageHeader eyebrow={<><PhoneIncoming className="size-3.5" />{agent?.name} · Call routing</>} title="Inbound & transfer"
        description="Choose who answers when customers call, and where the AI sends callers who need a person." />

      <Card className="mb-4">
        <CardHeader title="Who answers this line" description={ownerQ.data?.own_number
          ? `This agent has its own number: every call to it is answered here, with this agent's persona, knowledge and CRM. Its team members are recognised by their number and get check-in mode.`
          : "Several agents can dial out from one number. A customer who is already in an agent's CRM gets that agent back (its persona and knowledge); one known to several agents is asked which matter the call is about, then handed to that agent. A new number goes to the agent chosen here. Your own team members are recognised by their number and get their agent in check-in mode (asked which one, if they work with several). Give an agent its own number under Agent settings when you get one."} />
        <div className="flex flex-wrap items-center gap-3 px-4 pb-4 text-sm sm:px-5 sm:pb-5">
          {ownerQ.data ? (
            <>
              <span className="font-mono">+{ownerQ.data.number || '—'}</span>
              <span className="text-muted">·</span>
              {ownerQ.data.owner_id === agent?.id
                ? <Badge tone="success">This agent answers</Badge>
                : ownerQ.data.owner_id
                  ? <span>Answered by <strong>{ownerQ.data.sharing.find((a) => a.id === ownerQ.data!.owner_id)?.name ?? `agent #${ownerQ.data.owner_id}`}</strong></span>
                  : <span className="text-muted">No agent designated: known callers reach their own agent, new numbers reach the first agent on this line</span>}
              {ownerQ.data.owner_id !== agent?.id && (
                <Button size="sm" variant="primary" className="ml-auto" loading={setOwner.isPending} onClick={() => setOwner.mutate()}>
                  <PhoneIncoming />Make {agent?.name} answer
                </Button>
              )}
              {ownerQ.data.sharing.length > 1 && <span className="basis-full text-xs text-muted">Sharing this line: {ownerQ.data.sharing.map((a) => a.name).join(', ')}</span>}
            </>
          ) : <Skeleton className="h-6 w-64" />}
        </div>
      </Card>

      <Card className={cn('glint mb-4 transition-colors', liveInbound && 'beam beam-on beam-live is-live-card')}>
        {liveInbound && (
          <div className="flex items-center gap-2 border-b border-success/30 bg-success-soft px-4 py-2 text-[13px] font-bold text-success sm:px-5">
            <span className="size-2 shrink-0 animate-pulse-dot rounded-full bg-success" />
            <span className="min-w-0 flex-1 truncate">On the line now: {callParty(liveInbound)}</span>
            <Waveform bars={12} className="ml-auto h-4 shrink-0" />
          </div>
        )}
        <div className="flex flex-wrap items-center gap-4 px-4 py-4 sm:px-5">
          <div className="flex min-w-0 items-center gap-2.5 text-sm">
            <span className="relative grid size-9 shrink-0 place-items-center rounded-xl bg-surface-2">
              <span className="absolute inset-0 animate-live-ring rounded-xl bg-fg/10" />
              <PhoneIncoming className="relative size-4" />
            </span>
            <div className="min-w-0"><div className="font-bold">A customer calls now</div>
              {automation.isError && !automation.data ? (
                <div className="truncate text-xs text-danger">
                  Couldn't load hours · <button type="button" onClick={() => automation.refetch()} disabled={automation.isFetching} className="inline-flex min-h-10 items-center px-1 font-semibold underline disabled:opacity-50 sm:min-h-0">Retry</button>
                </div>
              ) : <div className="truncate text-xs text-muted">{automation.data ? `${automation.data.within_calling_hours ? 'Open' : 'Closed'} · ${hours}` : '…'}</div>}
            </div>
          </div>
          {/* A signal travelling from the caller to whoever answers right now: the route, shown working. */}
          <span className={cn('route-path hidden w-16 sm:block', liveInbound && 'is-live')} aria-hidden><span className="route-signal" /></span>
          <div className="flex min-w-0 items-center gap-2.5 text-sm">
            <span key={routeNow} className="animate-pop-in grid size-9 shrink-0 place-items-center rounded-xl bg-fg text-bg [&_svg]:size-4">{flow[routeNow]?.icon}</span>
            <div className="min-w-0"><div className="font-bold break-words">{flow[routeNow]?.label}</div><div className="truncate text-xs text-muted">{flow[routeNow]?.detail}</div></div>
          </div>
          <div className="grid w-full grid-cols-4 gap-3 text-center sm:ml-auto sm:w-auto sm:gap-5">
            {([['Inbound', stats.total], ['Answered', stats.answered], ['Forwarded', stats.forwarded], ['Missed', stats.missed]] as const).map(([l, v]) => (
              <div key={l}><div className="text-lg font-extrabold tabular-nums"><AnimatedNumber value={v} /></div><div className="text-[11px] text-muted">{l}</div></div>
            ))}
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <Stagger className="min-w-0 space-y-4" step={60}>
          <Card>
            <CardHeader title="1 · Phone number" description="Plivo must send calls on this number to the app." />
            <div className="px-4 pb-5 text-sm sm:px-5"><InboundSetup number={ownerQ.data?.own_number ? ownerQ.data.number : undefined} /></div>
          </Card>

          <Card>
            <CardHeader title="2 · Your team's number" description="Where calls go when a person should take over."
              action={<Badge tone={hasNumber ? 'success' : 'warning'} dot>{hasNumber ? 'Set' : 'Not set'}</Badge>} />
            <div className="space-y-3 px-4 pb-5 sm:px-5">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-semibold">Team members</div>
                  <button type="button" disabled={busy} onClick={() => { setRowKeys((k) => (k.length >= memberRows.length ? [...k.slice(0, memberRows.length), nextKey.current++] : [...k, ...Array.from({ length: memberRows.length + 1 - k.length }, () => nextKey.current++)])); set('team_members', [...memberRows, { name: '', phone: '', email: '' }]) }} className="min-h-10 px-2 text-xs font-semibold text-fg hover:underline disabled:opacity-50">+ Add team member</button>
                </div>
                <div className="text-[13px] text-muted mb-2">Team members who should receive urgent alerts and fallback calls.</div>
                {numberError && <div className="text-xs font-medium break-words text-danger">{numberError}</div>}
                {/* What is saved right now, named from Sales Team Accounts. Without this the page could
                    only show a bare number, or — with a half-filled row — nothing at all. */}
                {!form.team_members?.length && !!data.transfer_contacts?.length && (
                  <ul className="space-y-1.5">
                    {data.transfer_contacts.map((c) => (
                      <li key={c.phone} className="flex items-center gap-2 rounded-xl border border-border bg-surface-2 px-3 py-2 text-sm">
                        <UserRound className="size-4 shrink-0 text-fg-2" />
                        <span className="min-w-0 flex-1 truncate">
                          <span className="font-semibold">{c.name || 'Unnamed colleague'}</span>
                          <span className="ml-2 font-mono text-xs text-muted">{c.phone}</span>
                        </span>
                        <span className="shrink-0 text-[11px] font-semibold text-muted">Saved</span>
                      </li>
                    ))}
                    <li className="text-[11.5px] text-muted">
                      Names come from Sales Team Accounts under Integrations &amp; system. Add a row here to override
                      which numbers this agent rings.
                    </li>
                  </ul>
                )}
                {/* Rows are copied on edit: mutating them in place also mutated the cached profile, so the
                    page never saw the form as dirty and Discard had nothing to go back to. */}
                {memberRows.map((member, i, arr) => {
                  const edit = (patch: Partial<typeof member>) => set('team_members', arr.map((m, j) => (j === i ? { ...m, ...patch } : m)))
                  const remove = () => {
                    const rest = arr.filter((_, j) => j !== i)
                    set('team_members', rest)
                    setRowKeys((k) => k.filter((_, j) => j !== i))
                    // Keep the legacy comma list in sync (the server does the same on save): with rows gone, memberRows
                    // falls back to transfer_number, so a stale list would bring every removed row straight back.
                    set('transfer_number', rest.map((m) => (m?.phone ?? '').trim()).filter(Boolean).join(', '))
                  }
                  return (
                    <div key={rowKeys[i] ?? `row-${i}`} className="flex items-start gap-2 rounded-xl border border-border bg-surface-2 p-3">
                      <div className="min-w-0 flex-1 space-y-2">
                        <Input type="text" value={member?.name ?? ''} disabled={busy} onChange={(e) => edit({ name: e.target.value })} placeholder="Name (e.g. Alice)" aria-label="Team member name" className="text-sm sm:h-8" />
                        <Input type="tel" value={member?.phone ?? ''} disabled={busy} onChange={(e) => edit({ phone: e.target.value })} placeholder="Phone (e.g. +91 98765 43210)" aria-label="Team member phone" maxLength={20} className="text-sm sm:h-8" />
                        <Input type="email" value={member?.email ?? ''} disabled={busy} onChange={(e) => edit({ email: e.target.value })} placeholder="Email (e.g. alice@example.com)" aria-label="Team member email" className="text-sm sm:h-8" />
                      </div>
                      <button type="button" disabled={busy} onClick={remove} aria-label="Remove team member" className="grid size-10 shrink-0 place-items-center rounded-lg text-muted hover:bg-surface hover:text-fg disabled:opacity-50"><X className="size-4" /></button>
                    </div>
                  )
                })}
              </div>

              <label className={cn('flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm', !hasNumber && 'opacity-50')}>
                <UserRound className="size-4 shrink-0 text-fg-2" />
                <span className="min-w-0 flex-1"><span className="block font-semibold">AI transfers when a caller asks for a person</span><span className="text-xs text-muted">The agent says it is connecting them, then your number rings.</span></span>
                <Switch checked={!!form.transfer_on_request} onChange={(v) => set('transfer_on_request', v)} disabled={!hasNumber || busy} label="Transfer on request" />
              </label>
              <label className={cn('flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm', !hasNumber && 'opacity-50')}>
                <Bot className="size-4 shrink-0 text-fg-2" />
                <span className="min-w-0 flex-1"><span className="block font-semibold">If your team doesn't pick up, the AI takes the call</span><span className="text-xs text-muted">Off: the caller hears “our team will call you back” and the call ends.</span></span>
                <Switch checked={form.forward_fallback === 'ai'} onChange={(v) => set('forward_fallback', v ? 'ai' : 'message')} disabled={!hasNumber || busy} label="AI fallback" />
              </label>
              <label className={cn('flex items-center gap-3 rounded-xl border border-border px-3 py-2.5 text-sm', !hasNumber && 'opacity-50')}>
                <PhoneMissed className="size-4 shrink-0 text-fg-2" />
                <span className="min-w-0 flex-1"><span className="block font-semibold">Email me about missed forwarded calls</span><span className="text-xs text-muted">Sent to your Admin profile email with the caller's CRM details.</span></span>
                <Switch checked={!!form.notify_missed_calls} onChange={(v) => set('notify_missed_calls', v)} disabled={!hasNumber || busy} label="Missed call email" />
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader title="3 · New callers" description="When an unknown number calls, the agent saves them as a lead, asks for these details one at a time (in this order), then helps. Names are saved the moment they're said." />
            <div className="space-y-3 px-4 pb-5 sm:px-5">
              <div className="flex flex-wrap gap-2">
                {([['name', 'Name'], ['requirement', 'What they need'], ['city', 'City'], ['company', 'Company'], ['email', 'Email'], ['budget', 'Budget'], ['timeline', 'Timeline'], ['callback_time', 'Best time to call back'], ['source', 'How they heard about us']] as const).map(([k, l]) => {
                  const list = form.inbound_collect ?? []
                  const idx = list.indexOf(k)
                  return (
                    <button key={k} type="button" aria-pressed={idx >= 0} disabled={busy || (idx < 0 && list.length >= 4)} title={idx < 0 && list.length >= 4 ? 'At most 4 details' : undefined} onClick={() => set('inbound_collect', idx >= 0 ? list.filter((x) => x !== k) : [...list, k])}
                      className={cn('inline-flex min-h-10 items-center gap-1.5 rounded-xl border px-3 py-1.5 text-[13px] font-semibold transition disabled:opacity-50',
                        idx >= 0 ? 'border-fg bg-fg text-bg' : 'border-border text-fg-2 hover:border-border-strong')}>
                      {idx >= 0 && <span className="grid size-4 place-items-center rounded-full bg-bg/20 text-[10px]">{idx + 1}</span>}{l}
                    </button>
                  )
                })}
              </div>
              <p className="text-xs text-muted">At most 4, ideally 2–3: every question adds time to the call. Name and what they need always come first. Known callers skip details they already gave, and callers known to another agent are recognised.</p>
            </div>
          </Card>

          <Card>
            <CardHeader title="4 · Who answers" description={<>Open hours come from the <Link to={path('/automation')} className="text-brand hover:underline">Automation page</Link>.</>} />
            <div className="grid gap-5 px-4 pb-5 sm:px-5 md:grid-cols-2">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-xs font-bold tracking-wide text-muted uppercase">
                  <Clock className="size-3.5" />Open
                  {cfg && (
                    <div className="flex items-center gap-1 font-normal normal-case sm:ml-2">
                      <select value={cfg.calling_hours_start} disabled={saveHours.isPending} aria-label="Opening hour" onChange={(e) => { const start = +e.target.value; saveHours.mutate({ calling_hours_start: start, calling_hours_end: Math.max(cfg.calling_hours_end, start + 1) }) }} className="min-h-10 rounded-md border border-border bg-bg px-2 py-1 text-xs text-fg disabled:opacity-50">
                        {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                      </select>
                      <span>to</span>
                      <select value={cfg.calling_hours_end} disabled={saveHours.isPending} aria-label="Closing hour" onChange={(e) => { const end = +e.target.value; saveHours.mutate({ calling_hours_start: Math.min(cfg.calling_hours_start, end - 1), calling_hours_end: end }) }} className="min-h-10 rounded-md border border-border bg-bg px-2 py-1 text-xs text-fg disabled:opacity-50">
                        {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                      </select>
                    </div>
                  )}
                  {automation.isError && !cfg && (
                    <span className="flex items-center gap-1 font-normal normal-case text-danger sm:ml-2">
                      {automation.error.message}
                      <button type="button" onClick={() => automation.refetch()} disabled={automation.isFetching} className="min-h-10 px-1 font-semibold underline disabled:opacity-50">Retry</button>
                    </span>
                  )}
                </div>
                <div className="space-y-2" role="radiogroup" aria-label="Open hours">
                  {option('inbound_mode', 'ai', <Bot />, 'AI agent', 'Answers, qualifies and logs to the CRM')}
                  {option('inbound_mode', 'forward', <PhoneForwarded />, 'Your team', teamDetail, !hasNumber)}
                </div>
              </div>
              <div className="space-y-2" role="radiogroup" aria-label="After hours">
                <div className="flex items-center gap-2 text-xs font-bold tracking-wide text-muted uppercase"><MoonStar className="size-3.5" />After hours</div>
                {option('after_hours_mode', 'ai', <Bot />, 'AI agent', 'Around the clock')}
                {option('after_hours_mode', 'forward', <PhoneForwarded />, 'Your team', teamDetail, !hasNumber)}
                {option('after_hours_mode', 'message', <MoonStar />, 'Closed message', 'Message, then hang up')}
              </div>
              {form.after_hours_mode === 'message' && (
                <div className="md:col-span-2">
                  <Field label="Closed message" hint={`Leave empty for a default message in the caller's language. ${(form.after_hours_message ?? '').length}/200 chars — spoken text is billed per character.`}>
                    <Textarea rows={2} maxLength={200} value={form.after_hours_message ?? ''} disabled={busy} onChange={(e) => set('after_hours_message', e.target.value)}
                      placeholder="Thanks for calling. We're closed right now: please call again between 9 AM and 9 PM." />
                  </Field>
                </div>
              )}
            </div>
          </Card>
        </Stagger>

        <Card className="h-fit">
          <CardHeader title="Recent inbound calls" description="Updates live."
            action={<Link to={path('/calls')} className="inline-flex min-h-10 items-center text-xs font-semibold text-brand hover:underline sm:min-h-0">All calls</Link>} />
          {calls.isLoading ? <div className="space-y-2 px-4 pb-5 sm:px-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div>
            : calls.isError && !items.length ? <EmptyState icon={<PhoneMissed />} title="Couldn't load calls" description={calls.error.message} action={<Button size="sm" onClick={() => calls.refetch()}>Try again</Button>} />
            : items.length ? (
              <ul className="divide-y divide-border">
                {items.map((c, i) => (
                  <li key={c.id} style={{ animationDelay: `${Math.min(i, 10) * 45}ms` }} className="reveal reveal-in reveal-right">
                    <button type="button" onClick={() => setCallId(c.id)} className="flex w-full min-w-0 items-center gap-3 px-4 py-3 text-left transition hover:bg-surface-2 sm:px-5">
                      <span className={cn('grid size-8 shrink-0 place-items-center rounded-full', c.trigger === 'forwarded' || c.transferred_to ? 'bg-surface-2 text-fg-2' : 'bg-brand-soft text-brand')}>
                        {c.trigger === 'forwarded' || c.transferred_to ? <PhoneForwarded className="size-4" /> : <PhoneIncoming className="size-4" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">
                          {c.lead_name || callerName(c) || c.from_number || 'Unknown caller'}
                        </span>
                        {/* An AI call that was handed over mid-way used to read "Answered by AI", hiding the transfer. */}
                        <span className="block truncate text-xs text-muted">
                          {c.trigger === 'internal'
                            ? `Team check-in · ${data.profile.agent_name} (AI)`
                            : c.trigger === 'forwarded'
                            ? `Forwarded to ${c.transferred_to_name || c.transferred_to || 'your team'}`
                            : c.transferred_to
                              ? `${data.profile.agent_name} (AI), handed to ${c.transferred_to_name || c.transferred_to}`
                              : `Answered by ${data.profile.agent_name} (AI)`} · {timeAgo(c.created_at)}{c.duration ? ` · ${formatDuration(c.duration)}` : ''}
                        </span>
                      </span>
                      <span className="shrink-0"><CallStatusBadge status={c.status} /></span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : <EmptyState icon={<PhoneIncoming />} title="No inbound calls yet" description="Call your Plivo number from another phone to test." />}
        </Card>
      </div>

      {dirty && (
        <div className="sticky bottom-[max(1rem,env(safe-area-inset-bottom))] z-20 mt-4 flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-elevated px-4 py-3 shadow-pop">
          <span className="min-w-0 flex-1 basis-40 text-sm font-semibold">Unsaved routing changes{!hasNumber && wantsForward && <span className="block text-xs font-medium text-danger">Add a transfer number before forwarding calls to your team.</span>}</span>
          <Button disabled={busy} onClick={() => setForm(Object.fromEntries(KEYS.map((k) => [k, data.profile[k]])) as Routing)}>Discard</Button>
          <Button variant="primary" loading={busy} disabled={needsNumber} onClick={() => save.mutate(form)}><Save />Save routing</Button>
        </div>
      )}

      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); navigate(path(`/leads/${id}`)) }} />
    </>
  )
}
