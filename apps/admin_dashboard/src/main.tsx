import {
  Activity,
  ArrowLeft,
  Bell,
  BriefcaseBusiness,
  ClipboardList,
  ExternalLink,
  FileClock,
  FolderKanban,
  LogOut,
  Mail,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  UserMinus,
  UserPlus,
  Users,
  X,
} from "lucide-react"
import { type ReactNode, StrictMode, useEffect, useMemo, useRef, useState } from "react"
import { createRoot } from "react-dom/client"
import { Empty } from "@/components/empty"
import { SortableTableHead } from "@/components/sortable-table-head"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  daysSince,
  displayOnboarder,
  formatDate,
  githubUrl,
  isTerminalJobStatus,
  jobLeadClassificationMethodLabel,
  jsonPreview,
  labelForOnboardingState,
  linkedinUrl,
  onboardingStateValue,
  type Tone,
  toneForOnboardingState,
} from "@/dashboard-utils"
import { cn } from "@/lib/utils"
import {
  type ConfigurationItem,
  type ConfigurationResponse,
  ConfigurationView,
  type JobChannelsResponse,
  type JobPostChannel,
  type JobPostChannelTag,
} from "@/views/configuration-view"
import {
  type NewsletterStatus,
  type NewsletterSuppression,
  type NewsletterSyncPreview,
  newsletterPreviewSummary,
  newsletterProviderResults,
} from "@/views/newsletter-models"
import { NewsletterView } from "@/views/newsletter-view"
import "./index.css"

type View =
  | "people"
  | "gigs"
  | "projects"
  | "onboarding"
  | "newsletter"
  | "jobs"
  | "agent"
  | "audit"
  | "configuration"
type SortDirection = "asc" | "desc"
type GigTab = "gigs" | "leads"

type User = {
  subject: string
  email?: string
  display_name?: string
  actor_provider?: string
  crm_contact_id?: string
  crm_base_url?: string
  permissions?: string[]
}

type ProfileStatus = {
  crm_active?: boolean
  is_member?: boolean
  discord_linked?: boolean
  email_508?: boolean
  latest_resume?: boolean
  skills_count?: number
}

type IntakeSubmission = {
  source?: string
  form_id?: string
  submission_id?: string
  submitted_at?: string
  created_at?: string
  normalized_payload?: Record<string, unknown>
}

type Person = {
  id?: string
  crm_contact_id?: string
  name?: string
  email?: string
  email_508?: string
  created_at?: string
  address_country?: string
  discord_user_id?: string
  discord_username?: string
  contact_type?: string
  latest_resume_id?: string
  latest_resume_name?: string
  linkedin?: string
  github_username?: string
  onboarding_state?: string
  onboardingState?: string
  cOnboardingState?: string
  onboarding_status_label?: string
  onboarder?: string
  onboarding_updated_at?: string
  onboarding_email_sent_at?: string
  onboarding_email_sent_by?: string
  onboarding_email_recipient?: string
  sync_status?: string
  profile_status?: ProfileStatus
  latest_intake_submission?: IntakeSubmission
  latest_resume_intake_submission?: IntakeSubmission
}

type OnboardingEmailTriState = "yes" | "no" | "unknown"

type OnboardingEmailOptions = {
  has_contributed: boolean
  discord_joined: OnboardingEmailTriState
  agreement_signed: OnboardingEmailTriState
}

type OnboardingEmailDraft = {
  contact_id: string
  candidate_name?: string
  recipient_email?: string | null
  reply_to_email?: string | null
  cc_email?: string | null
  sender_display_name?: string | null
  signature_name?: string | null
  subject: string
  markdown_body: string
  can_send: boolean
  marker_status?: "saved" | "error" | null
  marker_error?: string | null
  onboarding_email_sent_at?: string | null
  onboarding_email_sent_by?: string | null
  onboarding_email_recipient?: string | null
}

type Job = {
  job_id: string
  type: string
  status: Tone | string
  attempts: number
  max_attempts: number
  updated_at?: string
  created_at?: string
  last_error?: string | null
}

type JobDetail = Job & {
  run_after?: string | null
  locked_by?: string | null
  payload?: unknown
  result?: unknown
}

const GIG_LEAD_SYNC_POLL_INTERVAL_MS = 2000
const GIG_LEAD_SYNC_MAX_POLLS = 90

type GigApplication = {
  id: string
  status?: string
  source?: string
  match_score?: number | null
  fit_score?: number | null
  evaluation?: Record<string, unknown>
  crm_contact_id?: string
  discord_user_id?: string
  name?: string
  email_508?: string
  discord_username?: string
  latest_resume_id?: string
  latest_resume_name?: string
  skills_count?: number
  is_member?: boolean
}

type Gig = {
  id: string
  status: string
  status_label?: string
  title?: string
  body_raw?: string
  required_skills?: string[]
  preferred_skills?: string[]
  discord_guild_id?: string
  discord_channel_id?: string
  discord_channel_name?: string
  posting_type?: string
  discord_thread_id?: string
  posted_by_discord_user_id?: string
  posted_at?: string
  last_status_changed_at?: string
  last_activity_at?: string
  created_at?: string
  updated_at?: string
  application_count?: number
  interested_count?: number
  applications?: GigApplication[]
}

type JobLead = {
  id: string
  status: "pending" | "approved" | "rejected" | "posted"
  source_key?: string
  source_type?: string
  external_id?: string
  external_parent_id?: string
  source_url?: string
  source_posted_at?: string
  title?: string
  organization?: string
  body_normalized?: string
  posting_type?: string
  location?: string
  remote?: boolean | null
  apply_url?: string
  tags?: string[]
  confidence?: number
  contractor_classification?: {
    is_contractor_friendly?: boolean
    posting_type?: string
    tags?: string[]
    confidence?: number
    confidence_label?: string
    rationale?: string
    method?: string
    contact_email?: string | null
  }
  review_summary?: string
  reviewed_by_discord_user_id?: string
  reviewed_at?: string
  discord_guild_id?: string
  discord_channel_id?: string
  discord_thread_id?: string
  posted_at?: string
  created_at?: string
  updated_at?: string
  posted_gig_url?: string
}

type JobLeadReviewStatus = "pending" | "approved" | "rejected"

type JobLeadScrapeThread = {
  story_id?: number
  title?: string
  url?: string
  created_at?: string | null
  comments_reported?: number | null
  potential_gigs_scraped?: number
  included?: number
  filtered_out?: number
  filter_reasons?: {
    empty?: number
    seeking_work?: number
    not_contractor_friendly?: number
  }
}

type JobLeadScrapeResult = {
  source?: string
  thread_found?: boolean
  threads?: JobLeadScrapeThread[]
  potential_gigs_scraped?: number
  included?: number
  filtered_out?: number
  filter_reasons?: JobLeadScrapeThread["filter_reasons"]
  created?: number
  updated?: number
  persisted?: number
  total?: number
}

type JobLeadScrapeStatus = {
  status: string
  job_id?: string | null
  source?: string
  story_id?: number | null
  created_at?: string | null
  updated_at?: string | null
  last_error?: string | null
  result?: JobLeadScrapeResult | null
}

type JobLeadPostResult = {
  status?: string
  lead_id?: string
  guild_id?: string
  channel_id?: string
  thread_id?: string
  engagement_id?: string | null
  engagement_status?: string
}

type DashboardNotification = {
  id: string
  type: string
  severity?: "info" | "warning" | "error"
  title: string
  message: string
  engagement_id?: string
  gig_title?: string
  age_days?: number
}

type DashboardNotificationsResponse = {
  stale_days: number
  contacted_reminder_days: number
  notifications: DashboardNotification[]
}

type ProjectRosterMember = {
  source?: string
  source_user_id?: string
  email?: string
  full_name?: string
  roster_kind?: string
  crm_contact_id?: string
  erpnext_user_url?: string
  supplier_erpnext_url?: string
  last_seen_at?: string
}

type HistoricalPersonCandidate = {
  candidate_id: string
  label?: string
  full_name?: string
  email?: string
  crm_contact_id?: string
  erpnext_user_id?: string
  supplier_erpnext_id?: string
  supplier_name?: string
  sources?: string[]
}

type Project = {
  id: string
  erpnext_project_id?: string
  erpnext_project_url?: string
  display_name: string
  customer?: string
  customer_erpnext_url?: string
  source_status?: string
  project_type?: string
  priority?: string
  percent_complete?: number | null
  expected_start_date?: string
  expected_end_date?: string
  actual_start_date?: string
  actual_end_date?: string
  source_modified_at?: string
  last_synced_at?: string
  linked_engagement_count?: number
  roster_count?: number
  roster_members?: ProjectRosterMember[]
}

type ERPNextCustomer = {
  name?: string
  customer_name?: string
  customer_type?: string
  default_currency?: string
  account_manager?: string
  url?: string
}

type ERPNextContact = {
  name?: string
  first_name?: string
  last_name?: string
  full_name?: string
  email_id?: string
  phone?: string
  mobile_no?: string
  company_name?: string
}

type ERPNextCostCenter = {
  name?: string
  cost_center_name?: string
  company?: string
}

type ERPNextUser = {
  name?: string
  email?: string
  full_name?: string
  enabled?: number | boolean
}

type ProjectsResponse = {
  projects: Project[]
  summary: {
    project_count?: number
    open_project_count?: number
    projects_with_roster?: number
    roster_member_count?: number
    last_synced_at?: string
  }
}

type WikiMatchPreview = {
  document?: {
    title?: string
    updatedAt?: string
    urlId?: string
  }
  wiki_rows?: Array<Record<string, string>>
  matches?: Array<{
    project?: Project
    best_match?: {
      score?: number
      confidence?: string
      row?: Record<string, string> | null
    } | null
    fuzzy_match?: {
      score?: number
      confidence?: string
      row?: Record<string, string> | null
    } | null
    manual_match?: {
      match_status?: string
      wiki_row_key?: string
      wiki_row_label?: string
      wiki_row_section?: string
    } | null
  }>
}

type AuditEvent = {
  id?: string
  occurred_at?: string
  actor_display_name?: string
  actor_subject?: string
  actor_provider?: string
  action?: string
  result?: string
}

type AgentReport = {
  summary?: Record<string, number>
  status_counts?: Record<string, number>
  intent_counts?: Record<string, number>
  planner_counts?: Record<string, number>
  model_counts?: Record<string, number>
  action_counts?: Record<string, number>
  tool_outcome_counts?: Record<string, number>
  recent_unsupported?: Array<{
    occurred_at?: string
    actor?: string
    message_sanitized?: string
    result?: string
  }>
}

type EngineerSetupRequest = {
  email: string
  first_name: string
  middle_name?: string
  last_name?: string
  country?: string
  gender?: string
  date_of_birth?: string
  date_of_joining?: string
  personal_email?: string
  prefered_email?: string
}

type EngineerSetupResult = {
  user?: string
  employee?: string
  employee_name?: string
  supplier?: string
  role?: string
  created?: Record<string, boolean>
  updated?: Record<string, boolean>
}

type DashboardDevError = {
  id: string
  occurredAt: string
  message: string
  name?: string
  status?: number
  statusText?: string
  method?: string
  url?: string
  path?: string
  view?: string
  detail?: string
  error?: string
  payload?: unknown
  stack?: string
}

const routes: Record<View, string> = {
  people: "/dashboard/people",
  gigs: "/dashboard/gigs",
  projects: "/dashboard/projects",
  onboarding: "/dashboard/onboarding",
  newsletter: "/dashboard/newsletter",
  jobs: "/dashboard/jobs",
  agent: "/dashboard/agent",
  audit: "/dashboard/audit",
  configuration: "/dashboard/configuration",
}

const routePermissions: Record<View, string> = {
  people: "people:read",
  gigs: "gigs:read",
  projects: "projects:read",
  onboarding: "onboarding:read",
  newsletter: "people:sync",
  jobs: "jobs:read",
  agent: "audit:read",
  audit: "audit:read",
  configuration: "configuration:read",
}

const peopleFilterDefinitions = {
  discord: {
    label: "Discord",
    options: [
      ["linked", "Linked"],
      ["missing", "Missing"],
    ],
  },
  email_508: {
    label: "508 email",
    options: [
      ["present", "Present"],
      ["missing", "Missing"],
    ],
  },
  resume: {
    label: "Resume",
    options: [
      ["present", "Present"],
      ["missing", "Missing"],
    ],
  },
  skills: {
    label: "Skills",
    options: [
      ["present", "Parsed"],
      ["missing", "Not parsed"],
    ],
  },
  sync_status: {
    label: "Sync status",
    options: [
      ["active", "Active"],
      ["conflict", "Conflict"],
      ["missing_in_crm", "Missing in CRM"],
    ],
  },
} as const

type PeopleFilterKey = keyof typeof peopleFilterDefinitions
type FilterState = Partial<Record<PeopleFilterKey, string>>

const onboardingStatusOptions = [
  ["pending", "Needs review"],
  ["selected", "Assigned to onboarder"],
  ["reachingout", "Reaching out"],
  ["awaitingcontribution", "Awaiting contribution"],
  ["onboarded", "Onboarded"],
  ["waitlist", "Waitlist"],
  ["rejected", "Rejected"],
] as const

const onboardingQueueFilterStatuses = onboardingStatusOptions.slice(0, 4)
const terminalOnboardingStatuses = new Set(["onboarded", "waitlist", "rejected"])

function normalizedOnboardingStatusValue(value?: string) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[-_\s]+/g, "")
}

class ApiRequestError extends Error {
  status: number
  statusText: string
  payload: unknown
  url: string
  method: string

  constructor(
    message: string,
    status: number,
    statusText: string,
    payload: unknown,
    url: string,
    method: string,
  ) {
    super(message)
    this.name = "ApiRequestError"
    this.status = status
    this.statusText = statusText
    this.payload = payload
    this.url = url
    this.method = method
  }
}

function stringFieldFromPayload(payload: unknown, key: string) {
  if (!payload || typeof payload !== "object") return undefined
  const value = (payload as Record<string, unknown>)[key]
  if (typeof value === "string") return value
  if (value === undefined || value === null) return undefined
  return JSON.stringify(value)
}

function messageForApiError(record: Record<string, unknown>, fallback: string) {
  const detail = record.detail
  if (typeof detail === "string" && detail.trim()) return detail

  const error = record.error
  if (typeof error !== "string") return fallback
  if (error === "person_not_found") {
    const person =
      typeof record.person === "string" && record.person.trim() ? record.person : "that person"
    return `No CRM person, ERPNext user, or ERPNext supplier matched "${person}". Try an email address or an exact name from CRM/ERPNext.`
  }
  if (error === "candidate_not_found") {
    return "The selected person record is no longer available. Search again and choose one of the current matches."
  }
  if (error === "invalid_crm_profile") {
    return "Paste a valid CRM Contact profile URL or Contact id."
  }
  if (error === "crm_profile_not_found") {
    return "That CRM Contact profile was not found."
  }
  if (error === "crm_profile_mismatch") {
    return "CRM returned a different Contact than the profile requested. Check the profile URL and try again."
  }
  if (error === "crm_profile_lookup_failed") {
    return "CRM profile lookup failed. Try again after CRM is reachable."
  }
  if (error === "ambiguous_person") {
    return "Multiple people matched. Choose the matching person record."
  }
  return error || fallback
}

function messageFromUnknown(error: unknown, fallback: string) {
  if (typeof error === "string" && error.trim()) return error
  if (error instanceof Error && error.message.trim()) return error.message
  return fallback
}

function devErrorFromUnknown(error: unknown, fallback: string): DashboardDevError {
  const message = messageFromUnknown(error, fallback)
  const apiError = error instanceof ApiRequestError ? error : null
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    occurredAt: new Date().toLocaleTimeString(),
    message,
    name: error instanceof Error ? error.name : undefined,
    status: apiError?.status,
    statusText: apiError?.statusText,
    method: apiError?.method,
    url: apiError?.url,
    path: `${window.location.pathname}${window.location.search}`,
    view: rawViewFromPath() || "people",
    detail: apiError ? stringFieldFromPayload(apiError.payload, "detail") : undefined,
    error: apiError ? stringFieldFromPayload(apiError.payload, "error") : undefined,
    payload: apiError?.payload,
    stack: error instanceof Error ? error.stack : undefined,
  }
}

function rawViewFromPath() {
  return window.location.pathname.split("/").filter(Boolean)[1] || ""
}

function viewFromPath(): View {
  const view = rawViewFromPath()
  return Object.hasOwn(routes, view) ? (view as View) : "people"
}

function detailIdFromPath(expectedView: "gigs" | "projects" = "gigs") {
  const [, view, detailId] = window.location.pathname.split("/").filter(Boolean)
  if (view !== expectedView || !detailId) return ""
  try {
    return decodeURIComponent(detailId)
  } catch {
    return ""
  }
}

