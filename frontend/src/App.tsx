import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Component, lazy, Suspense, useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import { Spinner } from '@/components/ui'
import { api } from '@/lib/api'
import Login from '@/pages/Login'

/**
 * A route chunk that fails to load is almost always a stale tab: its index.html points at asset hashes
 * a newer deploy replaced. Reload once to pick up the new manifest; a second failure surfaces normally.
 */
function page<T>(load: () => Promise<T>): () => Promise<T> {
  return () => load().then((m) => {
    try { sessionStorage.removeItem('chunk-reload:' + location.pathname) } catch { /* private mode */ }
    return m
  }).catch((err: unknown) => {
    const key = 'chunk-reload:' + location.pathname
    let retried = false
    try { retried = sessionStorage.getItem(key) === '1'; if (!retried) sessionStorage.setItem(key, '1') } catch { /* private mode */ }
    if (!retried) { location.reload(); return new Promise<T>(() => {}) }
    // Second failure: one broken page with a Reload button, not a blank app.
    console.error('Route chunk failed twice', err)
    return { default: ChunkFailed } as unknown as T
  })
}

function ChunkFailed() {
  return (
    <div className="mx-auto max-w-md px-4 py-16 text-center">
      <p className="text-lg font-semibold">This page failed to load</p>
      <p className="mt-2 text-sm text-muted">The app was updated while this tab was open.</p>
      <button type="button" onClick={() => location.reload()} className="mt-5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-brand-fg">Reload</button>
    </div>
  )
}

/** A render error in one page must not blank the whole app; navigating away (key change) resets it. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error) { console.error('Page crashed', error) }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="mx-auto max-w-md px-4 py-16 text-center">
        <p className="text-lg font-semibold">Something went wrong</p>
        <p className="mt-2 break-words text-sm text-muted">{this.state.error.message}</p>
        <button type="button" onClick={() => location.reload()} className="mt-5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-brand-fg">Reload</button>
      </div>
    )
  }
}

const Home = lazy(page(() => import('@/pages/Home')))
const Dashboard = lazy(page(() => import('@/pages/Dashboard')))
const Leads = lazy(page(() => import('@/pages/Leads')))
const LeadDetail = lazy(page(() => import('@/pages/LeadDetail')))
const Pipeline = lazy(page(() => import('@/pages/Pipeline')))
const Analytics = lazy(page(() => import('@/pages/Analytics')))
const Import = lazy(page(() => import('@/pages/Import')))
const Calls = lazy(page(() => import('@/pages/Calls')))
const ActivityPage = lazy(page(() => import('@/pages/Activity')))
const Agent = lazy(page(() => import('@/pages/Agent')))
const Knowledge = lazy(page(() => import('@/pages/Knowledge')))
const Automation = lazy(page(() => import('@/pages/Automation')))
const AgentSettings = lazy(page(() => import('@/pages/AgentSettings')))
const SettingsPage = lazy(page(() => import('@/pages/Settings')))
const ProfilePage = lazy(page(() => import('@/pages/Profile')))
const Inbound = lazy(page(() => import('@/pages/Inbound')))
const Emails = lazy(page(() => import('@/pages/Emails')))

const Loading = () => <div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>

export default function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: me, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: () => api<{ user: string | null; auth_enabled: boolean; display_name?: string; role?: string; can_create_agent?: boolean }>('/api/auth/me'),
    staleTime: 0,         // always re-fetch on mount so session changes (team↔admin) are detected immediately
    refetchOnWindowFocus: true,
  })

  useEffect(() => {
    const onExpired = () => { qc.setQueryData(['me'], { user: null, auth_enabled: true }); navigate('/login') }
    // Agent workspaces use their own query caches; they announce agent list changes to the app-wide one.
    const onAgentsChanged = () => qc.invalidateQueries({ queryKey: ['agents'] })
    window.addEventListener('auth:expired', onExpired)
    window.addEventListener('agents:changed', onAgentsChanged)
    return () => {
      window.removeEventListener('auth:expired', onExpired)
      window.removeEventListener('agents:changed', onAgentsChanged)
    }
  }, [navigate, qc])

  if (isLoading) return <div className="grid h-full place-items-center"><Spinner className="size-6" /></div>

  if (!me?.user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" state={{ from: location.pathname }} replace />} />
      </Routes>
    )
  }

  // A team member may create agents up to the limit their administrator set; the admin always can.
  const canCreateAgent = me.user !== 'team' || me.can_create_agent === true
  const shell = <AppShell user={me.display_name || me.user} role={me.user} canCreateAgent={canCreateAgent} />
  return (
    <ErrorBoundary key={location.pathname}>
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route element={shell}>
          <Route index element={<Home />} />
          <Route path="settings" element={me.user === 'team' ? <Navigate to="/" replace /> : <SettingsPage />} />
          <Route path="profile" element={<ProfilePage />} />
        </Route>
        <Route path="a/:agentId" element={shell}>
          <Route index element={<Dashboard />} />
          <Route path="pipeline" element={<Pipeline />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="leads" element={<Leads />} />
          <Route path="leads/:leadId" element={<LeadDetail />} />
          <Route path="import" element={<Import />} />
          <Route path="calls" element={<Calls />} />
          <Route path="inbound" element={<Inbound />} />
          <Route path="emails" element={<Emails />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="agent" element={<Agent />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="automation" element={<Automation />} />
          <Route path="settings" element={<AgentSettings />} />
          <Route path="*" element={<Navigate to="." replace />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
    </ErrorBoundary>
  )
}
