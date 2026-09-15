import { useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, CircleAlert, Download, FileSpreadsheet, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, Field, Input, PageHeader, Select, Switch } from '@/components/ui'
import { api } from '@/lib/api'
import { cn, LANGUAGES } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

type Preview = { rows: number; columns: string[]; mapping: Record<string, string>; sample: Record<string, string>[] }
type Result = { created: number; skipped_duplicates: number; errors: { row: number; error: string }[] }

const FIELDS = [
  ['', 'Ignore'], ['name', 'Name'], ['phone', 'Phone *'], ['company', 'Company'], ['email', 'Email'], ['city', 'City'],
  ['language', 'Language'], ['source', 'Source'], ['tags', 'Tags'], ['notes', 'Notes'], ['status', 'Status'],
] as const

const TEMPLATE = 'Name,Company,Phone,Email,City,Language,Tags,Notes\nRahul Sharma,Acme Pvt Ltd,9876543210,rahul@acme.in,Bhopal,Hindi,webinar,Interested in automation\nPriya Nair,Nair Traders,+91 98450 12345,priya@nair.in,Kochi,English,referral,\n'

export default function Import() {
  const { agent, base, path } = useAgent()
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [opts, setOpts] = useState({ skip: true, language: 'en-IN', tags: '', source: 'import' })
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  const choose = async (f: File) => {
    setBusy(true)
    const fd = new FormData()
    fd.append('file', f)
    try {
      const p = await api<Preview>(`${base}/leads/import/preview`, { method: 'POST', body: fd })
      setFile(f); setPreview(p); setMapping(Object.fromEntries(p.columns.map((c) => [c, p.mapping[c] ?? ''])))
      setStep(2)
    } catch (e) {
      toast.error('Could not read file', { description: (e as Error).message })
    } finally { setBusy(false) }
  }

  const run = async () => {
    if (!file) return
    setBusy(true)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('mapping', JSON.stringify(mapping))
    fd.append('skip_duplicates', String(opts.skip))
    fd.append('default_language', opts.language)
    fd.append('tags', opts.tags)
    fd.append('source', opts.source)
    try {
      const r = await api<Result>(`${base}/leads/import`, { method: 'POST', body: fd })
      setResult(r); setStep(3)
      qc.invalidateQueries({ queryKey: ['leads'] })
      toast.success(`Imported ${r.created} lead${r.created === 1 ? '' : 's'}`)
    } catch (e) {
      toast.error('Import failed', { description: (e as Error).message })
    } finally { setBusy(false) }
  }

  const hasPhone = Object.values(mapping).includes('phone')
  const reset = () => { setStep(1); setFile(null); setPreview(null); setResult(null); if (input.current) input.current.value = '' }

  return (
    <>
      <PageHeader eyebrow={<>{agent?.name} · Import</>} title="Import leads" description={`Bring prospects into ${agent?.name ?? 'this agent'} from CSV or Excel. Duplicates are checked against this agent only. Review the column mapping before anything is saved.`}
        actions={<Button onClick={() => {
          const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([TEMPLATE], { type: 'text/csv' })); a.download = 'leads_template.csv'; a.click()
        }}><Download />Template</Button>} />

      <ol className="mb-6 flex flex-wrap items-center gap-2 text-sm">
        {['Upload file', 'Map columns', 'Done'].map((label, i) => (
          <li key={label} className="flex items-center gap-2">
            <span className={cn('grid size-6 place-items-center rounded-full text-xs font-semibold', step > i ? 'bg-brand text-brand-fg' : 'bg-surface-2 text-muted ring-1 ring-border')}>{i + 1}</span>
            <span className={cn(step > i ? 'font-medium text-fg' : 'text-muted')}>{label}</span>
            {i < 2 && <span className="mx-2 h-px w-8 bg-border" />}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <Card className="p-6">
          <button type="button" onClick={() => input.current?.click()} disabled={busy}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) void choose(f) }}
            className={cn('flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-16 transition',
              drag ? 'border-brand bg-brand-soft' : 'border-border-strong bg-surface-2/50 hover:border-brand hover:bg-brand-soft/40')}>
            <span className="mb-4 grid size-12 place-items-center rounded-xl bg-brand-soft text-brand"><FileSpreadsheet className="size-6" /></span>
            <span className="text-base font-semibold">{busy ? 'Reading file…' : 'Drop your file here or click to browse'}</span>
            <span className="mt-1 text-sm text-muted">CSV, XLSX or XLS · up to 20 MB · first row must be headers</span>
          </button>
          <input ref={input} type="file" accept=".csv,.xlsx,.xls" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void choose(f) }} />
          <div className="mt-6 grid gap-4 text-sm sm:grid-cols-3">
            <div className="rounded-lg border border-border p-4"><div className="font-medium">Phone is required</div><p className="mt-1 text-muted">Detected from Phone, Mobile, Contact Number, WhatsApp. 10-digit numbers get +91.</p></div>
            <div className="rounded-lg border border-border p-4"><div className="font-medium">Duplicates handled</div><p className="mt-1 text-muted">Numbers already in your CRM are skipped, so re-importing is safe.</p></div>
            <div className="rounded-lg border border-border p-4"><div className="font-medium">Language per lead</div><p className="mt-1 text-muted">Add a Language column (Hindi, English, hi-IN…) so the agent greets in it.</p></div>
          </div>
        </Card>
      )}

      {step === 2 && preview && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <Card className="min-w-0 overflow-hidden">
            <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
              <div className="min-w-0"><div className="truncate font-semibold">{file?.name}</div><div className="text-sm text-muted">{preview.rows} rows · preview of first {preview.sample.length}</div></div>
              <Button variant="ghost" onClick={reset}><ArrowLeft />Change file</Button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-border bg-surface-2/60">
                  <tr>{preview.columns.map((c) => (
                    <th key={c} className="min-w-44 px-3 py-3 text-left align-top">
                      <div className="mb-1.5 truncate text-xs font-medium text-muted">{c}</div>
                      <Select value={mapping[c]} onChange={(e) => setMapping({ ...mapping, [c]: e.target.value })}
                        className={cn('h-8 text-[13px]', mapping[c] ? 'border-brand/40 bg-brand-soft/40' : '')}>
                        {FIELDS.map(([v, l]) => <option key={v} value={v} disabled={!!v && v !== mapping[c] && Object.values(mapping).includes(v)}>{l}</option>)}
                      </Select>
                    </th>
                  ))}</tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {preview.sample.map((row, i) => (
                    <tr key={i}>{preview.columns.map((c) => <td key={c} className={cn('max-w-56 truncate px-3 py-2', !mapping[c] && 'text-muted/60')}>{row[c]}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="h-fit p-5">
            <h3 className="font-semibold">Import options</h3>
            <div className="mt-4 space-y-4">
              <Field label="Default language" hint="Used when a row has no language">
                <Select value={opts.language} onChange={(e) => setOpts({ ...opts, language: e.target.value })}>{Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
              </Field>
              <Field label="Tag all leads" hint="e.g. campaign name"><Input value={opts.tags} onChange={(e) => setOpts({ ...opts, tags: e.target.value })} placeholder="diwali-campaign" /></Field>
              <Field label="Source"><Input value={opts.source} onChange={(e) => setOpts({ ...opts, source: e.target.value })} /></Field>
              <div className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                <div><div className="text-sm font-medium">Skip duplicates</div><div className="text-xs text-muted">By phone number</div></div>
                <Switch checked={opts.skip} onChange={(v) => setOpts({ ...opts, skip: v })} label="Skip duplicates" />
              </div>
              {!hasPhone && <p className="flex gap-2 rounded-lg bg-danger-soft p-3 text-sm text-danger"><CircleAlert className="mt-0.5 size-4 shrink-0" />Map one column to Phone to continue.</p>}
              <Button variant="primary" size="lg" className="w-full" disabled={!hasPhone} loading={busy} onClick={run}><Upload />Import {preview.rows} rows</Button>
            </div>
          </Card>
        </div>
      )}

      {step === 3 && result && (
        <Card className="p-8">
          <div className="flex flex-col items-center text-center">
            <span className="grid size-14 place-items-center rounded-full bg-success-soft text-success"><CheckCircle2 className="size-7" /></span>
            <h2 className="mt-4 text-xl font-semibold">Import complete</h2>
            <p className="mt-1 text-muted">New leads are marked <Badge tone="brand">New</Badge> and will be called by auto-dial when it's switched on.</p>
          </div>
          <div className="mx-auto mt-8 grid max-w-2xl gap-4 sm:grid-cols-3">
            {[['Imported', result.created, 'text-success'], ['Duplicates skipped', result.skipped_duplicates, 'text-fg'], ['Invalid rows', result.errors.length, result.errors.length ? 'text-danger' : 'text-fg']].map(([l, v, c]) => (
              <div key={l as string} className="rounded-xl border border-border p-4 text-center"><div className={`text-3xl font-semibold tabular-nums ${c}`}>{v}</div><div className="mt-1 text-sm text-muted">{l}</div></div>
            ))}
          </div>
          {result.errors.length > 0 && (
            <div className="mx-auto mt-6 max-h-56 max-w-2xl overflow-y-auto rounded-lg border border-border">
              {result.errors.slice(0, 100).map((e) => <div key={e.row} className="flex gap-3 border-b border-border px-4 py-2 text-sm last:border-0"><span className="w-16 text-muted">Row {e.row}</span><span>{e.error}</span></div>)}
            </div>
          )}
          <div className="mt-8 flex flex-wrap justify-center gap-2">
            <Button onClick={reset}>Import another file</Button>
            <Link to={path('/automation')}><Button>Set up auto-dial</Button></Link>
            <Link to={path('/leads')}><Button variant="primary">View leads</Button></Link>
          </div>
        </Card>
      )}
    </>
  )
}
