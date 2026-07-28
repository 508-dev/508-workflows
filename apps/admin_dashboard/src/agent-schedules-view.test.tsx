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
    prompt: "Inspect onboarding and ERP health.",
    execution_mode: "agent_loop",
    summary_mode: "deterministic",
    sources_are_public: false,
    tool_allowlist: ["onboarding_read.get_summary", "erp_read.search_projects"],
    delivery: { channel_id: "789" },
    actions: [],
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
    expect(screen.getByText("Bounded agent loop · 2 read-only tools")).toBeVisible()
    expect(screen.getByText(/fixed catalog of read-only tools/i)).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Pause" }))

    expect(onControl).toHaveBeenCalledWith(schedule.id, "pause")
  })
})
