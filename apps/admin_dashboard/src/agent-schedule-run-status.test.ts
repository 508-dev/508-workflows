import { describe, expect, it } from "vitest"

import { agentScheduleRunToastMessage } from "./agent-schedule-run-status"

describe("agentScheduleRunToastMessage", () => {
  it("distinguishes newly queued work from coalesced requests", () => {
    expect(agentScheduleRunToastMessage({ status: "queued" })).toBe(
      "Queued recurring agent schedule run",
    )
    expect(agentScheduleRunToastMessage({ status: "already_queued" })).toBe(
      "Recurring agent schedule run is already queued",
    )
    expect(agentScheduleRunToastMessage({ status: "already_requested" })).toBe(
      "A recent recurring agent schedule run already exists",
    )
  })
})
