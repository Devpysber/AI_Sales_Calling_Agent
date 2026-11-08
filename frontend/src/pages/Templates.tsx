import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FileText, Plus, RefreshCw, Send } from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import {
  Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Sheet, Dialog, Skeleton, Textarea,
} from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import type { Lead, Page } from "@/lib/types"

/* ---------------- Types (mirror app/models/crm.py MessageTemplate + Meta's components shape) ---------------- */

type ButtonType = "QUICK_REPLY" | "URL" | "PHONE_NUMBER"
type HeaderFormat = "NONE" | "TEXT" | "IMAGE" | "VIDEO" | "DOCUMENT"

interface WaButton {
  type: ButtonType
  text: string
  url?: string
  phone_number?: string
  example?: string[]
}

interface WaComponent {
  type: "HEADER" | "BODY" | "FOOTER" | "BUTTONS"
  format?: HeaderFormat
  text?: string
  buttons?: WaButton[]
  example?: { header_text?: string[]; body_text?: string[][] }
}

interface MessageTemplate {
  id: number
  agent_id: number
  name: string
  channel: string
  body: string
  whatsapp_template_name: string | null
  whatsapp_template_language: string | null
  is_meta_approved: boolean
  category: string | null
  components: WaComponent[] | null
  header_format: string
  rejected_reason: string | null
  whatsapp_template_id: string | null
  status: "draft" | "pending" | "approved" | "rejected"
  created_at: string
}

const STATUS_TONE: Record<MessageTemplate["status"], "neutral" | "warning" | "success" | "danger"> = {
  draft: "neutral", pending: "warning", approved: "success", rejected: "danger",
}

const TEMPLATE_LANGUAGES: Record<string, string> = {
  en_US: "English (US)", en_GB: "English (UK)", en: "English", hi: "Hindi", es: "Spanish",
  pt_BR: "Portuguese (Brazil)", fr: "French", ar: "Arabic", id: "Indonesian", de: "German",
}

const CATEGORY_HELP: Record<string, string> = {
  MARKETING: "Promotions, offers, announcements. Costs more and faces stricter Meta approval.",
  UTILITY: "Order updates, appointment reminders — transactional, tied to an existing interaction.",
  AUTHENTICATION: "One-time passcodes and verification codes only.",
}

const MAX_BUTTONS = 3

/** Highest {{N}} placeholder used in a piece of template text; 0 if none. */
function varCount(text: string | undefined): number {
  const nums = [...(text ?? "").matchAll(/\{\{(\d+)\}\}/g)].map((m) => Number(m[1]))
  return nums.length ? Math.max(...nums) : 0
}

const emptyButton = (): WaButton => ({ type: "QUICK_REPLY", text: "" })

const emptyForm = {
  name: "", language: "en_US", category: "MARKETING" as "MARKETING" | "UTILITY" | "AUTHENTICATION",
  headerFormat: "NONE" as HeaderFormat, headerText: "", headerExample: "",
  bodyText: "", bodyExamples: [] as string[],
  footerText: "", buttons: [] as WaButton[],
}

/** Reverse of buildComponents(): pre-fills the builder form from an existing (draft) template. */
function templateToForm(t: MessageTemplate): typeof emptyForm {
  const components = t.components ?? []
  const header = components.find((c) => c.type === "HEADER")
  const body = components.find((c) => c.type === "BODY")
  const footer = components.find((c) => c.type === "FOOTER")
  const buttonsComp = components.find((c) => c.type === "BUTTONS")
  return {
    name: t.whatsapp_template_name ?? t.name,
    language: t.whatsapp_template_language ?? "en_US",
    category: (t.category as typeof emptyForm.category) ?? "MARKETING",
    headerFormat: (header?.format ?? "NONE") as HeaderFormat,
    headerText: header?.format === "TEXT" ? (header.text ?? "") : "",
    headerExample: header?.example?.header_text?.[0] ?? "",
    bodyText: body?.text ?? "",
    bodyExamples: body?.example?.body_text?.[0] ?? [],
    footerText: footer?.text ?? "",
    buttons: buttonsComp?.buttons ?? [],
  }
}

/** Meta rejects template creation outright if a {{n}} variable has no "example" value to show reviewers
 * — falls back to a generic placeholder so a template is never blocked just because the builder's
 * optional example fields were left blank. */
