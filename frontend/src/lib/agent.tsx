import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react'
import { api, ApiError } from '@/lib/api'
import type { AgentSummary, AgentsResponse } from '@/lib/types'

type AgentContextValue = {
  id: number
  agent: AgentSummary | undefined
  /** API prefix for this agent: every data request goes through it. */
  base: string
  /** In-app route inside this agent's workspace. */
  path: (to: string) => string
}

const AgentContext = createContext<AgentContextValue | null>(null)

export function useAgent() {
  const value = useContext(AgentContext)
  if (!value) throw new Error('useAgent must be used inside an agent workspace')
  return value
}

export const useAgents = () => useQuery({
  queryKey: ['agents'],
  queryFn: () => api<AgentsResponse>('/api/agents'),
  refetchInterval: 8000,
})

// One query cache per agent: cached leads, calls and knowledge of one agent can never render in another.
const clients = new Map<number, QueryClient>()

function clientFor(id: number) {
  let client = clients.get(id)
  if (!client) {
    client = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 10_000,
          refetchOnWindowFocus: true,
          retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
        },
      },
    })
    clients.set(id, client)
  }
  return client
}

export function forgetAgent(id: number) {
  clients.get(id)?.clear()
  clients.delete(id)
}

export function AgentProvider({ id, agent, children }: { id: number; agent: AgentSummary | undefined; children: ReactNode }) {
  const value = useMemo<AgentContextValue>(() => ({
    id,
    agent,
    base: `/api/agents/${id}`,
    path: (to: string) => `/a/${id}${to === '/' ? '' : to}`,
  }), [id, agent])

  // Each workspace wears its own accent colour so it is obvious which agent you are in.
  useEffect(() => {
    const root = document.documentElement
    if (agent?.color) root.style.setProperty('--agent', agent.color)
    return () => { root.style.removeProperty('--agent') }
  }, [agent?.color])

  return (
    <QueryClientProvider client={clientFor(id)}>
      <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
    </QueryClientProvider>
  )
}
