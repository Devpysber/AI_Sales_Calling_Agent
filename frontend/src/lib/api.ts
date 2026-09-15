export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

type Options = Omit<RequestInit, 'body'> & { json?: unknown; body?: BodyInit; params?: Record<string, unknown> }

export async function api<T = unknown>(path: string, { json, params, ...init }: Options = {}): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
  }
  const headers = new Headers(init.headers)
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  const res = await fetch(url, { credentials: 'same-origin', ...init, headers, body: json !== undefined ? JSON.stringify(json) : init.body })

  if (res.status === 401 && !path.startsWith('/api/auth')) {
    window.dispatchEvent(new CustomEvent('auth:expired'))
  }
  const type = res.headers.get('content-type') ?? ''
  const data = type.includes('json') ? await res.json().catch(() => ({})) : await res.blob()
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join(', ') : `Request failed (${res.status})`
    throw new ApiError(message, res.status)
  }
  return data as T
}

export const apiGet = <T,>(path: string, params?: Record<string, unknown>) => api<T>(path, { params })
