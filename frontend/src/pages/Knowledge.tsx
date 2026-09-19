import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen, Check, CheckCircle2, RefreshCw, CircleAlert, CircleDashed, FilePlus2, FileText, FileType2, Loader2, Search, Sparkles,
  Trash2, Type, Upload, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Sheet, ShowMore, Skeleton, Tabs, Textarea, useConfirm } from '@/components/ui'
import { api } from '@/lib/api'
import type { KnowledgeDoc } from '@/lib/types'
import { cn, formatDate, timeAgo } from '@/lib/utils'
import { useAgent } from '@/lib/agent'

type CoverageTopic = { summary: string; documents: string[] }
type Coverage = { status: 'empty' | 'analyzing' | 'ready' | 'failed'; topics: Record<string, CoverageTopic>; updated_at?: string; model?: string; error?: string }
type ListResponse = { documents: KnowledgeDoc[]; stats: { chunks: number; documents: number; semantic: boolean }; coverage?: Coverage }
type SearchResult = { title: string; text: string; score: number }
type Filter = 'all' | 'ready' | 'processing' | 'failed'
type UploadItem = { id: string; name: string; state: 'uploading' | 'done' | 'error'; error?: string }

const ACCEPT = ['.pdf', '.docx', '.txt', '.md', '.csv']
const MAX_BYTES = 25 * 1024 * 1024

