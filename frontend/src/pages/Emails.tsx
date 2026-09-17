import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Bot, CheckCircle2, ChevronRight, Clock, Cpu, ExternalLink, Eye,
  FileText, Filter, Mail, PenLine, RefreshCw, Search, Send, Sparkles, User, X,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import CallSheet from "@/components/CallSheet"
import {
  Badge, Button, Card, CardHeader, Dialog,
  EmptyState, Input, PageHeader, Select, Skeleton, Switch, Tabs, Textarea,
} from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import { cn } from "@/lib/utils"
import type { ActivityEvent, Lead, Page } from "@/lib/types"

function dayLabel(iso: string) {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(Date.now() - 86_400_000)
  if (d.toDateString() === today.toDateString()) return "Today"
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday"
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60_000) return "just now"
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  return `${Math.floor(diff / 86_400_000)}d ago`
}

const ACTOR_MAP: Record<string, { label: string; tone: "brand" | "neutral" | "success" | "warning" }> = {
  ai:     { label: "AI",     tone: "brand" },
  system: { label: "System", tone: "neutral" },
  admin:  { label: "Admin",  tone: "success" },
  user:   { label: "Team",   tone: "success" },
}

const TEMPLATES = [
  {
    id: "follow_up", name: "Post-call follow-up", description: "Personalised summary + next steps after a call", icon: "✨",
    subject: "Following up on our conversation",
    body: (agent: string, company: string) => `Hi {name},\n\nIt was great speaking with you today.\n\n[Click "Draft with AI" to automatically summarize the call notes here]\n\nIf you have any further questions, feel free to reply to this email.\n\nBest regards,\n${agent}\n${company}`
  },
  {
    id: "intro", name: "Introduction", description: "First-touch intro from the agent to the lead", icon: "✨",
    subject: "Introduction: {company_name}",
    body: (agent: string, company: string) => `Hi {name},\n\nI hope you're having a great week.\n\nI'm ${agent} from ${company}. I'm reaching out because [Click "Draft with AI" to personalise this based on lead info].\n\nI'd love to connect and share how we can help. Are you available for a quick call sometime this week...\n\nBest regards,\n${agent}\n${company}`
  },
  {
    id: "reminder", name: "Meeting reminder", description: "Reminder before a booked meeting", icon: "✨",
    subject: "Reminder: Upcoming meeting with {company_name}",
    body: (agent: string, company: string) => `Hi {name},\n\nThis is a quick friendly reminder about our upcoming meeting.\n\nLooking forward to speaking with you!\n\nBest regards,\n${agent}\n${company}`
  },
  {
    id: "no_answer", name: "Missed-call note", description: "Friendly note when nobody answered", icon: "✨",
    subject: "Sorry I missed you!",
    body: (agent: string, company: string) => `Hi {name},\n\nI tried giving you a call earlier today but missed you.\n\nI was hoping to discuss [Click "Draft with AI" to add context].\n\nPlease let me know when might be a better time to connect, or feel free to reply directly to this email.\n\nBest regards,\n${agent}\n${company}`
  },
  {
    id: "report", name: "Daily report", description: "End-of-day digest sent to your team inbox", icon: "✨",
    subject: "Daily Agent Activity Report",
    body: (agent: string, _company: string) => `Hi Team,\n\nHere is the daily summary for ${agent}:\n\n[Click "Draft with AI" to generate today's metrics]\n\nBest,\nPsyber Voice AI`
  },
]

