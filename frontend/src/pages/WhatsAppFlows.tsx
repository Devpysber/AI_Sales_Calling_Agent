import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { LayoutGrid, Plus, Send, Trash2, UploadCloud } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"
import {
  Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Sheet, Dialog, Skeleton, Switch,
  Textarea,
} from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"
import type { Lead, Page } from "@/lib/types"

/* ---------------- Types (mirror app/models/crm.py WhatsAppFlow + Meta's Flow JSON shape) ---------------- */

type FieldType =
  | "TextHeading" | "TextSubheading" | "TextBody" | "TextInput" | "TextArea"
  | "Dropdown" | "RadioButtonsGroup" | "CheckboxGroup" | "Footer"

type InputType = "text" | "phone" | "email" | "number"

interface ScreenField {
  type: FieldType
  text?: string // TextHeading / TextSubheading / TextBody / Footer label
  name?: string // TextInput / TextArea / Dropdown / RadioButtonsGroup / CheckboxGroup
  label?: string
  required?: boolean
  inputType?: InputType // TextInput only
  options?: string[] // Dropdown / RadioButtonsGroup / CheckboxGroup
}

interface ScreenDef {
  id: string
  title: string
  terminal: boolean
  fields: ScreenField[]
}

interface WhatsAppFlow {
  id: number
  agent_id: number
  name: string
  category: string
  screens: ScreenDef[] // decoded from screens_json by the API
  meta_flow_id: string | null
  status: "draft" | "created" | "published"
  created_at: string
}

const STATUS_TONE: Record<WhatsAppFlow["status"], "neutral" | "warning" | "success"> = {
  draft: "neutral", created: "warning", published: "success",
}

const FIELD_TYPES: { value: FieldType; label: string; captures: boolean; choices: boolean }[] = [
  { value: "TextHeading", label: "Heading text", captures: false, choices: false },
  { value: "TextSubheading", label: "Subheading text", captures: false, choices: false },
  { value: "TextBody", label: "Body text", captures: false, choices: false },
  { value: "TextInput", label: "Text input", captures: true, choices: false },
  { value: "TextArea", label: "Text area", captures: true, choices: false },
  { value: "Dropdown", label: "Dropdown", captures: true, choices: true },
  { value: "RadioButtonsGroup", label: "Radio buttons", captures: true, choices: true },
  { value: "CheckboxGroup", label: "Checkboxes", captures: true, choices: true },
]

const emptyField = (): ScreenField => ({ type: "TextInput", name: "", label: "", required: true, inputType: "text" })

const emptyScreen = (id: string, terminal: boolean): ScreenDef => ({
  id, title: terminal ? "Thanks!" : "Get a quote", terminal,
  fields: terminal
    ? [{ type: "TextBody", text: "We've got your details and will be in touch shortly." },
       { type: "Footer", text: "Done" }]
    : [{ type: "TextHeading", text: "Tell us about your needs" }, emptyField(), { type: "Footer", text: "Continue" }],
})

const emptyFlow = { name: "", category: "LEAD_GENERATION", screens: [emptyScreen("WELCOME", false), emptyScreen("CONFIRM", true)] }

/** Every field name ("form.<name>") captured anywhere across the whole flow -- Meta's `${form.x}` values
 * persist across screens, so a terminal screen's payload can reference fields collected on earlier ones. */
function allFieldNames(screens: ScreenDef[]): string[] {
  const names: string[] = []
  for (const s of screens) for (const f of s.fields) if (f.name && !names.includes(f.name)) names.push(f.name)
  return names
}

