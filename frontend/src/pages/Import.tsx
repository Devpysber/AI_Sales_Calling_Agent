import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, CheckCircle2, CircleAlert, Copy as CopyIcon, Download, FileSpreadsheet, ListPlus, Loader2, RefreshCw, Upload, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Button, Card, Field, Input, PageHeader, Select, Switch } from '@/components/ui'
import { api } from '@/lib/api'
import { Stagger } from '@/lib/motion'
import { cn, LANGUAGES } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

type RowState = { state: 'ready' | 'duplicate' | 'invalid'; detail: string }
type Analysis = { ready: number; duplicates: number; invalid: number; row_status: RowState[] }
type Preview = { rows: number; columns: string[]; mapping: Record<string, string>; sample: Record<string, string>[]; analysis: Analysis }
type Result = { created: number; updated?: number; skipped_duplicates: number; errors: { row: number; error: string }[]; batch_tag?: string }

const FIELDS = [
  ['', 'Ignore'], ['name', 'Name'], ['phone', 'Phone *'], ['company', 'Company'], ['email', 'Email'], ['city', 'City'],
  ['language', 'Language'], ['source', 'Source'], ['tags', 'Tags'], ['notes', 'Notes'], ['status', 'Stage'],
] as const

const TEMPLATE = 'Name,Company,Phone,Email,City,Language,Tags,Notes\nRahul Sharma,Acme Pvt Ltd,9876543210,rahul@acme.in,Bhopal,Hindi,webinar,Interested in automation\nPriya Nair,Nair Traders,+91 98450 12345,priya@nair.in,Kochi,English,referral,\n'

const STATE_UI = {
  ready: { icon: CheckCircle2, className: 'text-success', label: 'Ready' },
  duplicate: { icon: CopyIcon, className: 'text-warning', label: 'Duplicate' },
  invalid: { icon: XCircle, className: 'text-danger', label: 'Invalid' },
}

