import { useEffect, useState } from "react"

function elapsedSecondsSince(startedAt: number, now = Date.now()) {
  return Math.max(0, Math.floor((now - startedAt) / 1000))
}

function visualSendProgress(seconds: number) {
  if (seconds < 10) {
    return `Sending — ${seconds}s elapsed. Checking details and waiting for the mail server.`
  }
  if (seconds < 25) {
    return `Still sending — ${seconds}s elapsed. Waiting for the mail server to confirm receipt.`
  }
  return `Taking longer than expected — ${seconds}s elapsed. The request is still active; do not retry yet, as that could send a duplicate email.`
}

function announcedSendPhase(seconds: number) {
  if (seconds < 10) {
    return "Sending onboarding email. Checking details and waiting for the mail server."
  }
  if (seconds < 25) {
    return "Onboarding email is still sending. Waiting for the mail server to confirm receipt."
  }
  return "Onboarding email is taking longer than expected. The request is still active; do not retry yet, as that could send a duplicate email."
}

export function OnboardingEmailSendStatus({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(Date.now)
  const elapsedSeconds = elapsedSecondsSince(startedAt, now)

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [])

  return (
    <>
      <span className="text-sm text-muted-foreground" aria-hidden="true">
        {visualSendProgress(elapsedSeconds)}
      </span>
      <span className="sr-only" role="status">
        {announcedSendPhase(elapsedSeconds)}
      </span>
    </>
  )
}
