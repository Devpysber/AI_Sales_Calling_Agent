import { useQuery } from '@tanstack/react-query'
import { CornerDownLeft, Search } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type ComponentType } from 'react'
import { createPortal } from 'react-dom'
import { Avatar, Spinner } from '@/components/ui'
import { api } from '@/lib/api'
import type { Lead, Page } from '@/lib/types'
import { cn } from '@/lib/utils'

export type Command = { id: string; label: string; group: string; icon?: ComponentType<{ className?: string }>; hint?: string; keywords?: string; run: () => void }

export function CommandPalette({ open, onClose, commands, leadsBase, onLead }: {
  open: boolean; onClose: () => void; commands: Command[]; leadsBase?: string; onLead?: (id: number) => void
}) {
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [index, setIndex] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const listId = useId()

  useEffect(() => {
    if (!open) return
    setQ(''); setDebounced(''); setIndex(0)
    const t = setTimeout(() => input.current?.focus(), 0)
    return () => clearTimeout(t)
  }, [open])
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 180); return () => clearTimeout(t) }, [q])

  const leads = useQuery({
    queryKey: ['palette', 'leads', leadsBase, debounced],
    queryFn: () => api<Page<Lead>>(`${leadsBase}/leads`, { params: { search: debounced, page_size: 6 } }),
    enabled: open && !!leadsBase && debounced.length >= 2,
  })

  const items = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const matched = commands.filter((c) => !needle || `${c.label} ${c.keywords ?? ''} ${c.group}`.toLowerCase().includes(needle))
    // Callers may interleave groups; order by first-seen group so each header renders once.
    const order: string[] = []
    for (const c of matched) if (!order.includes(c.group)) order.push(c.group)
    const grouped = order.flatMap((g) => matched.filter((c) => c.group === g))
    const leadItems: Command[] = (debounced.length >= 2 ? leads.data?.items ?? [] : []).map((l) => ({
      id: `lead-${l.id}`, group: 'Leads', label: l.name ?? l.phone, hint: [l.company, l.phone].filter(Boolean).join(' · '),
      run: () => onLead?.(l.id),
    }))
    return [...grouped, ...leadItems]
  }, [q, debounced, commands, leads.data, onLead])

  useEffect(() => { setIndex(0) }, [q])
  // Lead results arrive after the debounce; keep the highlight inside the list when it shrinks.
  useEffect(() => { setIndex((i) => Math.min(i, Math.max(0, items.length - 1))) }, [items.length])
  useEffect(() => { list.current?.querySelector(`[data-index="${index}"]`)?.scrollIntoView({ block: 'nearest' }) }, [index])

  // Keyboard handling lives on the document (like useEscape in ui.tsx) so it keeps working when a click
  // inside the panel moves focus off the input; the body scroll lock matches Sheet/Dialog.
  const latest = useRef({ items, index, onClose })
  latest.current = { items, index, onClose }
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const { items, index, onClose } = latest.current
      if (e.key === 'ArrowDown') { e.preventDefault(); setIndex((i) => Math.min(items.length - 1, i + 1)) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setIndex((i) => Math.max(0, i - 1)) }
      else if (e.key === 'Enter') { const c = items[index]; if (!c) return; e.preventDefault(); onClose(); c.run() }
      else if (e.key === 'Escape') { e.preventDefault(); onClose() }
      // The input is the only focusable control: Tab must not walk out to the (non-inert) page behind.
      else if (e.key === 'Tab') { e.preventDefault(); input.current?.focus() }
    }
    document.addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = overflow }
  }, [open])

  if (!open) return null

  const run = (c?: Command) => { if (!c) return; onClose(); c.run() }
  const optionId = (i: number) => `${listId}-opt-${i}`
  const searching = leads.isFetching && debounced.length >= 2

  let last = ''
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-start justify-center p-4 pt-[12vh]">
      <div className="absolute inset-0 animate-fade-in bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="Command palette" className="relative w-full min-w-0 max-w-xl animate-pop-in overflow-hidden rounded-xl border border-border bg-elevated shadow-pop">
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search className="size-4 shrink-0 text-muted" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search leads, pages and actions…"
            className="h-12 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted" aria-label="Command"
            role="combobox" aria-expanded={items.length > 0} aria-controls={listId} aria-autocomplete="list"
            aria-activedescendant={items.length > 0 ? optionId(index) : undefined} autoComplete="off" spellCheck={false} />
          {searching && items.length > 0 ? <Spinner className="shrink-0" /> : <kbd className="shrink-0" aria-hidden>Esc</kbd>}
        </div>
        <div ref={list} id={listId} role="listbox" aria-label="Results" className="max-h-[52vh] overflow-y-auto overscroll-contain p-2">
          {items.length === 0 && (
            <p className="px-3 py-8 text-center text-sm text-muted" role="status">
              {searching ? 'Searching…' : leads.isError ? 'Lead search is unavailable right now.' : 'No results'}
            </p>
          )}
          {items.length > 0 && leads.isError && debounced.length >= 2 && <p className="px-3 py-1 text-xs text-muted" role="status">Lead search is unavailable right now.</p>}
          {items.length > 0 && searching && <p className="px-3 py-1 text-xs text-muted" role="status">Searching leads…</p>}
          {items.map((c, i) => {
            const header = c.group !== last ? c.group : null
            last = c.group
            const Icon = c.icon
            return (
              <div key={c.id}>
                {header && <div className="px-3 pt-2 pb-1 text-[11px] font-semibold tracking-wider text-muted uppercase">{header}</div>}
                <button type="button" id={optionId(i)} role="option" aria-selected={i === index} tabIndex={-1}
                  data-index={i} onMouseMove={() => setIndex(i)} onClick={() => run(c)}
                  className={cn('flex min-h-10 w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm', i === index ? 'bg-brand-soft text-fg' : 'text-fg-2')}>
                  {c.group === 'Leads' ? <Avatar name={c.label} className="size-6 shrink-0 text-[10px]" /> : Icon && <Icon className={cn('size-4 shrink-0', i === index ? 'text-brand' : 'text-muted')} />}
                  <span className="min-w-0 flex-1 truncate">{c.label}{c.group === 'Leads' && c.hint && <span className="ml-2 text-xs text-muted">{c.hint}</span>}</span>
                  {c.group !== 'Leads' && c.hint && <span className="flex shrink-0 gap-1">{c.hint.split(' ').map((k, j) => <kbd key={j}>{k}</kbd>)}</span>}
                  {i === index && <CornerDownLeft className="size-3.5 shrink-0 text-muted" />}
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>,
    document.body,
  )
}
