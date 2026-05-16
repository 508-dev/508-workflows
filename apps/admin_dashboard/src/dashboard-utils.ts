export type Tone =
  | "neutral"
  | "succeeded"
  | "failed"
  | "dead"
  | "missing"
  | "running"
  | "queued"
  | "canceled"

export type OnboardingStateCarrier = {
  onboarding_state?: string
  onboardingState?: string
  cOnboardingState?: string
  onboarding_status_label?: string
}

const onboardingStateLabels: Record<string, string> = {
  pending: "Needs review",
  selected: "Assigned to onboarder",
  reachingout: "Reaching out",
  awaitingcontribution: "Awaiting contribution",
  onboarded: "Onboarded",
  waitlist: "Waitlist",
  rejected: "Rejected",
}

export function formatDate(value?: string | null) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function daysSince(value?: string | null, now: Date = new Date()) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  const diffMs = now.getTime() - date.getTime()
  if (diffMs < 0) return 0
  return Math.floor(diffMs / 86_400_000)
}

export function jsonPreview(value: unknown) {
  if (value === null || value === undefined) return ""
  return JSON.stringify(value, null, 2)
}

export function onboardingStateValue(person: OnboardingStateCarrier) {
  return person.onboarding_state || person.onboardingState || person.cOnboardingState || ""
}

export function labelForOnboardingState(value?: string) {
  const raw = String(value || "").trim()
  if (!raw) return "No status"
  const normalized = raw.toLowerCase()
  if (onboardingStateLabels[normalized]) return onboardingStateLabels[normalized]
  return raw
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase())
}

export function toneForOnboardingState(value?: string): Tone {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
  if (!normalized || normalized === "pending") return "neutral"
  if (normalized === "selected") return "queued"
  if (normalized === "rejected") return "failed"
  if (normalized === "onboarded") return "succeeded"
  if (normalized === "waitlist") return "running"
  return "queued"
}

export function displayOnboarder(value?: string) {
  const raw = String(value || "").trim()
  if (!raw || raw.toLowerCase() === "none") return ""
  return raw
}

export function urlWithProtocol(value?: string) {
  const raw = String(value || "").trim()
  if (!raw) return ""
  if (/^https?:\/\//i.test(raw)) return raw
  return `https://${raw.replace(/^\/+/, "")}`
}

function parsedExternalUrl(value?: string) {
  try {
    return new URL(urlWithProtocol(value))
  } catch {
    return null
  }
}

function hostMatches(hostname: string, domain: string) {
  const normalized = hostname.toLowerCase()
  return normalized === domain || normalized.endsWith(`.${domain}`)
}

function encodePathFragment(value: string) {
  return value
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/")
}

export function linkedinUrl(value?: string) {
  const raw = String(value || "").trim()
  if (!raw) return ""
  const url = parsedExternalUrl(raw)
  if (url && hostMatches(url.hostname, "linkedin.com")) return url.href
  if (/^https?:\/\//i.test(raw)) return ""
  const profile = raw
    .replace(/^@/, "")
    .replace(/^\/+|\/+$/g, "")
    .replace(/^in\//i, "")
  return profile ? `https://www.linkedin.com/in/${encodePathFragment(profile)}` : ""
}

export function githubUrl(value?: string) {
  const raw = String(value || "")
    .trim()
    .replace(/^@/, "")
  if (!raw) return ""
  const url = parsedExternalUrl(raw)
  if (url && hostMatches(url.hostname, "github.com")) return url.href
  if (/^https?:\/\//i.test(raw)) return ""
  const profile = raw.replace(/^\/+|\/+$/g, "")
  return profile ? `https://github.com/${encodePathFragment(profile)}` : ""
}