/** Build Meta's real Flow `screens` array from the builder's state. */
function buildScreens(screens: ScreenDef[]): Record<string, unknown>[] {
  return screens.map((screen, i) => {
    // Only fields collected on this screen or an earlier one exist in `form.*` by the time this
    // screen's footer fires -- a field defined on a later screen has no value to pass forward yet.
    const namesSoFar = allFieldNames(screens.slice(0, i + 1))
    const children = screen.fields.map((f) => {
      if (f.type === "Footer") {
        const payload = Object.fromEntries(namesSoFar.map((n) => [n, `\${form.${n}}`]))
        const next = screens[i + 1]
        return {
          type: "Footer", label: f.text || (screen.terminal ? "Done" : "Continue"),
          "on-click-action": screen.terminal
            ? { name: "complete", payload }
            : { name: "navigate", next: { type: "screen", name: next?.id || "" }, payload },
        }
      }
      if (f.type === "TextHeading" || f.type === "TextSubheading" || f.type === "TextBody") {
        return { type: f.type, text: f.text || "" }
      }
      if (f.type === "TextInput") {
        return { type: "TextInput", name: f.name, label: f.label, required: !!f.required, "input-type": f.inputType || "text" }
      }
      if (f.type === "TextArea") {
        return { type: "TextArea", name: f.name, label: f.label, required: !!f.required }
      }
      // Dropdown / RadioButtonsGroup / CheckboxGroup
      return {
        type: f.type, name: f.name, label: f.label, required: !!f.required,
        "data-source": (f.options || []).map((o, idx) => ({ id: `opt${idx}`, title: o })),
      }
    })
    return { id: screen.id, title: screen.title, terminal: screen.terminal, layout: { type: "SingleColumnLayout", children } }
  })
}

/* ---------------- Page ---------------- */

