import { act, cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { OnboardingEmailSendStatus } from "./onboarding-email-send-status"

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("OnboardingEmailSendStatus", () => {
  it("updates the visual timer while announcing only meaningful phase changes", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-27T00:00:00Z"))
    const startedAt = Date.now()

    render(<OnboardingEmailSendStatus startedAt={startedAt} />)

    const liveStatus = screen.getByRole("status")
    const initialAnnouncement = liveStatus.textContent
    expect(screen.getByText(/0s elapsed/)).toHaveAttribute("aria-hidden", "true")

    act(() => vi.advanceTimersByTime(1000))

    expect(screen.getByText(/1s elapsed/)).toHaveAttribute("aria-hidden", "true")
    expect(liveStatus).toHaveTextContent(initialAnnouncement || "")

    act(() => vi.advanceTimersByTime(9000))

    expect(screen.getByText(/10s elapsed/)).toHaveAttribute("aria-hidden", "true")
    expect(liveStatus).toHaveTextContent("Onboarding email is still sending")

    act(() => vi.advanceTimersByTime(15_000))

    expect(screen.getByText(/25s elapsed/)).toHaveAttribute("aria-hidden", "true")
    expect(liveStatus).toHaveTextContent("Onboarding email is taking longer than expected")
  })

  it("preserves the elapsed warning when remounted with the parent start time", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-08-27T00:00:00Z"))
    const startedAt = Date.now()
    const firstRender = render(<OnboardingEmailSendStatus startedAt={startedAt} />)

    act(() => vi.advanceTimersByTime(26_000))
    firstRender.unmount()
    render(<OnboardingEmailSendStatus startedAt={startedAt} />)

    expect(screen.getByText(/26s elapsed/)).toHaveAttribute("aria-hidden", "true")
    expect(screen.getByRole("status")).toHaveTextContent(
      "Onboarding email is taking longer than expected",
    )
  })
})
