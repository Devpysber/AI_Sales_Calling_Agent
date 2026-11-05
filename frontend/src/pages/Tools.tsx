import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Plus, Trash2, Wrench } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import {
  Badge, Button, Card, CardHeader, Dialog, EmptyState, Field, Input, PageHeader, Select, Skeleton, Switch, Textarea,
} from "@/components/ui"
import { api } from "@/lib/api"
import { useAgent } from "@/lib/agent"

type Tool = {
  id: number; agent_id: number; name: string; description: string; type: "builtin" | "http" | "connector" | "mcp"
  parameters: Record<string, unknown> | null; config: { url?: string; method?: string; auth_type?: string; auth_header?: string } | null
  risk: "read" | "write" | "money"; channels: string[] | null; needs_confirmation: boolean
  timeout_seconds: number; enabled: boolean; created_at: string; updated_at: string
}

const emptyForm = {
  name: "", description: "", parameters: '{\n  "type": "object",\n  "properties": {}\n}',
  url: "", method: "GET", auth_type: "none", auth_header: "X-API-Key", secret: "",
  timeout_seconds: 10, risk: "read" as Tool["risk"],
}

export default function Tools() {
  const { base } = useAgent()
  const qc = useQueryClient()
  const key = ["tools", base]
  const list = useQuery({ queryKey: key, queryFn: () => api<Tool[]>(`${base}/tools`) })
  const tools = list.data ?? []

  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState(emptyForm)
  const [editing, setEditing] = useState<Tool | null>(null)
  const [testing, setTesting] = useState<Tool | null>(null)
  const [testArgs, setTestArgs] = useState("{}")
  const [testResult, setTestResult] = useState<unknown>(null)

  const invalidate = () => qc.invalidateQueries({ queryKey: key })

  const create = useMutation({
    mutationFn: () => {
      let parameters: Record<string, unknown>
      try { parameters = JSON.parse(form.parameters) } catch { throw new Error("Parameters must be valid JSON.") }
      return api(`${base}/tools`, {
        method: "POST",
        json: {
          name: form.name.trim(), description: form.description.trim(), parameters,
          url: form.url.trim(), method: form.method, auth_type: form.auth_type, auth_header: form.auth_header,
          secret: form.secret.trim() || undefined, timeout_seconds: form.timeout_seconds, risk: form.risk,
        },
      })
    },
    onSuccess: () => { toast.success("Tool created"); setShowAdd(false); setForm(emptyForm); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const patch = useMutation({
    mutationFn: (vars: { id: number; body: Record<string, unknown> }) => api(`${base}/tools/${vars.id}`, { method: "PATCH", json: vars.body }),
    onSuccess: () => { invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/tools/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast.success("Tool deleted"); invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const runTest = useMutation({
    mutationFn: (id: number) => {
      let args: Record<string, unknown>
      try { args = JSON.parse(testArgs) } catch { throw new Error("Arguments must be valid JSON.") }
      return api(`${base}/tools/${id}/test`, { method: "POST", json: { arguments: args } })
    },
    onSuccess: (res) => setTestResult(res),
    onError: (e) => { toast.error(e.message); setTestResult({ error: e.message }) },
  })

  return (
    <>
      <PageHeader eyebrow="Build" title="Tools" description="Builtin and custom actions this agent can call during a chat."
        actions={<Button variant="primary" onClick={() => { setForm(emptyForm); setShowAdd(true) }}><Plus className="size-4" />Add custom tool</Button>} />

      {list.isLoading ? <Skeleton className="h-64" /> : tools.length === 0 ? (
        <Card><EmptyState icon={<Wrench />} title="No tools yet" description="Add a custom HTTP tool to let this agent call your own API." /></Card>
      ) : (
        <Card>
          <CardHeader title="Tools" />
          <div className="divide-y divide-border">
            {tools.map((t) => (
              <div key={t.id} className="flex flex-wrap items-start justify-between gap-3 p-4 sm:p-5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">{t.name}</span>
                    <Badge tone="neutral">{t.type}</Badge>
                    <Badge tone={t.risk === "read" ? "neutral" : t.risk === "write" ? "warning" : "danger"}>{t.risk}</Badge>
                    {!t.enabled && <Badge tone="neutral">Disabled</Badge>}
                  </div>
                  <p className="mt-1 text-sm text-muted">{t.description || "No description."}</p>
                  {t.type === "http" && t.config?.url && <p className="mt-1 truncate text-xs text-muted">{t.config.method} {t.config.url}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Switch checked={t.enabled} onChange={(v) => patch.mutate({ id: t.id, body: { enabled: v } })} label={`Enable ${t.name}`} />
                  {t.type === "http" && <Button type="button" onClick={() => { setTestArgs("{}"); setTestResult(null); setTesting(t) }}>Test</Button>}
                  {t.type === "http" && <Button type="button" onClick={() => {
                    setEditing(t)
                    setForm({
                      name: t.name, description: t.description, parameters: JSON.stringify(t.parameters ?? {}, null, 2),
                      url: t.config?.url ?? "", method: t.config?.method ?? "GET", auth_type: t.config?.auth_type ?? "none",
                      auth_header: t.config?.auth_header ?? "X-API-Key", secret: "", timeout_seconds: t.timeout_seconds, risk: t.risk,
                    })
                  }}>Edit</Button>}
                  {t.type === "http" && (
                    <Button type="button" onClick={() => { if (confirm(`Delete tool “${t.name}”?`)) remove.mutate(t.id) }}><Trash2 className="size-4" /></Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Dialog open={showAdd} onClose={() => setShowAdd(false)} title="Add custom tool"
        footer={<><Button onClick={() => setShowAdd(false)}>Cancel</Button><Button variant="primary" loading={create.isPending} onClick={() => create.mutate()}>Create</Button></>}>
        <ToolForm form={form} setForm={setForm} />
      </Dialog>

      <Dialog open={!!editing} onClose={() => setEditing(null)} title={`Edit ${editing?.name ?? ""}`}
        footer={<>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button variant="primary" loading={patch.isPending} onClick={() => {
            if (!editing) return
            let parameters: Record<string, unknown>
            try { parameters = JSON.parse(form.parameters) } catch { toast.error("Parameters must be valid JSON."); return }
            patch.mutate({
              id: editing.id,
              body: {
                description: form.description.trim(), parameters, url: form.url.trim(), method: form.method,
                auth_type: form.auth_type, auth_header: form.auth_header, secret: form.secret.trim() || undefined,
                timeout_seconds: form.timeout_seconds, risk: form.risk,
              },
            }, { onSuccess: () => { setEditing(null); invalidate() } })
          }}>Save</Button>
        </>}>
        <ToolForm form={form} setForm={setForm} hideName />
      </Dialog>

      <Dialog open={!!testing} onClose={() => setTesting(null)} title={`Test ${testing?.name ?? ""}`}
        footer={<><Button onClick={() => setTesting(null)}>Close</Button><Button variant="primary" loading={runTest.isPending} onClick={() => testing && runTest.mutate(testing.id)}>Run</Button></>}>
        <div className="space-y-3">
          <Field label="Arguments (JSON)"><Textarea rows={4} value={testArgs} onChange={(e) => setTestArgs(e.target.value)} /></Field>
          {testResult != null && <pre className="max-h-64 overflow-auto rounded-xl bg-surface-2 p-3 text-xs">{JSON.stringify(testResult, null, 2)}</pre>}
        </div>
      </Dialog>
    </>
  )
}

// A raw-JSON textarea for `parameters` rather than a dynamic schema builder: a first slice for custom
// tools, acceptable per plan; a form builder is future work.
// Secrets are write-only: the API never returns secret_encrypted, so the field here is always blank —
// leaving it empty on edit keeps whatever secret is already stored; typing a value replaces it.
function ToolForm({ form, setForm, hideName }: { form: typeof emptyForm; setForm: (f: typeof emptyForm) => void; hideName?: boolean }) {
  return (
    <div className="space-y-4">
      {!hideName && <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>}
      <Field label="Description"><Textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
      <Field label="Parameters (JSON schema)"><Textarea rows={5} value={form.parameters} onChange={(e) => setForm({ ...form, parameters: e.target.value })} /></Field>
      <Field label="Target URL"><Input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://example.com/api/..." /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Method">
          <Select value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
            {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
        </Field>
        <Field label="Risk tier">
          <Select value={form.risk} onChange={(e) => setForm({ ...form, risk: e.target.value as typeof form.risk })}>
            <option value="read">read</option><option value="write">write</option><option value="money">money</option>
          </Select>
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Auth type">
          <Select value={form.auth_type} onChange={(e) => setForm({ ...form, auth_type: e.target.value })}>
            <option value="none">none</option><option value="bearer">bearer</option><option value="api_key">api_key</option>
          </Select>
        </Field>
        <Field label="Auth header" hint="Used for api_key auth."><Input value={form.auth_header} onChange={(e) => setForm({ ...form, auth_header: e.target.value })} /></Field>
      </div>
      <Field label="Secret" hint="Bearer token or API key value. Leave blank to keep the stored value unchanged.">
        <Input type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} autoComplete="off" />
      </Field>
      <Field label="Timeout (seconds)">
        <Input type="number" min={1} max={60} value={form.timeout_seconds} onChange={(e) => setForm({ ...form, timeout_seconds: Number(e.target.value) || 10 })} />
      </Field>
    </div>
  )
}
