import {
  ArrowLeft,
  Bell,
  BriefcaseBusiness,
  ClipboardList,
  ExternalLink,
  FileClock,
  LogOut,
  RefreshCw,
  Search,
  ShieldCheck,
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

type View = "people" | "gigs" | "onboarding" | "jobs" | "agent" | "audit"
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

const routes: Record<View, string> = {
  people: "/dashboard/people",
  gigs: "/dashboard/gigs",
  onboarding: "/dashboard/onboarding",
  jobs: "/dashboard/jobs",
  agent: "/dashboard/agent",
  audit: "/dashboard/audit",
}

const routePermissions: Record<View, string> = {
  people: "people:read",
  gigs: "gigs:read",
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

function rawViewFromPath() {
  return window.location.pathname.split("/").filter(Boolean)[1] || ""
}

function viewFromPath(): View {
  const view = rawViewFromPath()
  return Object.hasOwn(routes, view) ? (view as View) : "people"
}

function detailIdFromPath() {
  const [, view, detailId] = window.location.pathname.split("/").filter(Boolean)
  if (view !== "gigs" || !detailId) return ""
  try {
    return decodeURIComponent(detailId)
  } catch {
    return ""
  }
}

async function requestJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set("Accept", "application/json")
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers,
  })
  if (response.status === 401) {
    const next = `${window.location.pathname}${window.location.search}` || "/dashboard"
    window.location.assign(`/auth/login?next=${encodeURIComponent(next)}`)
    throw new Error("Session expired")
  }
  if (!response.ok) {
    let detail: unknown = response.statusText
    try {
      const payload = await response.json()
      detail = payload.detail || payload.error || detail
    } catch {
      detail = response.statusText
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail))
  }
  return response.json() as Promise<T>
}

function sortValue(scope: View, item: Job | Person | Gig | AuditEvent, key: string) {
  if (scope === "gigs") {
    const gig = item as Gig
    if (key === "title") return gig.title || ""
    if (key === "status") return gig.status || ""
    if (key === "applications") return Number(gig.application_count || 0)
    if (key === "activity") return gigActivityTimestamp(gig)
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

function sortItems<T extends Job | Person | Gig | AuditEvent>(
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
  const [user, setUser] = useState<User | null>(null)
  const [view, setViewState] = useState<View>(viewFromPath())
  const [toast, setToast] = useState<{ message: string; tone?: "ok" | "error" }>({
    message: "",
  })
  const [permissions, setPermissions] = useState<string[]>([])
  const [crmBaseUrl, setCrmBaseUrl] = useState("")
  const [jobs, setJobs] = useState<Job[]>([])
  const [gigs, setGigs] = useState<Gig[]>([])
  const [gigDetail, setGigDetail] = useState<Gig | null>(null)
  const [selectedGigId, setSelectedGigId] = useState(detailIdFromPath())
  const [notifications, setNotifications] = useState<DashboardNotification[]>([])
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [people, setPeople] = useState<Person[]>([])
  const [onboarding, setOnboarding] = useState<Person[]>([])
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([])
  const [agentReport, setAgentReport] = useState<AgentReport | null>(null)
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [sort, setSortState] = useState<Record<View, { key: string; direction: SortDirection }>>({
    onboarding: { key: "onboarding_state", direction: "asc" },
    gigs: { key: "activity", direction: "desc" },
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
    if (normalized === "gigs" && push) setSelectedGigId("")
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

  async function loadJobs() {
    setBusy("jobs", true)
    showToast("Loading jobs")
    try {
      const payload = await requestJson<Job[]>(jobsUrl())
      setJobs(payload)
      showToast(`Loaded ${payload.length} jobs`, "ok")
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to load jobs", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to load gigs", "error")
    } finally {
      setBusy("gigs", false)
    }
  }

  async function loadGigDetail(gigId: string) {
    setBusy(`gig:${gigId}:detail`, true)
    try {
      const payload = await requestJson<Gig>(`/dashboard/api/gigs/${encodeURIComponent(gigId)}`)
      setGigDetail(payload)
    } catch (error) {
      setGigDetail(null)
      showToast(error instanceof Error ? error.message : "Unable to load gig", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to load notifications", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to update gig", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to update candidate", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to load people", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to load onboarding", "error")
    } finally {
      setBusy("onboarding", false)
    }
  }

  async function loadAuditEvents() {
    setBusy("audit", true)
    try {
      setAuditEvents(await requestJson<AuditEvent[]>("/dashboard/api/audit-events?limit=25"))
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to load audit events", "error")
    } finally {
      setBusy("audit", false)
    }
  }

  async function loadAgentReport() {
    setBusy("agent", true)
    try {
      setAgentReport(await requestJson<AgentReport>("/dashboard/api/agent?limit=100"))
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Unable to load agent report", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to load job detail", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to rerun job", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to queue people sync", "error")
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
      showToast(error instanceof Error ? error.message : "Unable to assign onboarder", "error")
    } finally {
      setBusy(`onboarder:${normalizedContactId}`, false)
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
      showToast(error instanceof Error ? error.message : "Unable to log out", "error")
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
        setViewState(nextView)
        if (!Object.hasOwn(routes, rawViewFromPath()) || nextView !== currentView) {
          window.history.replaceState({ view: nextView }, "", routes[nextView])
        }
      })
      .catch((error: unknown) => {
        showToast(error instanceof Error ? error.message : "Dashboard failed to load", "error")
      })
  }, [])

  useEffect(() => {
    const onPopState = () => {
      setSelectedGigId(detailIdFromPath())
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

  // biome-ignore lint/correctness/useExhaustiveDependencies: each loader reads the latest filter state for the active view.
  useEffect(() => {
    if (permissions.length === 0) return
    if (can("gigs:read")) void loadNotifications()
    if (view === "people") void loadPeople()
    if (view === "gigs") void loadGigs()
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
  const selectedGig = useMemo(() => {
    if (gigDetail?.id === selectedGigId) return gigDetail
    return sortedGigs.find((gig) => gig.id === selectedGigId) || null
  }, [gigDetail, selectedGigId, sortedGigs])
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

      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-5 px-5 py-5 md:grid-cols-[190px_minmax(0,1fr)]">
        <nav
          className="grid content-start gap-1 md:sticky md:top-24"
          aria-label="Dashboard sections"
        >
          {(
            [
              ["people", "People", Users],
              ["gigs", "Gigs", BriefcaseBusiness],
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
            rel="noreferrer"
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
        {resumeUrl ? (
          <a
            className="text-xs font-extrabold text-primary"
            href={resumeUrl}
            target="_blank"
            rel="noreferrer"
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
