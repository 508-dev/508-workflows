import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { type AgentSchedule, AgentSchedulesView } from "./views/agent-schedules-view"

afterEach(cleanup)

const schedule: AgentSchedule = {
  id: "e5e5e5e5-0000-4000-8000-000000000001",
  guild_id: "123",
  owner_discord_user_id: "456",
  name: "Weekly GitHub triage",
  cron_expression: "0 9 * * 1",
  timezone: "Asia/Tokyo",
  status: "active",
  next_run_at: "2026-08-03T00:00:00Z",
  last_run_at: null,
  definition: {
    prompt: "Group related issues and recommend priorities.",
    summary_mode: "deterministic",
    sources_are_public: false,
    delivery: { channel_id: "789" },
    actions: [
      {
        tool_name: "github_issue.search_issues",
        arguments: { repository: "508-dev/508-workflows", query: "label:bug" },
      },
    ],
  },
}

describe("AgentSchedulesView", () => {
  it("renders the frozen envelope and sends lifecycle controls", () => {
    const onControl = vi.fn()
    render(
      <AgentSchedulesView
        schedules={[schedule]}
        schedulerEnabled
        loading={{}}
        canWrite
        canCreate
        onRefresh={vi.fn()}
        onCreate={vi.fn().mockResolvedValue(true)}
        onControl={onControl}
        onRun={vi.fn()}
      />,
    )

    expect(screen.getByText("Weekly GitHub triage")).toBeVisible()
    expect(screen.getByText("508-dev/508-workflows · label:bug")).toBeVisible()
    expect(screen.getByText(/frozen read-only tool envelope/i)).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Pause" }))

    expect(onControl).toHaveBeenCalledWith(schedule.id, "pause")
  })
})
