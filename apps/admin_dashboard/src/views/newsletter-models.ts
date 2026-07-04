type NewsletterJobDetail = {
  type: string
  status: string
  attempts: number
  max_attempts: number
  updated_at?: string
  last_error?: string | null
  result?: unknown
}

type NewsletterSyncPreview = {
  mailboxes_scanned?: number
  contacts_considered?: number
  providers?: Record<
    string,
    {
      would_sync?: number
      synced?: number
      skipped?: number
      failed?: number
    }
  >
}

type NewsletterSuppression = {
  email: string
  source_provider: string
  reason: string
  active?: boolean
  first_seen_at?: string
  last_seen_at?: string
  updated_at?: string
  metadata?: Record<string, unknown>
}

type NewsletterStatus = {
  scheduler_enabled?: boolean
  interval_seconds?: number
  active_suppression_count?: number
  active_suppressed_email_count?: number
  latest_job?: NewsletterJobDetail | null
}

type NewsletterProviderResult = {
  synced?: number
  would_sync?: number
  skipped?: number
  failed?: number
  statuses?: Record<string, number>
}

function newsletterPreviewSummary(preview: NewsletterSyncPreview | undefined) {
  if (!preview) return null
  const providerSummaries = Object.entries(preview.providers || {}).map(([name, result]) => {
    const wouldSync = result.would_sync ?? result.synced ?? 0
    return `${name}: ${wouldSync} would sync, ${result.skipped || 0} skipped, ${result.failed || 0} failed`
  })
  const scanned = preview.mailboxes_scanned ?? 0
  const contacts = preview.contacts_considered ?? 0
  return [
    `${scanned} mailbox${scanned === 1 ? "" : "es"}`,
    `${contacts} contact${contacts === 1 ? "" : "s"}`,
    ...providerSummaries,
  ].join("; ")
}

function newsletterIntervalLabel(seconds?: number) {
  const normalized = Number(seconds || 0)
  if (!Number.isFinite(normalized) || normalized <= 0) return "Not configured"
  if (normalized % 86400 === 0) {
    const days = normalized / 86400
    return `${days} day${days === 1 ? "" : "s"}`
  }
  if (normalized % 3600 === 0) {
    const hours = normalized / 3600
    return `${hours} hour${hours === 1 ? "" : "s"}`
  }
  if (normalized % 60 === 0) {
    const minutes = normalized / 60
    return `${minutes} minute${minutes === 1 ? "" : "s"}`
  }
  return `${normalized} second${normalized === 1 ? "" : "s"}`
}

function newsletterProviderResults(job?: NewsletterJobDetail | null) {
  const result = job?.result
  if (!result || typeof result !== "object" || Array.isArray(result)) return []
  const providers = (result as { providers?: unknown }).providers
  if (!providers || typeof providers !== "object" || Array.isArray(providers)) return []
  return Object.entries(providers)
    .filter((entry): entry is [string, NewsletterProviderResult] => {
      const [, value] = entry
      return Boolean(value && typeof value === "object" && !Array.isArray(value))
    })
    .sort(([left], [right]) => left.localeCompare(right))
}

export type {
  NewsletterJobDetail,
  NewsletterProviderResult,
  NewsletterStatus,
  NewsletterSuppression,
  NewsletterSyncPreview,
}

export { newsletterIntervalLabel, newsletterPreviewSummary, newsletterProviderResults }
