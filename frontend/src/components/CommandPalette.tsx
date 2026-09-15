import { useQuery } from '@tanstack/react-query'
import { CornerDownLeft, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ComponentType } from 'react'
import { createPortal } from 'react-dom'
import { Avatar } from '@/components/ui'
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

  useEffect(() => { if (open) { setQ(''); setIndex(0); setTimeout(() => input.current?.focus(), 0) } }, [open])
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 180); return () => clearTimeout(t) }, [q])

  const leads = useQuery({
    queryKey: ['palette', 'leads', leadsBase, debounced],
    queryFn: () => api<Page<Lead>>(`${leadsBase}/leads`, { params: { search: debounced, page_size: 6 } }),
    enabled: open && !!leadsBase && debounced.length >= 2,
  })

  const items = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const matched = commands.filter((c) => !needle || `${c.label} ${c.keywords ?? ''} ${c.group}`.toLowerCase().includes(needle))
    const leadItems: Command[] = (debounced.length >= 2 ? leads.data?.items ?? [] : []).map((l) => ({
      id: `lead-${l.id}`, group: 'Leads', label: l.name ?? l.phone, hint: [l.company, l.phone].filter(Boolean).join(' · '),
      run: () => onLead?.(l.id),
    }))
    return [...matched, ...leadItems]
  }, [q, debounced, commands, leads.data, onLead])

  useEffect(() => { setIndex(0) }, [q])
  useEffect(() => { list.current?.querySelector(`[data-index="${index}"]`)?.scrollIntoView({ block: 'nearest' }) }, [index])

  if (!open) return null

  const run = (c?: Command) => { if (!c) return; onClose(); c.run() }
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setIndex((i) => Math.min(items.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIndex((i) => Math.max(0, i - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); run(items[index]) }
    else if (e.key === 'Escape') onClose()
  }

  let last = ''
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-start justify-center p-4 pt-[12vh]" onKeyDown={onKey}>
      <div className="absolute inset-0 animate-fade-in bg-black/40 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-label="Command palette" className="relative w-full max-w-xl animate-pop-in overflow-hidden rounded-xl border border-border bg-elevated shadow-pop">
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search className="size-4 text-muted" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search leads, pages and actions…"
            className="h-12 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted" aria-label="Command" />
          <kbd>Esc</kbd>
        </div>
        <div ref={list} className="max-h-[52vh] overflow-y-auto p-2">
          {items.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted">{leads.isFetching ? 'Searching…' : 'No results'}</p>}
          {items.map((c, i) => {
            const header = c.group !== last ? c.group : null
            last = c.group
            const Icon = c.icon
            return (
              <div key={c.id}>
                {header && <div className="px-3 pt-2 pb-1 text-[11px] font-semibold tracking-wider text-muted uppercase">{header}</div>}
                <button type="button" data-index={i} onMouseMove={() => setIndex(i)} onClick={() => run(c)}
                  className={cn('flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm', i === index ? 'bg-brand-soft text-fg' : 'text-fg-2')}>
                  {c.group === 'Leads' ? <Avatar name={c.label} className="size-6 text-[10px]" /> : Icon && <Icon className={cn('size-4', i === index ? 'text-brand' : 'text-muted')} />}
                  <span className="min-w-0 flex-1 truncate">{c.label}{c.group === 'Leads' && c.hint && <span className="ml-2 text-xs text-muted">{c.hint}</span>}</span>
                  {c.group !== 'Leads' && c.hint && <span className="flex gap-1">{c.hint.split(' ').map((k, j) => <kbd key={j}>{k}</kbd>)}</span>}
                  {i === index && <CornerDownLeft className="size-3.5 text-muted" />}
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