const placeholder = (v: string | undefined, i: number) => v?.trim() || `Example${i + 1}`

function buildComponents(form: typeof emptyForm): WaComponent[] {
  const comps: WaComponent[] = []
  if (form.headerFormat !== "NONE") {
    if (form.headerFormat === "TEXT") {
      const header: WaComponent = { type: "HEADER", format: "TEXT", text: form.headerText.trim() }
      if (varCount(form.headerText) > 0) header.example = { header_text: [placeholder(form.headerExample, 0)] }
      comps.push(header)
    } else comps.push({ type: "HEADER", format: form.headerFormat })
  }
  const body: WaComponent = { type: "BODY", text: form.bodyText }
  const bodyN = varCount(form.bodyText)
  if (bodyN > 0) body.example = { body_text: [Array.from({ length: bodyN }, (_, i) => placeholder(form.bodyExamples[i], i))] }
  comps.push(body)
  if (form.footerText.trim()) comps.push({ type: "FOOTER", text: form.footerText.trim() })
  if (form.buttons.length) {
    const buttons = form.buttons.map((b) => (b.type === "URL" && varCount(b.url) > 0) ? { ...b, example: [placeholder(undefined, 0)] } : b)
    comps.push({ type: "BUTTONS", buttons })
  }
  return comps
}

/** Required send-time variable slots, derived from {{N}} occurrences actually present in the stored
 * template (header text, body text, any URL button) — matches the server's _build_send_components keys. */
function sendSlots(components: WaComponent[] | null): { key: string; label: string; count: number }[] {
  const slots: { key: string; label: string; count: number }[] = []
  if (!components) return slots
  const header = components.find((c) => c.type === "HEADER")
  if (header?.format === "TEXT" && varCount(header.text) > 0) slots.push({ key: "header", label: "Header variable {{1}}", count: 1 })
  const body = components.find((c) => c.type === "BODY")
  const n = varCount(body?.text)
  if (n > 0) slots.push({ key: "body", label: `Body variable${n > 1 ? "s" : ""} (${n})`, count: n })
  const buttons = components.find((c) => c.type === "BUTTONS")?.buttons ?? []
  buttons.forEach((b, i) => { if (b.type === "URL" && varCount(b.url) > 0) slots.push({ key: `button_${i}`, label: `Button ${i + 1} URL variable {{1}}`, count: 1 }) })
  return slots
}

/* ---------------- WhatsApp-bubble style live preview ---------------- */

