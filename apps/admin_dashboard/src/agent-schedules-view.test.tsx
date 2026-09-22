import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  type AgentSchedule,
  type AgentScheduleRun,
  AgentSchedulesView,
} from "./views/agent-schedules-view"

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

const staleDelivery: AgentScheduleRun = {
  id: "e5e5e5e5-0000-4000-8000-000000000002",
  schedule_id: schedule.id,
  status: "running",
  delivery_status: "claimed",
  delivery_claimed_at: "2026-08-03T00:00:00Z",
}

describe("AgentSchedulesView", () => {
  it("renders the frozen envelope and sends lifecycle controls", () => {
    const onControl = vi.fn()
    render(
      <AgentSchedulesView
        schedules={[schedule]}
        deliveryAttention={[]}
        schedulerEnabled
        loading={{}}
        canWrite
        canCreate
        onRefresh={vi.fn()}
        onCreate={vi.fn().mockResolvedValue(true)}
        onControl={onControl}
        onRun={vi.fn()}
        onResolveDelivery={vi.fn()}
      />,
    )

    expect(screen.getByText("Weekly GitHub triage")).toBeVisible()
    expect(screen.getByText("Bounded agent loop · 2 read-only tools")).toBeVisible()
    expect(screen.getByText(/fixed catalog of read-only tools/i)).toBeVisible()
    expect(screen.getByText(/CRM, ERP, billing, onboarding, and public-web/i)).toBeVisible()
    expect(screen.getByText("/schedule-github-issues")).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Pause" }))

    expect(onControl).toHaveBeenCalledWith(schedule.id, "pause")
  })

  it("makes stale claims visible and lets an operator mark them unknown", () => {
    const onResolveDelivery = vi.fn()
    render(
      <AgentSchedulesView
        schedules={[schedule]}
        deliveryAttention={[staleDelivery]}
        schedulerEnabled
        loading={{}}
        canWrite
        canCreate
        onRefresh={vi.fn()}
        onCreate={vi.fn().mockResolvedValue(true)}
        onControl={vi.fn()}
        onRun={vi.fn()}
        onResolveDelivery={onResolveDelivery}
      />,
    )

    expect(screen.getByText("Delivery attention required")).toBeVisible()
    fireEvent.click(screen.getByRole("button", { name: "Mark outcome unknown" }))

    expect(onResolveDelivery).toHaveBeenCalledWith(staleDelivery.id)
  })

  it("reuses a creation operation ID after response loss", async () => {
    const onCreate = vi.fn().mockResolvedValue(false)
    render(
      <AgentSchedulesView
        schedules={[]}
        deliveryAttention={[]}
        schedulerEnabled
        loading={{}}
        canWrite
        canCreate
        onRefresh={vi.fn()}
        onCreate={onCreate}
        onControl={vi.fn()}
        onRun={vi.fn()}
        onResolveDelivery={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText("Schedule name"), {
      target: { value: "Weekly operations" },
    })
    fireEvent.change(screen.getByLabelText("Discord channel ID"), {
      target: { value: "789" },
    })
    fireEvent.change(screen.getByLabelText("Report prompt"), {
      target: { value: "Review operational health." },
    })
    const createButton = screen.getByRole("button", { name: "Create schedule" })

    fireEvent.click(createButton)
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    fireEvent.click(createButton)
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(2))

    const firstOperationId = onCreate.mock.calls[0][0].operation_id
    expect(firstOperationId).toMatch(/^[0-9a-f-]{36}$/i)
    expect(onCreate.mock.calls[1][0].operation_id).toBe(firstOperationId)

    fireEvent.change(screen.getByLabelText("Report prompt"), {
      target: { value: "Review updated operational health." },
    })
    fireEvent.click(createButton)
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(3))
    expect(onCreate.mock.calls[2][0].operation_id).not.toBe(firstOperationId)
  })
})
