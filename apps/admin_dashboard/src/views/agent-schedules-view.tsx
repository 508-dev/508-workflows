import { CalendarClock, Pause, Play, Plus, RefreshCw, Trash2 } from "lucide-react"
import { useState } from "react"

import { Empty } from "@/components/empty"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export type AgentSchedule = {
  id: string
  guild_id: string
  owner_discord_user_id: string
  name: string
  cron_expression: string
  timezone: string
  status: "active" | "paused" | "archived" | string
  next_run_at?: string | null
  last_run_at?: string | null
  definition: {
    prompt: string
    summary_mode: "deterministic" | "model_for_public_data" | string
    sources_are_public: boolean
    delivery: {
      channel_id: string
    }
    actions: Array<{
      tool_name: string
      arguments: Record<string, unknown>
    }>
  }
}

export type AgentSchedulesResponse = {
  scheduler_enabled: boolean
  schedules: AgentSchedule[]
}

export type AgentScheduleCreateValues = {
  name: string
  cron_expression: string
  timezone: string
  prompt: string
  repository: string
  query: string
  channel_id: string
  summary_mode: "deterministic" | "model_for_public_data"
  sources_are_public: boolean
}

function timestamp(value?: string | null) {
  if (!value) return "—"
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function statusVariant(status: string) {
  if (status === "active") return "succeeded" as const
  if (status === "paused") return "neutral" as const
  return "missing" as const
}

function githubActionSummary(schedule: AgentSchedule) {
  const action = schedule.definition.actions.find(
    (candidate) => candidate.tool_name === "github_issue.search_issues",
  )
  if (!action) return "Frozen read-only action"
  const repository = String(action.arguments.repository || "repository")
  const query = String(action.arguments.query || "").trim()
  return query ? `${repository} · ${query}` : repository
}

export function AgentSchedulesView({
  schedules,
  schedulerEnabled,
  loading,
  canWrite,
  canCreate,
  onRefresh,
  onCreate,
  onControl,
  onRun,
}: {
  schedules: AgentSchedule[]
  schedulerEnabled: boolean
  loading: Record<string, boolean>
  canWrite: boolean
  canCreate: boolean
  onRefresh: () => void
  onCreate: (values: AgentScheduleCreateValues) => Promise<boolean>
  onControl: (scheduleId: string, action: "pause" | "resume" | "archive") => void
  onRun: (scheduleId: string) => void
}) {
  const [name, setName] = useState("")
  const [cronExpression, setCronExpression] = useState("0 9 * * 1")
  const [timezone, setTimezone] = useState("UTC")
  const [repository, setRepository] = useState("")
  const [query, setQuery] = useState("")
  const [channelId, setChannelId] = useState("")
  const [prompt, setPrompt] = useState("")
  const [sourcesArePublic, setSourcesArePublic] = useState(false)
  const [useModelSummary, setUseModelSummary] = useState(false)

  const canSubmit =
    canCreate &&
    name.trim() &&
    cronExpression.trim() &&
    timezone.trim() &&
    repository.trim() &&
    channelId.trim() &&
    prompt.trim() &&
    !loading.createAgentSchedule

  async function submit() {
    if (!canSubmit) return
    const created = await onCreate({
      name: name.trim(),
      cron_expression: cronExpression.trim(),
      timezone: timezone.trim(),
      repository: repository.trim(),
      query: query.trim(),
      channel_id: channelId.trim(),
      prompt: prompt.trim(),
      summary_mode: useModelSummary ? "model_for_public_data" : "deterministic",
      sources_are_public: sourcesArePublic,
    })
    if (!created) return
    setName("")
    setRepository("")
    setQuery("")
    setChannelId("")
    setPrompt("")
    setSourcesArePublic(false)
    setUseModelSummary(false)
  }

  return (
    <div className="grid gap-5">
      <Card>
        <CardHeader className="items-start">
          <div className="grid gap-1">
            <CardTitle>Recurring agent reports</CardTitle>
            <p className="text-sm text-muted-foreground">
              Each report has a frozen read-only tool envelope. With the public-data model option,
              its prompt can shape the report; it can never add tools, write access, or a new
              delivery destination.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={schedulerEnabled ? "succeeded" : "missing"}>
              Dispatcher {schedulerEnabled ? "enabled" : "disabled"}
            </Badge>
            <Button
              type="button"
              variant="outline"
              onClick={onRefresh}
              disabled={loading.agentSchedules}
            >
              <RefreshCw className={loading.agentSchedules ? "animate-spin" : ""} />
              Refresh
            </Button>
          </div>
        </CardHeader>
      </Card>

      {canWrite ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>New GitHub issue report</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Runs at most once every five minutes. Use a Discord-linked admin session to create
                or control schedules.
              </p>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4">
            {!canCreate ? (
              <div className="rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                Open this dashboard through the Discord admin link to create or control schedules.
              </div>
            ) : null}
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <Label>
                Name
                <Input
                  aria-label="Schedule name"
                  value={name}
                  maxLength={140}
                  disabled={!canCreate}
                  placeholder="Weekly GitHub triage"
                  onChange={(event) => setName(event.target.value)}
                />
              </Label>
              <Label>
                Cron expression
                <Input
                  aria-label="Cron expression"
                  value={cronExpression}
                  disabled={!canCreate}
                  placeholder="0 9 * * 1"
                  onChange={(event) => setCronExpression(event.target.value)}
                />
              </Label>
              <Label>
                Timezone
                <Input
                  aria-label="Schedule timezone"
                  value={timezone}
                  disabled={!canCreate}
                  placeholder="Asia/Tokyo"
                  onChange={(event) => setTimezone(event.target.value)}
                />
              </Label>
              <Label>
                Repository
                <Input
                  aria-label="GitHub repository"
                  value={repository}
                  disabled={!canCreate}
                  placeholder="508-dev/508-workflows"
                  onChange={(event) => setRepository(event.target.value)}
                />
              </Label>
              <Label>
                Issue query
                <Input
                  aria-label="GitHub issue query"
                  value={query}
                  disabled={!canCreate}
                  placeholder="label:bug updated:>=2026-07-01"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </Label>
              <Label>
                Discord channel ID
                <Input
                  aria-label="Discord channel ID"
                  value={channelId}
                  inputMode="numeric"
                  disabled={!canCreate}
                  placeholder="123456789012345678"
                  onChange={(event) => setChannelId(event.target.value)}
                />
              </Label>
            </div>
            <Label>
              Report prompt
              <textarea
                aria-label="Report prompt"
                className="min-h-28 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                value={prompt}
                maxLength={4000}
                disabled={!canCreate}
                placeholder="Analyze open issues updated since the previous report. Group related work and recommend priorities."
                onChange={(event) => setPrompt(event.target.value)}
              />
            </Label>
            <div className="grid gap-2 text-sm">
              <label className="flex items-start gap-2">
                <input
                  aria-label="Sources are public"
                  type="checkbox"
                  checked={sourcesArePublic}
                  disabled={!canCreate}
                  onChange={(event) => {
                    setSourcesArePublic(event.target.checked)
                    if (!event.target.checked) setUseModelSummary(false)
                  }}
                />
                <span>
                  The issue metadata queried by this schedule is public and may be sent to the
                  configured model for summarization.
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  aria-label="Use model summary"
                  type="checkbox"
                  checked={useModelSummary}
                  disabled={!canCreate || !sourcesArePublic}
                  onChange={(event) => setUseModelSummary(event.target.checked)}
                />
                <span>
                  Use the prompt to generate a model summary. Otherwise, delivery uses a
                  deterministic issue list and never sends issue metadata to a model.
                </span>
              </label>
            </div>
            <div>
              <Button type="button" onClick={() => void submit()} disabled={!canSubmit}>
                <Plus />
                Create schedule
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Configured schedules</CardTitle>
        </CardHeader>
        <CardContent>
          {schedules.length === 0 ? (
            <Empty hidden={false}>No recurring agent schedules are configured.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <Table aria-label="Recurring agent schedules">
                <TableHeader>
                  <TableRow>
                    <TableHead>Schedule</TableHead>
                    <TableHead>Frozen action</TableHead>
                    <TableHead>Delivery</TableHead>
                    <TableHead>Next run</TableHead>
                    <TableHead>Last run</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Controls</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {schedules.map((schedule) => {
                    const isActive = schedule.status === "active"
                    const isPaused = schedule.status === "paused"
                    const controlKey = `agentSchedule:${schedule.id}`
                    return (
                      <TableRow key={schedule.id}>
                        <TableCell>
                          <div className="grid gap-1">
                            <strong>{schedule.name}</strong>
                            <code className="text-xs text-muted-foreground">{schedule.id}</code>
                            <span className="text-xs text-muted-foreground">
                              {schedule.cron_expression} · {schedule.timezone}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="max-w-60 text-sm">
                          <div>{githubActionSummary(schedule)}</div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            {schedule.definition.summary_mode === "model_for_public_data"
                              ? "Model summary of public metadata"
                              : "Deterministic report"}
                          </div>
                        </TableCell>
                        <TableCell>
                          <code className="text-xs">
                            #{schedule.definition.delivery.channel_id}
                          </code>
                        </TableCell>
                        <TableCell className="text-sm">{timestamp(schedule.next_run_at)}</TableCell>
                        <TableCell className="text-sm">{timestamp(schedule.last_run_at)}</TableCell>
                        <TableCell>
                          <Badge variant={statusVariant(schedule.status)}>{schedule.status}</Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex justify-end gap-2">
                            {isActive ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                disabled={!canCreate || loading[controlKey]}
                                onClick={() => onControl(schedule.id, "pause")}
                              >
                                <Pause />
                                Pause
                              </Button>
                            ) : null}
                            {isPaused ? (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                disabled={!canCreate || loading[controlKey]}
                                onClick={() => onControl(schedule.id, "resume")}
                              >
                                <Play />
                                Resume
                              </Button>
                            ) : null}
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={
                                !canCreate ||
                                !isActive ||
                                loading[`agentScheduleRun:${schedule.id}`]
                              }
                              onClick={() => onRun(schedule.id)}
                            >
                              <CalendarClock />
                              Run now
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={!canCreate || loading[controlKey]}
                              aria-label={`Archive ${schedule.name}`}
                              onClick={() => onControl(schedule.id, "archive")}
                            >
                              <Trash2 />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