function gigTabFromHash(hash = window.location.hash): GigTab {
  return hash.replace(/^#/, "") === "leads" ? "leads" : "gigs"
}

function updateGigTabHash(tab: GigTab) {
  const hash = tab === "leads" ? "#leads" : "#gigs"
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${window.location.search}${hash}`,
  )
}

async function requestJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const method = String(options.method || "GET").toUpperCase()
  const headers = new Headers(options.headers)
  headers.set("Accept", "application/json")
  let response: Response
  try {
    response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers,
    })
  } catch (error) {
    throw new ApiRequestError(
      messageFromUnknown(error, "Network request failed"),
      0,
      "Network request failed",
      null,
      url,
      method,
    )
  }
  if (response.status === 401) {
    const next = `${window.location.pathname}${window.location.search}` || "/dashboard"
    window.location.assign(`/auth/login?next=${encodeURIComponent(next)}`)
    throw new ApiRequestError(
      "Session expired",
      response.status,
      response.statusText,
      null,
      url,
      method,
    )
  }
  if (!response.ok) {
    let detail: unknown = response.statusText
    let payload: unknown = null
    try {
      payload = await response.json()
      if (payload && typeof payload === "object") {
        const record = payload as Record<string, unknown>
        detail = messageForApiError(record, String(detail || "Request failed"))
      }
    } catch {
      detail = response.statusText
    }
    throw new ApiRequestError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      response.status,
      response.statusText,
      payload,
      url,
      method,
    )
  }
  return response.json() as Promise<T>
}

type SortableItem = Job | Person | Gig | Project | AuditEvent | NewsletterSuppression

function sortValue(scope: View, item: SortableItem, key: string) {
  if (scope === "gigs") {
    const gig = item as Gig
    if (key === "title") return gig.title || ""
    if (key === "status") return gig.status || ""
    if (key === "applications") return Number(gig.application_count || 0)
    if (key === "activity") return gigActivityTimestamp(gig)
  }
  if (scope === "projects") {
    const project = item as Project
    if (key === "display_name") return project.display_name || ""
    if (key === "customer") return project.customer || ""
    if (key === "status") return project.source_status || ""
    if (key === "roster_count") return Number(project.roster_count || 0)
    if (key === "modified") return project.source_modified_at || project.last_synced_at || ""
  }
  if (scope === "onboarding") {
    const person = item as Person
    const status = person.profile_status || {}
    if (key === "name") return person.name || person.email_508 || person.email || ""
    if (key === "onboarding_state") {
      const state = onboardingStateValue(person)
      return state.toLowerCase() === "pending" ? `zzz-${state}` : state
    }
    if (key === "onboarder") return person.onboarder || ""
    if (key === "updated") return person.onboarding_updated_at || ""
    if (key === "profile_gaps") {
      return [
        !status.discord_linked,
        !status.latest_resume,
        Number(status.skills_count || 0) <= 0,
      ].filter(Boolean).length
    }
  }
  if (scope === "people") {
    const person = item as Person
    const status = person.profile_status || {}
    if (key === "name") return person.name || person.email_508 || person.email || ""
    if (key === "status") {
      return [
        status.crm_active,
        status.is_member,
        status.discord_linked,
        status.email_508,
        status.latest_resume,
      ].filter(Boolean).length
    }
    if (key === "discord") return person.discord_username || person.discord_user_id || ""
    if (key === "resume") return person.latest_resume_name || person.latest_resume_id || ""
  }
  if (scope === "audit") {
    const event = item as AuditEvent
    if (key === "actor")
      return event.actor_display_name || event.actor_subject || event.actor_provider || ""
  }
  if (scope === "newsletter") {
    const record = item as NewsletterSuppression
    if (key === "email") return record.email || ""
    if (key === "source_provider") return record.source_provider || ""
    if (key === "reason") return record.reason || ""
    if (key === "first_seen_at") return record.first_seen_at || ""
    if (key === "last_seen_at") return record.last_seen_at || record.updated_at || ""
  }
  return (item as Record<string, unknown>)[key] ?? ""
}

function sortItems<T extends SortableItem>(
  scope: View,
  items: T[],
  sort: { key: string; direction: SortDirection },
) {
  const multiplier = sort.direction === "asc" ? 1 : -1
  return [...items].sort((a, b) => {
    const left = sortValue(scope, a, sort.key)
    const right = sortValue(scope, b, sort.key)
    if (typeof left === "number" && typeof right === "number") {
      return (left - right) * multiplier
    }
    return String(left).localeCompare(String(right), undefined, { numeric: true }) * multiplier
  })
}

function Metric({ label, value, id }: { label: string; value: number; id?: string }) {
  return (
    <Card className="p-4">
      <span className="text-xs font-bold text-muted-foreground">{label}</span>
      <strong id={id} className="block text-2xl">
        {value}
      </strong>
    </Card>
  )
}

function HighlightedText({ value, query }: { value: string; query: string }) {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) return <>{value}</>

  const lowerValue = value.toLowerCase()
  const pieces: ReactNode[] = []
  let cursor = 0
  let matchIndex = lowerValue.indexOf(normalizedQuery)
  while (matchIndex >= 0) {
    if (matchIndex > cursor) {
      pieces.push(value.slice(cursor, matchIndex))
    }
    const matchEnd = matchIndex + normalizedQuery.length
    pieces.push(
      <mark
        key={`${matchIndex}-${matchEnd}`}
        className="rounded-sm bg-amber-200 px-0.5 text-inherit dark:bg-amber-500/35"
      >
        {value.slice(matchIndex, matchEnd)}
      </mark>,
    )
    cursor = matchEnd
    matchIndex = lowerValue.indexOf(normalizedQuery, cursor)
  }
  if (cursor < value.length) {
    pieces.push(value.slice(cursor))
  }
  return <>{pieces}</>
}

function App() {
  const initialProjectDetailId = detailIdFromPath("projects")
  const [user, setUser] = useState<User | null>(null)
  const [view, setViewState] = useState<View>(viewFromPath())
  const [toast, setToast] = useState<{ message: string; tone?: "ok" | "warning" | "error" }>({
    message: "",
  })
  const [permissions, setPermissions] = useState<string[]>([])
  const [crmBaseUrl, setCrmBaseUrl] = useState("")
  const [jobs, setJobs] = useState<Job[]>([])
  const [gigs, setGigs] = useState<Gig[]>([])
  const [gigLeads, setGigLeads] = useState<JobLead[]>([])
  const [gigLeadScrapeStatus, setGigLeadScrapeStatus] = useState<JobLeadScrapeStatus | null>(null)
  const [jobPostChannels, setJobPostChannels] = useState<JobPostChannel[]>([])
  const [availableJobPostChannels, setAvailableJobPostChannels] = useState<JobPostChannel[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [projectsSummary, setProjectsSummary] = useState<ProjectsResponse["summary"]>({})
  const [wikiMatches, setWikiMatches] = useState<WikiMatchPreview | null>(null)
  const [gigDetail, setGigDetail] = useState<Gig | null>(null)
  const [selectedGigId, setSelectedGigId] = useState(detailIdFromPath())
  const [selectedProjectId, setSelectedProjectId] = useState(initialProjectDetailId)
  const [notifications, setNotifications] = useState<DashboardNotification[]>([])
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [people, setPeople] = useState<Person[]>([])
  const [newsletterSuppressions, setNewsletterSuppressions] = useState<NewsletterSuppression[]>([])
  const [newsletterStatus, setNewsletterStatus] = useState<NewsletterStatus | null>(null)
  const [onboarding, setOnboarding] = useState<Person[]>([])
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([])
  const [agentReport, setAgentReport] = useState<AgentReport | null>(null)
  const [configurationItems, setConfigurationItems] = useState<ConfigurationItem[]>([])
  const [configurationFocus, setConfigurationFocus] = useState<{
    category: string
    nonce: number
  } | null>(null)
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [devErrors, setDevErrors] = useState<DashboardDevError[]>([])
  const [historicalPersonChoice, setHistoricalPersonChoice] = useState<{
    projectId: string
    person: string
    candidates: HistoricalPersonCandidate[]
  } | null>(null)
  const [sort, setSortState] = useState<Record<View, { key: string; direction: SortDirection }>>({
    onboarding: { key: "onboarding_state", direction: "asc" },
    gigs: { key: "activity", direction: "desc" },
    projects: { key: "display_name", direction: "asc" },
    newsletter: { key: "last_seen_at", direction: "desc" },
    jobs: { key: "updated_at", direction: "desc" },
    people: { key: "name", direction: "asc" },
    agent: { key: "occurred_at", direction: "desc" },
    audit: { key: "occurred_at", direction: "desc" },
    configuration: { key: "category", direction: "asc" },
  })

  const [minutes, setMinutes] = useState("60")
  const [status, setStatus] = useState("")
  const [jobType, setJobType] = useState("")
  const [gigStatus, setGigStatus] = useState("")
  const [gigQuery, setGigQuery] = useState("")
  const [gigIncludeHistorical, setGigIncludeHistorical] = useState(false)
  const [gigLimit, setGigLimit] = useState(100)
  const [activeGigTab, setActiveGigTab] = useState<GigTab>(gigTabFromHash)
  const [gigLeadStatus, setGigLeadStatus] = useState("pending")
  const [projectQuery, setProjectQuery] = useState("")
  const [projectStatus, setProjectStatus] = useState(initialProjectDetailId ? "" : "Open")
  const [staleRecruitingDays, setStaleRecruitingDays] = useState(7)
  const [contactedReminderDays, setContactedReminderDays] = useState(5)
  const [peopleQuery, setPeopleQuery] = useState("")
  const [peopleMember, setPeopleMember] = useState("")
  const [peopleFilters, setPeopleFilters] = useState<FilterState>({})
  const [peopleFilterKind, setPeopleFilterKind] = useState<PeopleFilterKey>("discord")
  const [peopleFilterValue, setPeopleFilterValue] = useState("linked")
  const [newsletterProviderFilter, setNewsletterProviderFilter] = useState("")
  const [onboardingQuery, setOnboardingQuery] = useState("")
  const [onboardingState, setOnboardingState] = useState("")
  const [onboarderFilter, setOnboarderFilter] = useState("")
  const [onboardingFilters, setOnboardingFilters] = useState<FilterState>({})
  const [onboardingFilterKind, setOnboardingFilterKind] = useState<PeopleFilterKey>("discord")
  const [onboardingFilterValue, setOnboardingFilterValue] = useState("linked")
  const navigateRef = useRef<(nextView: View, push?: boolean) => void>(() => undefined)

  function can(permission: string) {
    return permissions.includes(permission)
  }

  function canDryRun(permission: string) {
    return permissions.includes(`${permission}:dry_run`)
  }

  function canUse(permission: string) {
    return can(permission) || canDryRun(permission)
  }

  function canView(nextView: View) {
    return can(routePermissions[nextView])
  }

  function firstAllowedView() {
    return (Object.keys(routes) as View[]).find((candidate) => canView(candidate)) || "people"
  }

  function showToast(message: string, tone?: "ok" | "warning" | "error") {
    setToast({ message, tone })
  }

  function showError(error: unknown, fallback: string) {
    showToast(messageFromUnknown(error, fallback), "error")
    if (import.meta.env.DEV) {
      setDevErrors((current) => [devErrorFromUnknown(error, fallback), ...current].slice(0, 8))
    }
  }

  function setBusy(key: string, value: boolean) {
    setLoading((current) => ({ ...current, [key]: value }))
  }

  function navigate(nextView: View, push = false) {
    let normalized = nextView
    if (!canView(normalized)) {
      showToast(
        `${normalized[0].toUpperCase()}${normalized.slice(1)} requires SSO validation`,
        "error",
      )
      normalized = firstAllowedView()
    }
    if (normalized !== "gigs") setSelectedGigId("")
    if (normalized !== "projects") setSelectedProjectId("")
    if (normalized === "gigs" && push) setSelectedGigId("")
    if (normalized === "projects" && push) setSelectedProjectId("")
    if (normalized === "gigs") setActiveGigTab(push ? "gigs" : gigTabFromHash())
    setViewState(normalized)
    if (push) {
      window.history.pushState({ view: normalized }, "", routes[normalized])
    } else if (!Object.hasOwn(routes, rawViewFromPath()) || normalized !== nextView) {
      window.history.replaceState({ view: normalized }, "", routes[normalized])
    }
  }
  navigateRef.current = navigate

  function selectGigTab(tab: GigTab) {
    setActiveGigTab(tab)
    updateGigTabHash(tab)
  }

  function crmContactUrl(contactId?: string) {
    if (!crmBaseUrl || !contactId) return ""
    return `${crmBaseUrl}/#Contact/view/${encodeURIComponent(contactId)}`
  }

  function crmAttachmentUrl(attachmentId?: string) {
    if (!crmBaseUrl || !attachmentId) return ""
    return `${crmBaseUrl}/api/v1/Attachment/file/${encodeURIComponent(attachmentId)}`
  }

  function handleSort(scope: View, key: string) {
    setSortState((current) => {
      const existing = current[scope]
      return {
        ...current,
        [scope]: {
          key,
          direction: existing.key === key && existing.direction === "asc" ? "desc" : "asc",
        },
      }
    })
  }

  function openGigDetail(gigId: string) {
    setSelectedGigId(gigId)
    setGigDetail(gigs.find((gig) => gig.id === gigId) || null)
    setViewState("gigs")
    window.history.pushState(
      { view: "gigs", gigId },
      "",
      `/dashboard/gigs/${encodeURIComponent(gigId)}`,
    )
  }

  function closeGigDetail() {
    setSelectedGigId("")
    setGigDetail(null)
    window.history.replaceState({ view: "gigs" }, "", routes.gigs)
  }

  function openProjectDetail(projectId: string) {
    setSelectedProjectId(projectId)
    setViewState("projects")
    window.history.pushState(
      { view: "projects", projectId },
      "",
      `/dashboard/projects/${encodeURIComponent(projectId)}`,
    )
  }

  function closeProjectDetail() {
    setSelectedProjectId("")
    window.history.replaceState({ view: "projects" }, "", routes.projects)
  }

  async function loadUser() {
    const payload = await requestJson<User>("/dashboard/api/me")
    setUser(payload)
    const nextPermissions = Array.isArray(payload.permissions) ? payload.permissions : []
    setPermissions(nextPermissions)
    setCrmBaseUrl((payload.crm_base_url || "").replace(/\/+$/, ""))
    return nextPermissions
  }

  function jobsUrl() {
    const params = new URLSearchParams({ minutes, limit: "100" })
    if (status) params.set("status", status)
    if (jobType.trim()) params.set("type", jobType.trim())
    return `/dashboard/api/jobs?${params.toString()}`
  }

  function gigsUrl(options: { status?: string; query?: string } = {}) {
    const status = options.status ?? gigStatus
    const query = options.query ?? gigQuery
    const params = new URLSearchParams({ limit: String(gigLimit) })
    if (status) params.set("status", status)
    if (query.trim()) params.set("query", query.trim())
    if (gigIncludeHistorical) params.set("include_historical", "true")
    return `/dashboard/api/gigs?${params.toString()}`
  }

  function gigLeadsUrl() {
    const params = new URLSearchParams({ limit: "50" })
    if (gigLeadStatus) params.set("status", gigLeadStatus)
    return `/dashboard/api/gig-leads?${params.toString()}`
  }

  function gigLeadScrapeStatusUrl(jobId?: string) {
    const params = new URLSearchParams()
    if (jobId) params.set("job_id", jobId)
    const query = params.toString()
    return `/dashboard/api/gig-leads/scrape-status${query ? `?${query}` : ""}`
  }

  function jobChannelsUrl(options: { includeAvailable?: boolean } = {}) {
    const params = new URLSearchParams()
    if (options.includeAvailable) params.set("include_available", "true")
    const query = params.toString()
    return `/dashboard/api/job-channels${query ? `?${query}` : ""}`
  }

  function projectsUrl() {
    const params = new URLSearchParams({ limit: "100", status: projectStatus })
    if (projectQuery.trim()) params.set("query", projectQuery.trim())
    return `/dashboard/api/projects?${params.toString()}`
  }

  async function loadJobs() {
    setBusy("jobs", true)
    showToast("Loading background tasks")
    try {
      const payload = await requestJson<Job[]>(jobsUrl())
      setJobs(payload)
      showToast(`Loaded ${payload.length} background task${payload.length === 1 ? "" : "s"}`, "ok")
    } catch (error) {
      showError(error, "Unable to load background tasks")
    } finally {
      setBusy("jobs", false)
    }
  }

  async function loadGigs(options: { status?: string; query?: string } = {}) {
    setBusy("gigs", true)
    try {
      const payload = await requestJson<Gig[]>(gigsUrl(options))
      setGigs(payload)
      showToast(`Loaded ${payload.length} gig${payload.length === 1 ? "" : "s"}`, "ok")
      void loadNotifications()
    } catch (error) {
      showError(error, "Unable to load gigs")
    } finally {
      setBusy("gigs", false)
    }
  }

  async function loadGigLeads() {
    setBusy("gigLeads", true)
    try {
      const payload = await requestJson<JobLead[]>(gigLeadsUrl())
      setGigLeads(payload)
      showToast(`Loaded ${payload.length} lead${payload.length === 1 ? "" : "s"}`, "ok")
    } catch (error) {
      showError(error, "Unable to load job leads")
    } finally {
      setBusy("gigLeads", false)
    }
  }

  async function fetchGigLeadScrapeStatus(jobId?: string) {
    return requestJson<JobLeadScrapeStatus>(gigLeadScrapeStatusUrl(jobId))
  }

  async function loadGigLeadScrapeStatus(jobId?: string) {
    setBusy("gigLeadScrapeStatus", true)
    try {
      const payload = await fetchGigLeadScrapeStatus(jobId)
      setGigLeadScrapeStatus(payload)
      return payload
    } catch (error) {
      showError(error, "Unable to load HN scrape status")
      return null
    } finally {
      setBusy("gigLeadScrapeStatus", false)
    }
  }

  async function loadJobPostChannels(options: { includeAvailable?: boolean } = {}) {
    if (!can("gigs:read")) return
    setBusy("jobPostChannels", true)
    try {
      const payload = await requestJson<JobChannelsResponse>(jobChannelsUrl(options))
      setJobPostChannels(payload.channels || [])
      if (options.includeAvailable) {
        setAvailableJobPostChannels(payload.available_channels || payload.channels || [])
      }
    } catch (error) {
      showError(error, "Unable to load job channels")
    } finally {
      setBusy("jobPostChannels", false)
    }
  }

  function applyJobChannelsPayload(payload: JobChannelsResponse) {
    setJobPostChannels(payload.channels || [])
    setAvailableJobPostChannels(payload.available_channels || payload.channels || [])
  }

  async function updateJobPostChannel(channelId: string, postingType: string) {
    setBusy(`jobPostChannel:${channelId}`, true)
    try {
      const payload = await requestJson<JobChannelsResponse>(
        `/dashboard/api/job-channels/${encodeURIComponent(channelId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ posting_type: postingType }),
        },
      )
      applyJobChannelsPayload(payload)
      showToast("Saved job channel", "ok")
      return true
    } catch (error) {
      showError(error, "Unable to save job channel")
      return false
    } finally {
      setBusy(`jobPostChannel:${channelId}`, false)
    }
  }

  async function deleteJobPostChannel(channelId: string) {
    setBusy(`jobPostChannel:${channelId}`, true)
    try {
      const payload = await requestJson<JobChannelsResponse>(
        `/dashboard/api/job-channels/${encodeURIComponent(channelId)}`,
        { method: "DELETE" },
      )
      applyJobChannelsPayload(payload)
      showToast("Deregistered job channel", "ok")
    } catch (error) {
      showError(error, "Unable to deregister job channel")
    } finally {
      setBusy(`jobPostChannel:${channelId}`, false)
    }
  }

  async function pollGigLeadSyncJob(jobId: string) {
    for (let attempt = 0; attempt < GIG_LEAD_SYNC_MAX_POLLS; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, GIG_LEAD_SYNC_POLL_INTERVAL_MS))
      const detail = await fetchGigLeadScrapeStatus(jobId)
      setGigLeadScrapeStatus(detail)
      const status = String(detail.status || "")
        .trim()
        .toLowerCase()
      if (!isTerminalJobStatus(status)) continue

      if (status === "succeeded") {
        const result = detail.result
        const included = Number(result?.included)
        const filteredOut = Number(result?.filtered_out)
        const summary =
          Number.isFinite(included) && Number.isFinite(filteredOut)
            ? `: ${included} included, ${filteredOut} filtered out`
            : ""
        showToast(`HN lead scrape finished${summary}; refreshing leads`, "ok")
        await loadGigLeads()
        if (view === "jobs") await loadJobs()
        return
      }

      if (view === "jobs") await loadJobs()
      showToast(`HN lead scrape ${status || "finished"}; check background task ${jobId}`, "warning")
      return
    }

    showToast(
      `HN lead scrape is still running; refresh leads or check background task ${jobId}`,
      "warning",
    )
  }

  async function loadProjects() {
    setBusy("projects", true)
    try {
      const payload = await requestJson<ProjectsResponse>(projectsUrl())
      setProjects(payload.projects || [])
      setProjectsSummary(payload.summary || {})
      showToast(
        `Loaded ${(payload.projects || []).length} project${(payload.projects || []).length === 1 ? "" : "s"}`,
        "ok",
      )
    } catch (error) {
      showError(error, "Unable to load projects")
    } finally {
      setBusy("projects", false)
    }
  }

  async function syncProjects() {
    setBusy("syncProjects", true)
    showToast("Queueing project sync")
    try {
      const payload = await requestJson<{
        job_id?: string
        dry_run?: boolean
        would_enqueue?: { job_type?: string }
      }>("/dashboard/api/sync/projects", {
        method: "POST",
      })
      if (payload.dry_run) {
        showToast(
          `Dry run only: would queue ${payload.would_enqueue?.job_type || "project sync"}`,
          "warning",
        )
      } else {
        showToast(`Queued project sync ${payload.job_id}`, "ok")
      }
    } catch (error) {
      showError(error, "Unable to queue project sync")
    } finally {
      setBusy("syncProjects", false)
    }
  }

  async function searchERPNextCustomers(query: string) {
    const normalizedQuery = query.trim()
    if (normalizedQuery.length < 2) return []
    try {
      const params = new URLSearchParams({ query: normalizedQuery })
      const payload = await requestJson<{ customers: ERPNextCustomer[] }>(
        `/dashboard/api/erpnext/customers?${params.toString()}`,
      )
      return payload.customers || []
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to search customers", "error")
      return []
    }
  }

  async function searchERPNextContacts(query: string) {
    const normalizedQuery = query.trim()
    if (normalizedQuery.length < 2) return []
    try {
      const params = new URLSearchParams({ query: normalizedQuery })
      const payload = await requestJson<{ contacts: ERPNextContact[] }>(
        `/dashboard/api/erpnext/contacts?${params.toString()}`,
      )
      return payload.contacts || []
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to search contacts", "error")
      return []
    }
  }

  async function searchERPNextAccountManagers(query: string) {
    const normalizedQuery = query.trim()
    if (normalizedQuery.length < 2) return []
    try {
      const params = new URLSearchParams({ query: normalizedQuery })
      const payload = await requestJson<{ users: ERPNextUser[] }>(
        `/dashboard/api/erpnext/account-managers?${params.toString()}`,
      )
      return payload.users || []
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Unable to search account managers",
        "error",
      )
      return []
    }
  }

  async function loadERPNextCostCenters() {
    try {
      const payload = await requestJson<{ cost_centers: ERPNextCostCenter[] }>(
        "/dashboard/api/erpnext/cost-centers",
      )
      const costCenters = payload.cost_centers || []
      return costCenters.length
        ? costCenters
        : [{ name: "Projects - 5", cost_center_name: "Projects" }]
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to load cost centers", "error")
      return [{ name: "Projects - 5", cost_center_name: "Projects" }]
    }
  }

  async function createProjectSetup(values: {
    project_name: string
    customer_mode: "new" | "existing"
    customer_name?: string
    customer?: string
    account_manager?: string
    default_billing_currency?: string
    default_cost_center?: string
    activity_type?: string
    customer_details?: string
    customer_website?: string
    address_line1?: string
    address_line2?: string
    address_city?: string
    address_state?: string
    address_country?: string
    address_postal_code?: string
    contact?: string
    contact_first_name?: string
    contact_last_name?: string
    contact_email?: string
    contact_phone?: string
    contact_mobile?: string
  }) {
    setBusy("createProject", true)
    try {
      const payload = await requestJson<{
        project: Project
        customer: ERPNextCustomer
        activity_type: { name?: string }
        cache_refresh_error?: string
        cache_refresh_message?: string
        setup_warnings?: string[]
        setup_warning_message?: string
      }>("/dashboard/api/projects/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      })
      if (payload.project.id) {
        setProjects((current) => {
          const existing = current.some((project) => project.id === payload.project.id)
          return existing
            ? current.map((project) =>
                project.id === payload.project.id ? payload.project : project,
              )
            : [payload.project, ...current]
        })
        showToast(
          payload.setup_warnings?.length
            ? payload.setup_warning_message ||
                "Created ERP project setup; account manager setup needs follow-up"
            : "Created ERP project setup",
          payload.setup_warnings?.length ? "warning" : "ok",
        )
        openProjectDetail(payload.project.id)
      } else {
        const cacheRefreshMessage =
          payload.cache_refresh_message || "Created ERP project in ERPNext; local sync is pending"
        const setupWarningMessage = payload.setup_warnings?.length
          ? payload.setup_warning_message || "Account manager setup needs follow-up"
          : ""
        showToast(
          [cacheRefreshMessage, setupWarningMessage].filter(Boolean).join(" "),
          payload.setup_warnings?.length ? "warning" : "ok",
        )
        void loadProjects()
      }
      return true
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to create project", "error")
      return false
    } finally {
      setBusy("createProject", false)
    }
  }

  async function updateProjectStatus(projectId: string, nextStatus: string) {
    setBusy(`project:${projectId}:status`, true)
    try {
      const payload = await requestJson<{ project: Project }>(
        `/dashboard/api/projects/${encodeURIComponent(projectId)}/status`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: nextStatus }),
        },
      )
      setProjects((current) =>
        current.map((project) => (project.id === projectId ? payload.project : project)),
      )
      showToast("Updated project status", "ok")
    } catch (error) {
      showError(error, "Unable to update project")
    } finally {
      setBusy(`project:${projectId}:status`, false)
    }
  }

  async function bulkUpdateProjects(
    projectIds: string[],
    updates: { status?: string; project_type?: string },
  ) {
    if (projectIds.length === 0) return false
    setBusy("projectsBulkUpdate", true)
    try {
      const payload = await requestJson<{
        projects: Project[]
        failures: Array<{ error?: string }>
      }>("/dashboard/api/projects/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_ids: projectIds, ...updates }),
      })
      const updatedProjects = payload.projects || []
      setProjects((current) =>
        current.map((project) => updatedProjects.find((item) => item.id === project.id) || project),
      )
      const failures = payload.failures || []
      showToast(
        failures.length
          ? `Updated ${updatedProjects.length}; ${failures.length} failed`
          : `Updated ${updatedProjects.length} project${updatedProjects.length === 1 ? "" : "s"}`,
        failures.length ? "error" : "ok",
      )
      return failures.length === 0
    } catch (error) {
      showError(error, "Unable to bulk update projects")
      return false
    } finally {
      setBusy("projectsBulkUpdate", false)
    }
  }

  async function addProjectUser(
    projectId: string,
    userName: string,
    candidateId: string,
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) {
    const normalizedUser = userName.trim()
    const normalizedCandidateId = candidateId.trim()
    if (!normalizedUser || !normalizedCandidateId) return false
    setBusy(`project:${projectId}:user`, true)
    try {
      const payload = await requestJson<{
        project: Project
        activity_cost?: object | null
        activity_cost_error?: string | null
      }>(`/dashboard/api/projects/${encodeURIComponent(projectId)}/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user: normalizedUser,
          candidate_id: normalizedCandidateId,
          ...(rates || {}),
        }),
      })
      setProjects((current) =>
        current.map((project) => (project.id === projectId ? payload.project : project)),
      )
      showToast(
        payload.activity_cost_error
          ? "Added project user; rate failed"
          : payload.activity_cost
            ? "Added project user and rate"
            : "Added project user",
        payload.activity_cost_error ? "error" : "ok",
      )
      return true
    } catch (error) {
      showError(error, "Unable to add project user")
      return false
    } finally {
      setBusy(`project:${projectId}:user`, false)
    }
  }

  async function removeProjectUser(projectId: string, userName: string) {
    const normalizedUser = userName.trim()
    if (!normalizedUser) return false
    setBusy(`project:${projectId}:user`, true)
    try {
      const payload = await requestJson<{ project: Project }>(
        `/dashboard/api/projects/${encodeURIComponent(projectId)}/users/remove`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user: normalizedUser }),
        },
      )
      setProjects((current) =>
        current.map((project) => (project.id === projectId ? payload.project : project)),
      )
      showToast("Removed project user", "ok")
      return true
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to remove project user", "error")
      return false
    } finally {
      setBusy(`project:${projectId}:user`, false)
    }
  }

  async function addHistoricalProjectMember(
    projectId: string,
    person: string,
    candidateId?: string,
  ) {
    const normalizedPerson = person.trim()
    if (!normalizedPerson) return false
    setBusy(`project:${projectId}:historical`, true)
    try {
      const payload = await requestJson<{ project: Project }>(
        `/dashboard/api/projects/${encodeURIComponent(projectId)}/historical-members`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ person: normalizedPerson, candidate_id: candidateId }),
        },
      )
      setProjects((current) =>
        current.map((project) => (project.id === projectId ? payload.project : project)),
      )
      setHistoricalPersonChoice(null)
      showToast("Added historical project member", "ok")
      return true
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        const payload = error.payload as { candidates?: HistoricalPersonCandidate[] } | null
        const candidates = payload?.candidates || []
        if (candidates.length > 0) {
          setHistoricalPersonChoice({ projectId, person: normalizedPerson, candidates })
          showToast("Choose the matching person record", "error")
          return false
        }
      }
      showError(error, "Unable to add historical member")
      return false
    } finally {
      setBusy(`project:${projectId}:historical`, false)
    }
  }

  async function removeHistoricalProjectMember(projectId: string, sourceUserId: string) {
    const normalizedSourceUserId = sourceUserId.trim()
    if (!normalizedSourceUserId) return false
    setBusy(`project:${projectId}:historical`, true)
    try {
      const payload = await requestJson<{ project: Project }>(
        `/dashboard/api/projects/${encodeURIComponent(projectId)}/historical-members/remove`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_user_id: normalizedSourceUserId }),
        },
      )
      setProjects((current) =>
        current.map((project) => (project.id === projectId ? payload.project : project)),
      )
      showToast("Removed historical project member", "ok")
      return true
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Unable to remove historical member",
        "error",
      )
      return false
    } finally {
      setBusy(`project:${projectId}:historical`, false)
    }
  }

  async function updateProjectWikiMatch(projectId: string, status: string, rowKey?: string) {
    setBusy(`project:${projectId}:wiki`, true)
    try {
      await requestJson<{ manual_match: object }>(
        `/dashboard/api/projects/${encodeURIComponent(projectId)}/wiki-match`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, row_key: rowKey }),
        },
      )
      showToast(status === "no_row" ? "Marked as no wiki row" : "Confirmed wiki match", "ok")
      await loadWikiMatches()
    } catch (error) {
      showError(error, "Unable to save wiki match")
    } finally {
      setBusy(`project:${projectId}:wiki`, false)
    }
  }

  async function loadWikiMatches() {
    setBusy("wikiMatches", true)
    try {
      const payload = await requestJson<WikiMatchPreview>("/dashboard/api/projects/wiki-matches")
      setWikiMatches(payload)
      showToast("Loaded wiki match preview", "ok")
    } catch (error) {
      showError(error, "Unable to load wiki matches")
    } finally {
      setBusy("wikiMatches", false)
    }
  }

  async function loadGigDetail(gigId: string) {
    setBusy(`gig:${gigId}:detail`, true)
    try {
      const payload = await requestJson<Gig>(`/dashboard/api/gigs/${encodeURIComponent(gigId)}`)
      setGigDetail(payload)
    } catch (error) {
      setGigDetail(null)
      showError(error, "Unable to load gig")
    } finally {
      setBusy(`gig:${gigId}:detail`, false)
    }
  }

  async function refreshGigsView() {
    await loadGigs()
    await loadGigLeads()
    await loadGigLeadScrapeStatus()
    await loadJobPostChannels()
    if (selectedGigId) await loadGigDetail(selectedGigId)
  }

  async function syncGigLeads() {
    setBusy("gigLeadsSync", true)
    showToast("Queueing HN lead scrape")
    try {
      const payload = await requestJson<{
        job_id?: string
        created?: boolean
        source?: string
        story_id?: number | null
      }>("/dashboard/api/gig-leads/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "hackernews_who_is_hiring" }),
      })
      showToast(
        payload.created === false ? "HN lead scrape already queued" : "HN lead scrape queued",
        "ok",
      )
      if (payload.job_id) {
        setGigLeadScrapeStatus({
          status: "queued",
          job_id: payload.job_id,
          source: payload.source || "hackernews_who_is_hiring",
          story_id: payload.story_id || null,
          result: null,
        })
        try {
          await pollGigLeadSyncJob(payload.job_id)
        } catch (error) {
          showError(error, "Unable to monitor HN lead scrape")
        }
      } else {
        await loadGigLeads()
      }
    } catch (error) {
      showError(error, "Unable to queue HN lead scrape")
    } finally {
      setBusy("gigLeadsSync", false)
    }
  }

  async function reviewGigLead(leadId: string, nextStatus: JobLeadReviewStatus) {
    setBusy(`gigLead:${leadId}:review`, true)
    try {
      await requestJson<JobLead>(`/dashboard/api/gig-leads/${encodeURIComponent(leadId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      })
      const message =
        nextStatus === "pending"
          ? "Restored lead to pending"
          : `${nextStatus === "approved" ? "Approved" : "Rejected"} lead`
      showToast(message, "ok")
      await loadGigLeads()
    } catch (error) {
      showError(error, "Unable to review lead")
    } finally {
      setBusy(`gigLead:${leadId}:review`, false)
    }
  }

  async function postGigLead(
    leadId: string,
    options: {
      channelId?: string
      engagementStatus?: "lead" | "recruiting"
      tags?: string[]
    } = {},
  ) {
    setBusy(`gigLead:${leadId}:post`, true)
    try {
      const result = await requestJson<JobLeadPostResult>(
        `/dashboard/api/gig-leads/${encodeURIComponent(leadId)}/post`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel_id: options.channelId || undefined,
            engagement_status: options.engagementStatus || "lead",
            tags: (options.tags || []).join(",") || undefined,
          }),
        },
      )
      const engagementId = String(result.engagement_id || "").trim()
      const postedGigUrl = engagementId
        ? `${routes.gigs}/${encodeURIComponent(engagementId)}`
        : undefined
      setGigLeads((current) =>
        current.map((lead) =>
          lead.id === leadId
            ? {
                ...lead,
                status: "posted",
                discord_guild_id: result.guild_id || lead.discord_guild_id,
                discord_channel_id: result.channel_id || lead.discord_channel_id,
                discord_thread_id: result.thread_id || lead.discord_thread_id,
                posted_gig_url: postedGigUrl,
              }
            : lead,
        ),
      )
      showToast("Posted lead to Discord; staying on Leads", "ok")
    } catch (error) {
      showError(error, "Unable to post lead")
    } finally {
      setBusy(`gigLead:${leadId}:post`, false)
    }
  }

  async function loadNotifications() {
    if (!can("gigs:read")) return
    setBusy("notifications", true)
    try {
      const payload = await requestJson<DashboardNotificationsResponse>(
        "/dashboard/api/notifications?limit=20",
      )
      setStaleRecruitingDays(payload.stale_days || 7)
      setContactedReminderDays(payload.contacted_reminder_days || 5)
      setNotifications(payload.notifications || [])
    } catch (error) {
      showError(error, "Unable to load notifications")
    } finally {
      setBusy("notifications", false)
    }
  }

  async function updateGigStatus(gigId: string, nextStatus: string) {
    setBusy(`gig:${gigId}:status`, true)
    try {
      const payload = await requestJson<{
        status: string
        discord_title_sync?: { status?: string; reason?: string }
      }>(`/dashboard/api/gigs/${encodeURIComponent(gigId)}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      })
      const titleSyncStatus = payload.discord_title_sync?.status
      showToast(
        titleSyncStatus === "error"
          ? "Updated gig status; Discord title sync failed"
          : "Updated gig status",
        titleSyncStatus === "error" ? "error" : "ok",
      )
      await loadGigs()
      if (selectedGigId === gigId) await loadGigDetail(gigId)
    } catch (error) {
      showError(error, "Unable to update gig")
    } finally {
      setBusy(`gig:${gigId}:status`, false)
    }
  }

  async function updateGigApplicationStatus(
    gigId: string,
    applicationId: string,
    nextStatus: string,
  ) {
    setBusy(`application:${applicationId}:status`, true)
    try {
      await requestJson<{ status: string }>(
        `/dashboard/api/gigs/${encodeURIComponent(gigId)}/applications/${encodeURIComponent(
          applicationId,
        )}/status`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: nextStatus }),
        },
      )
      showToast("Updated candidate status", "ok")
      await loadGigs()
      if (selectedGigId === gigId) await loadGigDetail(gigId)
    } catch (error) {
      showError(error, "Unable to update candidate")
    } finally {
      setBusy(`application:${applicationId}:status`, false)
    }
  }

  async function addGigApplication(gigId: string, crmProfile: string) {
    const normalizedProfile = crmProfile.trim()
    if (!normalizedProfile) {
      showToast("Choose a candidate first", "warning")
      return false
    }
    setBusy(`gig:${gigId}:addCandidate`, true)
    try {
      await requestJson<GigApplication>(
        `/dashboard/api/gigs/${encodeURIComponent(gigId)}/applications`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ crm_profile: normalizedProfile }),
        },
      )
      showToast("Added candidate", "ok")
      await loadGigs()
      if (selectedGigId === gigId) await loadGigDetail(gigId)
      return true
    } catch (error) {
      showError(error, "Unable to add candidate")
      return false
    } finally {
      setBusy(`gig:${gigId}:addCandidate`, false)
    }
  }

  function peopleUrl() {
    const params = new URLSearchParams({ limit: "25" })
    if (peopleQuery.trim()) params.set("query", peopleQuery.trim())
    if (peopleMember) params.set("is_member", peopleMember)
    for (const [key, value] of Object.entries(peopleFilters)) {
      if (value) params.set(key, value)
    }
    return `/dashboard/api/people?${params.toString()}`
  }

  async function loadPeople() {
    setBusy("people", true)
    try {
      const payload = await requestJson<Person[]>(peopleUrl())
      setPeople(payload)
    } catch (error) {
      showError(error, "Unable to load people")
    } finally {
      setBusy("people", false)
    }
  }

  async function loadNewsletterStatus() {
    setBusy("newsletterStatus", true)
    try {
      const payload = await requestJson<NewsletterStatus>("/dashboard/api/newsletter/status")
      setNewsletterStatus(payload)
    } catch (error) {
      showError(error, "Unable to load newsletter sync status")
    } finally {
      setBusy("newsletterStatus", false)
    }
  }

  async function loadNewsletterSuppressions() {
    setBusy("newsletterSuppressions", true)
    try {
      const payload = await requestJson<{ suppressions: NewsletterSuppression[] }>(
        "/dashboard/api/newsletter/suppressions?limit=200",
      )
      setNewsletterSuppressions(payload.suppressions || [])
    } catch (error) {
      showError(error, "Unable to load newsletter suppressions")
    } finally {
      setBusy("newsletterSuppressions", false)
    }
  }

  async function loadNewsletterDashboard() {
    await Promise.all([loadNewsletterStatus(), loadNewsletterSuppressions()])
  }

  function onboardingUrl() {
    const params = new URLSearchParams({ limit: "25" })
    if (onboardingQuery.trim()) params.set("query", onboardingQuery.trim())
    if (onboardingState) params.set("onboarding_state", onboardingState)
    if (onboarderFilter.trim()) params.set("onboarder", onboarderFilter.trim())
    for (const [key, value] of Object.entries(onboardingFilters)) {
      if (value) params.set(key, value)
    }
    return `/dashboard/api/onboarding?${params.toString()}`
  }

  async function loadOnboarding() {
    setBusy("onboarding", true)
    try {
      const payload = await requestJson<Person[]>(onboardingUrl())
      setOnboarding(payload)
    } catch (error) {
      showError(error, "Unable to load onboarding")
    } finally {
      setBusy("onboarding", false)
    }
  }

  async function draftOnboardingEmail(
    contactId: string | undefined,
    options: OnboardingEmailOptions,
  ) {
    if (!contactId) {
      showToast("Missing CRM contact", "error")
      return null
    }
    const key = `onboarding-email-draft:${contactId}`
    setBusy(key, true)
    try {
      const payload = await requestJson<OnboardingEmailDraft>(
        `/dashboard/api/onboarding/${encodeURIComponent(contactId)}/email/draft`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(options),
        },
      )
      showToast("Drafted onboarding email", "ok")
      return payload
    } catch (error) {
      showError(error, "Unable to draft onboarding email")
      return null
    } finally {
      setBusy(key, false)
    }
  }

  async function sendOnboardingEmail(
    contactId: string | undefined,
    options: OnboardingEmailOptions,
    markdownBody: string,
  ) {
    if (!contactId) {
      showToast("Missing CRM contact", "error")
      return null
    }
    const key = `onboarding-email-send:${contactId}`
    setBusy(key, true)
    try {
      const payload = await requestJson<OnboardingEmailDraft>(
        `/dashboard/api/onboarding/${encodeURIComponent(contactId)}/email/send`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...options, markdown_body: markdownBody }),
        },
      )
      setOnboarding((current) =>
        current.map((person) =>
          person.crm_contact_id === contactId
            ? {
                ...person,
                onboarding_email_sent_at:
                  payload.onboarding_email_sent_at || person.onboarding_email_sent_at,
                onboarding_email_sent_by:
                  payload.onboarding_email_sent_by || person.onboarding_email_sent_by,
                onboarding_email_recipient:
                  payload.onboarding_email_recipient ||
                  payload.recipient_email ||
                  person.onboarding_email_recipient,
              }
            : person,
        ),
      )
      showToast("Sent onboarding email", "ok")
      return payload
    } catch (error) {
      showError(error, "Unable to send onboarding email")
      return null
    } finally {
      setBusy(key, false)
    }
  }

  async function loadAuditEvents() {
    setBusy("audit", true)
    try {
      setAuditEvents(await requestJson<AuditEvent[]>("/dashboard/api/audit-events?limit=25"))
    } catch (error) {
      showError(error, "Unable to load audit events")
    } finally {
      setBusy("audit", false)
    }
  }

  async function loadAgentReport() {
    setBusy("agent", true)
    try {
      setAgentReport(await requestJson<AgentReport>("/dashboard/api/agent?limit=100"))
    } catch (error) {
      showError(error, "Unable to load agent report")
    } finally {
      setBusy("agent", false)
    }
  }

  async function loadConfiguration() {
    setBusy("configuration", true)
    try {
      const payload = await requestJson<ConfigurationResponse>("/dashboard/api/configuration")
      setConfigurationItems(payload.items)
    } catch (error) {
      showError(error, "Unable to load configuration")
    } finally {
      setBusy("configuration", false)
    }
  }

  async function updateConfigurationValue(key: string, value: string) {
    setBusy(`configuration:${key}`, true)
    try {
      const payload = await requestJson<ConfigurationResponse>(
        `/dashboard/api/configuration/${encodeURIComponent(key)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        },
      )
      setConfigurationItems(payload.items)
      showToast(`Saved ${key}`, "ok")
      return true
    } catch (error) {
      showError(error, `Unable to save ${key}`)
      return false
    } finally {
      setBusy(`configuration:${key}`, false)
    }
  }

  async function clearConfigurationValue(key: string) {
    setBusy(`configuration:${key}`, true)
    try {
      const payload = await requestJson<ConfigurationResponse>(
        `/dashboard/api/configuration/${encodeURIComponent(key)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clear: true }),
        },
      )
      setConfigurationItems(payload.items)
      showToast(`Cleared ${key}`, "ok")
    } catch (error) {
      showError(error, `Unable to clear ${key}`)
    } finally {
      setBusy(`configuration:${key}`, false)
    }
  }

  async function loadJobDetail(jobId: string) {
    setBusy(`detail:${jobId}`, true)
    showToast(`Loading ${jobId}`)
    try {
      const detail = await requestJson<JobDetail>(
        `/dashboard/api/jobs/${encodeURIComponent(jobId)}`,
      )
      setJobDetail(detail)
      showToast(`Loaded ${jobId}`, "ok")
    } catch (error) {
      showError(error, "Unable to load task detail")
    } finally {
      setBusy(`detail:${jobId}`, false)
    }
  }

  async function rerunJob(jobId: string) {
    setBusy(`rerun:${jobId}`, true)
    showToast(`Rerunning ${jobId}`)
    try {
      const payload = await requestJson<{
        job_id?: string
        dry_run?: boolean
        would_enqueue?: { job_type?: string }
      }>(`/dashboard/api/jobs/${encodeURIComponent(jobId)}/rerun`, { method: "POST" })
      if (payload.dry_run) {
        showToast(
          `Dry run only: would rerun ${payload.would_enqueue?.job_type || jobId}`,
          "warning",
        )
      } else {
        showToast(`Queued rerun ${payload.job_id}`, "ok")
        await loadJobs()
      }
    } catch (error) {
      showError(error, "Unable to rerun task")
    } finally {
      setBusy(`rerun:${jobId}`, false)
    }
  }

  async function syncPeople() {
    setBusy("syncPeople", true)
    showToast("Queueing people sync")
    try {
      const payload = await requestJson<{
        job_id?: string
        dry_run?: boolean
        would_enqueue?: { job_type?: string }
      }>("/dashboard/api/sync/people", {
        method: "POST",
      })
      if (payload.dry_run) {
        showToast(
          `Dry run only: would queue ${payload.would_enqueue?.job_type || "people sync"}`,
          "warning",
        )
      } else {
        showToast(`Queued people sync ${payload.job_id}`, "ok")
      }
    } catch (error) {
      showError(error, "Unable to queue people sync")
    } finally {
      setBusy("syncPeople", false)
    }
  }

  async function syncNewsletters() {
    setBusy("syncNewsletters", true)
    showToast("Queueing newsletter sync")
    try {
      const payload = await requestJson<{
        job_id?: string
        dry_run?: boolean
        preview?: NewsletterSyncPreview
        would_enqueue?: { job_type?: string }
      }>("/dashboard/api/sync/newsletters", {
        method: "POST",
      })
      if (payload.dry_run) {
        const summary = newsletterPreviewSummary(payload.preview)
        showToast(summary ? `Dry run only: ${summary}` : "Dry run completed", "warning")
      } else {
        showToast(`Queued newsletter sync ${payload.job_id}`, "ok")
      }
      void loadNewsletterDashboard()
    } catch (error) {
      showError(error, "Unable to queue newsletter sync")
    } finally {
      setBusy("syncNewsletters", false)
    }
  }

  async function assignOnboarder(contactId: string | undefined, onboarder: string) {
    const normalizedContactId = String(contactId || "").trim()
    const normalizedOnboarder = onboarder.trim()
    if (!normalizedContactId) {
      showToast("Missing CRM contact id", "error")
      return
    }
    if (!normalizedOnboarder) {
      showToast("Enter a 508 username", "error")
      return
    }
    setBusy(`onboarder:${normalizedContactId}`, true)
    showToast(`Assigning ${normalizedOnboarder}`)
    try {
      const payload = await requestJson<{
        contact_id: string
        onboarder: string
        onboarding_state?: string
        onboarding_status_label?: string
        state_updated?: boolean
      }>(`/dashboard/api/onboarding/${encodeURIComponent(normalizedContactId)}/onboarder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ onboarder: normalizedOnboarder }),
      })
      setOnboarding((current) =>
        current.map((person) =>
          person.crm_contact_id === payload.contact_id
            ? {
                ...person,
                onboarder: payload.onboarder,
                onboarding_state:
                  payload.state_updated && payload.onboarding_state
                    ? payload.onboarding_state
                    : person.onboarding_state,
                onboarding_status_label:
                  payload.onboarding_status_label ||
                  (payload.state_updated ? undefined : person.onboarding_status_label),
              }
            : person,
        ),
      )
      showToast(`Assigned ${payload.onboarder}`, "ok")
    } catch (error) {
      showError(error, "Unable to assign onboarder")
    } finally {
      setBusy(`onboarder:${normalizedContactId}`, false)
    }
  }

  async function updateOnboardingStatus(contactId: string | undefined, status: string) {
    const normalizedContactId = String(contactId || "").trim()
    const normalizedStatus = status.trim()
    if (!normalizedContactId) {
      showToast("Missing CRM contact id", "error")
      return
    }
    if (!normalizedStatus) {
      showToast("Choose an onboarding status", "error")
      return
    }
    setBusy(`onboarding-status:${normalizedContactId}`, true)
    showToast("Updating onboarding status")
    try {
      const payload = await requestJson<{
        contact_id: string
        onboarding_state: string
        onboarding_status_label?: string
      }>(`/dashboard/api/onboarding/${encodeURIComponent(normalizedContactId)}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: normalizedStatus }),
      })
      const nextState = normalizedOnboardingStatusValue(payload.onboarding_state)
      const nextLabel = payload.onboarding_status_label || labelForOnboardingState(nextState)
      setOnboarding((current) =>
        current
          .map((person) =>
            person.crm_contact_id === payload.contact_id
              ? {
                  ...person,
                  onboarding_state: nextState,
                  onboarding_status_label: nextLabel,
                }
              : person,
          )
          .filter(
            (person) =>
              person.crm_contact_id !== payload.contact_id ||
              !terminalOnboardingStatuses.has(nextState),
          ),
      )
      showToast(`Status set to ${nextLabel}`, "ok")
    } catch (error) {
      showError(error, "Unable to update onboarding status")
    } finally {
      setBusy(`onboarding-status:${normalizedContactId}`, false)
    }
  }

  async function setupEngineer(payload: EngineerSetupRequest) {
    const normalizedEmail = payload.email.trim().toLowerCase()
    const normalizedFirstName = payload.first_name.trim()
    if (!normalizedEmail?.endsWith("@508.dev")) {
      showToast("Enter the engineer's @508.dev email", "error")
      return null
    }
    if (!normalizedFirstName) {
      showToast("Enter the engineer name", "error")
      return null
    }
    setBusy("engineerSetup", true)
    try {
      const result = await requestJson<EngineerSetupResult>("/dashboard/api/onboarding/engineers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...payload,
          email: normalizedEmail,
          first_name: normalizedFirstName,
        }),
      })
      showToast(`Set up ${result.employee_name || result.user || normalizedEmail}`, "ok")
      return result
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        const payload =
          error.payload && typeof error.payload === "object"
            ? (error.payload as { matches?: Array<{ label?: string; email?: string }> })
            : null
        const matches = Array.isArray(payload?.matches) ? payload.matches : []
        const matchLabel = matches
          .map((match) => match?.label || match?.email)
          .filter(Boolean)
          .slice(0, 2)
          .join(", ")
        showToast(
          matchLabel
            ? `Similar account exists: ${matchLabel}`
            : "Similar account exists; confirm before creating",
          "error",
        )
      } else {
        showToast(error instanceof Error ? error.message : "Unable to set up engineer", "error")
      }
      return null
    } finally {
      setBusy("engineerSetup", false)
    }
  }

  async function logout() {
    setBusy("logout", true)
    try {
      const payload = await requestJson<{ end_session_url?: string }>("/auth/logout", {
        method: "POST",
      })
      window.location.assign(payload.end_session_url || "/dashboard")
    } catch (error) {
      showError(error, "Unable to log out")
      setBusy("logout", false)
    }
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: dashboard bootstrap should run once; popstate uses a ref for live navigation state.
  useEffect(() => {
    loadUser()
      .then((nextPermissions) => {
        const currentView = viewFromPath()
        const allowed = nextPermissions.includes(routePermissions[currentView])
        const nextView = allowed
          ? currentView
          : (Object.keys(routes) as View[]).find((candidate) =>
              nextPermissions.includes(routePermissions[candidate]),
            ) || "people"
        setSelectedGigId(nextView === "gigs" ? detailIdFromPath() : "")
        setSelectedProjectId(nextView === "projects" ? detailIdFromPath("projects") : "")
        setViewState(nextView)
        if (!Object.hasOwn(routes, rawViewFromPath()) || nextView !== currentView) {
          window.history.replaceState({ view: nextView }, "", routes[nextView])
        }
      })
      .catch((error: unknown) => {
        showError(error, "Dashboard failed to load")
      })
  }, [])

  useEffect(() => {
    const onPopState = () => {
      setSelectedGigId(detailIdFromPath())
      setSelectedProjectId(detailIdFromPath("projects"))
      navigateRef.current(viewFromPath(), false)
    }
    window.addEventListener("popstate", onPopState)
    return () => window.removeEventListener("popstate", onPopState)
  }, [])

  useEffect(() => {
    const onHashChange = () => setActiveGigTab(gigTabFromHash())
    window.addEventListener("hashchange", onHashChange)
    return () => window.removeEventListener("hashchange", onHashChange)
  }, [])

  useEffect(() => {
    if (!toast.message) return undefined
    const timeout = window.setTimeout(() => setToast({ message: "" }), 4500)
    return () => window.clearTimeout(timeout)
  }, [toast.message])

  // biome-ignore lint/correctness/useExhaustiveDependencies: dev diagnostics should subscribe once to global browser errors.
  useEffect(() => {
    if (!import.meta.env.DEV) return undefined
    const onError = (event: ErrorEvent) => {
      showError(event.error || event.message, "Unhandled dashboard error")
    }
    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      showError(event.reason, "Unhandled promise rejection")
    }
    window.addEventListener("error", onError)
    window.addEventListener("unhandledrejection", onUnhandledRejection)
    return () => {
      window.removeEventListener("error", onError)
      window.removeEventListener("unhandledrejection", onUnhandledRejection)
    }
  }, [])

  // biome-ignore lint/correctness/useExhaustiveDependencies: each loader reads the latest filter state for the active view.
  useEffect(() => {
    if (permissions.length === 0) return
    if (can("gigs:read")) void loadNotifications()
    if (view === "people") void loadPeople()
    if (view === "gigs") {
      void loadGigs()
      void loadGigLeads()
      void loadGigLeadScrapeStatus()
      void loadJobPostChannels()
    }
    if (view === "projects") void loadProjects()
    if (view === "onboarding") void loadOnboarding()
    if (view === "newsletter") void loadNewsletterDashboard()
    if (view === "jobs") void loadJobs()
    if (view === "agent") void loadAgentReport()
    if (view === "audit") void loadAuditEvents()
    if (view === "configuration") {
      void loadConfiguration()
      void loadJobPostChannels({ includeAvailable: true })
    }
  }, [view])

  // biome-ignore lint/correctness/useExhaustiveDependencies: permission loading is the first authorized data fetch for the current view.
  useEffect(() => {
    if (permissions.length === 0) return
    if (can("gigs:read")) void loadNotifications()
    if (view === "people") void loadPeople()
    if (view === "gigs") {
      void loadGigs()
      void loadGigLeads()
      void loadGigLeadScrapeStatus()
      void loadJobPostChannels()
    }
    if (view === "projects") void loadProjects()
    if (view === "onboarding") void loadOnboarding()
    if (view === "newsletter") void loadNewsletterDashboard()
    if (view === "jobs") void loadJobs()
    if (view === "agent") void loadAgentReport()
    if (view === "audit") void loadAuditEvents()
    if (view === "configuration") {
      void loadConfiguration()
      void loadJobPostChannels({ includeAvailable: true })
    }
  }, [permissions])

  // biome-ignore lint/correctness/useExhaustiveDependencies: jobs reload intentionally follows filter changes only while jobs is active.
  useEffect(() => {
    if (view === "jobs" && permissions.length > 0) void loadJobs()
  }, [minutes, status])

  // biome-ignore lint/correctness/useExhaustiveDependencies: gigs reload intentionally follows list filter changes only while gigs is active.
  useEffect(() => {
    if (view === "gigs" && permissions.length > 0) void loadGigs()
  }, [gigStatus, gigIncludeHistorical, gigLimit])

  // biome-ignore lint/correctness/useExhaustiveDependencies: lead reload intentionally follows lead status changes only while gigs is active.
  useEffect(() => {
    if (view === "gigs" && permissions.length > 0) void loadGigLeads()
  }, [gigLeadStatus])

  // biome-ignore lint/correctness/useExhaustiveDependencies: projects reload intentionally follows status changes only while projects is active.
  useEffect(() => {
    if (view === "projects" && permissions.length > 0) void loadProjects()
  }, [projectStatus])

  // biome-ignore lint/correctness/useExhaustiveDependencies: detail reload intentionally follows the route-selected gig id.
  useEffect(() => {
    if (view === "gigs" && selectedGigId && permissions.length > 0) {
      void loadGigDetail(selectedGigId)
    }
  }, [view, selectedGigId, permissions])

  // biome-ignore lint/correctness/useExhaustiveDependencies: people reload intentionally follows membership filter changes only while people is active.
  useEffect(() => {
    if (view === "people" && permissions.length > 0) void loadPeople()
  }, [peopleMember])

  // biome-ignore lint/correctness/useExhaustiveDependencies: people reload intentionally follows chip filter changes only while people is active.
  useEffect(() => {
    if (view === "people" && permissions.length > 0) void loadPeople()
  }, [peopleFilters])

  // biome-ignore lint/correctness/useExhaustiveDependencies: onboarding reload intentionally follows state filter changes only while onboarding is active.
  useEffect(() => {
    if (view === "onboarding" && permissions.length > 0) void loadOnboarding()
  }, [onboardingState])

  // biome-ignore lint/correctness/useExhaustiveDependencies: onboarding reload intentionally follows chip filter changes only while onboarding is active.
  useEffect(() => {
    if (view === "onboarding" && permissions.length > 0) void loadOnboarding()
  }, [onboardingFilters])

  const sortedJobs = useMemo(() => sortItems("jobs", jobs, sort.jobs), [jobs, sort.jobs])
  const sortedPeople = useMemo(
    () => sortItems("people", people, sort.people),
    [people, sort.people],
  )
  const sortedOnboarding = useMemo(
    () => sortItems("onboarding", onboarding, sort.onboarding),
    [onboarding, sort.onboarding],
  )
  const sortedGigs = useMemo(() => sortItems("gigs", gigs, sort.gigs), [gigs, sort.gigs])
  const sortedProjects = useMemo(
    () => sortItems("projects", projects, sort.projects),
    [projects, sort.projects],
  )
  const newsletterProviderOptions = useMemo(() => {
    const providerNames = new Set<string>()
    for (const record of newsletterSuppressions) {
      if (record.source_provider) providerNames.add(record.source_provider)
    }
    for (const [providerName] of newsletterProviderResults(newsletterStatus?.latest_job)) {
      providerNames.add(providerName)
    }
    return [...providerNames].sort((left, right) => left.localeCompare(right))
  }, [newsletterSuppressions, newsletterStatus])
  const sortedNewsletterSuppressions = useMemo(
    () =>
      sortItems(
        "newsletter",
        newsletterProviderFilter
          ? newsletterSuppressions.filter(
              (record) => record.source_provider === newsletterProviderFilter,
            )
          : newsletterSuppressions,
        sort.newsletter,
      ),
    [newsletterProviderFilter, newsletterSuppressions, sort.newsletter],
  )
  const selectedGig = useMemo(() => {
    if (gigDetail?.id === selectedGigId) return gigDetail
    return sortedGigs.find((gig) => gig.id === selectedGigId) || null
  }, [gigDetail, selectedGigId, sortedGigs])
  const selectedProject = useMemo(
    () => sortedProjects.find((project) => project.id === selectedProjectId) || null,
    [selectedProjectId, sortedProjects],
  )
  const sortedAudit = useMemo(
    () => sortItems("audit", auditEvents, sort.audit),
    [auditEvents, sort.audit],
  )
  const jobCounts = useMemo(
    () =>
      jobs.reduce<Record<string, number>>((acc, job) => {
        acc[job.status] = (acc[job.status] || 0) + 1
        return acc
      }, {}),
    [jobs],
  )

  const peopleFilterKeys = (Object.keys(peopleFilterDefinitions) as PeopleFilterKey[]).filter(
    (key) => !peopleFilters[key],
  )
  const onboardingFilterKeys = (Object.keys(peopleFilterDefinitions) as PeopleFilterKey[]).filter(
    (key) => key !== "sync_status" && key !== "email_508" && !onboardingFilters[key],
  )

  function openNotification(notification: DashboardNotification) {
    if (notification.type === "stale_recruiting_gig" || notification.type === "contacted_gig") {
      const isContacted = notification.type === "contacted_gig"
      const notificationPrefix = isContacted ? "contacted-gig:" : "stale-recruiting:"
      const gigId =
        notification.engagement_id ||
        (notification.id.startsWith(notificationPrefix)
          ? notification.id.slice(notificationPrefix.length)
          : "")
      if (gigId) {
        openGigDetail(gigId)
      } else {
        setGigStatus(isContacted ? "contacted" : "recruiting")
        navigate("gigs", true)
      }
    }
    setNotificationsOpen(false)
  }

  useEffect(() => {
    if (!peopleFilterKeys.includes(peopleFilterKind) && peopleFilterKeys[0]) {
      setPeopleFilterKind(peopleFilterKeys[0])
    }
  }, [peopleFilterKeys, peopleFilterKind])

  useEffect(() => {
    const options = peopleFilterDefinitions[peopleFilterKind]?.options
    if (options?.[0] && !options.some(([value]) => value === peopleFilterValue)) {
      setPeopleFilterValue(options[0][0])
    }
  }, [peopleFilterKind, peopleFilterValue])

  useEffect(() => {
    if (!onboardingFilterKeys.includes(onboardingFilterKind) && onboardingFilterKeys[0]) {
      setOnboardingFilterKind(onboardingFilterKeys[0])
    }
  }, [onboardingFilterKeys, onboardingFilterKind])

  useEffect(() => {
    const options = peopleFilterDefinitions[onboardingFilterKind]?.options
    if (options?.[0] && !options.some(([value]) => value === onboardingFilterValue)) {
      setOnboardingFilterValue(options[0][0])
    }
  }, [onboardingFilterKind, onboardingFilterValue])

  const userMeta = [
    user?.email,
    user?.crm_contact_id ? `CRM ${user.crm_contact_id}` : "",
    user?.actor_provider,
  ]
    .filter(Boolean)
    .join(" | ")

  return (
    <>
      <header className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-xl font-bold">508 Operations Dashboard</h1>
            <p className="text-sm text-muted-foreground">
              Operations view for authenticated 508 operators.
            </p>
          </div>
          <div className="flex min-w-0 items-center gap-3">
            {can("gigs:read") ? (
              <div className="relative">
                <Button
                  id="notifications"
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label="Notifications"
                  aria-expanded={notificationsOpen}
                  onClick={() => setNotificationsOpen((current) => !current)}
                >
                  <Bell />
                  {notifications.length > 0 ? (
                    <span className="absolute -right-1 -top-1 grid min-h-5 min-w-5 place-items-center rounded-full bg-red-500 px-1 text-[11px] font-bold text-white">
                      {notifications.length}
                    </span>
                  ) : null}
                </Button>
              </div>
            ) : null}
            <div className="grid min-w-0 gap-0.5 text-right text-sm text-muted-foreground">
              <strong id="userName" className="truncate text-foreground">
                {user?.display_name || user?.email || user?.subject || "Loading user"}
              </strong>
              <span id="userMeta" className="truncate">
                {userMeta || "Checking session"}
              </span>
            </div>
            <Button
              id="logout"
              type="button"
              variant="outline"
              onClick={logout}
              disabled={loading.logout}
            >
              <LogOut />
              Log out
            </Button>
          </div>
        </div>
      </header>

      <NotificationDrawer
        open={notificationsOpen}
        notifications={notifications}
        loading={loading.notifications}
        onClose={() => setNotificationsOpen(false)}
        onRefresh={loadNotifications}
        onOpenNotification={openNotification}
      />
      <DashboardToast toast={toast} />
      {import.meta.env.DEV ? (
        <DashboardDevErrors errors={devErrors} onClear={() => setDevErrors([])} />
      ) : null}
      <HistoricalPersonChoiceModal
        choice={historicalPersonChoice}
        loading={Boolean(
          historicalPersonChoice &&
            loading[`project:${historicalPersonChoice.projectId}:historical`],
        )}
        crmContactUrl={crmContactUrl}
        onClose={() => setHistoricalPersonChoice(null)}
        onChoose={(candidateId) => {
          if (!historicalPersonChoice) return
          void addHistoricalProjectMember(
            historicalPersonChoice.projectId,
            historicalPersonChoice.person,
            candidateId,
          )
        }}
      />

      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-5 px-5 py-5 md:grid-cols-[190px_minmax(0,1fr)]">
        <nav
          className="grid content-start gap-1 md:sticky md:top-24"
          aria-label="Dashboard sections"
        >
          {(
            [
              ["people", "People", Users],
              ["gigs", "Gigs", BriefcaseBusiness],
              ["projects", "Projects", FolderKanban],
              ["onboarding", "Onboarding", ClipboardList],
              ["newsletter", "Newsletter", Mail],
              ["jobs", "Background tasks", Activity],
              ["agent", "Agent", ShieldCheck],
              ["audit", "Audit", FileClock],
              ["configuration", "Configuration", Settings],
            ] as const
          )
            .filter(([key]) => canView(key))
            .map(([key, label, Icon]) => {
              return (
                <a
                  key={key}
                  className={cn(
                    "flex min-h-10 items-center gap-2 rounded-md border border-transparent px-3 text-sm font-extrabold text-muted-foreground hover:border-border hover:bg-secondary hover:text-foreground",
                    view === key && "border-primary bg-accent text-accent-foreground",
                  )}
                  data-view-link={key}
                  data-permission={routePermissions[key]}
                  href={routes[key]}
                  aria-current={view === key ? "page" : undefined}
                  onClick={(event) => {
                    event.preventDefault()
                    navigate(key, true)
                  }}
                >
                  <Icon className="size-4" />
                  {label}
                </a>
              )
            })}
        </nav>

        <div className="grid min-w-0 gap-5">
          {view === "people" ? (
            <PeopleView
              crmBaseUrl={crmBaseUrl}
              people={sortedPeople}
              sort={sort.people}
              canSync={canUse("people:sync")}
              loading={loading}
              peopleQuery={peopleQuery}
              peopleMember={peopleMember}
              peopleFilters={peopleFilters}
              peopleFilterKind={peopleFilterKind}
              peopleFilterValue={peopleFilterValue}
              peopleFilterKeys={peopleFilterKeys}
              onSearch={loadPeople}
              onSync={syncPeople}
              onSort={(key) => handleSort("people", key)}
              setPeopleQuery={setPeopleQuery}
              setPeopleMember={setPeopleMember}
              setPeopleFilterKind={setPeopleFilterKind}
              setPeopleFilterValue={setPeopleFilterValue}
              addFilter={() => {
                setPeopleFilters((current) => ({
                  ...current,
                  [peopleFilterKind]: peopleFilterValue,
                }))
              }}
              removeFilter={(key) => {
                setPeopleFilters((current) => {
                  const next = { ...current }
                  delete next[key]
                  return next
                })
              }}
              crmContactUrl={crmContactUrl}
              crmAttachmentUrl={crmAttachmentUrl}
            />
          ) : null}

          {view === "newsletter" ? (
            <NewsletterView
              status={newsletterStatus}
              suppressions={sortedNewsletterSuppressions}
              providerOptions={newsletterProviderOptions}
              providerFilter={newsletterProviderFilter}
              sort={sort.newsletter}
              loading={loading}
              canSync={canUse("people:sync")}
              onRefresh={loadNewsletterDashboard}
              onSync={syncNewsletters}
              onProviderFilterChange={setNewsletterProviderFilter}
              onSort={(key) => handleSort("newsletter", key)}
            />
          ) : null}

          {view === "gigs" ? (
            <GigsView
              gigs={sortedGigs}
              leads={gigLeads}
              scrapeStatus={gigLeadScrapeStatus}
              jobPostChannels={jobPostChannels}
              selectedGig={selectedGig}
              selectedGigId={selectedGigId}
              sort={sort.gigs}
              loading={loading}
              activeTab={activeGigTab}
              status={gigStatus}
              query={gigQuery}
              leadStatus={gigLeadStatus}
              includeHistorical={gigIncludeHistorical}
              limit={gigLimit}
              staleDays={staleRecruitingDays}
              contactedReminderDays={contactedReminderDays}
              canWrite={can("gigs:write")}
              canSearchCandidates={can("people:read")}
              canManageLeads={can("people:read")}
              canIncludeHistorical={can("people:read")}
              crmContactUrl={crmContactUrl}
              crmAttachmentUrl={crmAttachmentUrl}
              setActiveTab={selectGigTab}
              setStatus={setGigStatus}
              setQuery={setGigQuery}
              setLeadStatus={setGigLeadStatus}
              setIncludeHistorical={setGigIncludeHistorical}
              setLimit={setGigLimit}
              onRefresh={refreshGigsView}
              onSort={(key) => handleSort("gigs", key)}
              onOpenGig={openGigDetail}
              onCloseGig={closeGigDetail}
              onUpdateStatus={updateGigStatus}
              onAddApplication={addGigApplication}
              onUpdateApplicationStatus={updateGigApplicationStatus}
              onSyncLeads={syncGigLeads}
              onReviewLead={reviewGigLead}
              onPostLead={postGigLead}
            />
          ) : null}

          {view === "projects" ? (
            <ProjectsView
              projects={sortedProjects}
              selectedProject={selectedProject}
              selectedProjectId={selectedProjectId}
              summary={projectsSummary}
              wikiMatches={wikiMatches}
              sort={sort.projects}
              loading={loading}
              query={projectQuery}
              status={projectStatus}
              canSync={canUse("projects:sync")}
              canWrite={can("projects:write")}
              crmContactUrl={crmContactUrl}
              setQuery={setProjectQuery}
              setStatus={setProjectStatus}
              onSearch={loadProjects}
              onSync={syncProjects}
              onSearchCustomers={searchERPNextCustomers}
              onSearchContacts={searchERPNextContacts}
              onSearchAccountManagers={searchERPNextAccountManagers}
              onLoadCostCenters={loadERPNextCostCenters}
              onCreateProject={createProjectSetup}
              onUpdateStatus={updateProjectStatus}
              onBulkUpdate={bulkUpdateProjects}
              onAddUser={addProjectUser}
              onRemoveUser={removeProjectUser}
              onAddHistoricalMember={addHistoricalProjectMember}
              onRemoveHistoricalMember={removeHistoricalProjectMember}
              onUpdateWikiMatch={updateProjectWikiMatch}
              onWikiMatches={loadWikiMatches}
              onOpenProject={openProjectDetail}
              onCloseProject={closeProjectDetail}
              onSort={(key) => handleSort("projects", key)}
            />
          ) : null}

          {view === "onboarding" ? (
            <OnboardingView
              people={sortedOnboarding}
              sort={sort.onboarding}
              loading={loading}
              onboardingQuery={onboardingQuery}
              onboardingState={onboardingState}
              onboarderFilter={onboarderFilter}
              onboardingFilters={onboardingFilters}
              onboardingFilterKind={onboardingFilterKind}
              onboardingFilterValue={onboardingFilterValue}
              onboardingFilterKeys={onboardingFilterKeys}
              onSearch={loadOnboarding}
              onSort={(key) => handleSort("onboarding", key)}
              onAssign={assignOnboarder}
              onStatusChange={updateOnboardingStatus}
              onDraftEmail={draftOnboardingEmail}
              onSendEmail={sendOnboardingEmail}
              onSetupEngineer={setupEngineer}
              setOnboardingQuery={setOnboardingQuery}
              setOnboardingState={setOnboardingState}
              setOnboarderFilter={setOnboarderFilter}
              setOnboardingFilterKind={setOnboardingFilterKind}
              setOnboardingFilterValue={setOnboardingFilterValue}
              addFilter={() => {
                setOnboardingFilters((current) => ({
                  ...current,
                  [onboardingFilterKind]: onboardingFilterValue,
                }))
              }}
              removeFilter={(key) => {
                setOnboardingFilters((current) => {
                  const next = { ...current }
                  delete next[key]
                  return next
                })
              }}
              crmContactUrl={crmContactUrl}
              crmAttachmentUrl={crmAttachmentUrl}
              canWrite={can("onboarding:write")}
              canConfigure={can("configuration:write")}
              onOpenConfiguration={() => {
                setConfigurationFocus({ category: "Onboarding", nonce: Date.now() })
                navigate("configuration", true)
              }}
            />
          ) : null}

          {view === "jobs" ? (
            <JobsView
              jobs={sortedJobs}
              jobDetail={jobDetail}
              sort={sort.jobs}
              loading={loading}
              minutes={minutes}
              status={status}
              jobType={jobType}
              jobCounts={jobCounts}
              canWrite={canUse("jobs:write")}
              setMinutes={setMinutes}
              setStatus={setStatus}
              setJobType={setJobType}
              onSearch={loadJobs}
              onSort={(key) => handleSort("jobs", key)}
              onDetail={loadJobDetail}
              onRerun={rerunJob}
            />
          ) : null}

          {view === "audit" ? (
            <AuditView
              events={sortedAudit}
              sort={sort.audit}
              loading={loading}
              onRefresh={loadAuditEvents}
              onSort={(key) => handleSort("audit", key)}
            />
          ) : null}

          {view === "agent" ? (
            <AgentView report={agentReport} loading={loading} onRefresh={loadAgentReport} />
          ) : null}

          {view === "configuration" ? (
            <ConfigurationView
              items={configurationItems}
              loading={loading}
              canWrite={can("configuration:write")}
              jobChannels={jobPostChannels}
              availableJobChannels={availableJobPostChannels}
              focusCategory={configurationFocus?.category}
              focusNonce={configurationFocus?.nonce}
              onRefresh={loadConfiguration}
              onRefreshJobChannels={() => loadJobPostChannels({ includeAvailable: true })}
              onSave={updateConfigurationValue}
              onClear={clearConfigurationValue}
              onSaveJobChannel={updateJobPostChannel}
              onDeleteJobChannel={deleteJobPostChannel}
            />
          ) : null}
        </div>
      </main>
    </>
  )
}

