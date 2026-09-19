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
  selected: "Selected",
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
    year: "numeric",
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

export function messageForApiError(record: Record<string, unknown>, fallback: string) {
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
  if (error === "crm_lookup_failed") {
    return "Could not verify the candidate in CRM. No email was sent; try again once CRM is reachable."
  }
  if (error === "contact_not_onboarding_eligible") {
    return "This candidate is no longer eligible for onboarding. Refresh the queue and review their status."
  }
  if (error === "candidate_terminal_onboarding_state") {
    return "This candidate is already in a terminal onboarding state, so no email was sent."
  }
  if (error === "recipient_email_required") {
    return "The candidate does not have a valid email address, so no email was sent."
  }
  if (error === "reply_to_email_required") {
    return "Your Reply-To email is unavailable, so no email was sent."
  }
  if (error === "smtp_not_configured") {
    return "Onboarding email SMTP is not configured, so no email was sent."
  }
  if (error === "email_send_failed") {
    return "The mail server could not confirm it accepted the email. It was not marked sent; check the recipient inbox or SMTP logs before retrying to avoid a duplicate."
  }
  if (error === "empty_email_body") {
    return "The email body is empty. Add content before sending."
  }
  if (error === "invalid_payload") {
    return "The request contains invalid information. Refresh the page and try again."
  }
  if (error === "ambiguous_person") {
    return "Multiple people matched. Choose the matching person record."
  }
  return error || fallback
}

export function isTerminalJobStatus(value?: string | null) {
  return ["succeeded", "dead", "canceled"].includes(
    String(value || "")
      .trim()
      .toLowerCase(),
  )
}

export function jobLeadClassificationMethodLabel(value?: string | null) {
  if (value === "llm") return "LLM"
  if (value === "heuristic") return "Keyword fallback"
  return "Unknown"
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
