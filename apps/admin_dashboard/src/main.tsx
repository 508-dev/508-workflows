import {
  ArrowLeft,
  Bell,
  BriefcaseBusiness,
  ClipboardList,
  ExternalLink,
  FileClock,
  FolderKanban,
  LogOut,
  RefreshCw,
  Search,
  ShieldCheck,
  UserPlus,
  Users,
  X,
} from "lucide-react"
import { StrictMode, useEffect, useMemo, useRef, useState } from "react"
import { createRoot } from "react-dom/client"

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
  jsonPreview,
  labelForOnboardingState,
  linkedinUrl,
  onboardingStateValue,
  type Tone,
  toneForOnboardingState,
} from "@/dashboard-utils"
import { cn } from "@/lib/utils"
import "./index.css"

type View = "people" | "gigs" | "projects" | "onboarding" | "jobs" | "agent" | "audit"
type SortDirection = "asc" | "desc"

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

type Person = {
  crm_contact_id?: string
  name?: string
  email?: string
  email_508?: string
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
  sync_status?: string
  profile_status?: ProfileStatus
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
  last_name?: string
  country?: string
  department?: string
  gender?: string
  date_of_birth?: string
  create_user_permission?: boolean
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
  jobs: "/dashboard/jobs",
  agent: "/dashboard/agent",
  audit: "/dashboard/audit",
}