function NotificationDrawer({
  open,
  notifications,
  loading,
  onClose,
  onRefresh,
  onOpenNotification,
}: {
  open: boolean
  notifications: DashboardNotification[]
  loading?: boolean
  onClose: () => void
  onRefresh: () => void
  onOpenNotification: (notification: DashboardNotification) => void
}) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-40"
      aria-labelledby="notificationsTitle"
      aria-modal="true"
      role="dialog"
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default bg-black/45"
        aria-label="Close notifications"
        onClick={onClose}
      />
      <aside className="absolute right-0 top-0 grid h-full w-full max-w-md grid-rows-[auto_minmax(0,1fr)] border-l bg-background shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b p-4">
          <div className="grid gap-0.5">
            <strong id="notificationsTitle" className="text-base">
              Notifications
            </strong>
            <span className="text-sm text-muted-foreground">
              {notifications.length === 0
                ? "No active notifications"
                : `${notifications.length} active`}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onRefresh}
              disabled={loading}
            >
              <RefreshCw />
              Refresh
            </Button>
            <Button type="button" variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
              <X />
            </Button>
          </div>
        </div>
        <div className="min-h-0 overflow-auto p-4">
          {notifications.length === 0 ? (
            <div className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">
              No active notifications.
            </div>
          ) : (
            <div className="grid gap-3">
              {notifications.map((notification) => (
                <button
                  key={notification.id}
                  type="button"
                  className="grid gap-2 rounded-md border p-3 text-left hover:bg-secondary"
                  onClick={() => onOpenNotification(notification)}
                >
                  <span className="text-sm font-bold">{notification.title}</span>
                  <span className="text-sm text-muted-foreground">{notification.message}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}

function HistoricalPersonChoiceModal({
  choice,
  loading,
  crmContactUrl,
  onClose,
  onChoose,
}: {
  choice: {
    projectId: string
    person: string
    candidates: HistoricalPersonCandidate[]
  } | null
  loading: boolean
  crmContactUrl: (contactId?: string) => string
  onClose: () => void
  onChoose: (candidateId: string) => void
}) {
  if (!choice) return null
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      aria-labelledby="historicalPersonChoiceTitle"
      aria-modal="true"
      role="dialog"
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default bg-black/45"
        aria-label="Close person selection"
        onClick={onClose}
      />
      <div className="relative grid w-full max-w-2xl gap-4 rounded-md border bg-background p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <strong id="historicalPersonChoiceTitle" className="block text-base">
              Choose person record
            </strong>
            <span className="text-sm text-muted-foreground">{choice.person}</span>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close person selection"
            onClick={onClose}
          >
            <X />
          </Button>
        </div>
        <div className="grid gap-2">
          {choice.candidates.map((candidate) => (
            <div
              key={candidate.candidate_id}
              className="grid gap-3 rounded-md border p-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
            >
              <div className="min-w-0">
                <strong className="block truncate">
                  {candidate.label || candidate.full_name || candidate.email || "Person"}
                </strong>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
                  {candidate.email ? <span>{candidate.email}</span> : null}
                  {candidate.sources?.length ? <span>{candidate.sources.join(", ")}</span> : null}
                  {candidate.erpnext_user_id ? <span>ERP {candidate.erpnext_user_id}</span> : null}
                  {candidate.supplier_erpnext_id ? (
                    <span>Supplier {candidate.supplier_erpnext_id}</span>
                  ) : null}
                  {candidate.crm_contact_id && crmContactUrl(candidate.crm_contact_id) ? (
                    <a
                      className="font-semibold text-primary underline-offset-4 hover:underline"
                      href={crmContactUrl(candidate.crm_contact_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      CRM
                    </a>
                  ) : null}
                </div>
              </div>
              <Button
                type="button"
                disabled={loading}
                onClick={() => onChoose(candidate.candidate_id)}
              >
                Select
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function DashboardToast({
  toast,
}: {
  toast: { message: string; tone?: "ok" | "warning" | "error" }
}) {
  if (!toast.message) return null
  return (
    <div
      id="toast"
      role="status"
      className={cn(
        "fixed bottom-5 right-5 z-50 max-w-sm rounded-md border bg-background px-4 py-3 text-sm font-semibold shadow-lg",
        toast.tone === "ok" && "border-emerald-500/40 text-emerald-300",
        toast.tone === "warning" && "border-amber-500/40 text-amber-200",
        toast.tone === "error" && "border-red-500/40 text-red-300",
      )}
    >
      {toast.message}
    </div>
  )
}

function DashboardDevErrors({
  errors,
  onClear,
}: {
  errors: DashboardDevError[]
  onClear: () => void
}) {
  if (errors.length === 0) return null
  const [latest, ...previous] = errors
  const status = latest.status === 0 ? "Network" : latest.status
  const endpoint = [latest.method, latest.url].filter(Boolean).join(" ")
  const statusLabel = [status, latest.statusText].filter(Boolean).join(" ")
  const payload =
    latest.payload === null || latest.payload === undefined
      ? ""
      : JSON.stringify(latest.payload, null, 2)
  const summaryFields = [
    ["Endpoint", endpoint],
    ["Status", statusLabel || latest.name],
    ["Route", latest.path],
    ["View", latest.view],
    ["Detail", latest.detail],
    ["Error", latest.error],
  ].filter(([, value]) => Boolean(value))

  return (
    <aside
      aria-label="Development request errors"
      className="fixed bottom-5 left-5 z-50 grid max-h-[78vh] w-[min(48rem,calc(100vw-2.5rem))] gap-3 overflow-auto rounded-md border border-red-500/40 bg-background p-4 text-sm shadow-2xl"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-red-300">Request failed</strong>
            <Badge variant="failed">{statusLabel || latest.name || "Error"}</Badge>
            <span className="text-muted-foreground">{latest.occurredAt}</span>
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {latest.name || "Dashboard error"}
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Clear errors"
          onClick={onClear}
        >
          <X />
        </Button>
      </div>
      <div className="rounded-md border border-red-500/25 bg-red-500/5 p-3 text-red-100">
        {latest.message}
      </div>
      {summaryFields.length > 0 ? (
        <dl className="grid gap-2 rounded-md border bg-black/20 p-3 md:grid-cols-[7rem_minmax(0,1fr)]">
          {summaryFields.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-xs font-bold uppercase text-muted-foreground">{label}</dt>
              <dd className="min-w-0 break-words font-mono text-xs text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {payload ? (
        <details open>
          <summary className="cursor-pointer font-semibold text-muted-foreground">Payload</summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-black/30 p-3 text-xs text-muted-foreground">
            {payload}
          </pre>
        </details>
      ) : null}
      {latest.stack ? (
        <details>
          <summary className="cursor-pointer font-semibold text-muted-foreground">Stack</summary>
          <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-black/30 p-3 text-xs text-muted-foreground">
            {latest.stack}
          </pre>
        </details>
      ) : null}
      {previous.length > 0 ? (
        <details>
          <summary className="cursor-pointer font-semibold text-muted-foreground">
            Previous failures ({previous.length})
          </summary>
          <div className="mt-2 grid gap-2">
            {previous.map((error) => (
              <div key={error.id} className="rounded-md border p-2">
                <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <span>{error.occurredAt}</span>
                  <span>{error.status === 0 ? "Network" : error.status || error.name}</span>
                  <code>{[error.method, error.url].filter(Boolean).join(" ")}</code>
                </div>
                <div className="mt-1">{error.message}</div>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </aside>
  )
}

function FilterChips({
  filters,
  onRemove,
  suffix = "filter",
}: {
  filters: FilterState
  onRemove: (key: PeopleFilterKey) => void
  suffix?: string
}) {
  return (
    <fieldset className="m-0 flex min-h-7 flex-wrap gap-2 border-0 p-0" aria-label="Active filters">
      {(Object.entries(filters) as Array<[PeopleFilterKey, string]>).map(([key, value]) => {
        const definition = peopleFilterDefinitions[key]
        const option = definition.options.find(([candidate]) => candidate === value)
        const label = `${definition.label}: ${option ? option[1] : value}`
        return (
          <Button
            key={key}
            type="button"
            variant="outline"
            size="sm"
            className="rounded-full"
            aria-label={`Remove ${label} ${suffix}`}
            onClick={() => onRemove(key)}
          >
            {label} x
          </Button>
        )
      })}
    </fieldset>
  )
}

const gigStatuses = [
  "lead",
  "recruiting",
  "contacted",
  "filled",
  "unknown",
  "lost",
  "duplicate",
  "outdated",
] as const
const applicationStatuses = [
  "suggested",
  "interested",
  "reviewing",
  "contacted",
  "accepted",
  "unavailable",
  "rejected",
  "withdrawn",
] as const

function titleCase(value?: string) {
  return String(value || "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

function gigActivityTimestamp(gig: Gig) {
  const activityTimes = [
    gig.last_activity_at,
    gig.last_status_changed_at,
    gig.posted_at,
    gig.created_at,
  ]
    .map((value) => (value ? new Date(value).getTime() : Number.NaN))
    .filter((value) => !Number.isNaN(value))
  return activityTimes.length > 0 ? new Date(Math.max(...activityTimes)).toISOString() : ""
}

function staleGigAge(gig: Gig, recruitingDays: number, contactedDays: number) {
  const isContacted = gig.status === "contacted"
  if (gig.status !== "recruiting" && !isContacted) return null
  const age = daysSince(
    isContacted
      ? gig.last_status_changed_at || gig.updated_at || gig.created_at
      : gigActivityTimestamp(gig),
  )
  const reminderDays = isContacted ? contactedDays : recruitingDays
  if (age === null || age < reminderDays) return null
  return age
}

function projectStatusTone(status?: string): Tone {
  const normalized = String(status || "").toLowerCase()
  if (normalized === "open") return "queued"
  if (["completed", "closed"].includes(normalized)) return "succeeded"
  if (["cancelled", "canceled"].includes(normalized)) return "failed"
  return "neutral"
}

function formatProjectDate(value?: string | null) {
  const text = String(value || "").trim()
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (!match) return formatDate(value)
  const [, year, month, day] = match
  return new Date(Number(year), Number(month) - 1, Number(day)).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

function memberLabel(member: ProjectRosterMember) {
  return member.full_name || member.email || member.source_user_id || "Unknown"
}

function optionalNumber(value: string) {
  const normalized = value.trim()
  if (!normalized) return undefined
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : undefined
}

function ProjectsView(props: {
  projects: Project[]
  selectedProject: Project | null
  selectedProjectId: string
  summary: ProjectsResponse["summary"]
  wikiMatches: WikiMatchPreview | null
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  query: string
  status: string
  canSync: boolean
  canWrite: boolean
  crmContactUrl: (contactId?: string) => string
  setQuery: (value: string) => void
  setStatus: (value: string) => void
  onSearch: () => void
  onSync: () => void
  onSearchCustomers: (query: string) => Promise<ERPNextCustomer[]>
  onSearchContacts: (query: string) => Promise<ERPNextContact[]>
  onSearchAccountManagers: (query: string) => Promise<ERPNextUser[]>
  onLoadCostCenters: () => Promise<ERPNextCostCenter[]>
  onCreateProject: (values: {
    project_name: string
    customer_mode: "new" | "existing"
    customer_name?: string
    customer?: string
    account_manager?: string
    default_billing_currency?: string
    default_cost_center?: string
    activity_type?: string
    customer_details?: string
    customer_website?: string
    address_line1?: string
    address_line2?: string
    address_city?: string
    address_state?: string
    address_country?: string
    address_postal_code?: string
    contact?: string
    contact_first_name?: string
    contact_last_name?: string
    contact_email?: string
    contact_phone?: string
    contact_mobile?: string
  }) => Promise<boolean>
  onUpdateStatus: (projectId: string, status: string) => void
  onBulkUpdate: (
    projectIds: string[],
    updates: { status?: string; project_type?: string },
  ) => Promise<boolean>
  onAddUser: (
    projectId: string,
    user: string,
    candidateId: string,
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) => Promise<boolean>
  onRemoveUser: (projectId: string, user: string) => Promise<boolean>
  onAddHistoricalMember: (
    projectId: string,
    person: string,
    candidateId?: string,
  ) => Promise<boolean>
  onRemoveHistoricalMember: (projectId: string, sourceUserId: string) => Promise<boolean>
  onUpdateWikiMatch: (projectId: string, status: string, rowKey?: string) => Promise<void>
  onWikiMatches: () => void
  onOpenProject: (projectId: string) => void
  onCloseProject: () => void
  onSort: (key: string) => void
}) {
  const matchRows = props.wikiMatches?.matches || []
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([])
  const [bulkStatus, setBulkStatus] = useState("")
  const [bulkProjectType, setBulkProjectType] = useState("")
  const [bulkModalOpen, setBulkModalOpen] = useState(false)
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const visibleProjectIds = useMemo(
    () => props.projects.map((project) => project.id),
    [props.projects],
  )
  const visibleProjectIdSet = useMemo(() => new Set(visibleProjectIds), [visibleProjectIds])
  const selectedVisibleProjectIds = selectedProjectIds.filter((projectId) =>
    visibleProjectIdSet.has(projectId),
  )
  const allVisibleSelected =
    props.projects.length > 0 && selectedVisibleProjectIds.length === props.projects.length

  useEffect(() => {
    setSelectedProjectIds((current) =>
      current.filter((projectId) => visibleProjectIdSet.has(projectId)),
    )
  }, [visibleProjectIdSet])

  function toggleProjectSelection(projectId: string, selected: boolean) {
    setSelectedProjectIds((current) =>
      selected
        ? Array.from(new Set([...current, projectId]))
        : current.filter((candidate) => candidate !== projectId),
    )
  }

  async function applyBulkUpdate() {
    const updates: { status?: string; project_type?: string } = {}
    if (bulkStatus) updates.status = bulkStatus
    if (bulkProjectType) updates.project_type = bulkProjectType
    const success = await props.onBulkUpdate(selectedVisibleProjectIds, updates)
    if (success) {
      setSelectedProjectIds([])
      setBulkStatus("")
      setBulkProjectType("")
      setBulkModalOpen(false)
    }
  }

  const filterBar = (
    <Card className="grid gap-3 p-4 md:grid-cols-[minmax(0,1fr)_180px_auto_auto_auto] md:items-end">
      <Label>
        Search projects
        <Input
          id="projectQuery"
          value={props.query}
          autoComplete="off"
          placeholder="Project, customer, ERP id"
          onChange={(event) => props.setQuery(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && props.onSearch()}
        />
      </Label>
      <Label>
        Status
        <Select
          id="projectStatus"
          value={props.status}
          onChange={(event) => props.setStatus(event.target.value)}
        >
          <option value="Open">Open</option>
          <option value="">Any status</option>
        </Select>
      </Label>
      <Button
        id="refreshProjects"
        type="button"
        onClick={props.onSearch}
        disabled={props.loading.projects}
      >
        <RefreshCw />
        Refresh
      </Button>
      {props.canSync ? (
        <Button
          id="syncProjects"
          type="button"
          variant="outline"
          onClick={props.onSync}
          disabled={props.loading.syncProjects}
        >
          <RefreshCw />
          Sync ERP
        </Button>
      ) : null}
      <Button
        id="wikiProjectMatches"
        type="button"
        variant="outline"
        onClick={props.onWikiMatches}
        disabled={props.loading.wikiMatches}
      >
        <Search />
        Wiki match
      </Button>
    </Card>
  )

  if (props.selectedProjectId && !props.selectedProject && props.loading.projects) {
    return (
      <>
        {filterBar}
        <Card>
          <CardHeader>
            <CardTitle>Project detail</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">Loading project.</CardContent>
        </Card>
      </>
    )
  }

  if (props.selectedProjectId && !props.selectedProject) {
    return (
      <>
        {filterBar}
        <Card>
          <CardHeader>
            <CardTitle>Project detail</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              This project is not in the current result set. Clear filters or refresh the project
              list.
            </p>
            <Button type="button" variant="outline" onClick={props.onCloseProject}>
              <ArrowLeft />
              Back to projects
            </Button>
          </CardContent>
        </Card>
      </>
    )
  }

  if (props.selectedProject) {
    return (
      <>
        {filterBar}
        <ProjectDetailPage
          project={props.selectedProject}
          loading={props.loading}
          canWrite={props.canWrite}
          crmContactUrl={props.crmContactUrl}
          onBack={props.onCloseProject}
          onUpdateStatus={props.onUpdateStatus}
          onAddUser={props.onAddUser}
          onRemoveUser={props.onRemoveUser}
          onAddHistoricalMember={props.onAddHistoricalMember}
          onRemoveHistoricalMember={props.onRemoveHistoricalMember}
        />
      </>
    )
  }

  return (
    <>
      {filterBar}

      <section className="grid gap-3 md:grid-cols-2" aria-label="Project summary">
        <Metric id="projectMetricOpen" label="Open" value={props.summary.open_project_count || 0} />
        <Metric id="projectMetricTotal" label="Projects" value={props.summary.project_count || 0} />
      </section>

      {props.canWrite ? (
        <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <span className="text-xs font-bold text-muted-foreground">Selected</span>
            <strong className="block">{selectedVisibleProjectIds.length} project(s)</strong>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" onClick={() => setCreateModalOpen(true)}>
              <Plus />
              New project
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={selectedVisibleProjectIds.length === 0}
              onClick={() => setBulkModalOpen(true)}
            >
              Bulk edit
            </Button>
          </div>
        </Card>
      ) : null}

      {createModalOpen ? (
        <CreateProjectModal
          loading={props.loading.createProject}
          onClose={() => setCreateModalOpen(false)}
          onSearchCustomers={props.onSearchCustomers}
          onSearchContacts={props.onSearchContacts}
          onSearchAccountManagers={props.onSearchAccountManagers}
          onLoadCostCenters={props.onLoadCostCenters}
          onCreateProject={props.onCreateProject}
        />
      ) : null}

      {bulkModalOpen ? (
        <div
          className="fixed inset-0 z-50 grid place-items-center p-4"
          aria-labelledby="bulkProjectEditTitle"
          aria-modal="true"
          role="dialog"
        >
          <button
            type="button"
            className="absolute inset-0 cursor-default bg-black/45"
            aria-label="Close bulk project edit"
            onClick={() => setBulkModalOpen(false)}
          />
          <div className="relative grid w-full max-w-lg gap-4 rounded-md border bg-background p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <strong id="bulkProjectEditTitle" className="block text-base">
                  Bulk edit projects
                </strong>
                <span className="text-sm text-muted-foreground">
                  {selectedVisibleProjectIds.length} selected
                </span>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Close bulk project edit"
                onClick={() => setBulkModalOpen(false)}
              >
                <X />
              </Button>
            </div>
            <div className="grid gap-3">
              <strong className="text-sm">Changes</strong>
              <Label>
                Status
                <Select value={bulkStatus} onChange={(event) => setBulkStatus(event.target.value)}>
                  <option value="">No change</option>
                  <option value="Open">Open</option>
                  <option value="Completed">Completed</option>
                  <option value="Cancelled">Cancelled</option>
                </Select>
              </Label>
              <Label>
                ERP Type
                <Select
                  value={bulkProjectType}
                  onChange={(event) => setBulkProjectType(event.target.value)}
                >
                  <option value="">No change</option>
                  <option value="Internal">Internal</option>
                  <option value="External">External</option>
                </Select>
              </Label>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setBulkModalOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                disabled={
                  props.loading.projectsBulkUpdate ||
                  selectedVisibleProjectIds.length === 0 ||
                  (!bulkStatus && !bulkProjectType)
                }
                onClick={() => void applyBulkUpdate()}
              >
                Apply changes
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>ERP projects</CardTitle>
          <span id="projectsStatus" className="text-sm text-muted-foreground">
            {props.loading.projects
              ? "Loading"
              : `${props.projects.length} shown | synced ${formatDate(
                  props.summary.last_synced_at,
                )}`}
          </span>
        </CardHeader>
        <Empty hidden={props.projects.length !== 0}>
          No projects match this view. Sync ERP projects if the cache is empty.
        </Empty>
        <div className="overflow-x-auto">
          <Table
            id="projectsTable"
            className={cn("min-w-[1100px]", props.projects.length === 0 && "hidden")}
            aria-label="ERP projects"
          >
            <TableHeader>
              <TableRow>
                {props.canWrite ? (
                  <TableHead className="w-[48px]">
                    <input
                      type="checkbox"
                      aria-label="Select all visible projects"
                      checked={allVisibleSelected}
                      onChange={(event) => {
                        setSelectedProjectIds(event.target.checked ? visibleProjectIds : [])
                      }}
                    />
                  </TableHead>
                ) : null}
                <SortableTableHead
                  className="w-[24%]"
                  label="Project"
                  scope="projects"
                  sort={props.sort}
                  sortKey="display_name"
                  onSort={(_scope, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[16%]"
                  label="Customer"
                  scope="projects"
                  sort={props.sort}
                  sortKey="customer"
                  onSort={(_scope, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[10%]"
                  label="Status"
                  scope="projects"
                  sort={props.sort}
                  sortKey="status"
                  onSort={(_scope, key) => props.onSort(key)}
                />
                <TableHead className="w-[16%]">Timeline</TableHead>
                <SortableTableHead
                  className="w-[10%]"
                  label="Roster"
                  scope="projects"
                  sort={props.sort}
                  sortKey="roster_count"
                  onSort={(_scope, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[14%]"
                  label="Modified"
                  scope="projects"
                  sort={props.sort}
                  sortKey="modified"
                  onSort={(_scope, key) => props.onSort(key)}
                />
                <TableHead>ERP</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody id="projectsBody">
              {props.projects.map((project) => {
                const members = project.roster_members || []
                return (
                  <TableRow key={project.id}>
                    {props.canWrite ? (
                      <TableCell>
                        <input
                          type="checkbox"
                          aria-label={`Select ${project.display_name}`}
                          checked={selectedVisibleProjectIds.includes(project.id)}
                          onChange={(event) =>
                            toggleProjectSelection(project.id, event.target.checked)
                          }
                        />
                      </TableCell>
                    ) : null}
                    <TableCell>
                      <button
                        type="button"
                        className="text-left font-bold text-primary underline-offset-4 hover:underline"
                        onClick={() => props.onOpenProject(project.id)}
                      >
                        {project.display_name}
                      </button>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        {project.project_type ? (
                          <Badge variant="neutral">{project.project_type}</Badge>
                        ) : null}
                        {project.linked_engagement_count ? (
                          <span className="text-sm text-muted-foreground">
                            {project.linked_engagement_count} linked gig
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      {project.customer_erpnext_url ? (
                        <a
                          className="inline-flex items-center gap-1 font-semibold text-primary underline-offset-4 hover:underline"
                          href={project.customer_erpnext_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {project.customer}
                          <ExternalLink className="size-3.5" />
                        </a>
                      ) : (
                        project.customer || "None"
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant={projectStatusTone(project.source_status)}>
                        {project.source_status || "Unknown"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {[project.actual_start_date, project.actual_end_date]
                        .filter(Boolean)
                        .map((value) => formatProjectDate(value))
                        .join(" to ") || "Not set"}
                    </TableCell>
                    <TableCell>
                      <div className="grid gap-1">
                        <strong>{members.length}</strong>
                        <span className="text-sm text-muted-foreground">
                          {members.map(memberLabel).slice(0, 4).join(", ") || "No ERP roster"}
                          {members.length > 4 ? ` +${members.length - 4}` : ""}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>{formatDate(project.source_modified_at)}</TableCell>
                    <TableCell className="text-xs">
                      {project.erpnext_project_url ? (
                        <a
                          className="inline-flex items-center gap-1 font-mono font-semibold text-primary underline-offset-4 hover:underline"
                          href={project.erpnext_project_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {project.erpnext_project_id}
                          <ExternalLink className="size-3.5" />
                        </a>
                      ) : (
                        <span className="font-mono">Unlinked</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      </Card>

      {props.wikiMatches ? (
        <Card>
          <CardHeader>
            <CardTitle>Wiki match preview</CardTitle>
            <span className="text-sm text-muted-foreground">
              {props.wikiMatches.document?.title || "Client & Project Info"} |{" "}
              {formatDate(props.wikiMatches.document?.updatedAt)}
            </span>
          </CardHeader>
          <div className="overflow-x-auto">
            <Table id="wikiMatchesTable" className="min-w-[920px]" aria-label="Wiki matches">
              <TableHeader>
                <TableRow>
                  <TableHead>ERP project</TableHead>
                  <TableHead>Best wiki row</TableHead>
                  <TableHead>Confidence</TableHead>
                  <TableHead>Section</TableHead>
                  <TableHead>Decision</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {matchRows.map((match, index) => {
                  const project = match.project
                  const row = match.best_match?.row || {}
                  const manualStatus = match.manual_match?.match_status || ""
                  const rowKey =
                    project?.id ||
                    row.row_key ||
                    [row.section, row.Client].filter(Boolean).join(":") ||
                    `wiki-match-${index}`
                  return (
                    <TableRow key={rowKey}>
                      <TableCell>{project?.display_name || "Unknown"}</TableCell>
                      <TableCell>
                        <strong>{row.Client || "No match"}</strong>
                        <div className="text-sm text-muted-foreground">
                          {[row.DRI, row.Members].filter(Boolean).join(" | ")}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            match.best_match?.confidence === "high"
                              ? "succeeded"
                              : match.best_match?.confidence === "medium"
                                ? "running"
                                : "neutral"
                          }
                        >
                          {match.best_match
                            ? `${match.best_match.confidence} ${match.best_match.score}`
                            : "none"}
                        </Badge>
                      </TableCell>
                      <TableCell>{row.section || ""}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap items-center gap-2">
                          {manualStatus ? (
                            <Badge variant={manualStatus === "confirmed" ? "succeeded" : "neutral"}>
                              {manualStatus === "no_row" ? "No wiki row" : "Confirmed"}
                            </Badge>
                          ) : null}
                          {props.canWrite && project?.id ? (
                            <>
                              {row.row_key ? (
                                <Button
                                  type="button"
                                  variant="outline"
                                  size="sm"
                                  disabled={props.loading[`project:${project.id}:wiki`]}
                                  onClick={() =>
                                    void props.onUpdateWikiMatch(
                                      project.id,
                                      "confirmed",
                                      row.row_key,
                                    )
                                  }
                                >
                                  Confirm
                                </Button>
                              ) : null}
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                disabled={props.loading[`project:${project.id}:wiki`]}
                                onClick={() => void props.onUpdateWikiMatch(project.id, "no_row")}
                              >
                                No row
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        </Card>
      ) : null}
    </>
  )
}

function CreateProjectModal(props: {
  loading?: boolean
  onClose: () => void
  onSearchCustomers: (query: string) => Promise<ERPNextCustomer[]>
  onSearchContacts: (query: string) => Promise<ERPNextContact[]>
  onSearchAccountManagers: (query: string) => Promise<ERPNextUser[]>
  onLoadCostCenters: () => Promise<ERPNextCostCenter[]>
  onCreateProject: (values: {
    project_name: string
    customer_mode: "new" | "existing"
    customer_name?: string
    customer?: string
    account_manager?: string
    default_billing_currency?: string
    default_cost_center?: string
    activity_type?: string
    customer_details?: string
    customer_website?: string
    address_line1?: string
    address_line2?: string
    address_city?: string
    address_state?: string
    address_country?: string
    address_postal_code?: string
    contact?: string
    contact_first_name?: string
    contact_last_name?: string
    contact_email?: string
    contact_phone?: string
    contact_mobile?: string
  }) => Promise<boolean>
}) {
  const [projectName, setProjectName] = useState("")
  const [customerMode, setCustomerMode] = useState<"new" | "existing">("new")
  const [customerName, setCustomerName] = useState("")
  const [customerQuery, setCustomerQuery] = useState("")
  const [selectedCustomer, setSelectedCustomer] = useState("")
  const [customerResults, setCustomerResults] = useState<ERPNextCustomer[]>([])
  const [accountManagerQuery, setAccountManagerQuery] = useState("")
  const [accountManager, setAccountManager] = useState("")
  const [accountManagerResults, setAccountManagerResults] = useState<ERPNextUser[]>([])
  const [currency, setCurrency] = useState("USD")
  const [customerDetails, setCustomerDetails] = useState("")
  const [customerWebsite, setCustomerWebsite] = useState("")
  const [addressLine1, setAddressLine1] = useState("")
  const [addressLine2, setAddressLine2] = useState("")
  const [addressCity, setAddressCity] = useState("")
  const [addressState, setAddressState] = useState("")
  const [addressCountry, setAddressCountry] = useState("United States")
  const [addressPostalCode, setAddressPostalCode] = useState("")
  const [contactMode, setContactMode] = useState<"new" | "existing">("new")
  const [contactQuery, setContactQuery] = useState("")
  const [selectedContact, setSelectedContact] = useState("")
  const [contactResults, setContactResults] = useState<ERPNextContact[]>([])
  const [contactFirstName, setContactFirstName] = useState("")
  const [contactLastName, setContactLastName] = useState("")
  const [contactEmail, setContactEmail] = useState("")
  const [contactPhone, setContactPhone] = useState("")
  const [contactMobile, setContactMobile] = useState("")
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [costCenters, setCostCenters] = useState<ERPNextCostCenter[]>([
    { name: "Projects - 5", cost_center_name: "Projects" },
  ])
  const [costCenter, setCostCenter] = useState("Projects - 5")
  const [activityType, setActivityType] = useState("")
  const [activityTypeEdited, setActivityTypeEdited] = useState(false)
  const onSearchCustomersRef = useRef(props.onSearchCustomers)
  const onSearchContactsRef = useRef(props.onSearchContacts)
  const onSearchAccountManagersRef = useRef(props.onSearchAccountManagers)
  const onLoadCostCentersRef = useRef(props.onLoadCostCenters)
  const costCenterRequestRef = useRef(0)
  const customerSearchRequestRef = useRef(0)
  const accountManagerSearchRequestRef = useRef(0)
  const contactSearchRequestRef = useRef(0)
  const defaultActivityType = projectName.trim()
    ? `Engineering for ${projectName.trim()}`.slice(0, 140)
    : ""
  const addressStarted = [
    addressLine1,
    addressLine2,
    addressCity,
    addressState,
    addressPostalCode,
  ].some((value) => value.trim())
  const contactStarted = [
    contactFirstName,
    contactLastName,
    contactEmail,
    contactPhone,
    contactMobile,
  ].some((value) => value.trim())
  const canSubmit =
    projectName.trim() &&
    (customerMode === "new" ? customerName.trim() : selectedCustomer.trim()) &&
    !props.loading

  useEffect(() => {
    onSearchCustomersRef.current = props.onSearchCustomers
  }, [props.onSearchCustomers])

  useEffect(() => {
    onSearchContactsRef.current = props.onSearchContacts
  }, [props.onSearchContacts])

  useEffect(() => {
    onSearchAccountManagersRef.current = props.onSearchAccountManagers
  }, [props.onSearchAccountManagers])

  useEffect(() => {
    onLoadCostCentersRef.current = props.onLoadCostCenters
  }, [props.onLoadCostCenters])

  useEffect(() => {
    let active = true
    const requestId = costCenterRequestRef.current + 1
    costCenterRequestRef.current = requestId
    void onLoadCostCentersRef.current().then((options) => {
      if (!active || costCenterRequestRef.current !== requestId) return
      setCostCenters(options)
      setCostCenter((current) =>
        options.some((option) => option.name === current) ? current : "Projects - 5",
      )
    })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (customerMode !== "existing") {
      customerSearchRequestRef.current += 1
      setCustomerResults([])
      return
    }
    let active = true
    const requestId = customerSearchRequestRef.current + 1
    customerSearchRequestRef.current = requestId
    const handle = window.setTimeout(() => {
      void onSearchCustomersRef.current(customerQuery).then((results) => {
        if (!active || customerSearchRequestRef.current !== requestId) return
        setCustomerResults(results)
      })
    }, 250)
    return () => {
      active = false
      window.clearTimeout(handle)
    }
  }, [customerMode, customerQuery])

  useEffect(() => {
    if (customerMode !== "new") {
      accountManagerSearchRequestRef.current += 1
      setAccountManagerResults([])
      return
    }
    let active = true
    const requestId = accountManagerSearchRequestRef.current + 1
    accountManagerSearchRequestRef.current = requestId
    const handle = window.setTimeout(() => {
      void onSearchAccountManagersRef.current(accountManagerQuery).then((results) => {
        if (!active || accountManagerSearchRequestRef.current !== requestId) return
        setAccountManagerResults(results)
      })
    }, 250)
    return () => {
      active = false
      window.clearTimeout(handle)
    }
  }, [customerMode, accountManagerQuery])

  useEffect(() => {
    if (customerMode !== "new" || contactMode !== "existing") {
      contactSearchRequestRef.current += 1
      setContactResults([])
      return
    }
    let active = true
    const requestId = contactSearchRequestRef.current + 1
    contactSearchRequestRef.current = requestId
    const handle = window.setTimeout(() => {
      void onSearchContactsRef.current(contactQuery).then((results) => {
        if (!active || contactSearchRequestRef.current !== requestId) return
        setContactResults(results)
      })
    }, 250)
    return () => {
      active = false
      window.clearTimeout(handle)
    }
  }, [customerMode, contactMode, contactQuery])

  async function submit() {
    if (!canSubmit) return
    const success = await props.onCreateProject({
      project_name: projectName.trim(),
      customer_mode: customerMode,
      customer_name: customerMode === "new" ? customerName.trim() : undefined,
      customer: customerMode === "existing" ? selectedCustomer.trim() : undefined,
      account_manager: customerMode === "new" ? accountManager.trim() || undefined : undefined,
      default_billing_currency: customerMode === "new" ? currency.trim() || "USD" : undefined,
      default_cost_center: costCenter.trim() || "Projects - 5",
      activity_type: activityTypeEdited ? activityType.trim() || undefined : undefined,
      customer_details: customerMode === "new" ? customerDetails.trim() || undefined : undefined,
      customer_website: customerMode === "new" ? customerWebsite.trim() || undefined : undefined,
      address_line1: customerMode === "new" ? addressLine1.trim() || undefined : undefined,
      address_line2: customerMode === "new" ? addressLine2.trim() || undefined : undefined,
      address_city: customerMode === "new" ? addressCity.trim() || undefined : undefined,
      address_state: customerMode === "new" ? addressState.trim() || undefined : undefined,
      address_country:
        customerMode === "new" && addressLine1.trim()
          ? addressCountry.trim() || "United States"
          : undefined,
      address_postal_code:
        customerMode === "new" ? addressPostalCode.trim() || undefined : undefined,
      contact:
        customerMode === "new" && contactMode === "existing"
          ? selectedContact.trim() || undefined
          : undefined,
      contact_first_name:
        customerMode === "new" && contactMode === "new"
          ? contactFirstName.trim() || undefined
          : undefined,
      contact_last_name:
        customerMode === "new" && contactMode === "new"
          ? contactLastName.trim() || undefined
          : undefined,
      contact_email:
        customerMode === "new" && contactMode === "new"
          ? contactEmail.trim() || undefined
          : undefined,
      contact_phone:
        customerMode === "new" && contactMode === "new"
          ? contactPhone.trim() || undefined
          : undefined,
      contact_mobile:
        customerMode === "new" && contactMode === "new"
          ? contactMobile.trim() || undefined
          : undefined,
    })
    if (success) props.onClose()
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      aria-labelledby="createProjectTitle"
      aria-modal="true"
      role="dialog"
    >
      <button
        type="button"
        className="absolute inset-0 cursor-default bg-black/45"
        aria-label="Close project creation"
        onClick={props.onClose}
      />
      <div className="relative grid max-h-[90vh] w-full max-w-2xl gap-4 overflow-y-auto rounded-md border bg-background p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <strong id="createProjectTitle" className="block text-base">
              New ERP project
            </strong>
            <span className="text-sm text-muted-foreground">
              Creates a project and links a new or existing customer.
            </span>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close project creation"
            onClick={props.onClose}
          >
            <X />
          </Button>
        </div>

        <div className="grid gap-3">
          <Label>
            Project name *
            <Input
              value={projectName}
              autoComplete="off"
              maxLength={140}
              placeholder="Acme Portal"
              onChange={(event) => setProjectName(event.target.value)}
            />
          </Label>

          <div className="grid gap-2">
            <span className="text-xs font-bold text-muted-foreground">Customer</span>
            <div className="grid grid-cols-2 gap-2">
              {(["new", "existing"] as const).map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  variant={customerMode === mode ? "default" : "outline"}
                  onClick={() => setCustomerMode(mode)}
                >
                  {mode === "new" ? "New customer" : "Existing customer"}
                </Button>
              ))}
            </div>
          </div>

          {customerMode === "new" ? (
            <div className="grid gap-3 md:grid-cols-2">
              <Label className="md:col-span-2">
                Customer name *
                <Input
                  value={customerName}
                  autoComplete="off"
                  maxLength={140}
                  placeholder="Acme"
                  onChange={(event) => setCustomerName(event.target.value)}
                />
              </Label>
              <Label>
                Account manager
                <Input
                  value={accountManagerQuery}
                  autoComplete="off"
                  placeholder="Search @508.dev user"
                  onChange={(event) => {
                    setAccountManagerQuery(event.target.value)
                    setAccountManager("")
                  }}
                />
              </Label>
              {accountManagerQuery.trim().length >= 2 ? (
                <div className="grid max-h-40 gap-2 overflow-y-auto rounded-md border p-2 md:col-span-2">
                  {accountManagerResults.length ? (
                    accountManagerResults.map((user) => {
                      const email = user.email || user.name || ""
                      return (
                        <label
                          key={email}
                          className="flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5 hover:bg-secondary"
                        >
                          <input
                            type="radio"
                            name="erpAccountManager"
                            value={email}
                            checked={accountManager === email}
                            onChange={() => {
                              setAccountManager(email)
                              setAccountManagerQuery(email)
                            }}
                          />
                          <span className="grid gap-0.5 text-sm">
                            <strong>{user.full_name || email}</strong>
                            <span className="text-muted-foreground">{email}</span>
                          </span>
                        </label>
                      )
                    })
                  ) : (
                    <span className="px-2 py-3 text-sm text-muted-foreground">
                      No enabled @508.dev users found.
                    </span>
                  )}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="grid gap-3">
              <Label>
                Find customer *
                <Input
                  value={customerQuery}
                  autoComplete="off"
                  placeholder="Search customer"
                  onChange={(event) => setCustomerQuery(event.target.value)}
                />
              </Label>
              <div className="grid max-h-48 gap-2 overflow-y-auto rounded-md border p-2">
                {customerResults.length ? (
                  customerResults.map((customer) => {
                    const customerId = customer.name || customer.customer_name || ""
                    return (
                      <label
                        key={customerId}
                        className="flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5 hover:bg-secondary"
                      >
                        <input
                          type="radio"
                          name="erpCustomer"
                          value={customerId}
                          checked={selectedCustomer === customerId}
                          onChange={() => setSelectedCustomer(customerId)}
                        />
                        <span className="grid gap-0.5 text-sm">
                          <strong>{customer.customer_name || customerId}</strong>
                          <span className="text-muted-foreground">
                            {[customerId, customer.default_currency].filter(Boolean).join(" | ")}
                          </span>
                        </span>
                      </label>
                    )
                  })
                ) : (
                  <span className="px-2 py-3 text-sm text-muted-foreground">
                    Search at least two characters.
                  </span>
                )}
              </div>
            </div>
          )}

          {customerMode === "new" ? (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                <Label className="md:col-span-2">
                  Customer details
                  <textarea
                    value={customerDetails}
                    className="min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                    maxLength={2000}
                    placeholder="More information"
                    onChange={(event) => setCustomerDetails(event.target.value)}
                  />
                </Label>
                <Label className="md:col-span-2">
                  Website
                  <Input
                    value={customerWebsite}
                    autoComplete="url"
                    placeholder="https://example.com"
                    onChange={(event) => setCustomerWebsite(event.target.value)}
                  />
                </Label>
              </div>

              <div className="grid gap-3">
                <div className="flex items-center justify-between gap-3">
                  <strong className="text-sm text-foreground">Contact</strong>
                  <div className="grid grid-cols-2 gap-2">
                    {(["new", "existing"] as const).map((mode) => (
                      <Button
                        key={mode}
                        type="button"
                        size="sm"
                        variant={contactMode === mode ? "default" : "outline"}
                        onClick={() => setContactMode(mode)}
                      >
                        {mode === "new" ? "New" : "Existing"}
                      </Button>
                    ))}
                  </div>
                </div>

                {contactMode === "new" ? (
                  <div className="grid gap-3 md:grid-cols-2">
                    <Label>
                      First name {contactStarted ? "*" : ""}
                      <Input
                        value={contactFirstName}
                        autoComplete="given-name"
                        onChange={(event) => setContactFirstName(event.target.value)}
                      />
                    </Label>
                    <Label>
                      Last name
                      <Input
                        value={contactLastName}
                        autoComplete="family-name"
                        onChange={(event) => setContactLastName(event.target.value)}
                      />
                    </Label>
                    <Label>
                      Email
                      <Input
                        value={contactEmail}
                        type="email"
                        autoComplete="email"
                        onChange={(event) => setContactEmail(event.target.value)}
                      />
                    </Label>
                    <Label>
                      Phone
                      <Input
                        value={contactPhone}
                        type="tel"
                        autoComplete="tel"
                        onChange={(event) => setContactPhone(event.target.value)}
                      />
                    </Label>
                    <Label>
                      Mobile
                      <Input
                        value={contactMobile}
                        type="tel"
                        autoComplete="tel"
                        onChange={(event) => setContactMobile(event.target.value)}
                      />
                    </Label>
                  </div>
                ) : (
                  <div className="grid gap-3">
                    <Label>
                      Find contact
                      <Input
                        value={contactQuery}
                        autoComplete="off"
                        placeholder="Search name or email"
                        onChange={(event) => setContactQuery(event.target.value)}
                      />
                    </Label>
                    <div className="grid max-h-48 gap-2 overflow-y-auto rounded-md border p-2">
                      {contactResults.length ? (
                        contactResults.map((contact) => {
                          const contactId = contact.name || ""
                          const contactLabel = contact.full_name || contactId
                          const contactMetadata = [
                            { key: "company", value: contact.company_name },
                            { key: "email", value: contact.email_id },
                            { key: "phone", value: contact.phone },
                            { key: "mobile", value: contact.mobile_no },
                          ].filter((item): item is { key: string; value: string } =>
                            Boolean(item.value),
                          )
                          return (
                            <label
                              key={contactId}
                              className="flex cursor-pointer items-start gap-2 rounded-sm px-2 py-1.5 hover:bg-secondary"
                            >
                              <input
                                type="radio"
                                name="erpContact"
                                value={contactId}
                                checked={selectedContact === contactId}
                                onChange={() => setSelectedContact(contactId)}
                              />
                              <span className="grid gap-0.5 text-sm">
                                <strong>
                                  <HighlightedText value={contactLabel} query={contactQuery} />
                                </strong>
                                {contactMetadata.length ? (
                                  <span className="text-muted-foreground">
                                    {contactMetadata.map((item, index) => (
                                      <span key={item.key}>
                                        {index > 0 ? " | " : ""}
                                        <HighlightedText value={item.value} query={contactQuery} />
                                      </span>
                                    ))}
                                  </span>
                                ) : null}
                              </span>
                            </label>
                          )
                        })
                      ) : (
                        <span className="px-2 py-3 text-sm text-muted-foreground">
                          Search at least two characters.
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <strong className="text-sm text-foreground md:col-span-2">Address</strong>
                <Label className="md:col-span-2">
                  Address line 1 {addressStarted ? "*" : ""}
                  <Input
                    value={addressLine1}
                    autoComplete="address-line1"
                    onChange={(event) => setAddressLine1(event.target.value)}
                  />
                </Label>
                <Label className="md:col-span-2">
                  Address line 2
                  <Input
                    value={addressLine2}
                    autoComplete="address-line2"
                    onChange={(event) => setAddressLine2(event.target.value)}
                  />
                </Label>
                <Label>
                  City
                  <Input
                    value={addressCity}
                    autoComplete="address-level2"
                    onChange={(event) => setAddressCity(event.target.value)}
                  />
                </Label>
                <Label>
                  State
                  <Input
                    value={addressState}
                    autoComplete="address-level1"
                    onChange={(event) => setAddressState(event.target.value)}
                  />
                </Label>
                <Label>
                  Postal code
                  <Input
                    value={addressPostalCode}
                    autoComplete="postal-code"
                    onChange={(event) => setAddressPostalCode(event.target.value)}
                  />
                </Label>
                <Label>
                  Country
                  <Input
                    value={addressCountry}
                    autoComplete="country-name"
                    onChange={(event) => setAddressCountry(event.target.value)}
                  />
                </Label>
              </div>
            </>
          ) : null}

          <div className="grid gap-3 rounded-md border p-3">
            <Button
              type="button"
              variant="outline"
              onClick={() => setAdvancedOpen((current) => !current)}
            >
              {advancedOpen ? "Hide advanced" : "Show advanced"}
            </Button>
            {advancedOpen ? (
              <div className="grid gap-3 md:grid-cols-2">
                {customerMode === "new" ? (
                  <Label>
                    Billing currency
                    <Input
                      value={currency}
                      autoComplete="off"
                      maxLength={3}
                      onChange={(event) => setCurrency(event.target.value.toUpperCase())}
                    />
                  </Label>
                ) : null}
                <Label>
                  Cost center
                  <Select
                    value={costCenter}
                    onChange={(event) => setCostCenter(event.target.value)}
                  >
                    {costCenters.map((option) => {
                      const value = option.name || ""
                      return (
                        <option key={value} value={value}>
                          {[value, option.company].filter(Boolean).join(" | ")}
                        </option>
                      )
                    })}
                  </Select>
                </Label>
                <Label>
                  Activity type
                  <Input
                    value={activityTypeEdited ? activityType : defaultActivityType}
                    autoComplete="off"
                    maxLength={140}
                    placeholder={defaultActivityType || "Engineering for project"}
                    onChange={(event) => {
                      setActivityTypeEdited(true)
                      setActivityType(event.target.value)
                    }}
                  />
                </Label>
              </div>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" onClick={props.onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={!canSubmit} onClick={() => void submit()}>
            Create project
          </Button>
        </div>
      </div>
    </div>
  )
}

function ProjectDetailPage(props: {
  project: Project
  loading: Record<string, boolean>
  canWrite: boolean
  crmContactUrl: (contactId?: string) => string
  onBack: () => void
  onUpdateStatus: (projectId: string, status: string) => void
  onAddUser: (
    projectId: string,
    user: string,
    candidateId: string,
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) => Promise<boolean>
  onRemoveUser: (projectId: string, user: string) => Promise<boolean>
  onAddHistoricalMember: (
    projectId: string,
    person: string,
    candidateId?: string,
  ) => Promise<boolean>
  onRemoveHistoricalMember: (projectId: string, sourceUserId: string) => Promise<boolean>
}) {
  const project = props.project
  const members = project.roster_members || []
  const [newUser, setNewUser] = useState("")
  const [rosterCandidates, setRosterCandidates] = useState<HistoricalPersonCandidate[]>([])
  const [selectedRosterCandidateId, setSelectedRosterCandidateId] = useState("")
  const [activityType, setActivityType] = useState("")
  const [billingRate, setBillingRate] = useState("")
  const [costingRate, setCostingRate] = useState("")
  const timeline =
    [
      project.actual_start_date || project.expected_start_date,
      project.actual_end_date || project.expected_end_date,
    ]
      .filter(Boolean)
      .map((value) => formatProjectDate(value))
      .join(" to ") || "Not set"
  const progress =
    typeof project.percent_complete === "number"
      ? `${Math.round(project.percent_complete)}%`
      : "Not set"
  const selectedRosterCandidate = rosterCandidates.find(
    (candidate) => candidate.candidate_id === selectedRosterCandidateId,
  )
  const queryReady = newUser.trim().includes("@")
    ? newUser.trim().length >= 5
    : newUser.trim().length >= 3
  const hasRateFields = Boolean(activityType.trim() || billingRate.trim() || costingRate.trim())
  const parsedBillingRate = optionalNumber(billingRate)
  const parsedCostingRate = optionalNumber(costingRate)
  const hasPartialRate = Boolean((billingRate.trim() || costingRate.trim()) && !activityType.trim())
  const hasIncompleteActivityCost = Boolean(
    activityType.trim() && (!billingRate.trim() || !costingRate.trim()),
  )
  const hasInvalidRateNumber =
    Boolean(billingRate.trim() && parsedBillingRate === undefined) ||
    Boolean(costingRate.trim() && parsedCostingRate === undefined)
  const hasNegativeRate =
    (parsedBillingRate !== undefined && parsedBillingRate < 0) ||
    (parsedCostingRate !== undefined && parsedCostingRate < 0)
  const rateInvalid =
    hasPartialRate || hasIncompleteActivityCost || hasInvalidRateNumber || hasNegativeRate
  const ratePayload =
    hasRateFields && !rateInvalid
      ? {
          activity_type: activityType.trim(),
          billing_rate: parsedBillingRate,
          costing_rate: parsedCostingRate,
        }
      : undefined

  useEffect(() => {
    if (!props.canWrite) return
    const query = newUser.trim()
    if (
      selectedRosterCandidateId &&
      selectedRosterCandidate &&
      query === (selectedRosterCandidate.email || selectedRosterCandidate.label || "")
    ) {
      return
    }
    if (selectedRosterCandidateId) {
      setSelectedRosterCandidateId("")
    }
    const readyForLookup = query.includes("@") ? query.length >= 5 : query.length >= 3
    if (!readyForLookup) {
      setRosterCandidates([])
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void requestJson<HistoricalPersonCandidate[]>(
        `/dashboard/api/project-member-candidates?query=${encodeURIComponent(query)}`,
        { signal: controller.signal },
      )
        .then((candidates) => setRosterCandidates(candidates))
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") return
          setRosterCandidates([])
        })
    }, 500)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [newUser, props.canWrite, selectedRosterCandidate, selectedRosterCandidateId])

  function chooseRosterCandidate(candidate: HistoricalPersonCandidate) {
    setSelectedRosterCandidateId(candidate.candidate_id)
    setNewUser(candidate.email || candidate.label || candidate.full_name || newUser)
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div className="grid gap-3 md:grid-cols-[auto_minmax(0,1fr)_auto] md:items-start">
            <Button type="button" variant="outline" onClick={props.onBack}>
              <ArrowLeft />
              Projects
            </Button>
            <div className="min-w-0">
              <CardTitle>{project.display_name}</CardTitle>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Badge variant={projectStatusTone(project.source_status)}>
                  {project.source_status || "Unknown"}
                </Badge>
                {project.erpnext_project_id ? (
                  <span className="font-mono">{project.erpnext_project_id}</span>
                ) : null}
                {project.last_synced_at ? (
                  <span>Synced {formatDate(project.last_synced_at)}</span>
                ) : null}
              </div>
            </div>
            <div className="flex flex-wrap justify-start gap-2 md:justify-end">
              {props.canWrite ? (
                <Select
                  className="w-[160px]"
                  aria-label={`Status for ${project.display_name}`}
                  value={project.source_status || ""}
                  disabled={props.loading[`project:${project.id}:status`]}
                  onChange={(event) => props.onUpdateStatus(project.id, event.target.value)}
                >
                  <option value="Open">Open</option>
                  <option value="Completed">Completed</option>
                  <option value="Cancelled">Cancelled</option>
                </Select>
              ) : null}
              {project.erpnext_project_url ? (
                <a
                  className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border bg-secondary px-3 text-sm font-semibold"
                  href={project.erpnext_project_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink className="size-4" />
                  ERP project
                </a>
              ) : null}
              {project.customer_erpnext_url ? (
                <a
                  className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border bg-secondary px-3 text-sm font-semibold"
                  href={project.customer_erpnext_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink className="size-4" />
                  ERP customer
                </a>
              ) : null}
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <div>
            <span className="text-xs font-bold text-muted-foreground">Customer</span>
            <strong className="block">{project.customer || "None"}</strong>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">Timeline</span>
            <strong className="block">{timeline}</strong>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">Progress</span>
            <strong className="block">{progress}</strong>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">Linked Gigs</span>
            <strong className="block">{project.linked_engagement_count || 0}</strong>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">ERP Type</span>
            <div className="mt-1">
              {project.project_type ? (
                <Badge variant="neutral">{project.project_type}</Badge>
              ) : (
                <strong className="block">Not set</strong>
              )}
            </div>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">ERP Modified</span>
            <strong className="block">{formatDate(project.source_modified_at)}</strong>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">Cache ID</span>
            <strong className="block break-all font-mono text-xs">{project.id}</strong>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Project roster</CardTitle>
          <span className="text-sm text-muted-foreground">
            {members.length
              ? `${members.length} synced ERP user${members.length === 1 ? "" : "s"}`
              : "No ERP roster"}
          </span>
        </CardHeader>
        {props.canWrite ? (
          <CardContent className="grid gap-3 border-b md:grid-cols-[minmax(260px,1fr)_minmax(180px,.7fr)_minmax(130px,.45fr)_minmax(130px,.45fr)_auto_auto] md:items-end">
            <div className="relative">
              <Label>
                Person search
                <Input
                  value={newUser}
                  autoComplete="off"
                  placeholder="Search @508.dev person"
                  onChange={(event) => setNewUser(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault()
                      if (rosterCandidates.length === 1) {
                        chooseRosterCandidate(rosterCandidates[0])
                      }
                    }
                  }}
                />
              </Label>
              {queryReady && !selectedRosterCandidateId ? (
                <div className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border bg-background shadow-lg">
                  {rosterCandidates.length ? (
                    rosterCandidates.map((candidate) => (
                      <button
                        key={candidate.candidate_id}
                        type="button"
                        className="grid w-full gap-0.5 px-3 py-2 text-left hover:bg-secondary focus:bg-secondary focus:outline-none"
                        onClick={() => chooseRosterCandidate(candidate)}
                      >
                        <span className="truncate text-sm font-bold">
                          {candidate.label || candidate.full_name || candidate.email || "Person"}
                        </span>
                        <span className="truncate text-xs text-muted-foreground">
                          {[candidate.email, candidate.sources?.join(", ")]
                            .filter(Boolean)
                            .join(" | ")}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="px-3 py-2 text-sm text-muted-foreground">
                      No verified @508.dev results
                    </div>
                  )}
                </div>
              ) : null}
            </div>
            <Label>
              Activity Type
              <Input
                value={activityType}
                autoComplete="off"
                placeholder="Optional rate step"
                onChange={(event) => setActivityType(event.target.value)}
              />
            </Label>
            <Label>
              Billing rate
              <Input
                value={billingRate}
                inputMode="decimal"
                autoComplete="off"
                placeholder="USD/hr"
                onChange={(event) => setBillingRate(event.target.value)}
              />
            </Label>
            <Label>
              Costing rate
              <Input
                value={costingRate}
                inputMode="decimal"
                autoComplete="off"
                placeholder="USD/hr"
                onChange={(event) => setCostingRate(event.target.value)}
              />
            </Label>
            <Button
              type="button"
              variant="outline"
              disabled={
                props.loading[`project:${project.id}:user`] ||
                !selectedRosterCandidateId ||
                !selectedRosterCandidate?.email ||
                rateInvalid
              }
              onClick={() =>
                void props
                  .onAddUser(
                    project.id,
                    selectedRosterCandidate?.email || newUser,
                    selectedRosterCandidateId,
                    ratePayload,
                  )
                  .then((updated) => {
                    if (updated) {
                      setNewUser("")
                      setRosterCandidates([])
                      setSelectedRosterCandidateId("")
                      setActivityType("")
                      setBillingRate("")
                      setCostingRate("")
                    }
                  })
              }
            >
              <Users />
              Add ERP user
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={props.loading[`project:${project.id}:historical`] || !newUser.trim()}
              onClick={() =>
                void props.onAddHistoricalMember(project.id, newUser).then((updated) => {
                  if (updated) setNewUser("")
                })
              }
            >
              <Users />
              Add historical
            </Button>
          </CardContent>
        ) : null}
        <div className="overflow-x-auto">
          <Table className="min-w-[860px]" aria-label="Project roster">
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>ERP user</TableHead>
                <TableHead>Links</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Last seen</TableHead>
                {props.canWrite ? <TableHead>Actions</TableHead> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.length ? (
                members.map((member) => {
                  const label = memberLabel(member)
                  const sourceUserId = member.source_user_id || member.email || ""
                  const isHistorical =
                    member.roster_kind === "historical" || member.source === "manual"
                  return (
                    <TableRow
                      key={`${member.source || ""}:${member.source_user_id || member.email}`}
                    >
                      <TableCell>
                        <strong>{member.full_name || member.email || member.source_user_id}</strong>
                      </TableCell>
                      <TableCell>{member.email || "None"}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {member.erpnext_user_url ? (
                          <a
                            className="inline-flex items-center gap-1 font-semibold text-primary underline-offset-4 hover:underline"
                            href={member.erpnext_user_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {member.source_user_id || "ERP user"}
                            <ExternalLink className="size-3.5" />
                          </a>
                        ) : (
                          member.source_user_id || "Unknown"
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-2">
                          {member.supplier_erpnext_url ? (
                            <a
                              className="inline-flex items-center gap-1 font-semibold text-primary underline-offset-4 hover:underline"
                              href={member.supplier_erpnext_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Supplier
                              <ExternalLink className="size-3.5" />
                            </a>
                          ) : null}
                          {member.crm_contact_id && props.crmContactUrl(member.crm_contact_id) ? (
                            <a
                              className="inline-flex items-center gap-1 font-semibold text-primary underline-offset-4 hover:underline"
                              href={props.crmContactUrl(member.crm_contact_id)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              CRM
                              <ExternalLink className="size-3.5" />
                            </a>
                          ) : null}
                          {!member.supplier_erpnext_url &&
                          !(member.crm_contact_id && props.crmContactUrl(member.crm_contact_id)) ? (
                            <span className="text-muted-foreground">None</span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>{member.roster_kind || member.source || "ERP"}</TableCell>
                      <TableCell>{formatDate(member.last_seen_at)}</TableCell>
                      {props.canWrite ? (
                        <TableCell>
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            disabled={
                              !sourceUserId ||
                              props.loading[
                                `project:${project.id}:${isHistorical ? "historical" : "user"}`
                              ]
                            }
                            onClick={() => {
                              const confirmed = window.confirm(
                                `Remove ${label} from this project roster?`,
                              )
                              if (!confirmed) return
                              if (isHistorical) {
                                void props.onRemoveHistoricalMember(project.id, sourceUserId)
                              } else {
                                void props.onRemoveUser(project.id, sourceUserId)
                              }
                            }}
                          >
                            <UserMinus />
                            Remove
                          </Button>
                        </TableCell>
                      ) : null}
                    </TableRow>
                  )
                })
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={props.canWrite ? 7 : 6}
                    className="text-sm text-muted-foreground"
                  >
                    No roster rows have been synced for this project.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  )
}

function jobLeadScrapeStatusTone(status?: string | null): Tone {
  const normalized = String(status || "")
    .trim()
    .toLowerCase()
  if (normalized === "not_run") return "neutral"
  if (
    normalized === "queued" ||
    normalized === "running" ||
    normalized === "succeeded" ||
    normalized === "failed" ||
    normalized === "dead" ||
    normalized === "canceled"
  ) {
    return normalized
  }
  return "neutral"
}

function scrapeCount(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "—"
}

function JobLeadScrapeStatusPanel({
  status,
  loading,
}: {
  status: JobLeadScrapeStatus | null
  loading: boolean
}) {
  const result = status?.result
  const threads = Array.isArray(result?.threads) ? result.threads : []
  const firstThread = threads[0]
  const hasDiscoveryCounts = typeof result?.thread_found === "boolean"
  const filterReasons = result?.filter_reasons
  const filterDetails = [
    ["empty", filterReasons?.empty],
    ["seeking work", filterReasons?.seeking_work],
    ["not contractor-friendly", filterReasons?.not_contractor_friendly],
  ].filter(([, count]) => typeof count === "number" && count > 0) as Array<[string, number]>
  const normalizedStatus = String(status?.status || "")
    .trim()
    .toLowerCase()
  const statusLabel =
    normalizedStatus === "not_run" ? "Not run" : titleCase(normalizedStatus || "Unknown")

  return (
    <Card id="gigLeadScrapeStatus">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Hacker News scrape</CardTitle>
          <Badge variant={jobLeadScrapeStatusTone(status?.status)}>{statusLabel}</Badge>
        </div>
        <span className="text-sm text-muted-foreground">
          {loading
            ? "Loading latest scrape status"
            : status?.updated_at
              ? `Last updated ${formatDate(status.updated_at)}`
              : normalizedStatus === "queued"
                ? "Scrape queued; waiting for a worker."
                : normalizedStatus === "running"
                  ? "Scrape is running."
                  : "No scrape has been recorded in the last year."}
        </span>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {normalizedStatus === "not_run" ? (
          <p className="text-muted-foreground">
            Run “Scrape HN” to discover the current monthly Who is Hiring thread.
          </p>
        ) : null}
        {status?.last_error ? (
          <p className="rounded-md border border-red-400/40 bg-red-500/10 p-3 text-red-200">
            {status.last_error}
          </p>
        ) : null}
        {!hasDiscoveryCounts &&
        normalizedStatus !== "not_run" &&
        normalizedStatus !== "succeeded" &&
        !status?.last_error ? (
          <p className="text-muted-foreground">
            The scrape is {normalizedStatus || "in progress"}. Discovery and filtering counts will
            appear when it finishes.
          </p>
        ) : null}
        {hasDiscoveryCounts ? (
          <>
            {result?.thread_found && firstThread ? (
              <div className="grid gap-1">
                {firstThread.url ? (
                  <a
                    className="inline-flex w-fit items-center gap-1 font-extrabold text-primary"
                    href={firstThread.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {firstThread.title || `HN thread ${firstThread.story_id}`}
                    <ExternalLink className="size-3.5" />
                  </a>
                ) : (
                  <strong>{firstThread.title || `HN thread ${firstThread.story_id}`}</strong>
                )}
                <span className="text-muted-foreground">
                  HN #{firstThread.story_id || "unknown"}
                  {firstThread.created_at ? ` · posted ${formatDate(firstThread.created_at)}` : ""}
                  {threads.length > 1 ? ` · ${threads.length} threads scanned` : ""}
                </span>
              </div>
            ) : (
              <p className="text-muted-foreground">
                No matching monthly Who is Hiring thread was found during this run.
              </p>
            )}

            <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              <div className="rounded-md border bg-background p-3">
                <dt className="text-xs font-bold text-muted-foreground">HN comments</dt>
                <dd id="gigLeadScrapeComments" className="text-lg font-extrabold">
                  {scrapeCount(firstThread?.comments_reported)}
                </dd>
              </div>
              <div className="rounded-md border bg-background p-3">
                <dt className="text-xs font-bold text-muted-foreground">Potential gigs scanned</dt>
                <dd id="gigLeadScrapeScanned" className="text-lg font-extrabold">
                  {scrapeCount(result?.potential_gigs_scraped)}
                </dd>
              </div>
              <div className="rounded-md border bg-background p-3">
                <dt className="text-xs font-bold text-muted-foreground">Included</dt>
                <dd id="gigLeadScrapeIncluded" className="text-lg font-extrabold">
                  {scrapeCount(result?.included)}
                </dd>
              </div>
              <div className="rounded-md border bg-background p-3">
                <dt className="text-xs font-bold text-muted-foreground">Filtered out</dt>
                <dd id="gigLeadScrapeFiltered" className="text-lg font-extrabold">
                  {scrapeCount(result?.filtered_out)}
                </dd>
              </div>
              <div className="rounded-md border bg-background p-3">
                <dt className="text-xs font-bold text-muted-foreground">Records changed</dt>
                <dd id="gigLeadScrapePersisted" className="text-lg font-extrabold">
                  {scrapeCount(result?.persisted ?? result?.total)}
                </dd>
              </div>
            </dl>
            {filterDetails.length > 0 ? (
              <p className="text-muted-foreground">
                Filtered: {filterDetails.map(([label, count]) => `${count} ${label}`).join(", ")}.
              </p>
            ) : null}
            {result?.thread_found && result.potential_gigs_scraped === 0 ? (
              <p className="text-muted-foreground">
                The monthly thread was found, but it has no top-level employer posts yet. Re-run the
                scrape as posts arrive.
              </p>
            ) : null}
            {typeof result?.created === "number" || typeof result?.updated === "number" ? (
              <p className="text-muted-foreground">
                {result.created || 0} created · {result.updated || 0} updated
              </p>
            ) : null}
          </>
        ) : null}
        {normalizedStatus === "succeeded" && !hasDiscoveryCounts ? (
          <p className="text-muted-foreground">
            This older scrape finished before discovery counts were recorded. Run it again for a
            full status report.
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function GigsView(props: {
  gigs: Gig[]
  leads: JobLead[]
  scrapeStatus: JobLeadScrapeStatus | null
  jobPostChannels: JobPostChannel[]
  selectedGig: Gig | null
  selectedGigId: string
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  activeTab: "gigs" | "leads"
  status: string
  query: string
  leadStatus: string
  includeHistorical: boolean
  limit: number
  staleDays: number
  contactedReminderDays: number
  canWrite: boolean
  canSearchCandidates: boolean
  canManageLeads: boolean
  canIncludeHistorical: boolean
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
  setActiveTab: (value: "gigs" | "leads") => void
  setStatus: (value: string) => void
  setQuery: (value: string) => void
  setLeadStatus: (value: string) => void
  setIncludeHistorical: (value: boolean) => void
  setLimit: (value: number) => void
  onRefresh: () => void
  onSort: (key: string) => void
  onOpenGig: (gigId: string) => void
  onCloseGig: () => void
  onUpdateStatus: (gigId: string, status: string) => void
  onAddApplication: (gigId: string, crmProfile: string) => Promise<boolean>
  onUpdateApplicationStatus: (gigId: string, applicationId: string, status: string) => void
  onSyncLeads: () => void
  onReviewLead: (leadId: string, status: JobLeadReviewStatus) => void
  onPostLead: (
    leadId: string,
    options?: {
      channelId?: string
      engagementStatus?: "lead" | "recruiting"
      tags?: string[]
    },
  ) => void
}) {
  const counts = props.gigs.reduce(
    (acc, gig) => {
      acc.total += 1
      acc.applications += Number(gig.application_count || 0)
      acc.interested += Number(gig.interested_count || 0)
      if (staleGigAge(gig, props.staleDays, props.contactedReminderDays) !== null) {
        acc.stale += 1
      }
      return acc
    },
    { total: 0, applications: 0, interested: 0, stale: 0 },
  )
  const leadCounts = props.leads.reduce(
    (acc, lead) => {
      acc.total += 1
      if (lead.status === "pending") acc.pending += 1
      if (lead.status === "approved") acc.approved += 1
      if (lead.status === "posted") acc.posted += 1
      return acc
    },
    { total: 0, pending: 0, approved: 0, posted: 0 },
  )
  const tabBar = (
    <div
      className="inline-flex w-fit rounded-md border bg-background p-1"
      role="tablist"
      aria-label="Gig views"
    >
      <Button
        id="gigsTab"
        type="button"
        size="sm"
        variant={props.activeTab === "gigs" ? "default" : "ghost"}
        aria-pressed={props.activeTab === "gigs"}
        onClick={() => props.setActiveTab("gigs")}
      >
        <BriefcaseBusiness />
        Gigs
      </Button>
      <Button
        id="gigLeadsTab"
        type="button"
        size="sm"
        variant={props.activeTab === "leads" ? "default" : "ghost"}
        aria-pressed={props.activeTab === "leads"}
        onClick={() => props.setActiveTab("leads")}
      >
        <ClipboardList />
        Leads
      </Button>
    </div>
  )
  const filterBar = (
    <Card className="grid gap-3 p-4">
      {tabBar}
      {props.activeTab === "leads" ? (
        <div className="grid gap-3 md:grid-cols-[minmax(160px,.6fr)_auto_auto] md:items-end">
          <Label>
            Lead status
            <Select
              id="gigLeadStatus"
              value={props.leadStatus}
              onChange={(event) => props.setLeadStatus(event.target.value)}
            >
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
              <option value="posted">Posted</option>
              <option value="all">All statuses</option>
            </Select>
          </Label>
          <Button
            id="refreshGigLeads"
            type="button"
            variant="outline"
            onClick={props.onRefresh}
            disabled={props.loading.gigLeads}
          >
            <RefreshCw />
            Refresh
          </Button>
          {props.canManageLeads ? (
            <Button
              id="syncGigLeads"
              type="button"
              onClick={props.onSyncLeads}
              disabled={props.loading.gigLeadsSync}
            >
              <RefreshCw />
              Scrape HN
            </Button>
          ) : null}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-[minmax(140px,.75fr)_minmax(220px,1.25fr)_auto_auto_auto] md:items-end">
          <Label>
            Status
            <Select
              id="gigStatus"
              value={props.status}
              onChange={(event) => props.setStatus(event.target.value)}
            >
              <option value="">Any status</option>
              {gigStatuses.map((status) => (
                <option key={status} value={status}>
                  {titleCase(status)}
                </option>
              ))}
            </Select>
          </Label>
          <Label>
            Search gigs
            <Input
              id="gigQuery"
              value={props.query}
              autoComplete="off"
              placeholder="Title, gig text, #tag, @poster"
              onChange={(event) => props.setQuery(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && props.onRefresh()}
            />
          </Label>
          {props.canIncludeHistorical ? (
            <label className="flex min-h-9 items-center gap-2 text-xs font-bold text-muted-foreground">
              <input
                type="checkbox"
                checked={props.includeHistorical}
                onChange={(event) => props.setIncludeHistorical(event.target.checked)}
              />
              Include historical
            </label>
          ) : null}
          <Button
            id="searchGigs"
            type="button"
            onClick={props.onRefresh}
            disabled={props.loading.gigs}
          >
            <Search />
            Search
          </Button>
          <Button
            id="refreshGigs"
            type="button"
            variant="outline"
            onClick={props.onRefresh}
            disabled={props.loading.gigs}
          >
            <RefreshCw />
            Refresh
          </Button>
          {props.gigs.length >= props.limit ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => props.setLimit(Math.min(props.limit + 100, 500))}
              disabled={props.loading.gigs || props.limit >= 500}
            >
              Load more
            </Button>
          ) : null}
        </div>
      )}
    </Card>
  )

  if (props.activeTab === "leads") {
    return (
      <>
        {filterBar}

        <JobLeadScrapeStatusPanel
          status={props.scrapeStatus}
          loading={Boolean(props.loading.gigLeadScrapeStatus)}
        />

        <section className="grid gap-3 md:grid-cols-4" aria-label="Lead summary">
          <Metric id="gigLeadMetricTotal" label="Leads" value={leadCounts.total} />
          <Metric id="gigLeadMetricPending" label="Pending" value={leadCounts.pending} />
          <Metric id="gigLeadMetricApproved" label="Approved" value={leadCounts.approved} />
          <Metric id="gigLeadMetricPosted" label="Posted" value={leadCounts.posted} />
        </section>

        <Card>
          <CardHeader>
            <CardTitle>Sourced leads</CardTitle>
            <span id="gigLeadsStatus" className="text-sm text-muted-foreground">
              {props.loading.gigLeads ? "Loading" : `${props.leads.length} shown`}
            </span>
          </CardHeader>
          <Empty hidden={props.leads.length !== 0}>No sourced leads match this view.</Empty>
          <div
            id="gigLeadsBody"
            className={cn("grid gap-3 p-4", props.leads.length === 0 && "hidden")}
          >
            {props.leads.map((lead) => (
              <JobLeadListItem
                key={lead.id}
                lead={lead}
                loading={props.loading}
                canWrite={props.canManageLeads}
                jobPostChannels={props.jobPostChannels}
                onReviewLead={props.onReviewLead}
                onPostLead={props.onPostLead}
              />
            ))}
          </div>
        </Card>
      </>
    )
  }

  const detailLoading = props.selectedGigId
    ? props.loading[`gig:${props.selectedGigId}:detail`]
    : false

  if (props.selectedGigId && !props.selectedGig && (props.loading.gigs || detailLoading)) {
    return (
      <>
        {filterBar}
        <Card>
          <CardHeader>
            <CardTitle>Gig detail</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">Loading gig.</CardContent>
        </Card>
      </>
    )
  }

  if (props.selectedGigId && !props.selectedGig) {
    return (
      <>
        {filterBar}
        <Card>
          <CardHeader>
            <CardTitle>Gig detail</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              This gig is not in the current result set. Clear filters or refresh the gig list.
            </p>
            <Button type="button" variant="outline" onClick={props.onCloseGig}>
              <ArrowLeft />
              Back to gigs
            </Button>
          </CardContent>
        </Card>
      </>
    )
  }

  if (props.selectedGig) {
    return (
      <>
        {filterBar}
        <GigDetailPage
          key={props.selectedGig.id}
          gig={props.selectedGig}
          loading={props.loading}
          canWrite={props.canWrite}
          canSearchCandidates={props.canSearchCandidates}
          crmContactUrl={props.crmContactUrl}
          crmAttachmentUrl={props.crmAttachmentUrl}
          staleDays={props.staleDays}
          contactedReminderDays={props.contactedReminderDays}
          onBack={props.onCloseGig}
          onUpdateStatus={props.onUpdateStatus}
          onAddApplication={props.onAddApplication}
          onUpdateApplicationStatus={props.onUpdateApplicationStatus}
        />
      </>
    )
  }

  return (
    <>
      {filterBar}

      <section className="grid gap-3 md:grid-cols-4" aria-label="Gig summary">
        <Metric id="gigMetricTotal" label="Gigs" value={counts.total} />
        <Metric id="gigMetricCandidates" label="Candidates" value={counts.applications} />
        <Metric id="gigMetricInterested" label="Interested" value={counts.interested} />
        <Metric id="gigMetricStale" label="Needs update" value={counts.stale} />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Discord gigs</CardTitle>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => props.onSort("activity")}
              aria-label="Sort gigs by activity"
            >
              Activity{" "}
              {props.sort.key === "activity" ? (props.sort.direction === "asc" ? "↑" : "↓") : ""}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => props.onSort("title")}
              aria-label="Sort gigs by title"
            >
              Title {props.sort.key === "title" ? (props.sort.direction === "asc" ? "↑" : "↓") : ""}
            </Button>
            <span id="gigsStatus" className="text-sm text-muted-foreground">
              {props.loading.gigs ? "Loading" : `${props.gigs.length} shown`}
            </span>
          </div>
        </CardHeader>
        <Empty hidden={props.gigs.length !== 0}>No gigs match this view.</Empty>
        <div id="gigsBody" className={cn("grid gap-3 p-4", props.gigs.length === 0 && "hidden")}>
          {props.gigs.map((gig) => (
            <GigListItem
              key={gig.id}
              gig={gig}
              loading={props.loading}
              canWrite={props.canWrite}
              staleDays={props.staleDays}
              contactedReminderDays={props.contactedReminderDays}
              onOpenGig={props.onOpenGig}
              onUpdateStatus={props.onUpdateStatus}
            />
          ))}
        </div>
      </Card>
    </>
  )
}

function defaultJobPostChannelId(lead: JobLead, channels: JobPostChannel[]) {
  if (channels.length === 0) return ""
  const leadPostingType = String(lead.posting_type || "").trim()
  const preferred =
    channels.find((channel) => channel.posting_type === leadPostingType) ||
    channels.find((channel) => channel.posting_type === "part_time") ||
    channels.find((channel) => channel.posting_type === "part_time_or_full_time") ||
    channels.find((channel) => channel.posting_type === "unknown") ||
    channels[0]
  return preferred?.channel_id || ""
}

function jobPostChannelLabel(channel: JobPostChannel) {
  const name = channel.channel_name ? `#${channel.channel_name}` : `#${channel.channel_id}`
  return `${titleCase(channel.posting_type)} ${name}`
}

function normalizedForumTagName(value?: string) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function leadForumTagTerms(lead: JobLead) {
  const terms = new Set((lead.tags || []).map(normalizedForumTagName).filter(Boolean))
  if (lead.remote) terms.add("remote")
  const postingType = String(lead.posting_type || "")
  if (postingType === "part_time") {
    for (const term of ["part time", "contract", "contractor", "freelance", "gig", "1099"]) {
      terms.add(term)
    }
  }
  if (postingType === "full_time") {
    for (const term of ["full time", "fulltime", "employee", "permanent"]) {
      terms.add(term)
    }
  }
  if (postingType === "part_time_or_full_time") {
    for (const term of ["part time", "full time", "contract", "employee"]) {
      terms.add(term)
    }
  }
  if (terms.has("contract to hire")) {
    terms.add("contract")
    terms.add("contractor")
  }
  if (terms.has("1099")) {
    terms.add("contract")
    terms.add("contractor")
  }
  return terms
}

function scoreLeadForumTag(tag: JobPostChannelTag, lead: JobLead, terms: Set<string>) {
  const tagName = normalizedForumTagName(tag.name)
  if (!tagName) return -1
  if (terms.has(tagName)) return 100
  const leadText = normalizedForumTagName(
    [lead.title, lead.body_normalized, lead.location, ...(lead.tags || [])].join(" "),
  )
  if (tagName === "remote" && lead.remote) return 90
  if (leadText.includes(tagName)) return 80
  const postingType = String(lead.posting_type || "")
  if (
    postingType === "part_time" &&
    ["part time", "contract", "contractor", "freelance", "gig", "1099"].includes(tagName)
  ) {
    return 70
  }
  if (
    postingType === "full_time" &&
    ["full time", "fulltime", "employee", "permanent"].includes(tagName)
  ) {
    return 70
  }
  const termWords = new Set(Array.from(terms).join(" ").split(" ").filter(Boolean))
  if (tagName.split(" ").some((word) => termWords.has(word))) return 40
  return 0
}

function defaultJobPostTagNames(lead: JobLead, channel?: JobPostChannel) {
  const availableTags = channel?.available_tags || []
  if (availableTags.length === 0) return []
  const terms = leadForumTagTerms(lead)
  const selected = availableTags
    .filter((tag) => terms.has(normalizedForumTagName(tag.name)))
    .map((tag) => tag.name)
    .slice(0, 5)
  if (selected.length > 0 || !channel?.requires_tag) return selected
  const [bestTag] = [...availableTags].sort((left, right) => {
    const scoreDelta = scoreLeadForumTag(right, lead, terms) - scoreLeadForumTag(left, lead, terms)
    if (scoreDelta !== 0) return scoreDelta
    return Number(Boolean(left.moderated)) - Number(Boolean(right.moderated))
  })
  return bestTag ? [bestTag.name] : []
}

function jobLeadClassificationLabel(lead: JobLead) {
  const method = lead.contractor_classification?.method
  const postingType = lead.contractor_classification?.posting_type || lead.posting_type
  const postingTypeLabel =
    {
      part_time: "Part-time / contract",
      full_time: "Full-time",
      part_time_or_full_time: "Full-time or part-time / contract",
      unknown: "Employment type unknown",
    }[String(postingType || "")] || "Employment type unknown"
  if (method) {
    const methodLabel = jobLeadClassificationMethodLabel(method)
    return `${postingTypeLabel} · ${methodLabel}`
  }
  return postingType ? postingTypeLabel : lead.review_summary || "Classification unavailable"
}

function JobLeadListItem({
  lead,
  loading,
  canWrite,
  jobPostChannels,
  onReviewLead,
  onPostLead,
}: {
  lead: JobLead
  loading: Record<string, boolean>
  canWrite: boolean
  jobPostChannels: JobPostChannel[]
  onReviewLead: (leadId: string, status: JobLeadReviewStatus) => void
  onPostLead: (
    leadId: string,
    options?: {
      channelId?: string
      engagementStatus?: "lead" | "recruiting"
      tags?: string[]
    },
  ) => void
}) {
  const canDecide = canWrite && (lead.status === "pending" || lead.status === "approved")
  const canRestore = canWrite && lead.status === "rejected"
  const reviewing = loading[`gigLead:${lead.id}:review`]
  const posting = loading[`gigLead:${lead.id}:post`]
  const canPost = canDecide
  const defaultChannelId = defaultJobPostChannelId(lead, jobPostChannels)
  const [engagementStatus, setEngagementStatus] = useState<"lead" | "recruiting">("lead")
  const engagementStatusRef = useRef<HTMLSelectElement>(null)
  const [channelId, setChannelId] = useState(defaultChannelId)
  const selectedChannel = jobPostChannels.find((channel) => channel.channel_id === channelId)
  const [selectedTags, setSelectedTags] = useState<string[]>(() =>
    defaultJobPostTagNames(lead, selectedChannel),
  )
  const [showFullComment, setShowFullComment] = useState(false)
  useEffect(() => {
    setChannelId(defaultChannelId)
  }, [defaultChannelId])
  useEffect(() => {
    setSelectedTags(defaultJobPostTagNames(lead, selectedChannel))
  }, [lead, selectedChannel])
  const requiredTagMissing =
    Boolean(selectedChannel?.requires_tag) &&
    Boolean(selectedChannel?.available_tags?.length) &&
    selectedTags.length === 0
  function toggleSelectedTag(tagName: string) {
    setSelectedTags((current) => {
      if (current.includes(tagName)) return current.filter((name) => name !== tagName)
      return [...current, tagName].slice(0, 5)
    })
  }
  const discordUrl =
    lead.discord_guild_id && lead.discord_thread_id
      ? `https://discord.com/channels/${encodeURIComponent(
          lead.discord_guild_id,
        )}/${encodeURIComponent(lead.discord_thread_id)}`
      : ""
  const contactEmail = lead.contractor_classification?.contact_email
  const commentText = lead.body_normalized || "No lead text captured."
  return (
    <article className="grid gap-4 rounded-md border bg-background p-4 lg:grid-cols-[minmax(0,1fr)_220px_190px] lg:items-start">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <strong className="text-base">{lead.title || "Untitled lead"}</strong>
          <Badge variant={jobLeadStatusTone(lead.status)}>{titleCase(lead.status)}</Badge>
          {lead.remote ? <Badge variant="queued">Remote</Badge> : null}
          <Badge variant="neutral">{jobLeadClassificationLabel(lead)}</Badge>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {lead.organization ? <Badge variant="neutral">{lead.organization}</Badge> : null}
          {lead.location ? <Badge variant="neutral">{lead.location}</Badge> : null}
          {(lead.tags || []).slice(0, 6).map((tag) => (
            <Badge key={tag} variant="queued">
              {tag}
            </Badge>
          ))}
        </div>
        <p
          id={`gigLeadComment-${lead.id}`}
          className={cn(
            "mt-3 whitespace-pre-wrap break-words text-sm text-muted-foreground",
            !showFullComment && "max-h-20 overflow-hidden",
          )}
        >
          {commentText}
        </p>
        {lead.body_normalized ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="mt-1"
            aria-expanded={showFullComment}
            aria-controls={`gigLeadComment-${lead.id}`}
            onClick={() => setShowFullComment((current) => !current)}
          >
            {showFullComment ? "Collapse comment" : "Show full comment"}
          </Button>
        ) : null}
        {lead.review_summary ? (
          <p className="mt-2 text-sm font-semibold text-foreground">{lead.review_summary}</p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Posted {formatDate(lead.source_posted_at) || "unknown"}</span>
          <span>Captured {formatDate(lead.created_at) || "unknown"}</span>
          {lead.source_url ? (
            <a
              className="inline-flex items-center gap-1 font-extrabold text-primary"
              href={lead.source_url}
              target="_blank"
              rel="noreferrer"
            >
              Source
              <ExternalLink className="size-3.5" />
            </a>
          ) : null}
          {lead.apply_url ? (
            <a
              className="inline-flex items-center gap-1 font-extrabold text-primary"
              href={lead.apply_url}
              target="_blank"
              rel="noreferrer"
            >
              Apply website
              <ExternalLink className="size-3.5" />
            </a>
          ) : null}
          {contactEmail ? (
            <a
              className="inline-flex items-center gap-1 font-extrabold text-primary"
              href={`mailto:${contactEmail}`}
            >
              Email {contactEmail}
            </a>
          ) : null}
          {discordUrl ? (
            <a
              className="inline-flex items-center gap-1 font-extrabold text-primary"
              href={discordUrl}
              target="_blank"
              rel="noreferrer"
            >
              Discord
              <ExternalLink className="size-3.5" />
            </a>
          ) : null}
          {lead.posted_gig_url ? (
            <a
              className="inline-flex items-center gap-1 font-extrabold text-primary"
              href={lead.posted_gig_url}
            >
              View posted gig
              <ExternalLink className="size-3.5" />
            </a>
          ) : null}
        </div>
      </div>
      <div className="grid gap-1 text-sm">
        <span className="text-xs font-bold text-muted-foreground">Review</span>
        <span>{lead.reviewed_at ? formatDate(lead.reviewed_at) : "Not reviewed"}</span>
        <span className="text-muted-foreground">
          {lead.reviewed_by_discord_user_id
            ? `By ${lead.reviewed_by_discord_user_id}`
            : "No reviewer recorded"}
        </span>
        <span className="font-mono text-xs text-muted-foreground">{lead.id.slice(0, 8)}</span>
      </div>
      {canWrite ? (
        <div className="grid gap-2">
          <Label>
            Post as
            <Select
              value={engagementStatus}
              ref={engagementStatusRef}
              disabled={!canPost || posting || reviewing}
              onChange={(event) =>
                setEngagementStatus(event.target.value === "recruiting" ? "recruiting" : "lead")
              }
            >
              <option value="lead">Lead</option>
              <option value="recruiting">Recruiting</option>
            </Select>
          </Label>
          {jobPostChannels.length > 0 ? (
            <Label>
              Channel
              <Select
                value={channelId}
                disabled={!canPost || posting || reviewing}
                onChange={(event) => setChannelId(event.target.value)}
              >
                {jobPostChannels.map((channel) => (
                  <option key={channel.channel_id} value={channel.channel_id}>
                    {jobPostChannelLabel(channel)}
                  </option>
                ))}
              </Select>
            </Label>
          ) : null}
          {selectedChannel?.available_tags?.length ? (
            <fieldset className="grid gap-1.5">
              <legend className="text-xs font-bold text-muted-foreground">
                Tags{selectedChannel.requires_tag ? " (required)" : ""}
              </legend>
              <div className="flex flex-wrap gap-1.5">
                {selectedChannel.available_tags.map((tag) => (
                  <label
                    key={tag.id || tag.name}
                    className="inline-flex min-h-8 items-center gap-1.5 rounded-md border px-2 text-xs font-bold text-muted-foreground"
                  >
                    <input
                      type="checkbox"
                      checked={selectedTags.includes(tag.name)}
                      disabled={!canPost || posting || reviewing}
                      onChange={() => toggleSelectedTag(tag.name)}
                    />
                    {tag.name}
                  </label>
                ))}
              </div>
            </fieldset>
          ) : null}
          <Button
            type="button"
            disabled={!canPost || posting || reviewing || requiredTagMissing}
            onClick={() => {
              const selectedStatus =
                engagementStatusRef.current?.value === "recruiting" ? "recruiting" : "lead"
              onPostLead(lead.id, {
                channelId,
                engagementStatus: selectedStatus,
                tags: selectedTags,
              })
            }}
          >
            <Send />
            Post to Discord
          </Button>
          {canRestore ? (
            <Button
              type="button"
              variant="outline"
              disabled={reviewing || posting}
              onClick={() => onReviewLead(lead.id, "pending")}
            >
              <RefreshCw />
              Restore to pending
            </Button>
          ) : (
            <Button
              type="button"
              variant="outline"
              disabled={!canDecide || reviewing || posting}
              onClick={() => onReviewLead(lead.id, "rejected")}
            >
              <UserMinus />
              Reject
            </Button>
          )}
        </div>
      ) : null}
    </article>
  )
}

function jobLeadStatusTone(status: JobLead["status"]): Tone {
  if (status === "approved") return "queued"
  if (status === "posted") return "succeeded"
  if (status === "rejected") return "failed"
  return "neutral"
}

function GigListItem({
  gig,
  loading,
  canWrite,
  onOpenGig,
  onUpdateStatus,
  staleDays,
  contactedReminderDays,
}: {
  gig: Gig
  loading: Record<string, boolean>
  canWrite: boolean
  onOpenGig: (gigId: string) => void
  onUpdateStatus: (gigId: string, status: string) => void
  staleDays: number
  contactedReminderDays: number
}) {
  const applications = Array.isArray(gig.applications) ? gig.applications : []
  const isActive = gig.status === "recruiting" || gig.status === "contacted"
  const threadUrl =
    gig.discord_guild_id && gig.discord_thread_id
      ? `https://discord.com/channels/${encodeURIComponent(
          gig.discord_guild_id,
        )}/${encodeURIComponent(gig.discord_thread_id)}`
      : ""
  const staleAge = staleGigAge(gig, staleDays, contactedReminderDays)
  return (
    <article
      className={cn(
        "grid gap-4 rounded-md border bg-background p-4 lg:grid-cols-[minmax(0,1fr)_220px_180px] lg:items-start",
        !isActive && "border-l-4 border-l-muted-foreground/60 bg-secondary/45",
      )}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <a
            className="text-base font-extrabold text-primary"
            href={`/dashboard/gigs/${encodeURIComponent(gig.id)}`}
            onClick={(event) => {
              event.preventDefault()
              onOpenGig(gig.id)
            }}
          >
            {gig.title || "Untitled gig"}
          </a>
          <Badge
            variant={
              gig.status === "filled"
                ? "succeeded"
                : gig.status === "lost" || gig.status === "duplicate"
                  ? "failed"
                  : isActive
                    ? "queued"
                    : "neutral"
            }
          >
            {gig.status_label || titleCase(gig.status)}
          </Badge>
          {!isActive ? <Badge variant="neutral">Not active</Badge> : null}
          {staleAge !== null ? <Badge variant="running">{staleAge}d stale</Badge> : null}
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {gig.posting_type ? <Badge variant="neutral">{titleCase(gig.posting_type)}</Badge> : null}
          {gig.discord_channel_name ? (
            <Badge variant="neutral">#{gig.discord_channel_name}</Badge>
          ) : null}
          {(gig.required_skills || []).slice(0, 5).map((skill) => (
            <Badge key={skill} variant="queued">
              {skill}
            </Badge>
          ))}
          {(gig.preferred_skills || []).slice(0, 3).map((skill) => (
            <Badge key={skill} variant="neutral">
              {skill}
            </Badge>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>Activity {formatDate(gigActivityTimestamp(gig)) || "unknown"}</span>
          <span>Posted {formatDate(gig.posted_at) || "unknown"}</span>
          {threadUrl ? (
            <a
              className="font-extrabold text-primary"
              href={threadUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open Discord thread
            </a>
          ) : null}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm lg:grid-cols-1">
        <div>
          <span className="block text-xs font-bold text-muted-foreground">People</span>
          <strong>{gig.application_count || applications.length}</strong>
          <span className="ml-2 text-muted-foreground">
            {Number(gig.interested_count || 0)} interested
          </span>
        </div>
        <div>
          <span className="block text-xs font-bold text-muted-foreground">Top candidates</span>
          <span className="text-muted-foreground">
            {applications
              .slice(0, 3)
              .map((application) => candidateDisplayName(application))
              .join(", ") || "None yet"}
          </span>
        </div>
      </div>
      <div className="grid gap-2">
        {canWrite ? (
          <Select
            aria-label={`Status for ${gig.title || "gig"}`}
            value={gig.status}
            disabled={loading[`gig:${gig.id}:status`]}
            onChange={(event) => onUpdateStatus(gig.id, event.target.value)}
          >
            {gigStatuses.map((status) => (
              <option key={status} value={status}>
                {titleCase(status)}
              </option>
            ))}
          </Select>
        ) : null}
        <Button type="button" onClick={() => onOpenGig(gig.id)}>
          Manage people
        </Button>
      </div>
    </article>
  )
}

function candidateDisplayName(application: GigApplication) {
  return (
    application.name ||
    application.email_508 ||
    application.discord_username ||
    (typeof application.evaluation?.discord_username === "string"
      ? application.evaluation.discord_username
      : "") ||
    "Candidate"
  )
}

function personSearchLabel(person: Person) {
  return person.name || person.email_508 || person.email || person.crm_contact_id || "CRM person"
}

function GigDetailPage({
  gig,
  loading,
  canWrite,
  canSearchCandidates,
  crmContactUrl,
  crmAttachmentUrl,
  staleDays,
  contactedReminderDays,
  onBack,
  onUpdateStatus,
  onAddApplication,
  onUpdateApplicationStatus,
}: {
  gig: Gig
  loading: Record<string, boolean>
  canWrite: boolean
  canSearchCandidates: boolean
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
  staleDays: number
  contactedReminderDays: number
  onBack: () => void
  onUpdateStatus: (gigId: string, status: string) => void
  onAddApplication: (gigId: string, crmProfile: string) => Promise<boolean>
  onUpdateApplicationStatus: (gigId: string, applicationId: string, status: string) => void
}) {
  const [candidateQuery, setCandidateQuery] = useState("")
  const [candidateMatches, setCandidateMatches] = useState<Person[]>([])
  const [selectedCandidate, setSelectedCandidate] = useState<Person | null>(null)
  const [candidateSearchError, setCandidateSearchError] = useState("")
  const [crmProfile, setCrmProfile] = useState("")
  const applications = Array.isArray(gig.applications) ? gig.applications : []
  const isActive = gig.status === "recruiting" || gig.status === "contacted"
  const candidateQueryReady = candidateQuery.trim().length >= 2
  const selectedCandidateId = selectedCandidate?.crm_contact_id || ""
  const candidateProfile = selectedCandidateId
    ? crmContactUrl(selectedCandidateId) || selectedCandidateId
    : ""
  const profileForAdd = canSearchCandidates ? candidateProfile : crmProfile.trim()
  const threadUrl =
    gig.discord_guild_id && gig.discord_thread_id
      ? `https://discord.com/channels/${encodeURIComponent(
          gig.discord_guild_id,
        )}/${encodeURIComponent(gig.discord_thread_id)}`
      : ""
  const staleAge = staleGigAge(gig, staleDays, contactedReminderDays)

  useEffect(() => {
    if (!canWrite || !canSearchCandidates) return
    const query = candidateQuery.trim()
    if (selectedCandidate && query === personSearchLabel(selectedCandidate)) {
      setCandidateMatches([])
      setCandidateSearchError("")
      return
    }
    if (query.length < 2) {
      setCandidateMatches([])
      setCandidateSearchError("")
      return
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ limit: "8", query })
      void requestJson<Person[]>(`/dashboard/api/people?${params.toString()}`, {
        signal: controller.signal,
      })
        .then((people) => {
          setCandidateMatches(people.filter((person) => Boolean(person.crm_contact_id)))
          setCandidateSearchError("")
        })
        .catch((error) => {
          if (
            controller.signal.aborted ||
            (error instanceof DOMException && error.name === "AbortError")
          ) {
            return
          }
          setCandidateMatches([])
          setCandidateSearchError(messageFromUnknown(error, "Unable to search candidates"))
        })
    }, 300)

    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [candidateQuery, canSearchCandidates, canWrite, selectedCandidate])

  function chooseCandidate(person: Person) {
    setSelectedCandidate(person)
    setCandidateQuery(personSearchLabel(person))
    setCandidateMatches([])
    setCandidateSearchError("")
  }

  return (
    <div className="grid gap-5">
      <Card className={cn(!isActive && "border-l-4 border-l-muted-foreground/60 bg-secondary/35")}>
        <CardHeader className="items-start">
          <div className="grid gap-2">
            <Button type="button" variant="ghost" size="sm" className="w-fit" onClick={onBack}>
              <ArrowLeft />
              Back to gigs
            </Button>
            <div>
              <CardTitle className="text-xl">{gig.title || "Untitled gig"}</CardTitle>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Badge
                  variant={
                    gig.status === "filled"
                      ? "succeeded"
                      : gig.status === "lost" || gig.status === "duplicate"
                        ? "failed"
                        : isActive
                          ? "queued"
                          : "neutral"
                  }
                >
                  {gig.status_label || titleCase(gig.status)}
                </Badge>
                {!isActive ? <Badge variant="neutral">Not active</Badge> : null}
                {staleAge !== null ? <Badge variant="running">{staleAge}d stale</Badge> : null}
                {gig.posting_type ? (
                  <Badge variant="neutral">{titleCase(gig.posting_type)}</Badge>
                ) : null}
                {gig.discord_channel_name ? (
                  <Badge variant="neutral">#{gig.discord_channel_name}</Badge>
                ) : null}
                {(gig.required_skills || []).map((skill) => (
                  <Badge key={skill} variant="queued">
                    {skill}
                  </Badge>
                ))}
                {(gig.preferred_skills || []).map((skill) => (
                  <Badge key={skill} variant="neutral">
                    {skill}
                  </Badge>
                ))}
              </div>
            </div>
          </div>
          <div className="grid min-w-[190px] gap-2">
            {canWrite ? (
              <Label>
                Gig status
                <Select
                  aria-label={`Status for ${gig.title || "gig"}`}
                  value={gig.status}
                  disabled={loading[`gig:${gig.id}:status`]}
                  onChange={(event) => onUpdateStatus(gig.id, event.target.value)}
                >
                  {gigStatuses.map((status) => (
                    <option key={status} value={status}>
                      {titleCase(status)}
                    </option>
                  ))}
                </Select>
              </Label>
            ) : null}
            {threadUrl ? (
              <a
                className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border bg-secondary px-3 text-sm font-semibold"
                href={threadUrl}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="size-4" />
                Discord thread
              </a>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 lg:grid-cols-[1fr_1fr_1fr]">
          <div>
            <span className="text-xs font-bold text-muted-foreground">Activity</span>
            <strong className="block">{formatDate(gigActivityTimestamp(gig)) || "unknown"}</strong>
            <span className="text-sm text-muted-foreground">
              Posted {formatDate(gig.posted_at) || "unknown"}
            </span>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">People</span>
            <strong className="block">{gig.application_count || applications.length}</strong>
            <span className="text-sm text-muted-foreground">
              {Number(gig.interested_count || 0)} interested
            </span>
          </div>
          <div>
            <span className="text-xs font-bold text-muted-foreground">Discord</span>
            <strong className="block">{gig.discord_channel_name || "No channel"}</strong>
            <span className="text-sm text-muted-foreground">
              {gig.discord_thread_id ? `Thread ${gig.discord_thread_id}` : "No thread"}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>People</CardTitle>
          <span className="text-sm text-muted-foreground">
            {applications.length} candidate{applications.length === 1 ? "" : "s"}
          </span>
        </CardHeader>
        {canWrite ? (
          <form
            className="grid gap-2 border-t p-4 md:grid-cols-[minmax(220px,1fr)_auto]"
            onSubmit={(event) => {
              event.preventDefault()
              void onAddApplication(gig.id, profileForAdd).then((added) => {
                if (added) {
                  setCandidateQuery("")
                  setCandidateMatches([])
                  setSelectedCandidate(null)
                  setCrmProfile("")
                }
              })
            }}
          >
            {canSearchCandidates ? (
              <div className="relative min-w-0">
                <Label>
                  Candidate
                  <Input
                    value={candidateQuery}
                    autoComplete="off"
                    placeholder="Search by name or email"
                    aria-label="Search candidates to add"
                    onChange={(event) => {
                      setCandidateQuery(event.target.value)
                      setCandidateMatches([])
                      setCandidateSearchError("")
                      setSelectedCandidate(null)
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" || candidateMatches.length !== 1) return
                      event.preventDefault()
                      chooseCandidate(candidateMatches[0])
                    }}
                  />
                </Label>
                {candidateQueryReady && !selectedCandidate ? (
                  <div className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border bg-background shadow-lg">
                    {candidateSearchError ? (
                      <div className="px-3 py-2 text-sm text-destructive">
                        {candidateSearchError}
                      </div>
                    ) : candidateMatches.length ? (
                      candidateMatches.map((person) => {
                        const label = personSearchLabel(person)
                        const detail = [person.email_508 || person.email, person.contact_type]
                          .filter(Boolean)
                          .join(" | ")
                        return (
                          <button
                            key={person.crm_contact_id}
                            type="button"
                            className="grid w-full gap-0.5 px-3 py-2 text-left hover:bg-secondary focus:bg-secondary focus:outline-none"
                            onClick={() => chooseCandidate(person)}
                          >
                            <span className="truncate text-sm font-bold">{label}</span>
                            {detail ? (
                              <span className="truncate text-xs text-muted-foreground">
                                {detail}
                              </span>
                            ) : null}
                          </button>
                        )
                      })
                    ) : (
                      <div className="px-3 py-2 text-sm text-muted-foreground">
                        No candidates match this search
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            ) : (
              <Label className="min-w-0">
                CRM profile
                <Input
                  value={crmProfile}
                  onChange={(event) => setCrmProfile(event.target.value)}
                  placeholder="https://crm.508.dev/#Contact/view/..."
                  aria-label="CRM profile for candidate"
                />
              </Label>
            )}
            <Button
              type="submit"
              className="self-end"
              disabled={loading[`gig:${gig.id}:addCandidate`] || !profileForAdd}
            >
              <UserPlus />
              Add candidate
            </Button>
          </form>
        ) : null}
        <Empty hidden={applications.length !== 0}>No suggested or interested people yet.</Empty>
        <div className={cn("grid gap-3 p-4", applications.length === 0 && "hidden")}>
          {applications.map((application) => (
            <GigApplicationRow
              key={application.id}
              gigId={gig.id}
              application={application}
              loading={loading}
              canWrite={canWrite}
              crmContactUrl={crmContactUrl}
              crmAttachmentUrl={crmAttachmentUrl}
              onUpdateApplicationStatus={onUpdateApplicationStatus}
            />
          ))}
        </div>
      </Card>
    </div>
  )
}

function GigApplicationRow({
  gigId,
  application,
  loading,
  canWrite,
  crmContactUrl,
  crmAttachmentUrl,
  onUpdateApplicationStatus,
}: {
  gigId: string
  application: GigApplication
  loading: Record<string, boolean>
  canWrite: boolean
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
  onUpdateApplicationStatus: (gigId: string, applicationId: string, status: string) => void
}) {
  const displayName = candidateDisplayName(application)
  const contactUrl = crmContactUrl(application.crm_contact_id)
  const resumeUrl = crmAttachmentUrl(application.latest_resume_id)
  const fitScore =
    typeof application.fit_score === "number"
      ? `${Math.round(application.fit_score)}/100`
      : typeof application.match_score === "number"
        ? application.match_score.toFixed(1)
        : ""
  const summary =
    typeof application.evaluation?.llm_summary === "string"
      ? application.evaluation.llm_summary
      : ""
  return (
    <div className="grid gap-2 rounded-md border bg-background p-2">
      <div className="flex flex-wrap items-center gap-2">
        {contactUrl ? (
          <a
            className="font-extrabold text-primary"
            href={contactUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {displayName}
          </a>
        ) : (
          <strong>{displayName}</strong>
        )}
        <Badge variant={application.status === "interested" ? "succeeded" : "neutral"}>
          {titleCase(application.status)}
        </Badge>
        <Badge variant="neutral">{titleCase(application.source || "manual_add")}</Badge>
        {fitScore ? (
          <span className="text-xs font-bold text-muted-foreground">Fit {fitScore}</span>
        ) : null}
        {contactUrl ? (
          <a
            className="text-xs font-extrabold text-primary"
            href={contactUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Open ${displayName} CRM profile`}
          >
            CRM profile
          </a>
        ) : null}
        {resumeUrl ? (
          <a
            className="text-xs font-extrabold text-primary"
            href={resumeUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Resume
          </a>
        ) : null}
      </div>
      {summary ? <div className="text-xs text-muted-foreground">{summary}</div> : null}
      {canWrite ? (
        <Select
          aria-label={`Candidate status for ${displayName}`}
          value={application.status || "suggested"}
          disabled={loading[`application:${application.id}:status`]}
          onChange={(event) => onUpdateApplicationStatus(gigId, application.id, event.target.value)}
        >
          {applicationStatuses.map((status) => (
            <option key={status} value={status}>
              {titleCase(status)}
            </option>
          ))}
        </Select>
      ) : null}
    </div>
  )
}

function PeopleView(props: {
  crmBaseUrl: string
  people: Person[]
  sort: { key: string; direction: SortDirection }
  canSync: boolean
  loading: Record<string, boolean>
  peopleQuery: string
  peopleMember: string
  peopleFilters: FilterState
  peopleFilterKind: PeopleFilterKey
  peopleFilterValue: string
  peopleFilterKeys: PeopleFilterKey[]
  onSearch: () => void
  onSync: () => void
  onSort: (key: string) => void
  setPeopleQuery: (value: string) => void
  setPeopleMember: (value: string) => void
  setPeopleFilterKind: (value: PeopleFilterKey) => void
  setPeopleFilterValue: (value: string) => void
  addFilter: () => void
  removeFilter: (key: PeopleFilterKey) => void
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
}) {
  const filterOptions = peopleFilterDefinitions[props.peopleFilterKind]?.options || []
  return (
    <Card>
      <CardHeader>
        <CardTitle>People lookup</CardTitle>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {props.canSync ? (
            <Button
              id="syncPeople"
              data-permission="people:sync"
              type="button"
              onClick={props.onSync}
              disabled={props.loading.syncPeople}
            >
              <RefreshCw />
              Sync people
            </Button>
          ) : null}
          {props.crmBaseUrl ? (
            <a
              id="crmHomeLink"
              className="text-sm font-extrabold text-primary"
              href={props.crmBaseUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open CRM
            </a>
          ) : null}
          <span id="peopleStatus" className="text-sm text-muted-foreground">
            {props.loading.people ? "Loading" : `${props.people.length} shown`}
          </span>
        </div>
      </CardHeader>
      <div className="grid gap-3 border-b p-4 md:grid-cols-[minmax(0,1fr)_auto]">
        <Label>
          Search CRM people cache
          <Input
            id="peopleQuery"
            value={props.peopleQuery}
            autoComplete="off"
            placeholder="Name, email, CRM id, Discord, resume"
            onChange={(event) => props.setPeopleQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") props.onSearch()
            }}
          />
        </Label>
        <Button
          id="searchPeople"
          type="button"
          onClick={props.onSearch}
          disabled={props.loading.people}
        >
          <Search />
          Search
        </Button>
      </div>
      <div className="grid gap-3 border-b bg-background p-4 md:grid-cols-[minmax(120px,.7fr)_minmax(150px,1fr)_minmax(150px,1fr)_auto]">
        <Label>
          Member
          <Select
            id="peopleMember"
            value={props.peopleMember}
            onChange={(event) => props.setPeopleMember(event.target.value)}
          >
            <option value="">Any</option>
            <option value="true">Member</option>
            <option value="false">Not member</option>
          </Select>
        </Label>
        <Label>
          Add filter
          <Select
            id="peopleFilterKind"
            value={props.peopleFilterKind}
            disabled={props.peopleFilterKeys.length === 0}
            onChange={(event) => props.setPeopleFilterKind(event.target.value as PeopleFilterKey)}
          >
            {props.peopleFilterKeys.map((key) => (
              <option key={key} value={key}>
                {peopleFilterDefinitions[key].label}
              </option>
            ))}
          </Select>
        </Label>
        <Label>
          Value
          <Select
            id="peopleFilterValue"
            value={props.peopleFilterValue}
            onChange={(event) => props.setPeopleFilterValue(event.target.value)}
          >
            {filterOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Label>
        <Button
          id="addPeopleFilter"
          type="button"
          onClick={props.addFilter}
          disabled={props.peopleFilterKeys.length === 0}
        >
          Add filter
        </Button>
        <div id="activePeopleFilters" className="md:col-span-4">
          <FilterChips filters={props.peopleFilters} onRemove={props.removeFilter} />
        </div>
      </div>
      <Empty hidden={props.people.length !== 0}>No people match this lookup.</Empty>
      <div className="overflow-x-auto">
        <Table
          id="peopleTable"
          className={cn("min-w-[900px]", props.people.length === 0 && "hidden")}
          aria-label="People lookup results"
        >
          <TableHeader>
            <TableRow>
              <SortableTableHead
                className="w-[27%]"
                label="Name"
                scope="people"
                sort={props.sort}
                sortKey="name"
                onSort={(_, key) => props.onSort(key)}
              />
              <SortableTableHead
                className="w-[28%]"
                label="Status"
                scope="people"
                sort={props.sort}
                sortKey="status"
                onSort={(_, key) => props.onSort(key)}
              />
              <SortableTableHead
                className="w-[20%]"
                label="Discord"
                scope="people"
                sort={props.sort}
                sortKey="discord"
                onSort={(_, key) => props.onSort(key)}
              />
              <SortableTableHead
                className="w-[25%]"
                label="Resume / skills"
                scope="people"
                sort={props.sort}
                sortKey="resume"
                onSort={(_, key) => props.onSort(key)}
              />
            </TableRow>
          </TableHeader>
          <TableBody id="peopleBody">
            {props.people.map((person) => {
              const displayName = person.name || person.email_508 || person.email || "CRM contact"
              const contactUrl = props.crmContactUrl(person.crm_contact_id)
              const status = person.profile_status || {}
              const skillsCount = Number(status.skills_count || 0)
              const resumeUrl = props.crmAttachmentUrl(person.latest_resume_id)
              const intakeSubmission = person.latest_intake_submission
              const resumeIntakeSubmission =
                person.latest_resume_intake_submission || intakeSubmission
              const intakeResumeHref = intakeResumeUrl(resumeIntakeSubmission)
              const resumeHref = resumeUrl || intakeResumeHref
              const resumeLabel = resumeUrl
                ? "Resume"
                : intakeResumeName(resumeIntakeSubmission) || person.latest_resume_name || "Resume"
              return (
                <TableRow key={person.crm_contact_id || displayName}>
                  <TableCell>
                    {contactUrl ? (
                      <a
                        className="font-extrabold text-primary"
                        href={contactUrl}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`Open ${displayName} in CRM`}
                      >
                        {displayName}
                      </a>
                    ) : (
                      <strong>{displayName}</strong>
                    )}
                    <div className="text-sm text-muted-foreground">
                      {[person.email_508 || person.email, person.contact_type]
                        .filter(Boolean)
                        .join(" | ")}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1.5">
                      {!status.crm_active ? (
                        <Badge variant="missing">{person.sync_status || "CRM sync issue"}</Badge>
                      ) : null}
                      <Badge variant={status.is_member ? "succeeded" : "missing"}>
                        {status.is_member ? "Member" : "Missing Member"}
                      </Badge>
                      <Badge variant={status.discord_linked ? "succeeded" : "missing"}>
                        {status.discord_linked ? "Discord" : "Missing Discord"}
                      </Badge>
                      <Badge variant={status.email_508 ? "succeeded" : "missing"}>
                        {status.email_508 ? "508 email" : "Missing 508 email"}
                      </Badge>
                      {!status.latest_resume ? (
                        <Badge variant="missing">Missing Resume</Badge>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>
                    {[person.discord_username, person.discord_user_id]
                      .filter(Boolean)
                      .join(" | ") || "Not linked"}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {resumeHref ? (
                        <a
                          className="inline-flex min-h-7 max-w-40 items-center truncate rounded-md border bg-secondary px-2 text-xs font-extrabold"
                          href={resumeHref}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={`Open ${displayName} resume`}
                          title={resumeLabel}
                        >
                          {resumeLabel}
                        </a>
                      ) : (
                        <span>
                          {person.latest_resume_name || person.latest_resume_id || "No resume"}
                        </span>
                      )}
                      <Badge variant={skillsCount > 0 ? "succeeded" : "missing"}>
                        {skillsCount > 0 ? "Skills parsed" : "Skills not parsed"}
                      </Badge>
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </Card>
  )
}

function OnboardingView(props: {
  people: Person[]
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  canWrite: boolean
  onboardingQuery: string
  onboardingState: string
  onboarderFilter: string
  onboardingFilters: FilterState
  onboardingFilterKind: PeopleFilterKey
  onboardingFilterValue: string
  onboardingFilterKeys: PeopleFilterKey[]
  onSearch: () => void
  onSort: (key: string) => void
  onAssign: (contactId: string | undefined, onboarder: string) => void
  onStatusChange: (contactId: string | undefined, status: string) => void
  onDraftEmail: (
    contactId: string | undefined,
    options: OnboardingEmailOptions,
  ) => Promise<OnboardingEmailDraft | null>
  onSendEmail: (
    contactId: string | undefined,
    options: OnboardingEmailOptions,
    markdownBody: string,
  ) => Promise<OnboardingEmailDraft | null>
  onSetupEngineer: (payload: EngineerSetupRequest) => Promise<EngineerSetupResult | null>
  canConfigure: boolean
  onOpenConfiguration: () => void
  setOnboardingQuery: (value: string) => void
  setOnboardingState: (value: string) => void
  setOnboarderFilter: (value: string) => void
  setOnboardingFilterKind: (value: PeopleFilterKey) => void
  setOnboardingFilterValue: (value: string) => void
  addFilter: () => void
  removeFilter: (key: PeopleFilterKey) => void
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
}) {
  const filterOptions = peopleFilterDefinitions[props.onboardingFilterKind]?.options || []
  return (
    <>
      {props.canWrite ? (
        <EngineerSetupPanel loading={props.loading.engineerSetup} onSetup={props.onSetupEngineer} />
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>Onboarding queue</CardTitle>
          <span id="onboardingStatus" className="text-sm text-muted-foreground">
            {props.loading.onboarding ? "Loading" : `${props.people.length} shown`}
          </span>
        </CardHeader>
        <div className="grid gap-3 border-b p-4 md:grid-cols-[minmax(0,1fr)_auto]">
          <Label>
            Search prospects
            <Input
              id="onboardingQuery"
              value={props.onboardingQuery}
              autoComplete="off"
              placeholder="Name, email, Discord, onboarder"
              onChange={(event) => props.setOnboardingQuery(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && props.onSearch()}
            />
          </Label>
          <Button
            id="searchOnboarding"
            type="button"
            onClick={props.onSearch}
            disabled={props.loading.onboarding}
          >
            <Search />
            Search
          </Button>
        </div>
        <div className="grid gap-3 border-b bg-background p-4 md:grid-cols-[minmax(140px,.8fr)_minmax(150px,1fr)_minmax(150px,1fr)_minmax(120px,.7fr)_auto]">
          <Label>
            Status
            <Select
              id="onboardingState"
              value={props.onboardingState}
              onChange={(event) => props.setOnboardingState(event.target.value)}
            >
              <option value="">Any state</option>
              {onboardingQueueFilterStatuses.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Label>
          <Label>
            Onboarder
            <Input
              id="onboarderFilter"
              value={props.onboarderFilter}
              autoComplete="off"
              placeholder="Any onboarder"
              onChange={(event) => props.setOnboarderFilter(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && props.onSearch()}
            />
          </Label>
          <Label>
            Add filter
            <Select
              id="onboardingFilterKind"
              value={props.onboardingFilterKind}
              disabled={props.onboardingFilterKeys.length === 0}
              onChange={(event) =>
                props.setOnboardingFilterKind(event.target.value as PeopleFilterKey)
              }
            >
              {props.onboardingFilterKeys.map((key) => (
                <option key={key} value={key}>
                  {peopleFilterDefinitions[key].label}
                </option>
              ))}
            </Select>
          </Label>
          <Label>
            Value
            <Select
              id="onboardingFilterValue"
              value={props.onboardingFilterValue}
              onChange={(event) => props.setOnboardingFilterValue(event.target.value)}
            >
              {filterOptions.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Label>
          <Button
            id="addOnboardingFilter"
            type="button"
            onClick={props.addFilter}
            disabled={props.onboardingFilterKeys.length === 0}
          >
            Add filter
          </Button>
          <div id="activeOnboardingFilters" className="md:col-span-5">
            <FilterChips
              filters={props.onboardingFilters}
              onRemove={props.removeFilter}
              suffix="onboarding filter"
            />
          </div>
        </div>
        <Empty hidden={props.people.length !== 0}>No prospects match this queue view.</Empty>
        <div className="overflow-x-auto">
          <Table
            id="onboardingTable"
            className={cn("min-w-[1340px]", props.people.length === 0 && "hidden")}
            aria-label="Onboarding queue"
          >
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  className="w-[18%]"
                  label="Name"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="name"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[12%]"
                  label="Status"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="onboarding_state"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[20%]"
                  label="Onboarder"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="onboarder"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[12%]"
                  label="Updated"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="updated"
                  onSort={(_, key) => props.onSort(key)}
                />
                <TableHead className="w-[15%]">Email</TableHead>
                <TableHead className="w-[12%]">Links</TableHead>
                <SortableTableHead
                  className="w-[11%]"
                  label="Needs"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="profile_gaps"
                  onSort={(_, key) => props.onSort(key)}
                />
              </TableRow>
            </TableHeader>
            <TableBody id="onboardingBody">
              {props.people.map((person) => (
                <OnboardingRow
                  key={
                    person.crm_contact_id ||
                    person.latest_intake_submission?.submission_id ||
                    person.email ||
                    person.name
                  }
                  person={person}
                  loading={props.loading}
                  canWrite={props.canWrite}
                  onAssign={props.onAssign}
                  onStatusChange={props.onStatusChange}
                  onDraftEmail={props.onDraftEmail}
                  onSendEmail={props.onSendEmail}
                  crmContactUrl={props.crmContactUrl}
                  crmAttachmentUrl={props.crmAttachmentUrl}
                  canConfigure={props.canConfigure}
                  onOpenConfiguration={props.onOpenConfiguration}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  )
}

const employeeGenderOptions = [
  "Female",
  "Genderqueer",
  "Male",
  "Non-Conforming",
  "Other",
  "Prefer not to say",
  "Transgender",
]
const preferredEmailOptions = ["Company Email", "Personal Email", "User ID"]

function splitPersonName(value?: string) {
  const parts = (value || "").trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return { first: "", middle: "", last: "" }
  if (parts.length === 1) return { first: parts[0], middle: "", last: "" }
  if (parts.length === 2) return { first: parts[0], middle: "", last: parts[1] }
  return {
    first: parts[0],
    middle: parts.slice(1, -1).join(" "),
    last: parts[parts.length - 1],
  }
}

function personalEmailFromPerson(person: Person) {
  const email = (person.email || "").trim()
  if (!email || email.toLowerCase().endsWith("@508.dev")) return ""
  return email
}

function companyEmailFromPerson(person: Person) {
  const email508 = (person.email_508 || "").trim()
  if (email508) return email508
  const email = (person.email || "").trim()
  return email.toLowerCase().endsWith("@508.dev") ? email : ""
}

function intakePayloadValue(submission: IntakeSubmission | undefined, key: string): string {
  const value = submission?.normalized_payload?.[key]
  if (value === null || value === undefined) return ""
  if (typeof value === "string") return value.trim()
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ")
  return ""
}

function intakeResumeUrl(submission: IntakeSubmission | undefined) {
  const value = intakePayloadValue(submission, "resume_url")
  if (!/^https:\/\//i.test(value)) return ""
  return value
}

function intakeResumeName(submission: IntakeSubmission | undefined) {
  const fileName = intakePayloadValue(submission, "resume_file_name")
  if (fileName) return fileName
  const value = intakeResumeUrl(submission)
  if (!value) return ""
  try {
    const pathName = new URL(value).pathname
    return decodeURIComponent(pathName.split("/").filter(Boolean).pop() || "Resume")
  } catch {
    return "Resume"
  }
}

function intakeSummaryItems(submission: IntakeSubmission | undefined) {
  if (!submission?.normalized_payload) return []
  return [
    ["Native name", intakePayloadValue(submission, "native_name")],
    ["Weekly hours", intakePayloadValue(submission, "ideal_weekly_hours")],
    [
      "Chat times",
      intakePayloadValue(submission, "chat_availability") ||
        intakePayloadValue(submission, "availability"),
    ],
    ["Rate", intakePayloadValue(submission, "rate_range")],
    ["Interest", intakePayloadValue(submission, "top_question_about_508")],
    ["Skills/interests", intakePayloadValue(submission, "primary_skills_interests")],
  ].filter(([, value]) => value)
}

function EngineerSetupPanel({
  loading,
  onSetup,
}: {
  loading?: boolean
  onSetup: (payload: EngineerSetupRequest) => Promise<EngineerSetupResult | null>
}) {
  const [crmQuery, setCrmQuery] = useState("")
  const [crmMatches, setCrmMatches] = useState<Person[]>([])
  const [crmLoading, setCrmLoading] = useState(false)
  const [crmError, setCrmError] = useState("")
  const [companyEmail, setCompanyEmail] = useState("")
  const [firstName, setFirstName] = useState("")
  const [middleName, setMiddleName] = useState("")
  const [lastName, setLastName] = useState("")
  const [country, setCountry] = useState("")
  const [gender, setGender] = useState("")
  const [dateOfBirth, setDateOfBirth] = useState("")
  const [dateOfJoining, setDateOfJoining] = useState("")
  const [personalEmail, setPersonalEmail] = useState("")
  const [preferedEmail, setPreferedEmail] = useState("")

  function fillFromPerson(person: Person) {
    const name = splitPersonName(person.name)
    setFirstName(name.first)
    setMiddleName(name.middle)
    setLastName(name.last)
    setCompanyEmail(companyEmailFromPerson(person))
    setPersonalEmail(personalEmailFromPerson(person))
    setCountry(person.address_country || "")
    setCrmQuery(person.name || person.email_508 || person.email || "")
    setCrmMatches([])
    setCrmError("")
  }

  async function searchCrmPeople() {
    const query = crmQuery.trim()
    if (!query) return
    setCrmLoading(true)
    setCrmError("")
    try {
      const params = new URLSearchParams({ limit: "8", query })
      setCrmMatches(await requestJson<Person[]>(`/dashboard/api/people?${params.toString()}`))
    } catch (error) {
      setCrmError(messageFromUnknown(error, "Unable to search people"))
      setCrmMatches([])
    } finally {
      setCrmLoading(false)
    }
  }

  async function submit() {
    const payload: EngineerSetupRequest = {
      email: companyEmail,
      first_name: firstName,
      middle_name: middleName,
      last_name: lastName,
      country,
      personal_email: personalEmail,
    }
    if (gender.trim()) payload.gender = gender
    if (dateOfBirth.trim()) payload.date_of_birth = dateOfBirth
    if (dateOfJoining.trim()) payload.date_of_joining = dateOfJoining
    if (preferedEmail.trim()) payload.prefered_email = preferedEmail

    const result = await onSetup(payload)
    if (result) {
      setCrmQuery("")
      setCrmMatches([])
      setCompanyEmail("")
      setFirstName("")
      setMiddleName("")
      setLastName("")
      setCountry("")
      setGender("")
      setDateOfBirth("")
      setDateOfJoining("")
      setPersonalEmail("")
      setPreferedEmail("")
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Engineer setup</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="grid gap-3 border-b pb-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <Label>
              CRM person
              <Input
                value={crmQuery}
                autoComplete="off"
                placeholder="Search name or email"
                onChange={(event) => setCrmQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter") return
                  event.preventDefault()
                  void searchCrmPeople()
                }}
              />
            </Label>
            <Button
              type="button"
              onClick={searchCrmPeople}
              disabled={crmLoading || !crmQuery.trim()}
            >
              <Search />
              Search
            </Button>
            {crmError ? (
              <span className="text-sm font-semibold text-destructive">{crmError}</span>
            ) : null}
            {crmMatches.length > 0 ? (
              <div className="grid gap-2 md:col-span-2">
                {crmMatches.map((person) => {
                  const label =
                    person.name || person.email_508 || person.email || person.crm_contact_id
                  const detail = [person.email_508 || person.email, person.contact_type]
                    .filter(Boolean)
                    .join(" | ")
                  return (
                    <button
                      key={person.crm_contact_id || label}
                      type="button"
                      className="grid rounded-md border bg-background px-3 py-2 text-left text-sm hover:border-primary"
                      onClick={() => fillFromPerson(person)}
                    >
                      <strong>{label}</strong>
                      {detail ? <span className="text-muted-foreground">{detail}</span> : null}
                    </button>
                  )
                })}
              </div>
            ) : null}
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(130px,.6fr)]">
            <Label>
              Company email
              <Input
                value={companyEmail}
                autoComplete="off"
                placeholder="engineer@508.dev"
                onChange={(event) => setCompanyEmail(event.target.value)}
              />
            </Label>
            <Label>
              First name
              <Input
                value={firstName}
                autoComplete="off"
                placeholder="First"
                onChange={(event) => setFirstName(event.target.value)}
              />
            </Label>
            <Label>
              Middle name
              <Input
                value={middleName}
                autoComplete="off"
                placeholder="Optional"
                onChange={(event) => setMiddleName(event.target.value)}
              />
            </Label>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(130px,.6fr)]">
            <Label>
              Last name
              <Input
                value={lastName}
                autoComplete="off"
                placeholder="Last"
                onChange={(event) => setLastName(event.target.value)}
              />
            </Label>
            <Label>
              Country
              <Input
                value={country}
                autoComplete="off"
                placeholder="Taiwan"
                onChange={(event) => setCountry(event.target.value)}
              />
            </Label>
          </div>
          <details className="rounded-md border bg-background p-3">
            <summary className="cursor-pointer text-sm font-extrabold">Advanced options</summary>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <Label>
                Gender
                <Select value={gender} onChange={(event) => setGender(event.target.value)}>
                  <option value="">Default</option>
                  {employeeGenderOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              </Label>
              <Label>
                Date of birth
                <Input
                  value={dateOfBirth}
                  type="date"
                  autoComplete="off"
                  onChange={(event) => setDateOfBirth(event.target.value)}
                />
              </Label>
              <Label>
                Date of joining
                <Input
                  value={dateOfJoining}
                  type="date"
                  autoComplete="off"
                  onChange={(event) => setDateOfJoining(event.target.value)}
                />
              </Label>
              <Label>
                Personal email
                <Input
                  value={personalEmail}
                  type="email"
                  autoComplete="off"
                  placeholder="Optional"
                  onChange={(event) => setPersonalEmail(event.target.value)}
                />
              </Label>
              <Label>
                Preferred contact email
                <Select
                  value={preferedEmail}
                  onChange={(event) => setPreferedEmail(event.target.value)}
                >
                  <option value="">Default</option>
                  {preferredEmailOptions.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              </Label>
            </div>
          </details>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button
              id="setupEngineer"
              type="submit"
              disabled={loading || !companyEmail.trim() || !firstName.trim()}
            >
              <UserPlus />
              Set up engineer
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function OnboardingRow({
  person,
  loading,
  canWrite,
  onAssign,
  onStatusChange,
  onDraftEmail,
  onSendEmail,
  crmContactUrl,
  crmAttachmentUrl,
  canConfigure,
  onOpenConfiguration,
}: {
  person: Person
  loading: Record<string, boolean>
  canWrite: boolean
  onAssign: (contactId: string | undefined, onboarder: string) => void
  onStatusChange: (contactId: string | undefined, status: string) => void
  onDraftEmail: (
    contactId: string | undefined,
    options: OnboardingEmailOptions,
  ) => Promise<OnboardingEmailDraft | null>
  onSendEmail: (
    contactId: string | undefined,
    options: OnboardingEmailOptions,
    markdownBody: string,
  ) => Promise<OnboardingEmailDraft | null>
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
  canConfigure: boolean
  onOpenConfiguration: () => void
}) {
  const displayName = person.name || person.email_508 || person.email || "CRM contact"
  const [value, setValue] = useState(displayOnboarder(person.onboarder))
  const [emailOpen, setEmailOpen] = useState(false)
  const [emailDraft, setEmailDraft] = useState<OnboardingEmailDraft | null>(null)
  const [emailDraftOptions, setEmailDraftOptions] = useState<OnboardingEmailOptions | null>(null)
  const [emailOptions, setEmailOptions] = useState<OnboardingEmailOptions>({
    has_contributed: normalizedOnboardingStatusValue(onboardingStateValue(person)) === "onboarded",
    discord_joined: person.discord_user_id ? "yes" : "unknown",
    agreement_signed: "unknown",
  })
  useEffect(() => setValue(displayOnboarder(person.onboarder)), [person.onboarder])
  const currentStatus = normalizedOnboardingStatusValue(onboardingStateValue(person))
  const hasCrmContact = Boolean(person.crm_contact_id)
  const status = person.profile_status || {}
  const gaps = [
    ["Discord", status.discord_linked],
    ["Resume", status.latest_resume],
    ["Skills", Number(status.skills_count || 0) > 0],
  ].filter(([, ok]) => !ok)
  const contactUrl = crmContactUrl(person.crm_contact_id)
  const resumeUrl = crmAttachmentUrl(person.latest_resume_id)
  const intakeSubmission = person.latest_intake_submission
  const resumeIntakeSubmission = person.latest_resume_intake_submission || intakeSubmission
  const intakeResumeHref = intakeResumeUrl(resumeIntakeSubmission)
  const resumeHref = resumeUrl || intakeResumeHref
  const resumeLabel = resumeUrl
    ? "Resume"
    : intakeResumeName(resumeIntakeSubmission) || person.latest_resume_name || "Resume"
  const intakeItems = intakeSummaryItems(intakeSubmission)
  const emailSentAt = emailDraft?.onboarding_email_sent_at || person.onboarding_email_sent_at
  const emailSentBy = emailDraft?.onboarding_email_sent_by || person.onboarding_email_sent_by
  const emailSentRecipient =
    emailDraft?.onboarding_email_recipient || person.onboarding_email_recipient
  const draftMatchesOptions =
    !emailDraft ||
    (emailDraftOptions !== null &&
      emailDraftOptions.has_contributed === emailOptions.has_contributed &&
      emailDraftOptions.discord_joined === emailOptions.discord_joined &&
      emailDraftOptions.agreement_signed === emailOptions.agreement_signed)
  const sendUnavailableMessage =
    emailDraft && !emailDraft.onboarding_email_sent_at
      ? !draftMatchesOptions
        ? "Send disabled: regenerate after changing draft options."
        : !emailDraft.can_send
          ? !emailDraft.recipient_email
            ? "Send disabled: candidate email is missing."
            : !emailDraft.reply_to_email
              ? "Send disabled: your Reply-To email is missing."
              : "Send disabled: onboarding email SMTP is not configured."
          : ""
      : ""
  const sendUnavailableIsSmtp = sendUnavailableMessage.includes("SMTP")
  const draftBusy = Boolean(loading[`onboarding-email-draft:${person.crm_contact_id}`])
  const sendBusy = Boolean(loading[`onboarding-email-send:${person.crm_contact_id}`])
  const draftBody = emailDraft?.markdown_body || ""
  async function generateDraft(nextOptions = emailOptions) {
    const draft = await onDraftEmail(person.crm_contact_id, nextOptions)
    if (draft) {
      setEmailDraft(draft)
      setEmailDraftOptions({ ...nextOptions })
      setEmailOpen(true)
    }
  }
  async function sendDraft() {
    if (!emailDraft || !emailDraftOptions || !draftMatchesOptions) return
    const sent = await onSendEmail(
      person.crm_contact_id,
      emailDraftOptions,
      emailDraft.markdown_body,
    )
    if (sent) setEmailDraft(sent)
  }
  return (
    <>
      <TableRow>
        <TableCell>
          {contactUrl ? (
            <a
              className="font-extrabold text-primary"
              href={contactUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${displayName} in CRM`}
            >
              {displayName}
            </a>
          ) : (
            <strong>{displayName}</strong>
          )}
          <div className="text-sm text-muted-foreground">
            {person.email_508 || person.email || ""}
          </div>
          {!hasCrmContact ? (
            <div className="mt-1">
              <Badge variant="missing">Application only</Badge>
            </div>
          ) : null}
          {intakeSubmission ? (
            <details className="mt-2 rounded-md border bg-secondary/30 px-2 py-1 text-xs">
              <summary className="cursor-pointer font-extrabold">
                Application
                {intakeSubmission.source ? ` via ${intakeSubmission.source}` : ""}
                {intakeSubmission.submitted_at
                  ? ` | ${formatDate(intakeSubmission.submitted_at)}`
                  : ""}
              </summary>
              <div className="mt-2 grid gap-1 text-muted-foreground">
                {intakeItems.length > 0 ? (
                  intakeItems.map(([label, value]) => (
                    <span key={label}>
                      <strong className="text-foreground">{label}:</strong> {value}
                    </span>
                  ))
                ) : (
                  <span>No extra application fields.</span>
                )}
                {intakeSubmission.submission_id ? (
                  <span>Submission {intakeSubmission.submission_id}</span>
                ) : null}
              </div>
            </details>
          ) : null}
        </TableCell>
        <TableCell>
          <div className="grid max-w-56 gap-2">
            <Badge variant={toneForOnboardingState(onboardingStateValue(person))}>
              {person.onboarding_status_label ||
                labelForOnboardingState(onboardingStateValue(person))}
            </Badge>
            {canWrite && hasCrmContact ? (
              <Select
                aria-label={`Onboarding status for ${displayName}`}
                value={currentStatus}
                disabled={loading[`onboarding-status:${person.crm_contact_id}`]}
                onChange={(event) => onStatusChange(person.crm_contact_id, event.target.value)}
              >
                {currentStatus ? null : (
                  <option value="" disabled>
                    No status
                  </option>
                )}
                {onboardingStatusOptions.map(([statusValue, label]) => (
                  <option key={statusValue} value={statusValue}>
                    {label}
                  </option>
                ))}
              </Select>
            ) : null}
          </div>
        </TableCell>
        <TableCell>
          <form
            className="grid max-w-64 grid-cols-[minmax(100px,1fr)_auto] items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              onAssign(person.crm_contact_id, value)
            }}
          >
            <Input
              aria-label={`Onboarder for ${displayName}`}
              value={value}
              placeholder="508 username"
              disabled={!hasCrmContact}
              onChange={(event) => setValue(event.target.value)}
            />
            <Button
              type="submit"
              size="sm"
              aria-label={`Save onboarder for ${displayName}`}
              disabled={!hasCrmContact || loading[`onboarder:${person.crm_contact_id}`]}
            >
              Save
            </Button>
          </form>
        </TableCell>
        <TableCell>{formatDate(person.onboarding_updated_at)}</TableCell>
        <TableCell>
          <div className="grid gap-2">
            {emailSentAt ? (
              <Badge variant="succeeded">Sent {formatDate(emailSentAt)}</Badge>
            ) : (
              <Badge variant="neutral">Not sent</Badge>
            )}
            {emailSentRecipient ? (
              <span className="text-xs text-muted-foreground">{emailSentRecipient}</span>
            ) : null}
            {emailSentBy ? (
              <span className="text-xs text-muted-foreground">By {emailSentBy}</span>
            ) : null}
            {canWrite && hasCrmContact ? (
              <Button
                type="button"
                size="sm"
                variant={emailOpen ? "outline" : "secondary"}
                onClick={() => {
                  if (emailOpen) {
                    setEmailOpen(false)
                    return
                  }
                  setEmailOpen(true)
                  if (!emailDraft) void generateDraft()
                }}
                disabled={draftBusy}
              >
                <Mail />
                {emailDraft ? "Edit draft" : "Draft email"}
              </Button>
            ) : null}
          </div>
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap gap-1.5">
            {resumeHref ? (
              <a
                className="inline-flex min-h-7 max-w-40 items-center truncate rounded-md border bg-secondary px-2 text-xs font-extrabold"
                href={resumeHref}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${displayName} resume`}
                title={resumeLabel}
              >
                {resumeLabel}
              </a>
            ) : null}
            {linkedinUrl(person.linkedin) ? (
              <a
                className="inline-flex min-h-7 items-center rounded-md border bg-secondary px-2 text-xs font-extrabold"
                href={linkedinUrl(person.linkedin)}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${displayName} LinkedIn`}
              >
                LinkedIn
              </a>
            ) : null}
            {githubUrl(person.github_username) ? (
              <a
                className="inline-flex min-h-7 items-center rounded-md border bg-secondary px-2 text-xs font-extrabold"
                href={githubUrl(person.github_username)}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${displayName} GitHub`}
              >
                {person.github_username || "GitHub"}
              </a>
            ) : null}
            {!resumeHref && !linkedinUrl(person.linkedin) && !githubUrl(person.github_username)
              ? "None"
              : null}
          </div>
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap gap-1.5">
            {gaps.map(([label]) => (
              <Badge key={String(label)} variant="missing">
                Missing {label}
              </Badge>
            ))}
            {gaps.length === 0 ? "None" : null}
          </div>
        </TableCell>
      </TableRow>
      {emailOpen ? (
        <TableRow>
          <TableCell colSpan={7} className="bg-secondary/30">
            <div className="grid gap-3 rounded-md border bg-background p-4">
              <div className="grid gap-3 md:grid-cols-[auto_minmax(150px,220px)_minmax(150px,220px)_auto] md:items-end">
                <label className="flex min-h-9 items-center gap-2 text-sm font-semibold">
                  <input
                    type="checkbox"
                    checked={emailOptions.has_contributed}
                    onChange={(event) => {
                      const next = { ...emailOptions, has_contributed: event.target.checked }
                      setEmailOptions(next)
                    }}
                  />
                  Contribution done
                </label>
                <Label>
                  Discord
                  <Select
                    value={emailOptions.discord_joined}
                    onChange={(event) =>
                      setEmailOptions({
                        ...emailOptions,
                        discord_joined: event.target.value as OnboardingEmailTriState,
                      })
                    }
                  >
                    <option value="unknown">Unknown</option>
                    <option value="yes">Joined</option>
                    <option value="no">Not joined</option>
                  </Select>
                </Label>
                <Label>
                  Agreement
                  <Select
                    value={emailOptions.agreement_signed}
                    onChange={(event) =>
                      setEmailOptions({
                        ...emailOptions,
                        agreement_signed: event.target.value as OnboardingEmailTriState,
                      })
                    }
                  >
                    <option value="unknown">Unknown</option>
                    <option value="yes">Signed</option>
                    <option value="no">Not signed</option>
                  </Select>
                </Label>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => generateDraft()}
                  disabled={draftBusy}
                >
                  <RefreshCw />
                  Regenerate
                </Button>
              </div>
              {emailDraft ? (
                <>
                  <div className="grid gap-2 text-sm md:grid-cols-4">
                    <span>
                      <strong>To:</strong> {emailDraft.recipient_email || "Missing"}
                    </span>
                    <span>
                      <strong>Reply-To:</strong> {emailDraft.reply_to_email || "Missing"}
                    </span>
                    <span>
                      <strong>Cc:</strong> {emailDraft.cc_email || "Missing"}
                    </span>
                    <span>
                      <strong>From:</strong> {emailDraft.sender_display_name || "onboarding"}
                    </span>
                  </div>
                  <Label>
                    Subject
                    <Input value={emailDraft.subject} readOnly />
                  </Label>
                  <Label>
                    Draft
                    <textarea
                      value={draftBody}
                      className="min-h-64 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm text-foreground shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
                      onChange={(event) =>
                        setEmailDraft({ ...emailDraft, markdown_body: event.target.value })
                      }
                    />
                  </Label>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="default"
                      onClick={sendDraft}
                      disabled={
                        sendBusy ||
                        !emailDraft.can_send ||
                        !draftMatchesOptions ||
                        !draftBody.trim()
                      }
                      title={sendUnavailableMessage || undefined}
                    >
                      <Send />
                      {sendBusy ? "Sending" : "Send"}
                    </Button>
                    {sendUnavailableMessage ? (
                      <span className="text-sm text-muted-foreground">
                        {sendUnavailableMessage}
                        {sendUnavailableIsSmtp && canConfigure ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="ml-2"
                            onClick={onOpenConfiguration}
                          >
                            <Settings />
                            Configure
                          </Button>
                        ) : null}
                      </span>
                    ) : null}
                    {emailDraft.marker_status === "error" ? (
                      <span className="text-sm text-muted-foreground">
                        Marker not saved: {emailDraft.marker_error || "unknown"}
                      </span>
                    ) : null}
                  </div>
                </>
              ) : (
                <span className="text-sm text-muted-foreground">
                  {draftBusy ? "Drafting email" : "No draft loaded"}
                </span>
              )}
            </div>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  )
}

function JobsView(props: {
  jobs: Job[]
  jobDetail: JobDetail | null
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  minutes: string
  status: string
  jobType: string
  jobCounts: Record<string, number>
  canWrite: boolean
  setMinutes: (value: string) => void
  setStatus: (value: string) => void
  setJobType: (value: string) => void
  onSearch: () => void
  onSort: (key: string) => void
  onDetail: (jobId: string) => void
  onRerun: (jobId: string) => void
}) {
  return (
    <>
      <Card className="grid gap-3 p-4 md:grid-cols-4 md:items-end">
        <Label>
          Window
          <Select
            id="minutes"
            value={props.minutes}
            onChange={(event) => props.setMinutes(event.target.value)}
          >
            <option value="15">15 minutes</option>
            <option value="60">1 hour</option>
            <option value="360">6 hours</option>
            <option value="1440">24 hours</option>
          </Select>
        </Label>
        <Label>
          Status
          <Select
            id="status"
            value={props.status}
            onChange={(event) => props.setStatus(event.target.value)}
          >
            <option value="">Any status</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="dead">Dead</option>
            <option value="canceled">Canceled</option>
          </Select>
        </Label>
        <Label>
          Type
          <Input
            id="jobType"
            value={props.jobType}
            autoComplete="off"
            placeholder="Any type"
            onChange={(event) => props.setJobType(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && props.onSearch()}
          />
        </Label>
        <Button
          id="refreshJobs"
          type="button"
          onClick={props.onSearch}
          disabled={props.loading.jobs}
        >
          <RefreshCw />
          Refresh background tasks
        </Button>
      </Card>

      <section className="grid gap-3 md:grid-cols-4" aria-label="Background task summary">
        <Metric id="metricTotal" label="Total" value={props.jobs.length} />
        <Metric id="metricQueued" label="Queued" value={props.jobCounts.queued || 0} />
        <Metric id="metricRunning" label="Running" value={props.jobCounts.running || 0} />
        <Metric
          id="metricFailed"
          label="Failed"
          value={(props.jobCounts.failed || 0) + (props.jobCounts.dead || 0)}
        />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Recent background tasks</CardTitle>
        </CardHeader>
        <Empty hidden={props.jobs.length !== 0}>No background tasks match these filters.</Empty>
        <div className="overflow-x-auto">
          <Table
            id="jobsTable"
            className={cn("min-w-[980px]", props.jobs.length === 0 && "hidden")}
            aria-label="Recent background tasks"
          >
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  className="w-[22%]"
                  label="Task id"
                  scope="jobs"
                  sort={props.sort}
                  sortKey="job_id"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[24%]"
                  label="Task type"
                  scope="jobs"
                  sort={props.sort}
                  sortKey="type"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[12%]"
                  label="Status"
                  scope="jobs"
                  sort={props.sort}
                  sortKey="status"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[12%]"
                  label="Attempts"
                  scope="jobs"
                  sort={props.sort}
                  sortKey="attempts"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[18%]"
                  label="Updated"
                  scope="jobs"
                  sort={props.sort}
                  sortKey="updated_at"
                  onSort={(_, key) => props.onSort(key)}
                />
                <TableHead>Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody id="jobsBody">
              {props.jobs.map((job) => (
                <TableRow key={job.job_id}>
                  <TableCell className="font-mono">{job.job_id}</TableCell>
                  <TableCell>{job.type}</TableCell>
                  <TableCell>
                    <Badge variant={(job.status as Tone) || "neutral"}>{job.status}</Badge>
                  </TableCell>
                  <TableCell>
                    {job.attempts}/{job.max_attempts}
                  </TableCell>
                  <TableCell>{formatDate(job.updated_at)}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap justify-end gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        aria-label={`View details for ${job.type} task ${job.job_id}`}
                        onClick={() => props.onDetail(job.job_id)}
                        disabled={props.loading[`detail:${job.job_id}`]}
                      >
                        Details
                      </Button>
                      {props.canWrite ? (
                        <Button
                          type="button"
                          size="sm"
                          aria-label={`Rerun ${job.type} task ${job.job_id}`}
                          onClick={() => props.onRerun(job.job_id)}
                          disabled={props.loading[`rerun:${job.job_id}`]}
                        >
                          Rerun
                        </Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>

      {props.jobDetail ? (
        <Card id="jobDetailPanel">
          <CardHeader>
            <CardTitle>Task detail</CardTitle>
            <span className="text-sm text-muted-foreground">{props.jobDetail.job_id}</span>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              {[
                ["Task type", props.jobDetail.type],
                ["Status", props.jobDetail.status],
                ["Attempts", `${props.jobDetail.attempts}/${props.jobDetail.max_attempts}`],
                ["Updated", formatDate(props.jobDetail.updated_at)],
                ["Created", formatDate(props.jobDetail.created_at)],
                ["Run after", formatDate(props.jobDetail.run_after)],
                ["Locked by", props.jobDetail.locked_by || "None"],
                ["Last error", props.jobDetail.last_error || "None"],
              ].map(([label, value]) => (
                <div key={label} className="grid gap-1 rounded-md border bg-background p-3">
                  <span className="text-[11px] font-extrabold uppercase text-muted-foreground">
                    {label}
                  </span>
                  <strong className="break-words text-sm">{value}</strong>
                </div>
              ))}
            </div>
            <div>
              <h2 className="mb-2 text-[15px] font-bold">Payload</h2>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-3 font-mono text-xs">
                {jsonPreview(props.jobDetail.payload) || "No payload"}
              </pre>
            </div>
            <div>
              <h2 className="mb-2 text-[15px] font-bold">Result</h2>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-3 font-mono text-xs">
                {jsonPreview(props.jobDetail.result) || "No result"}
              </pre>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </>
  )
}

function AuditView(props: {
  events: AuditEvent[]
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  onRefresh: () => void
  onSort: (key: string) => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent audit</CardTitle>
        <Button
          id="refreshAudit"
          type="button"
          variant="outline"
          onClick={props.onRefresh}
          disabled={props.loading.audit}
        >
          <RefreshCw />
          Refresh
        </Button>
      </CardHeader>
      <Empty hidden={props.events.length !== 0}>No audit events found.</Empty>
      <div className="overflow-x-auto">
        <Table
          id="auditTable"
          className={cn("min-w-[760px]", props.events.length === 0 && "hidden")}
          aria-label="Recent audit events"
        >
          <TableHeader>
            <TableRow>
              <SortableTableHead
                className="w-[24%]"
                label="Time"
                scope="audit"
                sort={props.sort}
                sortKey="occurred_at"
                onSort={(_, key) => props.onSort(key)}
              />
              <SortableTableHead
                className="w-[28%]"
                label="Actor"
                scope="audit"
                sort={props.sort}
                sortKey="actor"
                onSort={(_, key) => props.onSort(key)}
              />
              <SortableTableHead
                className="w-[28%]"
                label="Action"
                scope="audit"
                sort={props.sort}
                sortKey="action"
                onSort={(_, key) => props.onSort(key)}
              />
              <SortableTableHead
                className="w-[20%]"
                label="Result"
                scope="audit"
                sort={props.sort}
                sortKey="result"
                onSort={(_, key) => props.onSort(key)}
              />
            </TableRow>
          </TableHeader>
          <TableBody id="auditBody">
            {props.events.map((event) => (
              <TableRow
                key={
                  event.id ||
                  `${event.occurred_at || ""}-${event.actor_subject || ""}-${event.action || ""}`
                }
              >
                <TableCell>{formatDate(event.occurred_at)}</TableCell>
                <TableCell>
                  {event.actor_display_name || event.actor_subject || event.actor_provider}
                </TableCell>
                <TableCell>{event.action}</TableCell>
                <TableCell>
                  <Badge variant={event.result === "success" ? "succeeded" : "failed"}>
                    {event.result}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Card>
  )
}

function AgentView({
  report,
  loading,
  onRefresh,
}: {
  report: AgentReport | null
  loading: Record<string, boolean>
  onRefresh: () => void
}) {
  const summary = report?.summary || {}
  const breakdownRows = (
    [
      ["Status", report?.status_counts || {}],
      ["Intent", report?.intent_counts || {}],
      ["Planner", report?.planner_counts || {}],
      ["Model", report?.model_counts || {}],
      ["Action", report?.action_counts || {}],
      ["Tool outcome", report?.tool_outcome_counts || {}],
    ] as const
  )
    .flatMap(([label, counts]) =>
      Object.entries(counts).map(([value, count]) => ({ label, value, count })),
    )
    .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label))
  const unsupported = Array.isArray(report?.recent_unsupported) ? report.recent_unsupported : []
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Agent requests</CardTitle>
          <Button
            id="refreshAgent"
            type="button"
            variant="outline"
            onClick={onRefresh}
            disabled={loading.agent}
          >
            <RefreshCw />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-5">
          <Metric id="agentMetricTotal" label="Total" value={summary.total || 0} />
          <Metric id="agentMetricHandled" label="Handled" value={summary.handled || 0} />
          <Metric
            id="agentMetricConfirmations"
            label="Confirmations"
            value={summary.requires_confirmation || 0}
          />
          <Metric
            id="agentMetricClarifications"
            label="Clarifications"
            value={summary.needs_clarification || 0}
          />
          <Metric
            id="agentMetricUnsupported"
            label="Not understood"
            value={summary.unsupported || 0}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Request mix</CardTitle>
          <span className="text-sm text-muted-foreground">Recent agent.request audit events.</span>
        </CardHeader>
        <Empty hidden={breakdownRows.length !== 0}>No agent request data found.</Empty>
        <div className="overflow-x-auto">
          <Table
            id="agentBreakdownTable"
            className={cn("min-w-[860px]", breakdownRows.length === 0 && "hidden")}
            aria-label="Agent request breakdown"
          >
            <TableHeader>
              <TableRow>
                <TableHead>Dimension</TableHead>
                <TableHead>Value</TableHead>
                <TableHead>Count</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody id="agentBreakdownBody">
              {breakdownRows.map((item) => (
                <TableRow key={`${item.label}-${item.value}`}>
                  <TableCell>{item.label}</TableCell>
                  <TableCell>{item.value}</TableCell>
                  <TableCell>{item.count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Not understood</CardTitle>
          <span className="text-sm text-muted-foreground">Sanitized request text only.</span>
        </CardHeader>
        <Empty hidden={unsupported.length !== 0}>No unsupported agent requests found.</Empty>
        <div className="overflow-x-auto">
          <Table
            id="agentUnsupportedTable"
            className={cn("min-w-[860px]", unsupported.length === 0 && "hidden")}
            aria-label="Unsupported agent requests"
          >
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Message</TableHead>
                <TableHead>Result</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody id="agentUnsupportedBody">
              {unsupported.map((event) => (
                <TableRow
                  key={`${event.occurred_at || ""}-${event.actor || ""}-${event.message_sanitized || ""}`}
                >
                  <TableCell>{formatDate(event.occurred_at)}</TableCell>
                  <TableCell>{event.actor}</TableCell>
                  <TableCell>{event.message_sanitized}</TableCell>
                  <TableCell>
                    <Badge variant={event.result === "success" ? "succeeded" : "failed"}>
                      {event.result || "unknown"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  )
}

const root = document.getElementById("root")

if (!root) {
  if (import.meta.env.MODE !== "test") {
    throw new Error("Missing #root container")
  }
} else {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

export type { ConfigurationItem } from "@/views/configuration-view"
export { ConfigurationView } from "@/views/configuration-view"