export default function WhatsAppFlows() {
  const { base } = useAgent()
  const qc = useQueryClient()
  const key = ["wa-flows", base]
  const list = useQuery({ queryKey: key, queryFn: () => api<WhatsAppFlow[]>(`${base}/whatsapp/flows`) })
  const flows = list.data ?? []
  const invalidate = () => qc.invalidateQueries({ queryKey: key })

  const [showBuilder, setShowBuilder] = useState(false)
  const [flow, setFlow] = useState(emptyFlow)
  const [sending, setSending] = useState<WhatsAppFlow | null>(null)

  const create = useMutation({
    mutationFn: () => api<WhatsAppFlow>(`${base}/whatsapp/flows`, {
      method: "POST", json: { name: flow.name.trim(), category: flow.category, screens: buildScreens(flow.screens) },
    }),
    onSuccess: () => { toast.success("Saved as draft"); setShowBuilder(false); setFlow(emptyFlow); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const publish = useMutation({
    mutationFn: (id: number) => api<WhatsAppFlow>(`${base}/whatsapp/flows/${id}/publish`, { method: "POST" }),
    onSuccess: () => { toast.success("Published to Meta"); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/whatsapp/flows/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("Deleted"); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const preview = useMemo(() => JSON.stringify({ version: "3.1", screens: buildScreens(flow.screens) }, null, 2), [flow.screens])

  const setScreen = (i: number, patch: Partial<ScreenDef>) =>
    setFlow({ ...flow, screens: flow.screens.map((s, j) => (j === i ? { ...s, ...patch } : s)) })
  const setField = (si: number, fi: number, patch: Partial<ScreenField>) =>
    setScreen(si, { fields: flow.screens[si].fields.map((f, j) => (j === fi ? { ...f, ...patch } : f)) })

  const addScreen = () => {
    const n = flow.screens.length
    const id = `SCREEN_${n}`
    // keep exactly one terminal screen, always last
    const nonTerminal = flow.screens.filter((s) => !s.terminal)
    const terminal = flow.screens.find((s) => s.terminal)
    setFlow({ ...flow, screens: [...nonTerminal, emptyScreen(id, false), terminal!].filter(Boolean) as ScreenDef[] })
  }
  const removeScreen = (i: number) => {
    if (flow.screens.length <= 2) return // at least one data screen + the terminal screen
    if (flow.screens[i].terminal) return // terminal screen can't be removed
    setFlow({ ...flow, screens: flow.screens.filter((_, j) => j !== i) })
  }

  return (
    <>
      <PageHeader eyebrow="Build" title="WhatsApp Flows" description="Native multi-screen forms inside a WhatsApp chat — lead capture, appointment booking, surveys."
        actions={<Button variant="primary" onClick={() => { setFlow(emptyFlow); setShowBuilder(true) }}><Plus className="size-4" />New flow</Button>} />

      {list.isLoading ? <Skeleton className="h-64" /> : flows.length === 0 ? (
        <Card><EmptyState icon={<LayoutGrid />} title="No flows yet" description="Build a flow, publish it to Meta, then send it to a lead." /></Card>
      ) : (
        <div className="space-y-3">
          {flows.map((f) => (
            <Card key={f.id}>
              <CardHeader
                title={<span className="flex flex-wrap items-center gap-2">{f.name}<Badge tone="neutral">{f.category}</Badge><Badge tone={STATUS_TONE[f.status]}>{f.status}</Badge></span>}
                description={`${f.screens.length} screen${f.screens.length === 1 ? "" : "s"}`}
                action={<div className="flex flex-wrap items-center gap-2">
                  {f.status !== "published" && (
                    <Button variant="primary" loading={publish.isPending} onClick={() => publish.mutate(f.id)}><UploadCloud className="size-4" />Publish</Button>
                  )}
                  {f.status === "published" && (
                    <Button variant="primary" onClick={() => setSending(f)}><Send className="size-4" />Send to lead</Button>
                  )}
                  {f.status === "draft" && (
                    <Button onClick={() => remove.mutate(f.id)}><Trash2 className="size-4" />Delete</Button>
                  )}
                </div>}
              />
            </Card>
          ))}
        </div>
      )}

      {/* ---------------- Builder ---------------- */}
      <Sheet open={showBuilder} onClose={() => setShowBuilder(false)} title="New WhatsApp Flow" width="max-w-4xl"
        footer={<>
          <Button onClick={() => setShowBuilder(false)}>Cancel</Button>
          <Button variant="primary" loading={create.isPending} disabled={!flow.name.trim()} onClick={() => create.mutate()}>Save as draft</Button>
        </>}>
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-4">
            <Field label="Name"><Input value={flow.name} onChange={(e) => setFlow({ ...flow, name: e.target.value })} placeholder="Get a quote" /></Field>
            <Field label="Category">
              <Select value={flow.category} onChange={(e) => setFlow({ ...flow, category: e.target.value })}>
                <option value="LEAD_GENERATION">Lead generation</option>
                <option value="APPOINTMENT_BOOKING">Appointment booking</option>
                <option value="SURVEY">Survey</option>
                <option value="OTHER">Other</option>
              </Select>
            </Field>

            <div className="flex items-center justify-between">
              <span className="text-[13px] font-semibold text-fg-2">Screens</span>
              <Button size="sm" onClick={addScreen} disabled={flow.screens.length >= 4}><Plus className="size-3.5" />Add screen</Button>
            </div>

            {flow.screens.map((screen, si) => (
              <div key={screen.id} className="space-y-3 rounded-xl border border-border p-3">
                <div className="flex items-center justify-between gap-2">
                  <Badge tone={screen.terminal ? "success" : "neutral"}>{screen.terminal ? "Terminal screen" : `Screen ${si + 1}`}</Badge>
                  {!screen.terminal && flow.screens.length > 2 && (
                    <Button size="sm" onClick={() => removeScreen(si)}>Remove</Button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Screen ID"><Input value={screen.id} onChange={(e) => setScreen(si, { id: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} /></Field>
                  <Field label="Title"><Input value={screen.title} onChange={(e) => setScreen(si, { title: e.target.value })} /></Field>
                </div>

                {screen.fields.map((f, fi) => (
                  <div key={fi} className="space-y-2 rounded-lg bg-bg-2 p-2.5">
                    <div className="flex items-center gap-2">
                      <Select value={f.type} onChange={(e) => setField(si, fi, { type: e.target.value as FieldType })} disabled={f.type === "Footer"}>
                        {f.type === "Footer"
                          ? <option value="Footer">Footer (submit button)</option>
                          : FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </Select>
                      {f.type !== "Footer" && (
                        <Button size="sm" onClick={() => setScreen(si, { fields: screen.fields.filter((_, j) => j !== fi) })}>Remove</Button>
                      )}
                    </div>

                    {(f.type === "TextHeading" || f.type === "TextSubheading" || f.type === "TextBody") && (
                      <Textarea rows={2} placeholder="Text" value={f.text ?? ""} onChange={(e) => setField(si, fi, { text: e.target.value })} />
                    )}
                    {f.type === "Footer" && (
                      <Input placeholder="Button label" value={f.text ?? ""} onChange={(e) => setField(si, fi, { text: e.target.value })} />
                    )}
                    {(f.type === "TextInput" || f.type === "TextArea" || f.type === "Dropdown" || f.type === "RadioButtonsGroup" || f.type === "CheckboxGroup") && (
                      <div className="grid grid-cols-2 gap-2">
                        <Input placeholder="Field name (e.g. full_name)" value={f.name ?? ""}
                          onChange={(e) => setField(si, fi, { name: e.target.value.replace(/[^a-zA-Z0-9_]/g, "_") })} />
                        <Input placeholder="Label shown to the customer" value={f.label ?? ""} onChange={(e) => setField(si, fi, { label: e.target.value })} />
                      </div>
                    )}
                    {f.type === "TextInput" && (
                      <Select value={f.inputType ?? "text"} onChange={(e) => setField(si, fi, { inputType: e.target.value as InputType })}>
                        <option value="text">Text</option><option value="phone">Phone</option><option value="email">Email</option><option value="number">Number</option>
                      </Select>
                    )}
                    {(f.type === "Dropdown" || f.type === "RadioButtonsGroup" || f.type === "CheckboxGroup") && (
                      <Textarea rows={2} placeholder="One option per line" value={(f.options ?? []).join("\n")}
                        onChange={(e) => setField(si, fi, { options: e.target.value.split("\n").map((o) => o.trim()).filter(Boolean) })} />
                    )}
                    {(f.type === "TextInput" || f.type === "TextArea" || f.type === "Dropdown" || f.type === "RadioButtonsGroup" || f.type === "CheckboxGroup") && (
                      <Switch checked={!!f.required} onChange={(v) => setField(si, fi, { required: v })} label="Required" />
                    )}
                  </div>
                ))}

                {!screen.terminal && (
                  <Button size="sm" onClick={() => setScreen(si, { fields: [...screen.fields.slice(0, -1), emptyField(), screen.fields[screen.fields.length - 1]] })}>
                    <Plus className="size-3.5" />Add field
                  </Button>
                )}
              </div>
            ))}
          </div>

          <div className="space-y-2 lg:sticky lg:top-0 lg:self-start">
            <span className="text-[13px] font-semibold text-fg-2">Flow JSON preview</span>
            <pre className="max-h-[560px] overflow-auto rounded-xl border border-border bg-bg-2 p-3 text-[11px] leading-relaxed">{preview}</pre>
          </div>
        </div>
      </Sheet>

      {/* ---------------- Send dialog ---------------- */}
      <SendFlowDialog base={base} flow={sending} onClose={() => setSending(null)} />
    </>
  )
}

function SendFlowDialog({ base, flow, onClose }: { base: string; flow: WhatsAppFlow | null; onClose: () => void }) {
  const [mode, setMode] = useState<"lead" | "phone">("lead")
  const [leadId, setLeadId] = useState<number | "">("")
  const [phone, setPhone] = useState("")
  const [bodyText, setBodyText] = useState("Let's get started")
  const [ctaText, setCtaText] = useState("Start")

  const leadsQ = useQuery({
    queryKey: ["flows-leads", base],
    queryFn: () => api<Page<Lead>>(`${base}/leads`, { params: { page_size: 100 } }),
    enabled: !!flow,
  })

  const send = useMutation({
    mutationFn: () => {
      if (!flow) throw new Error("No flow selected")
      return api(`${base}/whatsapp/flows/${flow.id}/send`, {
        method: "POST",
        json: {
          lead_id: mode === "lead" ? (leadId || undefined) : undefined,
          phone: mode === "phone" ? phone.trim() : undefined,
          body_text: bodyText, cta_text: ctaText,
        },
      })
    },
    onSuccess: () => { toast.success("Flow sent"); reset(); onClose() },
    onError: (e) => toast.error(e.message),
  })

  const reset = () => { setMode("lead"); setLeadId(""); setPhone(""); setBodyText("Let's get started"); setCtaText("Start") }

  return (
    <Dialog open={!!flow} onClose={() => { reset(); onClose() }} title={`Send “${flow?.name ?? ""}”`}
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
        <Field label="Message"><Input value={bodyText} onChange={(e) => setBodyText(e.target.value)} /></Field>
        <Field label="Button text"><Input value={ctaText} onChange={(e) => setCtaText(e.target.value)} /></Field>
      </div>
    </Dialog>
  )
}
