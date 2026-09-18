import { useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import AppShell from '@/components/AppShell'
import { Spinner } from '@/components/ui'
import { api } from '@/lib/api'
import Login from '@/pages/Login'

const Home = lazy(() => import('@/pages/Home'))
const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Leads = lazy(() => import('@/pages/Leads'))
const LeadDetail = lazy(() => import('@/pages/LeadDetail'))
const Pipeline = lazy(() => import('@/pages/Pipeline'))
const Analytics = lazy(() => import('@/pages/Analytics'))
const Import = lazy(() => import('@/pages/Import'))
const Calls = lazy(() => import('@/pages/Calls'))
const ActivityPage = lazy(() => import('@/pages/Activity'))
const Agent = lazy(() => import('@/pages/Agent'))
const Knowledge = lazy(() => import('@/pages/Knowledge'))
const Automation = lazy(() => import('@/pages/Automation'))
const AgentSettings = lazy(() => import('@/pages/AgentSettings'))
const SettingsPage = lazy(() => import('@/pages/Settings'))
const ProfilePage = lazy(() => import('@/pages/Profile'))
const Inbound = lazy(() => import('@/pages/Inbound'))
const Emails = lazy(() => import('@/pages/Emails'))

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
  )
}