export default function Emails() {
  const { agent, base, path } = useAgent()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const qc = useQueryClient()

  const [tab, setTab] = useState<"logs" | "templates" | "compose">(params.get("compose") || params.get("lead") ? "compose" : "logs")
  const [q, setQ]               = useState("")
  const [filterType, setFilterType] = useState("all")
  const [callId, setCallId]     = useState<number | null>(null)
  const [composeLead, setComposeLead] = useState<string>(params.get("lead") || "")
  const [composeSub, setComposeSub]   = useState("")
  const [composeBody, setComposeBody] = useState("")
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewTpl, setPreviewTpl]   = useState<typeof TEMPLATES[0] | null>(null)

  const feedQuery = useInfiniteQuery({
    queryKey: ["activity", "feed", "email", agent?.id],
    queryFn: ({ pageParam }) =>
      api<ActivityEvent[]>(`${base}/activity`, { params: { type: "email", before_id: pageParam, limit: 50 } }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === 50 ? last[last.length - 1]!.id : undefined),
    refetchInterval: 10_000,
  })

  // Read the setting rather than trusting a cache entry the Automation page may never have written:
  // without this the toggle showed "Enabled" even when auto-emails were off.
  const automationQuery = useQuery({
    queryKey: ["agent", "automation", agent?.id],
    queryFn: () => api<{ settings: Record<string, unknown> }>(`${base}/automation`),
  })
  const aiAutoEmails = (automationQuery.data?.settings?.ai_auto_emails as boolean | undefined) ?? true

  const saveAuto = useMutation({
    mutationFn: (v: boolean) => api<any>(`${base}/automation`, { method: "PUT", json: { ai_auto_emails: v } }),
    onSuccess: (data) => { qc.setQueryData(["agent", "automation", agent?.id], data); toast.success("Auto-email setting saved") },
    onError: () => toast.error("Failed to save setting"),
  })

  const leadsQuery = useQuery({
    queryKey: ["leads-mini", agent?.id],
    queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 200 } }),
    enabled: tab === "compose",
  })

  const draftAI = useMutation({
    mutationFn: () => api<{ subject: string; body: string }>(`${base}/leads/${composeLead}/draft_email`),
    onSuccess: (d) => { setComposeSub(d.subject); setComposeBody(d.body); toast.success("AI draft ready") },
    onError: (e) => toast.error(String(e)),
  })

  const sendEmail = useMutation({
    mutationFn: () =>
      api(`${base}/leads/${composeLead}/email`, { method: "POST", json: { subject: composeSub, body: composeBody } }),
    onSuccess: () => {
      toast.success("Email sent!")
      setComposeLead(""); setComposeSub(""); setComposeBody("")
      setTab("logs")
      feedQuery.refetch()
    },
    onError: (e) => toast.error(String(e)),
  })

  const allEvents = feedQuery.data?.pages.flat() ?? []
  const groups = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const filtered = allEvents.filter((e) => {
      if (needle && !`${e.title} ${e.detail ?? ""} ${e.lead_name ?? ""}`.toLowerCase().includes(needle)) return false
      if (filterType === "ai"     && e.actor !== "ai") return false
      if (filterType === "manual" && e.actor !== "user" && e.actor !== "admin") return false
      if (filterType === "system" && e.actor !== "system") return false
      return true
    })
    const out: { day: string; events: ActivityEvent[] }[] = []
    for (const e of filtered) {
      const day = dayLabel(e.created_at)
      if (out[out.length - 1]?.day !== day) out.push({ day, events: [] })
      out[out.length - 1]!.events.push(e)
    }
    return out
  }, [allEvents, q, filterType])

  // send_email logs an event whether or not the provider accepted it, so a rejected address used to
  // be counted and shown as sent. The detail line carries the outcome ("sent via resend" / "failed: …").
  const failed = (e: ActivityEvent) => !(e.detail ?? "").startsWith("sent via")
  const stats = useMemo(() => {
    const sent = allEvents.filter(e => !failed(e))
    return {
      total:  sent.length,
      ai:     sent.filter(e => e.actor === "ai").length,
      manual: sent.filter(e => e.actor === "user" || e.actor === "admin").length,
      system: sent.filter(e => e.actor === "system").length,
      failed: allEvents.length - sent.length,
    }
  }, [allEvents])

  const selectedLead = leadsQuery.data?.items.find(l => String(l.id) === composeLead)

  const FPill = ({ id, label, icon: Icon, count }: { id: string; label: string; icon: typeof Filter; count?: number }) => (
    <button
      onClick={() => setFilterType(id)}
      className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-all ${
        filterType === id ? "bg-brand/10 text-brand font-semibold" : "text-fg-2 hover:bg-surface-2"
      }`}
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="flex-1 text-left">{label}</span>
      {count !== undefined && (
        <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${filterType === id ? "bg-brand/20" : "bg-surface-2"}`}>
          {count}
        </span>
      )}
    </button>
  )

  return (
    <>
      <PageHeader
        eyebrow={<><Mail className="size-3.5" />{agent?.name} · Email Centre</>}
        title="Email Service"
        description="Send manual emails, preview templates, monitor AI auto-follow-ups and system reports."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => feedQuery.refetch()} loading={feedQuery.isFetching}>
              <RefreshCw className="size-3.5" />
            </Button>
            <Button onClick={() => setTab("compose")}><PenLine className="size-4" />Compose</Button>
          </div>
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: "Total sent", value: stats.total,  icon: Mail, tone: "bg-brand-soft text-brand" },
          { label: "AI emails",  value: stats.ai,     icon: Bot,  tone: "bg-brand-soft text-brand" },
          { label: "Manual",     value: stats.manual, icon: User, tone: "bg-success-soft text-success" },
          stats.failed
            ? { label: "Failed", value: stats.failed, icon: X, tone: "bg-danger-soft text-danger" }
            : { label: "Reports", value: stats.system, icon: Cpu, tone: "bg-surface-2 text-fg-2" },
        ].map(({ label, value, icon: Icon, tone }) => (
          <Card key={label} className="flex items-center gap-3 p-4">
            <div className={`rounded-xl p-2 ${tone}`}><Icon className="size-4" /></div>
            <div>
              <p className="text-xs text-muted">{label}</p>
              <p className="text-xl font-bold">{feedQuery.isLoading ? "–" : value}</p>
            </div>
          </Card>
        ))}
      </div>

      <Tabs
        value={tab}
        onChange={(v) => setTab(v as typeof tab)}
        items={[
          { value: "logs",      label: <span className="flex items-center gap-1.5"><Clock className="size-3.5" />Logs</span> },
          { value: "templates", label: <span className="flex items-center gap-1.5"><FileText className="size-3.5" />Templates</span> },
          { value: "compose",   label: <span className="flex items-center gap-1.5"><PenLine className="size-3.5" />Compose</span> },
        ]}
      />

      <div className="mt-5">
        {tab === "logs" && (
          <div className="grid gap-5 lg:grid-cols-[240px_1fr]">
            <div className="space-y-4 lg:sticky lg:top-4 lg:self-start">
              <Card className="p-4">
                <div className="mb-3">
                  <h3 className="flex items-center gap-2 text-sm font-bold"><Bot className="size-4 text-brand" />Autonomous AI Emails</h3>
                  <p className="mt-1 text-xs text-muted leading-relaxed">AI sends personalised follow-ups after every call automatically.</p>
                </div>
                <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-border bg-surface-2 px-3 py-2.5">
                  <span className="text-sm font-medium">{aiAutoEmails ? "Enabled" : "Disabled"}</span>
                  <Switch checked={aiAutoEmails} onChange={(v) => saveAuto.mutate(v)} disabled={saveAuto.isPending} />
                </label>
              </Card>
              <Card className="p-2">
                <div className="px-1 py-2">
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
                    <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Search emails…" className="h-8 pl-8 text-xs" />
                  </div>
                </div>
                <div className="space-y-0.5 pb-1">
                  <FPill id="all"    label="All emails"         icon={Filter} count={stats.total} />
                  <FPill id="ai"     label="AI auto-emails"     icon={Bot}    count={stats.ai} />
                  <FPill id="manual" label="Manual and templates" icon={User} count={stats.manual} />
                  <FPill id="system" label="System reports"     icon={Cpu}    count={stats.system} />
                </div>
              </Card>
            </div>

            <div className="space-y-5 min-w-0">
              {feedQuery.isLoading
                ? <Card className="space-y-4 p-6">{Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-14" />)}</Card>
                : groups.length
                  ? groups.map((g) => (
                    <section key={g.day}>
                      <div className="sticky top-0 z-10 mb-2 flex items-center gap-3 bg-bg/90 py-1 backdrop-blur-sm">
                        <h2 className="text-[11px] font-extrabold uppercase tracking-widest text-muted">{g.day}</h2>
                        <span className="h-px flex-1 bg-border" />
                        <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-bold text-muted">{g.events.length}</span>
                      </div>
                      <Card className="divide-y divide-border overflow-hidden">
                        {g.events.map((e) => {
                          const actor = ACTOR_MAP[e.actor] ?? { label: e.actor, tone: "neutral" as const }
                          return (
                            <div key={e.id} className="flex items-start gap-4 px-5 py-4 transition hover:bg-surface-2/50">
                              <div className={cn('mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full',
                                failed(e) ? 'bg-danger-soft text-danger' : 'bg-brand-soft text-brand')}>
                                <Mail className="size-3.5" />
                              </div>
                              <div className="min-w-0 flex-1">
                                <p className="text-sm font-semibold leading-snug">{e.title}</p>
                                {e.detail && <p className={cn('mt-0.5 text-xs truncate', failed(e) ? 'text-danger' : 'text-muted')}>{e.detail}</p>}
                                {e.lead_name && (
                                  <button onClick={() => e.lead_id && navigate(path(`/leads/${e.lead_id}`))}
                                    className="mt-1 flex items-center gap-1 text-xs text-brand hover:underline">
                                    <ExternalLink className="size-3" />{e.lead_name}
                                  </button>
                                )}
                              </div>
                              <div className="flex shrink-0 flex-col items-end gap-1.5">
                                <Badge tone={actor.tone}>{actor.label}</Badge>
                                <span className="text-[11px] text-muted">{timeAgo(e.created_at)}</span>
                              </div>
                            </div>
                          )
                        })}
                      </Card>
                    </section>
                  ))
                  : <Card><EmptyState icon={<Mail />} title="No emails found" description={q || filterType !== "all" ? "Try adjusting your search or filters." : "Emails sent by AI, your team or the system will appear here."} /></Card>
              }
              {feedQuery.hasNextPage && (
                <div className="text-center">
                  <Button variant="secondary" loading={feedQuery.isFetchingNextPage} onClick={() => feedQuery.fetchNextPage()}>
                    Load older emails
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "templates" && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {TEMPLATES.map((t) => (
              <Card key={t.id} className="group flex flex-col gap-4 p-5 transition hover:shadow-md hover:border-brand/30">
                <div className="flex items-start gap-3">
                  <span className="text-2xl">{t.icon}</span>
                  <div>
                    <p className="font-bold text-sm">{t.name}</p>
                    <p className="text-xs text-muted mt-0.5">{t.description}</p>
                  </div>
                </div>
                <div className="mt-auto flex gap-2">
                  <Button size="sm" variant="secondary" className="flex-1" onClick={() => setPreviewTpl(t)}>
                    <Eye className="size-3.5" />Preview
                  </Button>
                  <Button size="sm" className="flex-1" onClick={() => {
                    setComposeSub(t.subject)
                    setComposeBody(t.body(agent?.persona.agent_name ?? "Jay", agent?.persona.company_name ?? ""))
                    setTab("compose")
                  }}>
                    <PenLine className="size-3.5" />Use
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}

        {tab === "compose" && (
          <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
            <Card className="p-6 space-y-5">
              <CardHeader title="Compose Email" description="Write manually or let AI draft a personalised message from call history." />
              <div className="space-y-1.5">
                <label className="text-sm font-bold">To (Lead) <span className="text-danger">*</span></label>
                <Select value={composeLead} onChange={e => { setComposeLead(e.target.value); setComposeSub(""); setComposeBody("") }}>
                  <option value="" disabled>Select a lead…</option>
                  {leadsQuery.data?.items.map((l) => (
                    <option key={l.id} value={l.id}>{l.name || l.phone}{l.email ? ` — ${l.email}` : " (no email)"}</option>
                  ))}
                </Select>
                {selectedLead && !selectedLead.email && (
                  <p className="text-xs text-warning flex items-center gap-1.5"><X className="size-3" />This lead has no email address on file.</p>
                )}
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-bold">Subject <span className="text-danger">*</span></label>
                <Input value={composeSub} onChange={e => setComposeSub(e.target.value)} placeholder="Enter email subject…" />
              </div>
              <div className="space-y-1.5">
                <label className="text-sm font-bold">Message <span className="text-danger">*</span></label>
                <Textarea value={composeBody} onChange={e => setComposeBody(e.target.value)} placeholder="Write your message here…" rows={10} />
              </div>
              <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-border">
                <Button variant="secondary" loading={draftAI.isPending} disabled={!composeLead} onClick={() => draftAI.mutate()}>
                  <Sparkles className="size-4" />Draft with AI
                </Button>
                <Button variant="secondary" disabled={!composeSub || !composeBody} onClick={() => setPreviewOpen(true)}>
                  <Eye className="size-4" />Preview
                </Button>
                <div className="ml-auto">
                  <Button variant="primary" loading={sendEmail.isPending}
                    disabled={!composeLead || !composeSub || !composeBody || !selectedLead?.email}
                    onClick={() => sendEmail.mutate()}>
                    <Send className="size-4" />Send Email
                  </Button>
                </div>
              </div>
            </Card>

            <div className="space-y-4">
              <Card className="p-5">
                <h3 className="flex items-center gap-2 text-sm font-bold mb-3"><Sparkles className="size-4 text-brand" />AI Drafting Steps</h3>
                <ol className="space-y-2.5 text-sm text-muted">
                  {["Select a lead above", "Click Draft with AI to generate from call history", "Review and edit the draft", "Click Send Email"].map((s, i) => (
                    <li key={i} className="flex items-start gap-2.5">
                      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-brand-soft text-brand text-[11px] font-bold">{i + 1}</span>
                      <span>{s}</span>
                    </li>
                  ))}
                </ol>
              </Card>
              <Card className="p-5">
                <h3 className="flex items-center gap-2 text-sm font-bold mb-3"><FileText className="size-4" />Quick Templates</h3>
                <div className="space-y-1.5">
                  {TEMPLATES.slice(0, 3).map(t => (
                    <button key={t.id} onClick={() => {
                      setComposeSub(t.subject)
                      setComposeBody(t.body(agent?.persona.agent_name ?? "Jay", agent?.persona.company_name ?? ""))
                    }} className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-sm hover:bg-surface-2 transition">
                      <span>{t.icon}</span>
                      <span className="flex-1 font-medium">{t.name}</span>
                      <ChevronRight className="size-3.5 text-muted" />
                    </button>
                  ))}
                  <button onClick={() => setTab("templates")} className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-left text-xs text-brand hover:bg-brand-soft transition">
                    View all templates →
                  </button>
                </div>
              </Card>
              <Card className="p-5">
                <h3 className="flex items-center gap-2 text-sm font-bold mb-2"><Bot className="size-4 text-brand" />Auto-Emails</h3>
                <p className="text-xs text-muted mb-3">AI automatically sends follow-ups after every call.</p>
                <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-border bg-surface-2 px-3 py-2.5">
                  <span className="text-sm font-medium">{aiAutoEmails ? "Enabled" : "Disabled"}</span>
                  <Switch checked={aiAutoEmails} onChange={(v) => saveAuto.mutate(v)} disabled={saveAuto.isPending} />
                </label>
              </Card>
            </div>
          </div>
        )}
      </div>

      <Dialog open={previewOpen} onClose={() => setPreviewOpen(false)} title="Email Preview"
        footer={
          <>
            <Button onClick={() => setPreviewOpen(false)}>Close</Button>
            <Button variant="primary" loading={sendEmail.isPending}
              disabled={!composeLead || !selectedLead?.email}
              onClick={() => { setPreviewOpen(false); sendEmail.mutate() }}>
              <Send className="size-4" />Send now
            </Button>
          </>
        }>
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-surface-2 p-4 text-sm">
            <div className="flex gap-3 border-b border-border pb-3 mb-3 text-xs text-muted">
              <span><b>To:</b> {selectedLead?.email ?? "—"}</span>
            </div>
            <p className="font-bold mb-2">{composeSub || "(no subject)"}</p>
            <pre className="whitespace-pre-wrap text-xs leading-relaxed font-sans text-fg-2">{composeBody || "(empty body)"}</pre>
          </div>
          {selectedLead && !selectedLead.email
            ? <p className="text-sm text-warning flex items-center gap-2"><X className="size-4" />This lead has no email — add one in Leads first.</p>
            : selectedLead?.email && <p className="text-sm text-success flex items-center gap-2"><CheckCircle2 className="size-4" />Ready to send to {selectedLead.email}</p>
          }
        </div>
      </Dialog>

      <Dialog open={!!previewTpl} onClose={() => setPreviewTpl(null)} title={previewTpl?.name ?? ""}
        footer={
          <>
            <Button onClick={() => setPreviewTpl(null)}>Close</Button>
              <Button variant="primary" onClick={() => {
                if (!previewTpl) return
                setComposeSub(previewTpl.subject)
                setComposeBody(previewTpl.body(agent?.persona.agent_name ?? "Jay", agent?.persona.company_name ?? ""))
                setPreviewTpl(null)
                setTab("compose")
              }}>
              <PenLine className="size-4" />Use this template
            </Button>
          </>
        }>
        {previewTpl && (
          <div className="space-y-4">
            <p className="text-sm text-muted">{previewTpl.description}</p>
              <div className="rounded-xl border border-border bg-surface-2 p-4">
                <p className="font-bold text-sm mb-1">Subject: {previewTpl.subject}</p>
                <pre className="mt-3 whitespace-pre-wrap text-xs leading-relaxed font-sans text-fg-2">
                  {previewTpl.body(agent?.persona.agent_name ?? "Jay", agent?.persona.company_name ?? "")}
                </pre>
              </div>
            <p className="text-xs text-muted">Click "Use this template" to load it into the composer, then hit "Draft with AI" for a fully personalised version.</p>
          </div>
        )}
      </Dialog>

      <CallSheet callId={callId} onClose={() => setCallId(null)} onOpenLead={(id) => { setCallId(null); navigate(path(`/leads/${id}`)) }} />
    </>
  )
}