const size = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} KB`)

// What a sales agent needs to answer most calls. Keys match app/services/knowledge_profile.py; the AI fills them from the documents.
const TOPICS: { key: string; label: string; hint: string; match: RegExp; template: string }[] = [
  { key: 'overview', label: 'Company overview', hint: 'Who you are, years in business, locations', match: /about|company|overview|profile|who we/i,
    template: 'About us\nWe are … founded in … and based in …. We help … by ….\n\nWhy customers choose us\n- …\n- …' },
  { key: 'services', label: 'Services & products', hint: 'What you sell and who it is for', match: /service|product|offering|solution|brochure|catalog/i,
    template: 'Service: …\nWho it is for: …\nWhat is included: …\nTypical timeline: …' },
  { key: 'pricing', label: 'Pricing', hint: 'Price ranges, packages, payment terms', match: /pric|cost|rate|package|plan|quote|fee/i,
    template: 'Pricing\n\nStarter package — ₹… : includes …\nGrowth package — ₹… : includes …\n\nPayment terms: …\nWhat affects the price: …' },
  { key: 'faq', label: 'FAQs', hint: 'Questions prospects ask on calls', match: /faq|question|q&a|qna/i,
    template: 'Frequently asked questions\n\nQ: How long does a project take?\nA: …\n\nQ: Do you offer support after launch?\nA: …' },
  { key: 'proof', label: 'Case studies', hint: 'Clients, results, testimonials', match: /case|client|portfolio|testimonial|success|result/i,
    template: 'Case study: [Client name]\nChallenge: …\nWhat we did: …\nResult: …' },
  { key: 'policy', label: 'Process & policies', hint: 'Onboarding, support, refunds, contracts', match: /process|policy|policies|terms|support|onboard|refund|warranty/i,
    template: 'How we work\n1. Discovery call\n2. Proposal within … days\n3. …\n\nSupport: …\nRefunds: …' },
]

const anyProcessing = (docs: KnowledgeDoc[]) => docs.some((d) => d.status === 'processing')

export default function Knowledge() {
  const { agent, base } = useAgent()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const input = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [note, setNote] = useState<{ title: string; text: string } | null>(null)
  const [viewId, setViewId] = useState<number | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [find, setFind] = useState('')
  const [uploads, setUploads] = useState<UploadItem[]>([])

  const list = useQuery({
    queryKey: ['knowledge'],
    queryFn: () => api<ListResponse>(`${base}/knowledge`),
    // Poll while documents are processed and while the AI fills topic coverage
    refetchInterval: (q) => (q.state.data?.documents.some((d) => d.status === 'processing') ? 1500
      : q.state.data?.coverage?.status === 'analyzing' ? 2500 : false),
  })
  const docs = useMemo(() => list.data?.documents ?? [], [list.data])
  const stats = list.data?.stats

  const remove = useMutation({
    mutationFn: (id: number) => api(`${base}/knowledge/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Document removed'); qc.invalidateQueries({ queryKey: ['knowledge'] }) },
    onError: (e) => toast.error('Could not remove', { description: e.message }),
  })

  const askRemove = async (d: KnowledgeDoc) => {
    if (await confirm({ title: `Remove “${d.title}”?`, description: 'The agent stops using this document on the next call.', confirmLabel: 'Remove', danger: true })) {
      remove.mutate(d.id)
      if (viewId === d.id) setViewId(null)
    }
  }

  const uploadFiles = async (fl: FileList | null) => {
    const files = Array.from(fl ?? [])
    for (const f of files) {
      const id = `${f.name}-${f.size}-${Date.now()}-${Math.random()}`
      const dot = f.name.lastIndexOf('.')
      const ext = dot > 0 ? f.name.slice(dot).toLowerCase() : ''
      const reject = !ACCEPT.includes(ext) ? `Unsupported type (${ext || 'no extension'})` : f.size > MAX_BYTES ? 'Larger than 25 MB' : null
      if (docs.some((d) => d.filename === f.name && d.status !== 'failed') && !reject) {
        if (!(await confirm({ title: `“${f.name}” is already uploaded`, description: 'Upload it again as a separate document?', confirmLabel: 'Upload anyway' }))) continue
      }
      setUploads((u) => [...u, { id, name: f.name, state: reject ? 'error' : 'uploading', error: reject ?? undefined }])
      if (reject) continue
      const fd = new FormData()
      fd.append('file', f)
      fd.append('title', f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim())
      api<KnowledgeDoc>(`${base}/knowledge/upload`, { method: 'POST', body: fd })
        .then(() => {
          setUploads((u) => u.map((x) => (x.id === id ? { ...x, state: 'done' } : x)))
          setTimeout(() => setUploads((u) => u.filter((x) => x.id !== id)), 2500)
          qc.invalidateQueries({ queryKey: ['knowledge'] })
        })
        .catch((e: Error) => setUploads((u) => u.map((x) => (x.id === id ? { ...x, state: 'error', error: e.message } : x))))
    }
  }

  const cov = list.data?.coverage
  const analyzing = cov?.status === 'analyzing' || anyProcessing(docs)
  // AI-filled topics; before the first analysis finishes, fall back to matching document names
  const coverage = TOPICS.map((t) => {
    const fromAi = cov?.topics?.[t.key]
    // Only link a document the AI actually named; a paraphrased name means summary only (no arbitrary "View" target)
    const doc = fromAi?.summary ? docs.find((d) => (fromAi.documents ?? []).includes(d.title))
      : cov?.status === 'ready' ? undefined : docs.find((d) => d.status === 'ready' && t.match.test(`${d.title} ${d.filename ?? ''}`))
    return { ...t, summary: fromAi?.summary ?? '', doc }
  })
  const covered = coverage.filter((c) => c.summary || c.doc).length
  // Keep Refresh disabled from the click until the list has been refetched afterwards, so a second rebuild can't start
  // in the gap between the POST resolving and coverage.status turning 'analyzing'
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null)
  const refresh = useMutation({
    mutationFn: () => api(`${base}/knowledge/coverage`, { method: 'POST' }),
    onMutate: () => setRefreshedAt(Date.now()),
    onSuccess: () => { toast.success('Re-reading your documents', { description: 'Coverage updates in a few seconds.' }); setTimeout(() => qc.invalidateQueries({ queryKey: ['knowledge'] }), 800) },
    onError: (e) => { setRefreshedAt(null); toast.error('Could not refresh coverage', { description: e.message }) },
  })
  const listSettledAt = Math.max(list.dataUpdatedAt, list.errorUpdatedAt)
  useEffect(() => {
    if (refreshedAt !== null && listSettledAt > refreshedAt) setRefreshedAt(null)
  }, [refreshedAt, listSettledAt])
  const counts = { all: docs.length, ready: 0, processing: 0, failed: 0 } as Record<Filter, number>
  docs.forEach((d) => { counts[d.status] = (counts[d.status] ?? 0) + 1 })
  // The processing/failed tabs disappear once their count hits 0; fall back to 'all' so the list never goes empty on a hidden tab
  const active: Filter = filter === 'all' || filter === 'ready' || counts[filter] > 0 ? filter : 'all'
  // Reset the hidden tab so it doesn't silently reapply once a document reaches that status again
  useEffect(() => { if (active !== filter) setFilter(active) }, [active, filter])
  const shown = docs.filter((d) => (active === 'all' || d.status === active) && `${d.title} ${d.filename ?? ''}`.toLowerCase().includes(find.toLowerCase()))
  const lastUpdate = docs.reduce<string | null>((a, d) => (!a || d.created_at > a ? d.created_at : a), null)

  return (
    <>
      <PageHeader eyebrow={<><BookOpen className="size-3.5" />{agent?.name} · Private knowledge</>} title="Knowledge base" description="The only facts this agent may state on calls: services, pricing, FAQs and proof. Other agents can't see these documents, and anything missing here the agent won't make up."
        actions={<><Button onClick={() => setNote({ title: '', text: '' })}><Type />Write note</Button><Button variant="primary" onClick={() => input.current?.click()}><Upload />Upload files</Button></>} />
      <input ref={input} type="file" multiple accept={ACCEPT.join(',')} hidden onChange={(e) => { void uploadFiles(e.target.files); e.target.value = '' }} />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Documents" value={stats?.documents ?? '—'} sub={counts.processing ? `${counts.processing} processing` : counts.failed ? <span className="text-danger">{counts.failed} failed</span> : 'All processed'} />
        <Stat label="Searchable passages" value={stats?.chunks ?? '—'} sub="Chunks the agent retrieves from" />
        {/* With nothing uploaded "Keyword" reads like a limitation; it is simply that there is nothing to search yet. */}
        <Stat label="Search mode"
          value={!stats || !stats.documents ? '—' : stats.semantic ? 'Hybrid' : 'Keyword'}
          sub={!stats || !stats.documents ? 'Add a document to switch it on'
            : stats.semantic ? 'Meaning + keyword, across languages' : 'Exact words only — embeddings unavailable for these documents'} />
        <Stat label="Topic coverage"
          value={list.isLoading || list.isError ? '—' : analyzing ? <span className="inline-flex items-center gap-2">{covered}/{TOPICS.length}<Loader2 className="size-4 animate-spin text-muted" /></span> : `${covered}/${TOPICS.length}`}
          sub={list.isLoading ? 'Loading…' : list.isError ? 'Unavailable' : analyzing ? 'AI is reading your documents…' : lastUpdate ? `Last added ${timeAgo(lastUpdate)}` : 'Nothing added yet'} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); void uploadFiles(e.dataTransfer.files) }}
            className={cn('rounded-xl border-2 border-dashed transition', drag ? 'border-brand bg-brand-soft' : 'border-border-strong')}>
            <button type="button" onClick={() => input.current?.click()} className="flex w-full items-center gap-3 rounded-xl p-4 text-left hover:bg-brand-soft/40 sm:gap-4 sm:p-5">
              <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand"><FilePlus2 className="size-5" /></span>
              <span className="min-w-0"><span className="block font-semibold">{drag ? 'Drop to upload' : 'Drop brochures, price lists, FAQs or case studies'}</span><span className="text-sm text-muted">PDF, DOCX, TXT, MD or CSV · up to 25 MB each · text-based PDFs only (no scans)</span></span>
            </button>
            {uploads.length > 0 && (
              <ul className="divide-y divide-border border-t border-border">
                {uploads.map((u) => (
                  <li key={u.id} className="flex min-h-11 items-center gap-3 px-4 py-2 text-sm sm:px-5">
                    {u.state === 'uploading' ? <Loader2 className="size-4 animate-spin text-brand" /> : u.state === 'done' ? <CheckCircle2 className="size-4 text-success" /> : <CircleAlert className="size-4 text-danger" />}
                    <span className="min-w-0 flex-1 truncate">{u.name}</span>
                    <span title={u.error} className={cn('min-w-0 max-w-[45%] truncate text-xs', u.state === 'error' ? 'text-danger' : 'text-muted')}>{u.state === 'uploading' ? 'Uploading…' : u.state === 'done' ? 'Uploaded' : u.error}</span>
                    {u.state === 'error' && <button type="button" onClick={() => setUploads((x) => x.filter((y) => y.id !== u.id))} aria-label="Dismiss" className="-mr-2 grid size-10 shrink-0 place-items-center rounded-lg text-muted hover:text-fg sm:size-8"><X className="size-4" /></button>}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3 sm:px-5">
              <h3 className="mr-auto text-[15px] font-semibold">Documents</h3>
              <div className="relative min-w-0 flex-1 basis-40 sm:flex-none">
                <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted" />
                <Input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Filter by name" className="h-10 w-full pl-8 sm:h-9 sm:w-48" />
              </div>
              <Tabs value={active} onChange={setFilter} items={(['all', 'ready', 'processing', 'failed'] as Filter[])
                .filter((f) => f === 'all' || f === 'ready' || counts[f] > 0)
                .map((f) => ({ value: f, label: `${f[0]!.toUpperCase()}${f.slice(1)} ${counts[f]}` }))} />
            </div>

            {list.isLoading ? <div className="space-y-2 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14" />)}</div>
              : list.isError ? (
                <EmptyState icon={<CircleAlert />} title="Could not load documents" description={list.error.message}
                  action={<Button onClick={() => void list.refetch()} loading={list.isFetching}><RefreshCw />Retry</Button>} />
              ) : !docs.length ? (
                <EmptyState icon={<BookOpen />} title="No knowledge yet"
                  description="Without documents the agent won't quote prices or specifics — it offers a follow-up with a specialist instead. Start with your pricing and services."
                  action={<><Button onClick={() => setNote({ title: 'Pricing', text: TOPICS[2]!.template })}><Type />Write pricing note</Button><Button variant="primary" onClick={() => input.current?.click()}><Upload />Upload files</Button></>} />
              ) : !shown.length ? (
                <div className="flex flex-col items-center gap-3 px-5 py-10 text-center text-sm text-muted">
                  <p>No documents match.</p>
                  {(find || active !== 'all') && <Button size="sm" onClick={() => { setFind(''); setFilter('all') }}><X />Clear filter</Button>}
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {shown.map((d) => (
                    <li key={d.id} className="group flex items-center gap-3 px-4 py-3 sm:gap-4 sm:px-5 sm:py-3.5">
                      <FileIcon doc={d} />
                      <button className="min-w-0 flex-1 text-left disabled:cursor-default" disabled={d.status !== 'ready'} onClick={() => setViewId(d.id)}>
                        <div className="truncate font-medium group-hover:text-brand">{d.title}</div>
                        <div className="truncate text-xs text-muted">
                          {[d.filename ?? 'Written note', size(d.size_bytes), d.status === 'ready' && `${d.chunk_count} passages`, formatDate(d.created_at)].filter(Boolean).join(' · ')}
                        </div>
                        {d.error && <div className="mt-0.5 text-xs break-words text-danger">{d.error}</div>}
                      </button>
                      {d.status === 'processing' && <Badge tone="info"><Loader2 className="size-3 animate-spin" /><span className="hidden sm:inline">Processing</span></Badge>}
                      {d.status === 'ready' && <Badge tone={d.embedded ? 'success' : 'neutral'} className="max-sm:hidden"><CheckCircle2 className="size-3" />{d.embedded ? 'Semantic' : 'Keyword'}</Badge>}
                      {d.status === 'failed' && <Badge tone="danger"><CircleAlert className="size-3" /><span className="hidden sm:inline">Failed</span></Badge>}
                      <Button size="icon" variant="ghost" aria-label={`Remove ${d.title}`} loading={remove.isPending && remove.variables === d.id} onClick={() => void askRemove(d)}><Trash2 /></Button>
                    </li>
                  ))}
                </ul>
              )}
          </Card>
        </div>

        <div className="space-y-4 xl:sticky xl:top-20 xl:h-fit">
          <RetrievalTester docs={docs} onOpen={setViewId} />

          <Card>
            <CardHeader title="Coverage"
              description={list.isLoading ? 'Loading your documents…'
                : list.isError ? 'Coverage is unavailable until the documents load.'
                : analyzing ? 'AI is reading your documents to fill each topic…'
                : cov?.status === 'failed' ? 'Could not analyse the documents. Try Refresh.'
                : cov?.updated_at ? `What your documents say, filled by AI · ${timeAgo(cov.updated_at)}` : 'Topics prospects ask about most.'}
              action={docs.some((d) => d.status === 'ready') && (
                <Button size="sm" variant="ghost" loading={refresh.isPending} disabled={analyzing || refreshedAt !== null} onClick={() => refresh.mutate()} title="Re-read documents"><RefreshCw />Refresh</Button>
              )} />
            {list.isLoading ? <div className="space-y-2 p-4 sm:p-5">{TOPICS.map((t) => <Skeleton key={t.key} className="h-9" />)}</div>
              : list.isError ? (
                <ul className="divide-y divide-border">
                  {TOPICS.map((t) => (
                    <li key={t.key} className="flex gap-3 px-4 py-3 sm:px-5">
                      <CircleDashed className="mt-0.5 size-4 shrink-0 text-muted" />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium break-words text-fg-2">{t.label}</div>
                        <div className="mt-0.5 text-xs break-words text-muted">Unavailable · {t.hint}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
            <ul className="divide-y divide-border">
              {coverage.map((c) => {
                const filled = Boolean(c.summary || c.doc)
                return (
                  <li key={c.key} className="flex gap-3 px-4 py-3 sm:px-5">
                    {analyzing && !filled ? <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-muted" />
                      : filled ? <Check className="mt-0.5 size-4 shrink-0 text-success" /> : <CircleDashed className="mt-0.5 size-4 shrink-0 text-muted" />}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={cn('min-w-0 flex-1 text-sm font-medium break-words', filled ? 'text-fg' : 'text-fg-2')}>{c.label}</span>
                        {filled && c.doc
                          ? <Button size="sm" variant="ghost" onClick={() => setViewId(c.doc!.id)}>View</Button>
                          : !filled && <Button size="sm" onClick={() => setNote({ title: c.label, text: c.template })}>Add</Button>}
                      </div>
                      {c.summary
                        ? <div className="mt-1 text-xs leading-relaxed text-fg-2"><ShowMore text={c.summary} lines={3} limit={160} /></div>
                        : <div className="mt-0.5 text-xs break-words text-muted">{filled ? c.doc?.title : `Missing · ${c.hint}`}</div>}
                    </div>
                  </li>
                )
              })}
            </ul>
              )}
          </Card>
        </div>
      </div>

      <NoteSheet initial={note} onClose={() => setNote(null)} />
      <DocumentSheet key={viewId ?? 'none'} id={viewId} onClose={() => setViewId(null)} onRemove={(d) => void askRemove(d)} removing={remove.isPending && remove.variables === viewId} />
    </>
  )
}

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{value}</div>
      <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>
    </Card>
  )
}

function FileIcon({ doc }: { doc: KnowledgeDoc }) {
  const ext = doc.filename?.split('.').pop()?.toUpperCase()
  return (
    <span className={cn('relative grid size-10 shrink-0 place-items-center rounded-lg', doc.status === 'failed' ? 'bg-danger-soft text-danger' : 'bg-surface-2 text-muted')}>
      {doc.filename ? <FileType2 className="size-4" /> : <FileText className="size-4" />}
      {ext && <span className="absolute -bottom-1 rounded bg-surface px-1 text-[9px] font-semibold ring-1 ring-border">{ext}</span>}
    </span>
  )
}

/* ---------------- Retrieval tester ---------------- */

const EXAMPLES = ['How much does it cost?', 'What services do you offer?', 'How long does a project take?', 'Do you have any case studies?']

function RetrievalTester({ docs, onOpen }: { docs: KnowledgeDoc[]; onOpen: (id: number) => void }) {
  const { base } = useAgent()
  const [query, setQuery] = useState('')
  const search = useMutation({ mutationFn: (q: string) => api<{ results: SearchResult[] }>(`${base}/knowledge/search`, { method: 'POST', json: { query: q, top_k: 4 } }) })
  const run = (q: string) => { const t = q.trim(); if (t) { setQuery(t); search.mutate(t) } }
  const results = search.data?.results
  const best = results?.[0]?.score ?? 0
  const verdict = !results ? null : !results.length || best < 0.2
    ? { tone: 'danger' as const, text: 'Not covered — the agent will offer a specialist follow-up instead of answering.' }
    : best < 0.45 ? { tone: 'warning' as const, text: 'Weak match — the answer may be vague. Consider adding a clearer note.' }
      : { tone: 'success' as const, text: 'Well covered — the agent can answer this confidently.' }

  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-brand" />Test a question</span>} description="Ask what a prospect would ask and see what the agent would draw on." />
      <form className="flex min-w-0 gap-2 px-4 pt-4" onSubmit={(e) => { e.preventDefault(); run(query) }}>
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. What does app development cost?" maxLength={500} />
        <Button type="submit" variant="primary" size="icon" loading={search.isPending} disabled={!query.trim()} aria-label="Search">{!search.isPending && <Search />}</Button>
      </form>
      {!results && (
        <div className="flex flex-wrap gap-1.5 px-4 pt-3">
          {EXAMPLES.map((q) => <button key={q} type="button" onClick={() => run(q)} className="min-h-10 rounded-full border border-border px-3 py-1 text-xs text-fg-2 hover:border-brand hover:text-brand sm:min-h-0 sm:px-2.5">{q}</button>)}
        </div>
      )}
      <div className="space-y-2 p-4">
        {search.isError && <div className="rounded-lg bg-danger-soft px-3 py-2 text-xs font-medium break-words text-danger">Search failed: {search.error.message}</div>}
        {verdict && <div className={cn('rounded-lg px-3 py-2 text-xs font-medium', { success: 'bg-success-soft text-success', warning: 'bg-warning-soft text-warning', danger: 'bg-danger-soft text-danger' }[verdict.tone])}>{verdict.text}</div>}
        {results?.map((r, i) => {
          const doc = docs.find((d) => d.title === r.title)
          return (
            <button key={i} type="button" disabled={!doc} onClick={() => doc && onOpen(doc.id)} className="block w-full rounded-lg border border-border p-3 text-left transition enabled:hover:border-brand/50">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="min-w-0 truncate font-medium">{r.title}</span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="h-1.5 w-12 overflow-hidden rounded-full bg-surface-2"><span className={cn('block h-full', r.score > 0.45 ? 'bg-success' : r.score > 0.2 ? 'bg-warning' : 'bg-muted')} style={{ width: `${Math.round(Math.min(1, r.score) * 100)}%` }} /></span>
                  <span className="w-8 text-right text-muted tabular-nums">{Math.round(r.score * 100)}%</span>
                </span>
              </div>
              <p className="mt-1.5 line-clamp-4 text-[13px] break-words text-fg-2">{r.text}</p>
            </button>
          )
        })}
      </div>
    </Card>
  )
}

/* ---------------- Sheets ---------------- */

function DocumentSheet({ id, onClose, onRemove, removing }: { id: number | null; onClose: () => void; onRemove: (d: KnowledgeDoc) => void; removing?: boolean }) {
  const { base } = useAgent()
  const [find, setFind] = useState('')
  const doc = useQuery({ queryKey: ['knowledge', id], queryFn: () => api<KnowledgeDoc>(`${base}/knowledge/${id}`), enabled: id !== null })
  const d = doc.data
  const needle = find.trim().toLowerCase()
  const chunks = (d?.chunks ?? []).map((text, i) => ({ text, i })).filter((c) => !needle || c.text.toLowerCase().includes(needle))

  const highlight = (text: string) => {
    if (!needle) return text
    const parts = text.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'))
    return parts.map((p, i) => (p.toLowerCase() === needle ? <mark key={i} className="rounded bg-warning-soft px-0.5 text-fg">{p}</mark> : p))
  }

  return (
    <Sheet open={id !== null} onClose={() => { setFind(''); onClose() }} width="max-w-2xl"
      title={d?.title ?? 'Document'}
      description={d && [d.filename ?? 'Written note', size(d.size_bytes ?? 0), `${d.chunk_count ?? 0} passages`, `${(d.chars ?? 0).toLocaleString()} characters`, `added ${formatDate(d.created_at)}`].join(' · ')}
      footer={d && <Button variant="outline-danger" loading={removing} onClick={() => onRemove(d)}><Trash2 />Remove document</Button>}>
      {doc.isError ? (
        <EmptyState icon={<CircleAlert />} title="Could not load this document" description={doc.error.message}
          action={<Button onClick={() => void doc.refetch()} loading={doc.isFetching}><RefreshCw />Retry</Button>} />
      ) : !d ? <Skeleton className="h-64" /> : (
        <div className="space-y-3">
          <div className="relative">
            <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted" />
            <Input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find in document" className="pl-8" />
          </div>
          {needle && <p className="text-xs text-muted">{chunks.length} of {d.chunks?.length ?? 0} passages match</p>}
          {!d.chunks?.length && <p className="py-6 text-center text-sm text-muted">No passages to show.</p>}
          {chunks.map(({ text, i }) => (
            <div key={i} className="rounded-lg border border-border p-3">
              <div className="mb-1 text-xs text-muted">Passage {i + 1}</div>
              <p className="text-sm break-words whitespace-pre-wrap text-fg-2">{highlight(text)}</p>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}

function NoteSheet({ initial, onClose }: { initial: { title: string; text: string } | null; onClose: () => void }) {
  const qc = useQueryClient()
  return initial && <NoteForm key={initial.title + initial.text} initial={initial} onClose={onClose} qc={qc} />
}

function NoteForm({ initial, onClose, qc }: { initial: { title: string; text: string }; onClose: () => void; qc: ReturnType<typeof useQueryClient> }) {
  const { base } = useAgent()
  const [title, setTitle] = useState(initial.title)
  const [text, setText] = useState(initial.text)
  const confirm = useConfirm()
  const hasPlaceholders = text.includes('…')
  const save = useMutation({
    mutationFn: () => api(`${base}/knowledge/text`, { method: 'POST', json: { title: title.trim(), text: text.trim() } }),
    onSuccess: () => { toast.success('Knowledge added', { description: 'The agent can use it on the next call.' }); qc.invalidateQueries({ queryKey: ['knowledge'] }); onClose() },
    onError: (e) => toast.error('Could not save', { description: e.message }),
  })
  const close = async () => {
    const touched = title !== initial.title || text !== initial.text
    if (!touched || await confirm({ title: 'Discard this note?', confirmLabel: 'Discard', danger: true })) onClose()
  }
  const submit = async () => {
    if (hasPlaceholders && !(await confirm({ title: 'Note still has “…” placeholders', description: 'The agent may read them out literally. Save anyway?', confirmLabel: 'Save anyway' }))) return
    save.mutate()
  }

  return (
    <Sheet open onClose={() => { if (!save.isPending) void close() }} width="max-w-2xl" title="Knowledge note" description="Best for pricing, offers, FAQs and service descriptions. Write facts plainly; one topic per paragraph."
      footer={<><Button onClick={() => void close()} disabled={save.isPending}>Cancel</Button><Button variant="primary" type="submit" form="note-form" loading={save.isPending} disabled={!title.trim() || text.trim().length < 30}>Add to knowledge</Button></>}>
      <form id="note-form" className="space-y-4" onSubmit={(e) => { e.preventDefault(); if (!save.isPending) void submit() }}>
        <Field label="Title" hint="Include the topic, e.g. “Pricing 2026” — it also powers coverage detection"><Input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} autoFocus={!initial.title} disabled={save.isPending} /></Field>
        <Field label="Content" error={text.trim().length > 0 && text.trim().length < 30 ? 'At least 30 characters' : undefined} hint={`${text.length.toLocaleString()} characters`}>
          <Textarea value={text} onChange={(e) => setText(e.target.value)} required minLength={30} maxLength={500_000} rows={20} disabled={save.isPending} className="font-[inherit]"
            placeholder={'Mobile App Development\nWe build iOS and Android apps with React Native and Flutter. Typical projects take 8–12 weeks and start at ₹3,00,000.'} />
        </Field>
      </form>
    </Sheet>
  )
}
