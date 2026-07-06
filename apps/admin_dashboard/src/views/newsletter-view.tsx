import { RefreshCw } from "lucide-react"

import { Empty } from "@/components/empty"
import { SortableTableHead } from "@/components/sortable-table-head"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table"
import { formatDate, jsonPreview, type Tone } from "@/dashboard-utils"
import { cn } from "@/lib/utils"
import {
  type NewsletterStatus,
  type NewsletterSuppression,
  newsletterIntervalLabel,
  newsletterProviderResults,
} from "@/views/newsletter-models"

type SortDirection = "asc" | "desc"

function NewsletterView(props: {
  status: NewsletterStatus | null
  suppressions: NewsletterSuppression[]
  providerOptions: string[]
  providerFilter: string
  sort: { key: string; direction: SortDirection }
  loading: Record<string, boolean>
  canSync: boolean
  onRefresh: () => void
  onSync: () => void
  onProviderFilterChange: (value: string) => void
  onSort: (key: string) => void
}) {
  const latestJob = props.status?.latest_job || null
  const uniqueSuppressed = props.status?.active_suppressed_email_count
  const suppressionRows = props.status?.active_suppression_count
  const providerResults = newsletterProviderResults(latestJob)
  const providerOptions = props.providerFilter
    ? [...new Set([...props.providerOptions, props.providerFilter])].sort((left, right) =>
        left.localeCompare(right),
      )
    : props.providerOptions
  const suppressionsByProvider = props.suppressions.reduce<Record<string, NewsletterSuppression[]>>(
    (groups, record) => {
      const provider = record.source_provider || "unknown"
      groups[provider] = [...(groups[provider] || []), record]
      return groups
    },
    {},
  )
  const suppressionGroups = Object.entries(suppressionsByProvider).sort(([left], [right]) =>
    left.localeCompare(right),
  )
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Newsletter sync</CardTitle>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              id="refreshNewsletter"
              type="button"
              variant="outline"
              onClick={props.onRefresh}
              disabled={props.loading.newsletterStatus || props.loading.newsletterSuppressions}
            >
              <RefreshCw />
              Refresh
            </Button>
            {props.canSync ? (
              <Button
                id="syncNewsletters"
                data-permission="people:sync"
                type="button"
                onClick={props.onSync}
                disabled={props.loading.syncNewsletters}
              >
                <RefreshCw />
                Sync now
              </Button>
            ) : null}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-4">
            <div className="rounded-md border bg-background p-4">
              <span className="text-xs font-bold text-muted-foreground">Scheduler</span>
              <strong id="newsletterScheduler" className="block text-2xl">
                {props.status?.scheduler_enabled ? "Enabled" : "Disabled"}
              </strong>
            </div>
            <div className="rounded-md border bg-background p-4">
              <span className="text-xs font-bold text-muted-foreground">Interval</span>
              <strong id="newsletterInterval" className="block text-2xl">
                {newsletterIntervalLabel(props.status?.interval_seconds)}
              </strong>
            </div>
            <div className="rounded-md border bg-background p-4">
              <span className="text-xs font-bold text-muted-foreground">Suppressed emails</span>
              <strong id="newsletterSuppressedEmails" className="block text-2xl">
                {uniqueSuppressed ?? "Loading"}
              </strong>
            </div>
            <div className="rounded-md border bg-background p-4">
              <span className="text-xs font-bold text-muted-foreground">Suppression rows</span>
              <strong id="newsletterSuppressionRows" className="block text-2xl">
                {suppressionRows ?? "Loading"}
              </strong>
            </div>
          </div>
          <div className="mt-4 grid gap-2 rounded-md border bg-secondary p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-extrabold">Latest sync</span>
              {latestJob ? (
                <Badge variant={(latestJob.status as Tone) || "neutral"}>{latestJob.status}</Badge>
              ) : (
                <Badge variant="neutral">No job found</Badge>
              )}
            </div>
            {latestJob ? (
              <>
                <div className="text-muted-foreground">
                  {latestJob.type} | updated {formatDate(latestJob.updated_at)} | attempts{" "}
                  {latestJob.attempts}/{latestJob.max_attempts}
                </div>
                {latestJob.last_error ? (
                  <div className="text-red-700 dark:text-red-300">{latestJob.last_error}</div>
                ) : null}
                <div className="grid gap-2 md:grid-cols-2">
                  {providerResults.length ? (
                    providerResults.map(([providerName, providerResult]) => {
                      const statuses = providerResult.statuses || {}
                      const statusEntries = Object.entries(statuses).sort(([left], [right]) =>
                        left.localeCompare(right),
                      )
                      const synced = providerResult.synced ?? providerResult.would_sync ?? 0
                      return (
                        <div
                          key={providerName}
                          className="grid gap-2 rounded-md border bg-background p-3"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <strong>{providerName}</strong>
                            <div className="flex flex-wrap gap-1">
                              <Badge variant="succeeded">{synced} synced</Badge>
                              <Badge variant="neutral">{providerResult.skipped || 0} skipped</Badge>
                              {providerResult.failed ? (
                                <Badge variant="failed">{providerResult.failed} failed</Badge>
                              ) : null}
                            </div>
                          </div>
                          {statusEntries.length ? (
                            <div className="flex flex-wrap gap-1">
                              {statusEntries.map(([status, count]) => (
                                <Badge key={status} variant="neutral">
                                  {status.replaceAll("_", " ")}: {count}
                                </Badge>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      )
                    })
                  ) : (
                    <div className="rounded-md border bg-background p-3 text-muted-foreground">
                      No provider status details recorded for the latest sync.
                    </div>
                  )}
                </div>
                {latestJob.result ? (
                  <pre className="max-h-40 overflow-auto rounded-md bg-background p-3 text-xs">
                    {jsonPreview(latestJob.result)}
                  </pre>
                ) : null}
              </>
            ) : (
              <div className="text-muted-foreground">
                No 508 members newsletter sync job was found in the last 90 days.
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Newsletter suppressions</CardTitle>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <Label className="min-w-44">
              Provider
              <Select
                id="newsletterProviderFilter"
                value={props.providerFilter}
                onChange={(event) => props.onProviderFilterChange(event.target.value)}
              >
                <option value="">All providers</option>
                {providerOptions.map((provider) => (
                  <option key={provider} value={provider}>
                    {provider}
                  </option>
                ))}
              </Select>
            </Label>
            <span id="newsletterSuppressionsStatus" className="text-sm text-muted-foreground">
              {props.loading.newsletterSuppressions
                ? "Loading"
                : `${props.suppressions.length} shown`}
            </span>
          </div>
        </CardHeader>
        <Empty hidden={props.suppressions.length !== 0}>
          No active newsletter suppressions recorded.
        </Empty>
        <div className={cn("grid gap-4", props.suppressions.length === 0 && "hidden")}>
          {suppressionGroups.map(([provider, records]) => (
            <section key={provider} className="grid gap-2" aria-label={`${provider} suppressions`}>
              <div className="flex flex-wrap items-center gap-2 px-4 pt-4">
                <Badge variant="neutral">{provider}</Badge>
                <span className="text-sm text-muted-foreground">
                  {records.length} suppression{records.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="overflow-x-auto">
                <Table
                  id={`newsletterSuppressionsTable-${provider}`}
                  className="min-w-[900px]"
                  aria-label={`${provider} newsletter suppressions`}
                >
                  <TableHeader>
                    <TableRow>
                      <SortableTableHead
                        className="w-[34%]"
                        label="Email"
                        scope="newsletter"
                        sort={props.sort}
                        sortKey="email"
                        onSort={(_, key) => props.onSort(key)}
                      />
                      <SortableTableHead
                        className="w-[14%]"
                        label="Source"
                        scope="newsletter"
                        sort={props.sort}
                        sortKey="source_provider"
                        onSort={(_, key) => props.onSort(key)}
                      />
                      <SortableTableHead
                        className="w-[22%]"
                        label="Reason"
                        scope="newsletter"
                        sort={props.sort}
                        sortKey="reason"
                        onSort={(_, key) => props.onSort(key)}
                      />
                      <SortableTableHead
                        className="w-[15%]"
                        label="First seen"
                        scope="newsletter"
                        sort={props.sort}
                        sortKey="first_seen_at"
                        onSort={(_, key) => props.onSort(key)}
                      />
                      <SortableTableHead
                        className="w-[15%]"
                        label="Last seen"
                        scope="newsletter"
                        sort={props.sort}
                        sortKey="last_seen_at"
                        onSort={(_, key) => props.onSort(key)}
                      />
                    </TableRow>
                  </TableHeader>
                  <TableBody id={`newsletterSuppressionsBody-${provider}`}>
                    {records.map((record) => (
                      <TableRow key={`${record.email}-${record.source_provider}`}>
                        <TableCell className="font-mono text-sm">{record.email}</TableCell>
                        <TableCell>
                          <Badge variant="neutral">{record.source_provider}</Badge>
                        </TableCell>
                        <TableCell>{record.reason.replaceAll("_", " ")}</TableCell>
                        <TableCell>{formatDate(record.first_seen_at)}</TableCell>
                        <TableCell>
                          {formatDate(record.last_seen_at || record.updated_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          ))}
        </div>
      </Card>
    </>
  )
}

export { NewsletterView }
