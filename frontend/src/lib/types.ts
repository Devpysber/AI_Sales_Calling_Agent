export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Lead {
  id: number
  name: string | null
  company: string | null
  phone: string
  email: string | null
  city: string | null
  language: string
  source: string | null
  tags: string[]
  notes: string | null
  do_not_call: boolean
  status: string
  call_status: string | null
  retry_count: number
  qualification: string | null
  summary: string | null
  requirements: string | null
  objections: string | null
  follow_up_date: string | null
  meeting_at: string | null
  /** "YYYY-MM-DD HH:MM" IST: the scheduler calls back at this time */
  callback_at?: string | null
  last_contacted_at: string | null
  created_at: string
  updated_at: string
}

export interface Turn {
  role: 'assistant' | 'customer'
  text: string
  at?: string
}

export interface Call {
  agent_id?: number | null
  id: number
  lead_id: number | null
  lead_name: string | null
  direction: 'outbound' | 'inbound'
  trigger: string
  from_number: string | null
  to_number: string | null
  call_uuid: string | null
  status: string
  hangup_cause: string | null
  duration: number
  error: string | null
  summary: string | null
  qualification: string | null
  sentiment: string | null
  outcome: string | null
  recording_url: string | null
  avg_latency_ms: number | null
  created_at: string
  answered_at: string | null
  ended_at: string | null
  transcript?: Turn[]
  turns?: number
  events?: ActivityEvent[]
}

export interface ActivityEvent {
  id: number
  type: string
  title: string
  detail: string | null
  data: Record<string, unknown> | null
  actor: string
  lead_id: number | null
  lead_name?: string | null
  call_id: number | null
  created_at: string
}

export interface LeadStats {
  total: number
  pending: number
  meetings: number
  by_status: Record<string, number>
  by_call_status: Record<string, number>
  by_qualification: Record<string, number>
}

export interface CallStats {
  active: number
  today: { total: number; connected: number; talk_seconds: number }
  series: { date: string; total: number; connected: number; unanswered: number; meetings: number }[]
  outcomes: Record<string, number>
  avg_latency_ms: number | null
  within_calling_hours: boolean
}

export interface KnowledgeDoc {
  id: number
  title: string
  filename: string | null
  content_type: string | null
  size_bytes: number
  chars: number
  chunk_count: number
  status: 'processing' | 'ready' | 'failed'
  embedded: boolean
  error: string | null
  created_at: string
  chunks?: string[]
}

export interface AgentProfile {
  agent_name: string
  company_name: string
  company_tagline: string
  voice_speaker: string
  default_language: string
  objective: string
  call_to_action: string
  greeting_en: string
  greeting_hi: string
  instructions: string
  objection_handling: string
  qualification_criteria: string
  forbidden_topics: string
  max_call_minutes: number
  record_calls: boolean
  detect_voicemail: boolean
}

export interface AutomationSettings {
  auto_dial_enabled: boolean
  auto_dial_interval_minutes: number
  max_calls_per_run: number
  retry_enabled: boolean
  retry_interval_minutes: number
  retry_min_gap_minutes: number
  max_retries: number
  calling_hours_start: number
  calling_hours_end: number
  calling_days: number[]
  max_concurrent_calls: number
  meeting_reminder_enabled: boolean
  meeting_reminder_hour: number
  daily_report_enabled: boolean
  daily_report_hour: number
  daily_report_email: string
}

export interface AnalyticsKpis {
  calls: number
  connected: number
  connect_rate: number | null
  talk_seconds: number
  avg_duration: number | null
  meetings: number
  meeting_rate: number | null
  hot: number
  avg_latency_ms: number | null
}

export interface AnalyticsReport {
  days: number
  kpis: AnalyticsKpis
  previous: AnalyticsKpis
  new_leads: number
  series: { date: string; calls: number; connected: number; meetings: number; talk_seconds: number }[]
  heatmap: { weekday: number; hour: number; calls: number; connected: number }[]
  outcomes: Record<string, number>
  qualification: Record<string, number>
  sentiment: Record<string, number>
  triggers: { trigger: string; calls: number; connected: number; meetings: number }[]
  failures: { reason: string; count: number }[]
  funnel: { stage: string; count: number }[]
  pipeline: Record<string, number>
  sources: { source: string; leads: number; meetings: number; hot: number }[]
}

export type Board = Record<string, { total: number; items: Lead[] }>

export interface AgentTurnResult {
  reply: string
  language: string | null
  intent: string
  qualification: string | null
  sentiment: string | null
  end_call: boolean
  crm_update: Record<string, string>
  knowledge: { title: string; score: number; text: string }[]
  provider: string
  llm_ms: number
  total_ms: number
  audio_url?: string
  audio_error?: string
}

export interface AgentStats {
  leads: number
  hot: number
  meetings: number
  calls_today: number
  connected_today: number
  live: number
  documents: number
  last_call_at: string | null
}

export interface AgentSummary {
  id: number
  name: string
  description: string | null
  color: string
  phone_number: string | null
  status: 'active' | 'paused'
  created_at: string | null
  persona: { agent_name: string; company_name: string; voice_speaker: string; default_language: string }
  stats: AgentStats
  within_calling_hours: boolean
  automation_on: boolean
  setup: { persona: boolean; knowledge: boolean; leads: boolean; number: boolean; automation: boolean }
}

export interface AgentOverviewItem extends AgentSummary {
  series: { date: string; calls: number; connected: number; meetings: number }[]
  period: { calls: number; connected: number; meetings: number; connect_rate: number | null; talk_seconds: number }
  pipeline: Record<string, number>
}

export interface AgentsOverview {
  days: number
  agents: AgentOverviewItem[]
  series: { date: string; calls: number; connected: number }[]
  live_calls: (Call & { agent_name: string | null })[]
  activity: (ActivityEvent & { agent_id: number; agent_name: string | null })[]
}

export interface AgentsResponse {
  agents: AgentSummary[]
  voices: string[]
  languages: Record<string, string>
}