function TemplatePreview({ form }: { form: typeof emptyForm }) {
  const buttons = form.buttons.slice(0, MAX_BUTTONS)
  return (
    <div className="rounded-xl bg-[#e5ddd5] p-4 dark:bg-[#0b141a]">
      <div className="max-w-[280px] rounded-lg bg-[#d9fdd3] p-2.5 text-[13px] text-[#111b21] shadow-sm dark:text-[#111b21]">
        {form.headerFormat === "TEXT" && form.headerText && <p className="mb-1 font-bold whitespace-pre-wrap">{form.headerText}</p>}
        {form.headerFormat !== "NONE" && form.headerFormat !== "TEXT" && (
          <div className="mb-1.5 grid h-28 place-items-center rounded-md bg-black/10 text-xs font-semibold text-black/50">{form.headerFormat}</div>
        )}
        <p className="whitespace-pre-wrap">{form.bodyText || "Your message body appears here…"}</p>
        {form.footerText && <p className="mt-1 text-xs text-black/50">{form.footerText}</p>}
        <p className="mt-1 text-right text-[10px] text-black/40">12:00</p>
        {buttons.length > 0 && (
          <div className="-mx-2.5 -mb-2.5 mt-1.5 border-t border-black/10">
            {buttons.map((b, i) => (
              <div key={i} className="border-t border-black/10 py-1.5 text-center text-[13px] font-semibold text-[#00a5f4] first:border-t-0">{b.text || "Button"}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ---------------- Page ---------------- */

export default function Templates() {
  const { base, path } = useAgent()
  const qc = useQueryClient()
  const key = ["wa-templates", base]
  const list = useQuery({ queryKey: key, queryFn: () => api<MessageTemplate[]>(`${base}/whatsapp/templates`) })
  const templates = list.data ?? []
  const invalidate = () => qc.invalidateQueries({ queryKey: key })

  const [showBuilder, setShowBuilder] = useState(false)
  const [form, setForm] = useState(emptyForm)
  const [createdId, setCreatedId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [sending, setSending] = useState<MessageTemplate | null>(null)

  const create = useMutation({
    mutationFn: () => api<MessageTemplate>(`${base}/whatsapp/templates`, {
      method: "POST",
      json: { name: form.name.trim(), language: form.language, category: form.category, components: buildComponents(form) },
    }),
    onSuccess: (row) => { toast.success("Saved as draft"); setCreatedId(row.id); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const patch = useMutation({
    mutationFn: (id: number) => api<MessageTemplate>(`${base}/whatsapp/templates/${id}`, {
      method: "PATCH",
      json: { name: form.name.trim(), language: form.language, category: form.category, components: buildComponents(form) },
    }),
    onSuccess: () => { toast.success("Template updated"); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const submit = useMutation({
    mutationFn: (id: number) => api<MessageTemplate>(`${base}/whatsapp/templates/${id}/submit`, { method: "POST" }),
    onSuccess: () => { toast.success("Submitted to Meta for approval"); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const syncStatus = useMutation({
    mutationFn: (id: number) => api<MessageTemplate>(`${base}/whatsapp/templates/${id}/status`, { method: "GET" }),
    onSuccess: () => { toast.success("Status synced"); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const closeBuilder = () => { setShowBuilder(false); setForm(emptyForm); setCreatedId(null); setEditingId(null) }

  const bodyVarN = varCount(form.bodyText)
  const locked = !!createdId && !editingId

  return (
    <>
      <PageHeader eyebrow="Build" title="WhatsApp templates" description="Meta-approved message templates for notifications and re-engagement outside the 24-hour chat window."
        actions={<Button variant="primary" onClick={() => { setForm(emptyForm); setCreatedId(null); setEditingId(null); setShowBuilder(true) }}><Plus className="size-4" />New template</Button>} />

      {list.isLoading ? <Skeleton className="h-64" /> : templates.length === 0 ? (
        <Card><EmptyState icon={<FileText />} title="No templates yet" description="Create a template, submit it to Meta for approval, then send it to leads." /></Card>
      ) : (
        <div className="space-y-3">
          {templates.map((t) => (
            <Card key={t.id}>
              <CardHeader
                title={<span className="flex flex-wrap items-center gap-2">{t.name}<Badge tone="neutral">{t.category ?? "—"}</Badge><Badge tone={STATUS_TONE[t.status]}>{t.status}</Badge></span>}
                description={t.body}
                action={<div className="flex flex-wrap items-center gap-2">
                  {t.status === "pending" && <Button loading={syncStatus.isPending} onClick={() => syncStatus.mutate(t.id)}><RefreshCw className="size-4" />Sync status</Button>}
                  {t.status === "draft" && <Button onClick={() => { setForm(templateToForm(t)); setCreatedId(null); setEditingId(t.id); setShowBuilder(true) }}>Edit</Button>}
                  {t.status === "draft" && <Button variant="primary" loading={submit.isPending} onClick={() => submit.mutate(t.id)}>Submit for approval</Button>}
                  {t.status === "approved" && <Button variant="primary" onClick={() => setSending(t)}><Send className="size-4" />Send</Button>}
                </div>}
              />
              {t.status === "rejected" && t.rejected_reason && <p className="px-4 pb-4 text-xs text-danger sm:px-5">Rejected: {t.rejected_reason}</p>}
            </Card>
          ))}
        </div>
      )}

      {/* ---------------- Builder ---------------- */}
      <Sheet open={showBuilder} onClose={closeBuilder} title={editingId ? "Edit WhatsApp template" : "New WhatsApp template"} width="max-w-3xl"
        footer={<>
          <Button onClick={closeBuilder}>{createdId && !editingId ? "Close" : "Cancel"}</Button>
          {editingId ? (
            <Button variant="primary" loading={patch.isPending} onClick={() => patch.mutate(editingId)} disabled={!form.name.trim() || !form.bodyText.trim()}>Save changes</Button>
          ) : !createdId ? (
            <Button variant="primary" loading={create.isPending} onClick={() => create.mutate()} disabled={!form.name.trim() || !form.bodyText.trim()}>Save as draft</Button>
          ) : (
            <Button variant="primary" loading={submit.isPending} onClick={() => submit.mutate(createdId, { onSuccess: closeBuilder })}>Submit for approval</Button>
          )}
        </>}>
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-4">
            <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} disabled={locked} placeholder="order_confirmation" /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Language">
                <Select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })} disabled={locked}>
                  {Object.entries(TEMPLATE_LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </Select>
              </Field>
              <Field label="Category">
                <Select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as typeof form.category })} disabled={locked}>
                  <option value="MARKETING">Marketing</option><option value="UTILITY">Utility</option><option value="AUTHENTICATION">Authentication</option>
                </Select>
              </Field>
            </div>
            <p className="text-xs text-muted">{CATEGORY_HELP[form.category]}</p>

            <Field label="Header">
              <Select value={form.headerFormat} onChange={(e) => setForm({ ...form, headerFormat: e.target.value as HeaderFormat })} disabled={locked}>
                <option value="NONE">None</option><option value="TEXT">Text</option><option value="IMAGE">Image</option><option value="VIDEO">Video</option><option value="DOCUMENT">Document</option>
              </Select>
            </Field>
            {form.headerFormat === "TEXT" && (
              <Field label="Header text" hint="One {{1}} variable is allowed.">
                <Input value={form.headerText} onChange={(e) => setForm({ ...form, headerText: e.target.value })} disabled={locked} placeholder="Hi {{1}}!" />
              </Field>
            )}
            {(form.headerFormat === "IMAGE" || form.headerFormat === "VIDEO" || form.headerFormat === "DOCUMENT") && (
              <p className="text-xs text-muted">The actual {form.headerFormat.toLowerCase()} is supplied when you send the template, not here.</p>
            )}

            <Field label="Body" hint={`Use {{1}}, {{2}}… for variables. ${bodyVarN} variable${bodyVarN === 1 ? "" : "s"} used.`}>
              <Textarea rows={5} value={form.bodyText} onChange={(e) => setForm({ ...form, bodyText: e.target.value })} disabled={locked}
                placeholder="Hi {{1}}, your order {{2}} has shipped." />
            </Field>
            {bodyVarN > 0 && (
              <div className="grid gap-2">
                {Array.from({ length: bodyVarN }, (_, i) => (
                  <Field key={i} label={`Example for {{${i + 1}}}`}>
                    <Input value={form.bodyExamples[i] ?? ""} disabled={locked}
                      onChange={(e) => { const ex = [...form.bodyExamples]; ex[i] = e.target.value; setForm({ ...form, bodyExamples: ex }) }} />
                  </Field>
                ))}
              </div>
            )}

            <Field label="Footer (optional)"><Input value={form.footerText} onChange={(e) => setForm({ ...form, footerText: e.target.value })} disabled={locked} /></Field>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold text-fg-2">Buttons (up to {MAX_BUTTONS})</span>
                {form.buttons.length < MAX_BUTTONS && !locked && (
                  <Button size="sm" onClick={() => setForm({ ...form, buttons: [...form.buttons, emptyButton()] })}><Plus className="size-3.5" />Add button</Button>
                )}
              </div>
              {form.buttons.map((b, i) => (
                <div key={i} className="space-y-2 rounded-xl border border-border p-3">
                  <div className="grid grid-cols-2 gap-2">
                    <Select value={b.type} disabled={locked} onChange={(e) => {
                      const buttons = [...form.buttons]; buttons[i] = { type: e.target.value as ButtonType, text: b.text }; setForm({ ...form, buttons })
                    }}>
                      <option value="QUICK_REPLY">Quick reply</option><option value="URL">URL</option><option value="PHONE_NUMBER">Phone number</option>
                    </Select>
                    <Input placeholder="Button text" value={b.text} disabled={locked}
                      onChange={(e) => { const buttons = [...form.buttons]; buttons[i] = { ...b, text: e.target.value }; setForm({ ...form, buttons }) }} />
                  </div>
                  {b.type === "URL" && (
                    <Input placeholder="https://example.com/orders/{{1}}" value={b.url ?? ""} disabled={locked}
                      onChange={(e) => { const buttons = [...form.buttons]; buttons[i] = { ...b, url: e.target.value }; setForm({ ...form, buttons }) }} />
                  )}
                  {b.type === "PHONE_NUMBER" && (
                    <Input placeholder="+1 555 0100" value={b.phone_number ?? ""} disabled={locked}
                      onChange={(e) => { const buttons = [...form.buttons]; buttons[i] = { ...b, phone_number: e.target.value }; setForm({ ...form, buttons }) }} />
                  )}
                  {!locked && <Button size="sm" onClick={() => setForm({ ...form, buttons: form.buttons.filter((_, j) => j !== i) })}>Remove</Button>}
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-3 lg:sticky lg:top-0 lg:self-start">
            <span className="text-[13px] font-semibold text-fg-2">Preview</span>
            <TemplatePreview form={form} />
            <p className="text-xs text-muted">
              Submitting requires a WhatsApp Business Account ID configured for this agent — set it on the{" "}
              <Link to={path("/channels")} className="font-semibold text-brand hover:underline">Channels</Link> page.
            </p>
          </div>
        </div>
      </Sheet>

      {/* ---------------- Send dialog ---------------- */}
      <SendDialog base={base} template={sending} onClose={() => setSending(null)} />
    </>
  )
}

function SendDialog({ base, template, onClose }: { base: string; template: MessageTemplate | null; onClose: () => void }) {
  const [mode, setMode] = useState<"lead" | "phone">("lead")
  const [leadId, setLeadId] = useState<number | "">("")
  const [phone, setPhone] = useState("")
  const [values, setValues] = useState<Record<string, string[]>>({})

  const leadsQ = useQuery({
    queryKey: ["templates-leads", base],
    queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 100 } }),
    enabled: !!template,
  })

  const slots = useMemo(() => sendSlots(template?.components ?? null), [template])

  const send = useMutation({
    mutationFn: () => {
      if (!template) throw new Error("No template selected")
      const variables: Record<string, string[]> = {}
      for (const s of slots) variables[s.key] = Array.from({ length: s.count }, (_, i) => values[s.key]?.[i] ?? "")
      return api(`${base}/whatsapp/templates/${template.id}/send`, {
        method: "POST",
        json: { lead_id: mode === "lead" ? (leadId || undefined) : undefined, phone: mode === "phone" ? phone.trim() : undefined, variables },
      })
    },
    onSuccess: () => { toast.success("Template sent"); reset(); onClose() },
    onError: (e) => toast.error(e.message),
  })

  const reset = () => { setMode("lead"); setLeadId(""); setPhone(""); setValues({}) }

  return (
    <Dialog open={!!template} onClose={() => { reset(); onClose() }} title={`Send “${template?.name ?? ""}”`}
      footer={<>
        <Button onClick={() => { reset(); onClose() }}>Cancel</Button>
        <Button variant="primary" loading={send.isPending} disabled={mode === "lead" ? !leadId : !phone.trim()} onClick={() => send.mutate()}><Send className="size-4" />Send</Button>
      </>}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2">
          <Button variant={mode === "lead" ? "primary" : "secondary"} onClick={() => setMode("lead")}>Pick a lead</Button>
          <Button variant={mode === "phone" ? "primary" : "secondary"} onClick={() => setMode("phone")}>Phone number</Button>
        </div>
        {mode === "lead" ? (
          <Field label="Lead">
            <Select value={leadId} onChange={(e) => setLeadId(e.target.value ? Number(e.target.value) : "")} disabled={leadsQ.isLoading}>
              <option value="">Select a lead…</option>
              {(leadsQ.data?.items ?? []).map((l) => <option key={l.id} value={l.id}>{l.name || "Unnamed"} · {l.phone}</option>)}
            </Select>
          </Field>
        ) : (
          <Field label="Phone number" hint="Include country code."><Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" /></Field>
        )}
        {slots.length === 0 ? (
          <p className="text-sm text-muted">This template has no variables to fill in.</p>
        ) : slots.map((s) => (
          <div key={s.key} className="space-y-2">
            <span className="text-[13px] font-semibold text-fg-2">{s.label}</span>
            {Array.from({ length: s.count }, (_, i) => (
              <Input key={i} placeholder={`Value ${i + 1}`} value={values[s.key]?.[i] ?? ""}
                onChange={(e) => { const arr = [...(values[s.key] ?? [])]; arr[i] = e.target.value; setValues({ ...values, [s.key]: arr }) }} />
            ))}
          </div>
        ))}
      </div>
    </Dialog>
  )
}