const routePermissions: Record<View, string> = {
  people: "people:read",
  gigs: "gigs:read",
  projects: "projects:read",
  onboarding: "onboarding:read",
  jobs: "jobs:read",
  agent: "audit:read",
  audit: "audit:read",
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

function sortValue(scope: View, item: Job | Person | Gig | Project | AuditEvent, key: string) {
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
  return (item as Record<string, unknown>)[key] ?? ""
}

function sortItems<T extends Job | Person | Gig | Project | AuditEvent>(
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

function SortButton({
  label,
  scope,
  sort,
  sortKey,
  onSort,
}: {
  label: string
  scope: View
  sort: { key: string; direction: SortDirection }
  sortKey: string
  onSort: (scope: View, key: string) => void
}) {
  const active = sort.key === sortKey
  const arrow = sort.direction === "asc" ? "↑" : "↓"
  return (
    <button
      type="button"
      data-sort-scope={scope}
      data-sort-key={sortKey}
      className="text-left font-[inherit] text-inherit hover:text-foreground"
      onClick={() => onSort(scope, sortKey)}
    >
      {active ? `${label} ${arrow}` : label}
    </button>
  )
}

function SortableTableHead({
  className,
  label,
  scope,
  sort,
  sortKey,
  onSort,
}: {
  className?: string
  label: string
  scope: View
  sort: { key: string; direction: SortDirection }
  sortKey: string
  onSort: (scope: View, key: string) => void
}) {
  const ariaSort =
    sort.key === sortKey ? (sort.direction === "asc" ? "ascending" : "descending") : "none"
  return (
    <TableHead className={className} aria-sort={ariaSort}>
      <SortButton label={label} scope={scope} sort={sort} sortKey={sortKey} onSort={onSort} />
    </TableHead>
  )
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

function Empty({ children, hidden }: { children: string; hidden: boolean }) {
  if (hidden) return null
  return <div className="px-4 py-7 text-center text-sm text-muted-foreground">{children}</div>
}

function App() {
  const initialProjectDetailId = detailIdFromPath("projects")
  const [user, setUser] = useState<User | null>(null)
  const [view, setViewState] = useState<View>(viewFromPath())
  const [toast, setToast] = useState<{ message: string; tone?: "ok" | "error" }>({
    message: "",
  })
  const [permissions, setPermissions] = useState<string[]>([])
  const [crmBaseUrl, setCrmBaseUrl] = useState("")
  const [jobs, setJobs] = useState<Job[]>([])
  const [gigs, setGigs] = useState<Gig[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [projectsSummary, setProjectsSummary] = useState<ProjectsResponse["summary"]>({})
  const [wikiMatches, setWikiMatches] = useState<WikiMatchPreview | null>(null)
  const [gigDetail, setGigDetail] = useState<Gig | null>(null)
  const [selectedGigId, setSelectedGigId] = useState(detailIdFromPath())
  const [selectedProjectId, setSelectedProjectId] = useState(initialProjectDetailId)
  const [notifications, setNotifications] = useState<DashboardNotification[]>([])
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [people, setPeople] = useState<Person[]>([])
  const [onboarding, setOnboarding] = useState<Person[]>([])
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([])
  const [agentReport, setAgentReport] = useState<AgentReport | null>(null)
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
    jobs: { key: "updated_at", direction: "desc" },
    people: { key: "name", direction: "asc" },
    agent: { key: "occurred_at", direction: "desc" },
    audit: { key: "occurred_at", direction: "desc" },
  })

  const [minutes, setMinutes] = useState("60")
  const [status, setStatus] = useState("")
  const [jobType, setJobType] = useState("")
  const [gigStatus, setGigStatus] = useState("")
  const [gigLimit, setGigLimit] = useState(100)
  const [projectQuery, setProjectQuery] = useState("")
  const [projectStatus, setProjectStatus] = useState(initialProjectDetailId ? "" : "Open")
  const [staleRecruitingDays, setStaleRecruitingDays] = useState(7)
  const [peopleQuery, setPeopleQuery] = useState("")
  const [peopleMember, setPeopleMember] = useState("")
  const [peopleFilters, setPeopleFilters] = useState<FilterState>({})
  const [peopleFilterKind, setPeopleFilterKind] = useState<PeopleFilterKey>("discord")
  const [peopleFilterValue, setPeopleFilterValue] = useState("linked")
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

  function canView(nextView: View) {
    return can(routePermissions[nextView])
  }

  function firstAllowedView() {
    return (Object.keys(routes) as View[]).find((candidate) => canView(candidate)) || "people"
  }

  function showToast(message: string, tone?: "ok" | "error") {
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
    setViewState(normalized)
    if (push) {
      window.history.pushState({ view: normalized }, "", routes[normalized])
    } else if (!Object.hasOwn(routes, rawViewFromPath()) || normalized !== nextView) {
      window.history.replaceState({ view: normalized }, "", routes[normalized])
    }
  }
  navigateRef.current = navigate

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

  function gigsUrl() {
    const params = new URLSearchParams({ limit: String(gigLimit) })
    if (gigStatus) params.set("status", gigStatus)
    return `/dashboard/api/gigs?${params.toString()}`
  }

  function projectsUrl() {
    const params = new URLSearchParams({ limit: "100", status: projectStatus })
    if (projectQuery.trim()) params.set("query", projectQuery.trim())
    return `/dashboard/api/projects?${params.toString()}`
  }

  async function loadJobs() {
    setBusy("jobs", true)
    showToast("Loading jobs")
    try {
      const payload = await requestJson<Job[]>(jobsUrl())
      setJobs(payload)
      showToast(`Loaded ${payload.length} jobs`, "ok")
    } catch (error) {
      showError(error, "Unable to load jobs")
    } finally {
      setBusy("jobs", false)
    }
  }

  async function loadGigs() {
    setBusy("gigs", true)
    try {
      const payload = await requestJson<Gig[]>(gigsUrl())
      setGigs(payload)
      showToast(`Loaded ${payload.length} gig${payload.length === 1 ? "" : "s"}`, "ok")
      void loadNotifications()
    } catch (error) {
      showError(error, "Unable to load gigs")
    } finally {
      setBusy("gigs", false)
    }
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
      const payload = await requestJson<{ job_id: string }>("/dashboard/api/sync/projects", {
        method: "POST",
      })
      showToast(`Queued project sync ${payload.job_id}`, "ok")
    } catch (error) {
      showError(error, "Unable to queue project sync")
    } finally {
      setBusy("syncProjects", false)
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
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) {
    const normalizedUser = userName.trim()
    if (!normalizedUser) return false
    setBusy(`project:${projectId}:user`, true)
    try {
      const payload = await requestJson<{ project: Project; activity_cost?: object | null }>(
        `/dashboard/api/projects/${encodeURIComponent(projectId)}/users`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user: normalizedUser, ...(rates || {}) }),
        },
      )
      setProjects((current) =>
        current.map((project) => (project.id === projectId ? payload.project : project)),
      )
      showToast(payload.activity_cost ? "Added project user and rate" : "Added project user", "ok")
      return true
    } catch (error) {
      showError(error, "Unable to add project user")
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
    if (selectedGigId) await loadGigDetail(selectedGigId)
  }

  async function loadNotifications() {
    if (!can("gigs:read")) return
    setBusy("notifications", true)
    try {
      const payload = await requestJson<DashboardNotificationsResponse>(
        "/dashboard/api/notifications?limit=20",
      )
      setStaleRecruitingDays(payload.stale_days || 7)
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
      showError(error, "Unable to load job detail")
    } finally {
      setBusy(`detail:${jobId}`, false)
    }
  }

  async function rerunJob(jobId: string) {
    setBusy(`rerun:${jobId}`, true)
    showToast(`Rerunning ${jobId}`)
    try {
      const payload = await requestJson<{ job_id: string }>(
        `/dashboard/api/jobs/${encodeURIComponent(jobId)}/rerun`,
        { method: "POST" },
      )
      showToast(`Queued rerun ${payload.job_id}`, "ok")
      await loadJobs()
    } catch (error) {
      showError(error, "Unable to rerun job")
    } finally {
      setBusy(`rerun:${jobId}`, false)
    }
  }

  async function syncPeople() {
    setBusy("syncPeople", true)
    showToast("Queueing people sync")
    try {
      const payload = await requestJson<{ job_id: string }>("/dashboard/api/sync/people", {
        method: "POST",
      })
      showToast(`Queued people sync ${payload.job_id}`, "ok")
    } catch (error) {
      showError(error, "Unable to queue people sync")
    } finally {
      setBusy("syncPeople", false)
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
        const payload = error.payload as { matches?: Array<{ label?: string; email?: string }> }
        const matches = payload.matches || []
        const matchLabel = matches
          .map((match) => match.label || match.email)
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
    if (view === "gigs") void loadGigs()
    if (view === "projects") void loadProjects()
    if (view === "onboarding") void loadOnboarding()
    if (view === "jobs") void loadJobs()
    if (view === "agent") void loadAgentReport()
    if (view === "audit") void loadAuditEvents()
  }, [view])

  // biome-ignore lint/correctness/useExhaustiveDependencies: permission loading is the first authorized data fetch for the current view.
  useEffect(() => {
    if (permissions.length === 0) return
    if (can("gigs:read")) void loadNotifications()
    if (view === "people") void loadPeople()
    if (view === "gigs") void loadGigs()
    if (view === "projects") void loadProjects()
    if (view === "onboarding") void loadOnboarding()
    if (view === "jobs") void loadJobs()
    if (view === "agent") void loadAgentReport()
    if (view === "audit") void loadAuditEvents()
  }, [permissions])

  // biome-ignore lint/correctness/useExhaustiveDependencies: jobs reload intentionally follows filter changes only while jobs is active.
  useEffect(() => {
    if (view === "jobs" && permissions.length > 0) void loadJobs()
  }, [minutes, status])

  // biome-ignore lint/correctness/useExhaustiveDependencies: gigs reload intentionally follows status filter changes only while gigs is active.
  useEffect(() => {
    if (view === "gigs" && permissions.length > 0) void loadGigs()
  }, [gigStatus, gigLimit])

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
    if (notification.type === "stale_recruiting_gig") {
      setGigStatus("recruiting")
      navigate("gigs", true)
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
              ["jobs", "Jobs", BriefcaseBusiness],
              ["agent", "Agent", ShieldCheck],
              ["audit", "Audit", FileClock],
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
              canSync={can("people:sync")}
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

          {view === "gigs" ? (
            <GigsView
              gigs={sortedGigs}
              selectedGig={selectedGig}
              selectedGigId={selectedGigId}
              sort={sort.gigs}
              loading={loading}
              status={gigStatus}
              limit={gigLimit}
              staleDays={staleRecruitingDays}
              canWrite={can("gigs:write")}
              crmContactUrl={crmContactUrl}
              crmAttachmentUrl={crmAttachmentUrl}
              setStatus={setGigStatus}
              setLimit={setGigLimit}
              onRefresh={refreshGigsView}
              onSort={(key) => handleSort("gigs", key)}
              onOpenGig={openGigDetail}
              onCloseGig={closeGigDetail}
              onUpdateStatus={updateGigStatus}
              onUpdateApplicationStatus={updateGigApplicationStatus}
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
              canSync={can("projects:sync")}
              canWrite={can("projects:write")}
              crmContactUrl={crmContactUrl}
              setQuery={setProjectQuery}
              setStatus={setProjectStatus}
              onSearch={loadProjects}
              onSync={syncProjects}
              onUpdateStatus={updateProjectStatus}
              onBulkUpdate={bulkUpdateProjects}
              onAddUser={addProjectUser}
              onAddHistoricalMember={addHistoricalProjectMember}
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
              canWrite={can("jobs:write")}
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

function DashboardToast({ toast }: { toast: { message: string; tone?: "ok" | "error" } }) {
  if (!toast.message) return null
  return (
    <div
      id="toast"
      role="status"
      className={cn(
        "fixed bottom-5 right-5 z-50 max-w-sm rounded-md border bg-background px-4 py-3 text-sm font-semibold shadow-lg",
        toast.tone === "ok" && "border-emerald-500/40 text-emerald-300",
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

const gigStatuses = ["recruiting", "filled", "unknown", "lost", "outdated"] as const
const applicationStatuses = [
  "suggested",
  "interested",
  "reviewing",
  "contacted",
  "accepted",
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

function staleRecruitingAge(gig: Gig, staleDays: number) {
  if (gig.status !== "recruiting") return null
  const latestActivity = gigActivityTimestamp(gig)
  const age = daysSince(latestActivity)
  if (age === null || age < staleDays) return null
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
  onUpdateStatus: (projectId: string, status: string) => void
  onBulkUpdate: (
    projectIds: string[],
    updates: { status?: string; project_type?: string },
  ) => Promise<boolean>
  onAddUser: (
    projectId: string,
    user: string,
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) => Promise<boolean>
  onAddHistoricalMember: (
    projectId: string,
    person: string,
    candidateId?: string,
  ) => Promise<boolean>
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
          onAddHistoricalMember={props.onAddHistoricalMember}
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
          <Button
            type="button"
            disabled={selectedVisibleProjectIds.length === 0}
            onClick={() => setBulkModalOpen(true)}
          >
            Bulk edit
          </Button>
        </Card>
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

function ProjectRosterAddForm({
  projectId,
  loading,
  onAddUser,
  onAddHistoricalMember,
}: {
  projectId: string
  loading: Record<string, boolean>
  onAddUser: (
    projectId: string,
    user: string,
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) => Promise<boolean>
  onAddHistoricalMember: (
    projectId: string,
    person: string,
    candidateId?: string,
  ) => Promise<boolean>
}) {
  const [newUser, setNewUser] = useState("")
  const [activityType, setActivityType] = useState("")
  const [billingRate, setBillingRate] = useState("")
  const [costingRate, setCostingRate] = useState("")
  const normalizedUser = newUser.trim()
  const hasRateFields = Boolean(activityType.trim() || billingRate.trim() || costingRate.trim())
  const parsedBillingRate = optionalNumber(billingRate)
  const parsedCostingRate = optionalNumber(costingRate)
  const hasPartialRate = Boolean((billingRate.trim() || costingRate.trim()) && !activityType.trim())
  const hasIncompleteRate = Boolean(
    activityType.trim() && (!billingRate.trim() || !costingRate.trim()),
  )
  const rateInvalid =
    Boolean(billingRate.trim() && parsedBillingRate === undefined) ||
    Boolean(costingRate.trim() && parsedCostingRate === undefined) ||
    hasPartialRate ||
    hasIncompleteRate

  async function submitEngineer() {
    const updated = await onAddUser(
      projectId,
      newUser,
      hasRateFields
        ? {
            activity_type: activityType.trim(),
            billing_rate: parsedBillingRate,
            costing_rate: parsedCostingRate,
          }
        : undefined,
    )
    if (updated) {
      setNewUser("")
      setActivityType("")
      setBillingRate("")
      setCostingRate("")
    }
  }

  return (
    <form
      className="grid gap-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (!rateInvalid) void submitEngineer()
      }}
    >
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(180px,.7fr)_minmax(130px,.45fr)_minmax(130px,.45fr)]">
        <Label>
          ERP user
          <Input
            value={newUser}
            autoComplete="off"
            placeholder="engineer@508.dev"
            onChange={(event) => setNewUser(event.target.value)}
          />
        </Label>
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
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="submit"
          variant="outline"
          disabled={loading[`project:${projectId}:user`] || !normalizedUser || rateInvalid}
        >
          <Users />
          Add engineer
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={loading[`project:${projectId}:historical`] || !normalizedUser}
          onClick={() =>
            void onAddHistoricalMember(projectId, newUser).then((updated) => {
              if (updated) setNewUser("")
            })
          }
        >
          <Users />
          Add historical
        </Button>
      </div>
    </form>
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
    rates?: { activity_type?: string; billing_rate?: number; costing_rate?: number },
  ) => Promise<boolean>
  onAddHistoricalMember: (
    projectId: string,
    person: string,
    candidateId?: string,
  ) => Promise<boolean>
}) {
  const project = props.project
  const members = project.roster_members || []
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
          <CardContent className="grid gap-4 border-b">
            <ProjectRosterAddForm
              projectId={project.id}
              loading={props.loading}
              onAddUser={props.onAddUser}
              onAddHistoricalMember={props.onAddHistoricalMember}
            />
          </CardContent>
        ) : null}
        <div className="overflow-x-auto">
          <Table className="min-w-[760px]" aria-label="Project roster">
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>ERP user</TableHead>
                <TableHead>Links</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Last seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.length ? (
                members.map((member) => (
                  <TableRow key={`${member.source || ""}:${member.source_user_id || member.email}`}>
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
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={6} className="text-sm text-muted-foreground">
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

function GigsView(props: {
  gigs: Gig[]
  selectedGig: Gig | null
  selectedGigId: string
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  status: string
  limit: number
  staleDays: number
  canWrite: boolean
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
  setStatus: (value: string) => void
  setLimit: (value: number) => void
  onRefresh: () => void
  onSort: (key: string) => void
  onOpenGig: (gigId: string) => void
  onCloseGig: () => void
  onUpdateStatus: (gigId: string, status: string) => void
  onUpdateApplicationStatus: (gigId: string, applicationId: string, status: string) => void
}) {
  const counts = props.gigs.reduce(
    (acc, gig) => {
      acc.total += 1
      acc.applications += Number(gig.application_count || 0)
      acc.interested += Number(gig.interested_count || 0)
      if (staleRecruitingAge(gig, props.staleDays) !== null) acc.stale += 1
      return acc
    },
    { total: 0, applications: 0, interested: 0, stale: 0 },
  )
  const filterBar = (
    <Card className="grid gap-3 p-4 md:grid-cols-[minmax(160px,1fr)_auto_auto] md:items-end">
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
      <Button
        id="refreshGigs"
        type="button"
        onClick={props.onRefresh}
        disabled={props.loading.gigs}
      >
        <RefreshCw />
        Refresh gigs
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
    </Card>
  )

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
          gig={props.selectedGig}
          loading={props.loading}
          canWrite={props.canWrite}
          crmContactUrl={props.crmContactUrl}
          crmAttachmentUrl={props.crmAttachmentUrl}
          staleDays={props.staleDays}
          onBack={props.onCloseGig}
          onUpdateStatus={props.onUpdateStatus}
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
        <Metric id="gigMetricStale" label="Stale recruiting" value={counts.stale} />
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
              onOpenGig={props.onOpenGig}
              onUpdateStatus={props.onUpdateStatus}
            />
          ))}
        </div>
      </Card>
    </>
  )
}

function GigListItem({
  gig,
  loading,
  canWrite,
  onOpenGig,
  onUpdateStatus,
  staleDays,
}: {
  gig: Gig
  loading: Record<string, boolean>
  canWrite: boolean
  onOpenGig: (gigId: string) => void
  onUpdateStatus: (gigId: string, status: string) => void
  staleDays: number
}) {
  const applications = Array.isArray(gig.applications) ? gig.applications : []
  const threadUrl =
    gig.discord_guild_id && gig.discord_thread_id
      ? `https://discord.com/channels/${encodeURIComponent(
          gig.discord_guild_id,
        )}/${encodeURIComponent(gig.discord_thread_id)}`
      : ""
  const staleAge = staleRecruitingAge(gig, staleDays)
  return (
    <article className="grid gap-4 rounded-md border bg-background p-4 lg:grid-cols-[minmax(0,1fr)_220px_180px] lg:items-start">
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
              gig.status === "filled" ? "succeeded" : gig.status === "lost" ? "failed" : "queued"
            }
          >
            {gig.status_label || titleCase(gig.status)}
          </Badge>
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

function GigDetailPage({
  gig,
  loading,
  canWrite,
  crmContactUrl,
  crmAttachmentUrl,
  staleDays,
  onBack,
  onUpdateStatus,
  onUpdateApplicationStatus,
}: {
  gig: Gig
  loading: Record<string, boolean>
  canWrite: boolean
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
  staleDays: number
  onBack: () => void
  onUpdateStatus: (gigId: string, status: string) => void
  onUpdateApplicationStatus: (gigId: string, applicationId: string, status: string) => void
}) {
  const applications = Array.isArray(gig.applications) ? gig.applications : []
  const threadUrl =
    gig.discord_guild_id && gig.discord_thread_id
      ? `https://discord.com/channels/${encodeURIComponent(
          gig.discord_guild_id,
        )}/${encodeURIComponent(gig.discord_thread_id)}`
      : ""
  const staleAge = staleRecruitingAge(gig, staleDays)
  return (
    <div className="grid gap-5">
      <Card>
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
                      : gig.status === "lost"
                        ? "failed"
                        : "queued"
                  }
                >
                  {gig.status_label || titleCase(gig.status)}
                </Badge>
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
                      {resumeUrl ? (
                        <a
                          className="inline-flex min-h-7 items-center rounded-md border bg-secondary px-2 text-xs font-extrabold"
                          href={resumeUrl}
                          target="_blank"
                          rel="noreferrer"
                          aria-label={`Open ${displayName} resume`}
                        >
                          Resume
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
  onSetupEngineer: (payload: EngineerSetupRequest) => Promise<EngineerSetupResult | null>
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
              <option value="pending">Needs review</option>
              <option value="selected">Assigned to onboarder</option>
              <option value="reachingout">Reaching out</option>
              <option value="awaitingcontribution">Awaiting contribution</option>
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
            className={cn("min-w-[1180px]", props.people.length === 0 && "hidden")}
            aria-label="Onboarding queue"
          >
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  className="w-[20%]"
                  label="Name"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="name"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[13%]"
                  label="Status"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="onboarding_state"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[22%]"
                  label="Onboarder"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="onboarder"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[13%]"
                  label="Updated"
                  scope="onboarding"
                  sort={props.sort}
                  sortKey="updated"
                  onSort={(_, key) => props.onSort(key)}
                />
                <TableHead className="w-[15%]">Links</TableHead>
                <SortableTableHead
                  className="w-[17%]"
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
                  key={person.crm_contact_id || person.name}
                  person={person}
                  loading={props.loading}
                  onAssign={props.onAssign}
                  crmContactUrl={props.crmContactUrl}
                  crmAttachmentUrl={props.crmAttachmentUrl}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </>
  )
}

function EngineerSetupPanel({
  loading,
  onSetup,
}: {
  loading?: boolean
  onSetup: (payload: EngineerSetupRequest) => Promise<EngineerSetupResult | null>
}) {
  const [email, setEmail] = useState("")
  const [fullName, setFullName] = useState("")
  const [country, setCountry] = useState("")
  const [department, setDepartment] = useState("")
  const [gender, setGender] = useState("")
  const [dateOfBirth, setDateOfBirth] = useState("")
  const [createUserPermission, setCreateUserPermission] = useState(true)

  async function submit() {
    const normalizedName = fullName.trim()
    const [first, ...rest] = normalizedName.split(/\s+/)
    const result = await onSetup({
      email,
      first_name: first || normalizedName,
      last_name: rest.join(" "),
      country,
      department,
      gender,
      date_of_birth: dateOfBirth,
      create_user_permission: createUserPermission,
    })
    if (result) {
      setEmail("")
      setFullName("")
      setCountry("")
      setDepartment("")
      setGender("")
      setDateOfBirth("")
      setCreateUserPermission(true)
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
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(130px,.6fr)_minmax(140px,.7fr)]">
            <Label>
              508 email
              <Input
                value={email}
                autoComplete="off"
                placeholder="engineer@508.dev"
                onChange={(event) => setEmail(event.target.value)}
              />
            </Label>
            <Label>
              Name
              <Input
                value={fullName}
                autoComplete="off"
                placeholder="First Last"
                onChange={(event) => setFullName(event.target.value)}
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
            <Label>
              Department
              <Input
                value={department}
                autoComplete="off"
                placeholder="Optional"
                onChange={(event) => setDepartment(event.target.value)}
              />
            </Label>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <Label>
              Gender
              <Input
                value={gender}
                autoComplete="off"
                placeholder="Optional"
                onChange={(event) => setGender(event.target.value)}
              />
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
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex min-h-9 items-center gap-2 text-sm font-semibold">
              <input
                type="checkbox"
                checked={createUserPermission}
                onChange={(event) => setCreateUserPermission(event.target.checked)}
              />
              Create User Permission
            </label>
            <Button
              id="setupEngineer"
              type="submit"
              disabled={loading || !email.trim() || !fullName.trim() || !country.trim()}
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
  onAssign,
  crmContactUrl,
  crmAttachmentUrl,
}: {
  person: Person
  loading: Record<string, boolean>
  onAssign: (contactId: string | undefined, onboarder: string) => void
  crmContactUrl: (contactId?: string) => string
  crmAttachmentUrl: (attachmentId?: string) => string
}) {
  const displayName = person.name || person.email_508 || person.email || "CRM contact"
  const [value, setValue] = useState(displayOnboarder(person.onboarder))
  useEffect(() => setValue(displayOnboarder(person.onboarder)), [person.onboarder])
  const status = person.profile_status || {}
  const gaps = [
    ["Discord", status.discord_linked],
    ["Resume", status.latest_resume],
    ["Skills", Number(status.skills_count || 0) > 0],
  ].filter(([, ok]) => !ok)
  const contactUrl = crmContactUrl(person.crm_contact_id)
  const resumeUrl = crmAttachmentUrl(person.latest_resume_id)
  return (
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
      </TableCell>
      <TableCell>
        <Badge variant={toneForOnboardingState(onboardingStateValue(person))}>
          {person.onboarding_status_label || labelForOnboardingState(onboardingStateValue(person))}
        </Badge>
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
            onChange={(event) => setValue(event.target.value)}
          />
          <Button
            type="submit"
            size="sm"
            aria-label={`Save onboarder for ${displayName}`}
            disabled={loading[`onboarder:${person.crm_contact_id}`]}
          >
            Save
          </Button>
        </form>
      </TableCell>
      <TableCell>{formatDate(person.onboarding_updated_at)}</TableCell>
      <TableCell>
        <div className="flex flex-wrap gap-1.5">
          {resumeUrl ? (
            <a
              className="inline-flex min-h-7 items-center rounded-md border bg-secondary px-2 text-xs font-extrabold"
              href={resumeUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${displayName} resume`}
            >
              Resume
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
          {!resumeUrl && !linkedinUrl(person.linkedin) && !githubUrl(person.github_username)
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
          Refresh jobs
        </Button>
      </Card>

      <section className="grid gap-3 md:grid-cols-4" aria-label="Job summary">
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
          <CardTitle>Recent jobs</CardTitle>
        </CardHeader>
        <Empty hidden={props.jobs.length !== 0}>No jobs match these filters.</Empty>
        <div className="overflow-x-auto">
          <Table
            id="jobsTable"
            className={cn("min-w-[980px]", props.jobs.length === 0 && "hidden")}
            aria-label="Recent jobs"
          >
            <TableHeader>
              <TableRow>
                <SortableTableHead
                  className="w-[22%]"
                  label="Job id"
                  scope="jobs"
                  sort={props.sort}
                  sortKey="job_id"
                  onSort={(_, key) => props.onSort(key)}
                />
                <SortableTableHead
                  className="w-[24%]"
                  label="Type"
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
                        aria-label={`View details for ${job.type} job ${job.job_id}`}
                        onClick={() => props.onDetail(job.job_id)}
                        disabled={props.loading[`detail:${job.job_id}`]}
                      >
                        Details
                      </Button>
                      {props.canWrite ? (
                        <Button
                          type="button"
                          size="sm"
                          aria-label={`Rerun ${job.type} job ${job.job_id}`}
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
            <CardTitle>Job detail</CardTitle>
            <span className="text-sm text-muted-foreground">{props.jobDetail.job_id}</span>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              {[
                ["Type", props.jobDetail.type],
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
  throw new Error("Missing #root container")
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
