import { describe, expect, it } from "vitest"

import {
  displayOnboarder,
  githubUrl,
  labelForOnboardingState,
  linkedinUrl,
  onboardingStateValue,
  toneForOnboardingState,
} from "./dashboard-utils"

describe("dashboard utility helpers", () => {
  it("normalizes onboarding state labels and tones", () => {
    expect(labelForOnboardingState("Reachingout")).toBe("Reaching out")
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
})
