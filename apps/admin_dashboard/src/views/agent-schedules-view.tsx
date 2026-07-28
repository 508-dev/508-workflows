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
    execution_mode?: "frozen_actions" | "agent_loop" | string
    summary_mode: "deterministic" | "model_for_public_data" | string
    sources_are_public: boolean
    tool_allowlist?: string[]
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
  channel_id: string
  execution_mode: "agent_loop"
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

function scheduleActionSummary(schedule: AgentSchedule) {
  if (schedule.definition.execution_mode === "agent_loop") {
    const toolCount = schedule.definition.tool_allowlist?.length || 0
    return toolCount ? `Bounded agent loop · ${toolCount} read-only tools` : "Bounded agent loop"
  }
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
  const [channelId, setChannelId] = useState("")
  const [prompt, setPrompt] = useState("")

  const canSubmit =
    canCreate &&
    name.trim() &&
    cronExpression.trim() &&
    timezone.trim() &&
    channelId.trim() &&
    prompt.trim() &&
    !loading.createAgentSchedule

  async function submit() {
    if (!canSubmit) return
    const created = await onCreate({
      name: name.trim(),
      cron_expression: cronExpression.trim(),
      timezone: timezone.trim(),
      channel_id: channelId.trim(),
      prompt: prompt.trim(),
      execution_mode: "agent_loop",
    })
    if (!created) return
    setName("")
    setChannelId("")
    setPrompt("")
  }

  return (
    <div className="grid gap-5">
      <Card>
        <CardHeader className="items-start">
          <div className="grid gap-1">
            <CardTitle>Recurring agent reports</CardTitle>
            <p className="text-sm text-muted-foreground">
              Each report saves a goal and a fixed catalog of read-only tools. At run time the agent
              can plan a short loop over that catalog, but it can never write, add a tool, widen
              permissions, or change its delivery destination.
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
              <CardTitle>New agent report</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Runs at most once every five minutes. The saved catalog includes every currently
                supported read-only GitHub, CRM, ERP, billing, onboarding, and public-web tool that
                your Discord permissions permit.
              </p>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4">
            {!canCreate ? (
              <div className="rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
                Open this dashboard through the Discord admin link to create or control schedules.
              </div>
            ) : null}
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
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
                placeholder="Inspect the onboarding queue and ERP projects at risk. Summarize trends, blockers, and the next sensible follow-up."
                onChange={(event) => setPrompt(event.target.value)}
              />
            </Label>
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
              The planner receives only safe aggregate observations from CRM, ERP, billing, and
              onboarding tools. Raw contact and financial records are never fed back into the model
              or posted by this generic report.
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
                    <TableHead>Capability envelope</TableHead>
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
                          <div>{scheduleActionSummary(schedule)}</div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            {schedule.definition.execution_mode === "agent_loop"
                              ? "Model-planned, bounded read-only loop"
                              : schedule.definition.summary_mode === "model_for_public_data"
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