export default function Import() {
  const { agent, base, path } = useAgent()
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [checking, setChecking] = useState(false)
  const [opts, setOpts] = useState({ onDuplicate: 'skip' as 'skip' | 'update', language: 'en-IN', tags: '', source: 'import', queue: false })
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  const choose = async (f: File) => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
    if (!['.csv', '.xlsx', '.xls'].includes(ext)) return void toast.error('Unsupported file', { description: 'Upload a .csv, .xlsx or .xls file.' })
    if (f.size > 20 * 1024 * 1024) return void toast.error('File too large', { description: 'The limit is 20 MB.' })
    setBusy(true)
    const fd = new FormData()
    fd.append('file', f)
    try {
      const p = await api<Preview>(`${base}/leads/import/preview`, { method: 'POST', body: fd })
      if (!p.rows) throw new Error('The file has headers but no rows.')
      setFile(f); setPreview(p); setAnalysis(p.analysis)
      setMapping(Object.fromEntries(p.columns.map((c) => [c, p.mapping[c] ?? ''])))
      setStep(2)
    } catch (e) {
      toast.error('Could not read file', { description: (e as Error).message })
    } finally { setBusy(false) }
  }

  // Re-check rows whenever the mapping changes (debounced): the counts always match what Import will do.
  const firstRun = useRef(true)
  useEffect(() => {
    if (!file || step !== 2) return
    if (firstRun.current) { firstRun.current = false; return }
    const t = setTimeout(async () => {
      setChecking(true)
      const fd = new FormData()
      fd.append('file', file)
      fd.append('mapping', JSON.stringify(mapping))
      try { setAnalysis(await api<Analysis>(`${base}/leads/import/analyze`, { method: 'POST', body: fd })) } catch { /* keep last counts */ }
      finally { setChecking(false) }
    }, 350)
    return () => clearTimeout(t)
  }, [mapping, file, step, base])

  const run = async () => {
    if (!file) return
    setBusy(true)
    const fd = new FormData()
    fd.append('file', file)
    fd.append('mapping', JSON.stringify(mapping))
    fd.append('on_duplicate', opts.onDuplicate)
    fd.append('skip_duplicates', String(opts.onDuplicate === 'skip'))
    fd.append('default_language', opts.language)
    fd.append('tags', opts.tags)
    fd.append('source', opts.source)
    fd.append('queue_for_calls', String(opts.queue))
    try {
      const r = await api<Result>(`${base}/leads/import`, { method: 'POST', body: fd })
      setResult(r); setStep(3)
      qc.invalidateQueries({ queryKey: ['leads'] })
      toast.success(`Imported ${r.created} lead${r.created === 1 ? '' : 's'}${r.updated ? `, updated ${r.updated}` : ''}`)
    } catch (e) {
      toast.error('Import failed', { description: (e as Error).message })
    } finally { setBusy(false) }
  }

  const hasPhone = Object.values(mapping).includes('phone')
  const reset = () => { setStep(1); setFile(null); setPreview(null); setAnalysis(null); setResult(null); firstRun.current = true; if (input.current) input.current.value = '' }
  const willImport = analysis ? analysis.ready + (opts.onDuplicate === 'update' ? analysis.duplicates : 0) : 0
  const downloadTemplate = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([TEMPLATE], { type: 'text/csv' }))
    a.download = 'leads_template.csv'
    a.click()
  }

  return (
    <>
      <PageHeader eyebrow={<>{agent?.name} · Import</>} title="Import leads"
        description={`Bring prospects into ${agent?.name ?? 'this agent'} from CSV or Excel. Every row is checked before anything is saved; duplicates are matched by phone within this agent only.`}
        actions={<Button onClick={downloadTemplate}><Download />Template</Button>} />

      <ol className="mb-6 flex flex-wrap items-center gap-2 text-sm">
        {['Upload file', 'Check & map', 'Done'].map((label, i) => (
          <li key={label} className="flex items-center gap-2">
            <span className={cn('grid size-6 place-items-center rounded-full text-xs font-bold',
              step > i + 1 ? 'bg-success text-white' : step === i + 1 ? 'bg-fg text-bg' : 'bg-surface-2 text-muted ring-1 ring-border')}>
              {step > i + 1 ? '✓' : i + 1}
            </span>
            <span className={cn(step >= i + 1 ? 'font-semibold text-fg' : 'text-muted')}>{label}</span>
            {i < 2 && <span className="mx-2 h-px w-8 bg-border" />}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <Card className="p-6">
          <button type="button" onClick={() => input.current?.click()} disabled={busy}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) void choose(f) }}
            className={cn('flex w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed px-6 py-16 transition',
              drag ? 'border-fg bg-surface-2' : 'border-border-strong bg-surface-2/40 hover:border-fg hover:bg-surface-2')}>
            <span className="mb-4 grid size-12 place-items-center rounded-xl bg-surface text-fg ring-1 ring-border">
              {busy ? <Loader2 className="size-6 animate-spin" /> : <FileSpreadsheet className="size-6" />}
            </span>
            <span className="text-base font-semibold text-fg">{busy ? 'Reading and checking your file…' : 'Drop your file here or click to browse'}</span>
            <span className="mt-1 text-sm text-muted">CSV, XLSX or XLS · up to 20 MB · first row must be column names</span>
          </button>
          <input ref={input} type="file" accept=".csv,.xlsx,.xls" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void choose(f) }} />
          <div className="mt-6 grid gap-3 text-sm sm:grid-cols-3">
            {[
              ['Phone is required', 'Detected from Phone, Mobile, Contact Number, WhatsApp. 10-digit numbers get +91; incomplete numbers are flagged.'],
              ['Duplicates handled', 'Skip numbers already in this agent, or update those leads with new details, tags and notes.'],
              ['Language & stage', 'Add Language (Hindi, English, hi-IN) and Stage columns; unknown values fall back safely.'],
            ].map(([t, d]) => (
              <div key={t} className="rounded-xl border border-border bg-surface p-4"><div className="font-semibold text-fg">{t}</div><p className="mt-1 text-muted">{d}</p></div>
            ))}
          </div>
        </Card>
      )}

      {step === 2 && preview && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-w-0 space-y-4">
            <div className="grid grid-cols-3 gap-3">
              {([['ready', analysis?.ready], ['duplicate', analysis?.duplicates], ['invalid', analysis?.invalid]] as const).map(([k, v]) => {
                const ui = STATE_UI[k]
                return (
                  <Card key={k} className="flex items-center gap-3 p-4">
                    <ui.icon className={cn('size-5 shrink-0', ui.className)} />
                    <div><div className="text-2xl font-extrabold text-fg tabular-nums">{checking ? '…' : v ?? 0}</div>
                      <div className="text-xs text-muted">{k === 'ready' ? 'New leads' : k === 'duplicate' ? 'Duplicates' : 'Invalid rows'}</div></div>
                  </Card>
                )
              })}
            </div>

            <Card className="min-w-0 overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
                <div className="min-w-0"><div className="truncate font-semibold text-fg">{file?.name}</div>
                  <div className="flex items-center gap-2 text-sm text-muted">{preview.rows} rows · preview of first {preview.sample.length}{checking && <RefreshCw className="size-3.5 animate-spin" />}</div></div>
                <Button variant="ghost" onClick={reset}><ArrowLeft />Change file</Button>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-border bg-surface-2">
                    <tr>
                      <th className="w-28 px-3 py-3 text-left align-bottom text-xs font-semibold text-muted">Check</th>
                      {preview.columns.map((c) => (
                        <th key={c} className="min-w-44 px-3 py-3 text-left align-top">
                          <div className="mb-1.5 truncate text-xs font-semibold text-fg-2">{c}</div>
                          <Select value={mapping[c]} onChange={(e) => setMapping({ ...mapping, [c]: e.target.value })}
                            className={cn('h-8 text-[13px]', mapping[c] ? 'border-fg/40 font-semibold text-fg' : 'text-muted')}>
                            {FIELDS.map(([v, l]) => <option key={v} value={v} disabled={!!v && v !== mapping[c] && Object.values(mapping).includes(v)}>{l}</option>)}
                          </Select>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {preview.sample.map((row, i) => {
                      const st = analysis?.row_status[i]
                      const ui = st ? STATE_UI[st.state] : null
                      return (
                        <tr key={i} className={cn('reveal reveal-in reveal-up', st?.state === 'invalid' && 'bg-danger-soft/40')} style={{ animationDelay: `${i * 25}ms` }}>
                          <td className="px-3 py-2" title={st?.detail}>
                            {ui && <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', ui.className)}><ui.icon className="size-3.5" />{ui.label}</span>}
                          </td>
                          {preview.columns.map((c) => <td key={c} className={cn('max-w-56 truncate px-3 py-2', mapping[c] ? 'text-fg' : 'text-muted')}>{row[c]}</td>)}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <Card className="h-fit p-5">
            <h3 className="font-semibold text-fg">Import options</h3>
            <div className="mt-4 space-y-4">
              <Field label="When a phone number already exists">
                <Select value={opts.onDuplicate} onChange={(e) => setOpts({ ...opts, onDuplicate: e.target.value as 'skip' | 'update' })}>
                  <option value="skip">Skip it (keep the existing lead)</option>
                  <option value="update">Update it (fill empty fields, add tags & notes)</option>
                </Select>
              </Field>
              <Field label="Default language" hint="Used when a row has no language">
                <Select value={opts.language} onChange={(e) => setOpts({ ...opts, language: e.target.value })}>{Object.entries(LANGUAGES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
              </Field>
              <Field label="Tag all leads" hint="Added to any tags already in the file"><Input value={opts.tags} onChange={(e) => setOpts({ ...opts, tags: e.target.value })} placeholder="e.g. diwali-campaign" /></Field>
              <Field label="Source" hint="Used when a row has no source column"><Input value={opts.source} onChange={(e) => setOpts({ ...opts, source: e.target.value })} /></Field>
              <label className="flex items-center justify-between gap-3 rounded-xl border border-border p-3">
                <span><span className="flex items-center gap-1.5 text-sm font-semibold text-fg"><ListPlus className="size-4" />Add to call queue</span>
                  <span className="text-xs text-muted">Auto-dial calls new leads within calling hours</span></span>
                <Switch checked={opts.queue} onChange={(v) => setOpts({ ...opts, queue: v })} label="Queue for calls" />
              </label>
              {!hasPhone && <p className="flex gap-2 rounded-xl bg-danger-soft p-3 text-sm text-danger"><CircleAlert className="mt-0.5 size-4 shrink-0" />Map one column to Phone to continue.</p>}
              {hasPhone && analysis && analysis.invalid > 0 && (
                <p className="flex gap-2 rounded-xl bg-warning-soft p-3 text-sm text-warning"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{analysis.invalid} row{analysis.invalid > 1 ? 's' : ''} will be skipped (invalid phone). You'll see which after import.</p>
              )}
              <Button variant="primary" size="lg" className="w-full" disabled={!hasPhone || checking || willImport === 0} loading={busy} onClick={run}>
                <Upload />{willImport ? `Import ${willImport} lead${willImport === 1 ? '' : 's'}` : 'Nothing new to import'}
              </Button>
            </div>
          </Card>
        </div>
      )}

      {step === 3 && result && (
        <Card className="p-8">
          <div className="flex flex-col items-center text-center">
            <span className="grid size-14 place-items-center rounded-full bg-success-soft text-success"><CheckCircle2 className="size-7" /></span>
            <h2 className="mt-4 text-xl font-semibold text-fg">Import complete</h2>
            {result.batch_tag && (result.created > 0 || (result.updated ?? 0) > 0) && <p className="mt-2 text-sm text-muted">Tagged <Badge>{result.batch_tag}</Badge> so you can find this import later.</p>}
            <p className="mt-1 text-muted">New leads start as <Badge tone="brand">New</Badge>{opts.queue ? ' and are queued: auto-dial calls them within calling hours.' : '. Queue them from Leads or switch on auto-dial.'}</p>
          </div>
          <Stagger className="mx-auto mt-8 grid max-w-3xl gap-4 sm:grid-cols-4">
            {[['Imported', result.created, 'text-success'], ['Updated', result.updated ?? 0, 'text-fg'], ['Duplicates skipped', result.skipped_duplicates, 'text-fg'],
              ['Invalid rows', result.errors.length, result.errors.length ? 'text-danger' : 'text-fg']].map(([l, v, c]) => (
              <div key={l as string} className="rounded-xl border border-border bg-surface p-4 text-center"><div className={`text-3xl font-semibold tabular-nums ${c}`}>{v}</div><div className="mt-1 text-sm text-muted">{l}</div></div>
            ))}
          </Stagger>
          {result.created === 0 && (result.updated ?? 0) === 0 && result.skipped_duplicates > 0 && (
            <p className="mx-auto mt-6 max-w-2xl rounded-xl bg-warning-soft p-3 text-center text-sm text-warning">
              Every phone number is already in this agent's leads, so nothing new was added. Import again with “Update it” to refresh those leads, or use different numbers.
            </p>
          )}
          {result.errors.length > 0 && (
            <div className="mx-auto mt-6 max-h-56 max-w-3xl overflow-y-auto rounded-xl border border-border bg-surface">
              {result.errors.slice(0, 100).map((e) => <div key={e.row} className="flex gap-3 border-b border-border px-4 py-2 text-sm text-fg last:border-0"><span className="w-16 shrink-0 text-muted">Row {e.row}</span><span>{e.error}</span></div>)}
            </div>
          )}
          <div className="mt-8 flex flex-wrap justify-center gap-2">
            <Button onClick={reset}>Import another file</Button>
            <Link to={path('/automation')}><Button>Set up auto-dial</Button></Link>
            <Link to={path(result.batch_tag && (result.created || result.updated) ? `/leads?search=${encodeURIComponent(result.batch_tag)}` : '/leads')}>
              <Button variant="primary">{result.created || result.updated ? `View these ${(result.created || 0) + (result.updated || 0)} leads` : 'View leads'}</Button>
            </Link>
          </div>
        </Card>
      )}
    </>
  )
}
