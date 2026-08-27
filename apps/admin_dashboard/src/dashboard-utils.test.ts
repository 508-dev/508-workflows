import { describe, expect, it } from "vitest"

import {
  daysSince,
  displayOnboarder,
  formatDate,
  githubUrl,
  isTerminalJobStatus,
  jobLeadClassificationMethodLabel,
  labelForOnboardingState,
  linkedinUrl,
  messageForApiError,
  onboardingStateValue,
  toneForOnboardingState,
} from "./dashboard-utils"

describe("dashboard utility helpers", () => {
  it("normalizes onboarding state labels and tones", () => {
    expect(labelForOnboardingState("Reachingout")).toBe("Reaching out")
    expect(labelForOnboardingState("selected")).toBe("Selected")
    expect(labelForOnboardingState("awaiting-contribution")).toBe("Awaiting Contribution")
    expect(labelForOnboardingState("")).toBe("No status")
    expect(toneForOnboardingState("rejected")).toBe("failed")
    expect(toneForOnboardingState("onboarded")).toBe("succeeded")
  })

  it("reads onboarding state from legacy and current field names", () => {
    expect(onboardingStateValue({ onboarding_state: "pending" })).toBe("pending")
    expect(onboardingStateValue({ onboardingState: "selected" })).toBe("selected")
    expect(onboardingStateValue({ cOnboardingState: "waitlist" })).toBe("waitlist")
  })

  it("builds only trusted profile URLs", () => {
    expect(linkedinUrl("linkedin.com/in/bea-prospect")).toBe("https://linkedin.com/in/bea-prospect")
    expect(linkedinUrl("https://example.com/in/bea-prospect")).toBe("")
    expect(linkedinUrl("/in/jane-doe")).toBe("https://www.linkedin.com/in/jane-doe")
    expect(githubUrl("@beaprospect")).toBe("https://github.com/beaprospect")
    expect(githubUrl("/octocat/")).toBe("https://github.com/octocat")
    expect(githubUrl("https://github.com/508-dev")).toBe("https://github.com/508-dev")
  })

  it("hides empty and none onboarder values", () => {
    expect(displayOnboarder(" none ")).toBe("")
    expect(displayOnboarder("jane")).toBe("jane")
  })

  it("calculates whole elapsed days from an ISO timestamp", () => {
    const now = new Date("2026-05-16T12:00:00Z")
    expect(daysSince("2026-05-06T11:59:00Z", now)).toBe(10)
    expect(daysSince("2026-05-17T00:00:00Z", now)).toBe(0)
    expect(daysSince("not a date", now)).toBeNull()
  })

  it("includes the year in formatted timestamps", () => {
    expect(formatDate("2026-01-27T02:26:00Z")).toContain("2026")
  })

  it("identifies terminal background job statuses", () => {
    expect(isTerminalJobStatus("succeeded")).toBe(true)
    expect(isTerminalJobStatus(" failed ")).toBe(false)
    expect(isTerminalJobStatus("dead")).toBe(true)
    expect(isTerminalJobStatus("canceled")).toBe(true)
    expect(isTerminalJobStatus("queued")).toBe(false)
    expect(isTerminalJobStatus("running")).toBe(false)
    expect(isTerminalJobStatus(undefined)).toBe(false)
  })

  it("labels job lead classification provenance without guessing", () => {
    expect(jobLeadClassificationMethodLabel("llm")).toBe("LLM")
    expect(jobLeadClassificationMethodLabel("heuristic")).toBe("Keyword fallback")
    expect(jobLeadClassificationMethodLabel("unknown")).toBe("Unknown")
    expect(jobLeadClassificationMethodLabel()).toBe("Unknown")
  })

  it("turns malformed onboarding email requests into actionable messages", () => {
    expect(messageForApiError({ error: "empty_email_body" }, "Request failed")).toBe(
      "The email body is empty. Add content before sending.",
    )
    expect(messageForApiError({ error: "invalid_payload" }, "Request failed")).toBe(
      "The request contains invalid information. Refresh the page and try again.",
    )
  })

  it("keeps shared onboarding eligibility errors action-neutral", () => {
    expect(messageForApiError({ error: "contact_not_onboarding_eligible" }, "Request failed")).toBe(
      "This candidate is no longer eligible for onboarding. Refresh the queue and review their status.",
    )
  })
})
