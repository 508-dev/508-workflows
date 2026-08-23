import { ExternalLink, UserCheck, UserX } from "lucide-react"
import { useState } from "react"

import { Empty } from "@/components/empty"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

export type ContactEmailCandidate = {
  id: string
  status: "pending" | "approved" | "dismissed"
  delivered_to: string
  forwarded_by_name?: string | null
  forwarded_by_email?: string | null
  proposed_name?: string | null
  proposed_email?: string | null
  subject?: string | null
  body_text?: string | null
  links?: string[]
  extraction_method?: string
  created_at?: string
}

export type ContactEmailCandidateDecision = {
  decision: "approve" | "dismiss"
  name?: string
  email?: string
}

function CandidateCard({
  candidate,
  canWrite,
  loading,
  onReview,
}: {
  candidate: ContactEmailCandidate
  canWrite: boolean
  loading: boolean
  onReview: (candidate: ContactEmailCandidate, decision: ContactEmailCandidateDecision) => void
}) {
  const [name, setName] = useState(candidate.proposed_name || "")
  const [email, setEmail] = useState(candidate.proposed_email || "")
  const forwarder = [candidate.forwarded_by_name, candidate.forwarded_by_email]
    .filter(Boolean)
    .join(" · ")

  return (
    <article className="grid gap-3 rounded-md border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold">{candidate.subject || "Forwarded contact"}</h3>
          <p className="text-sm text-muted-foreground">
            {forwarder ? `Forwarded by ${forwarder}` : "Forwarded email"} ·{" "}
            {candidate.extraction_method?.replace(/_/g, " ") || "deterministic extraction"}
          </p>
        </div>
        <Badge variant="queued">Needs review</Badge>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Label>
          Contact name
          <Input
            aria-label={`Contact name for ${candidate.id}`}
            value={name}
            autoComplete="off"
            onChange={(event) => setName(event.target.value)}
            disabled={!canWrite || loading}
          />
        </Label>
        <Label>
          Contact email
          <Input
            aria-label={`Contact email for ${candidate.id}`}
            value={email}
            type="email"
            autoComplete="off"
            onChange={(event) => setEmail(event.target.value)}
            disabled={!canWrite || loading}
          />
        </Label>
      </div>

      {candidate.links?.length ? (
        <div className="flex flex-wrap gap-2 text-sm">
          {candidate.links.map((link) => (
            <a
              key={link}
              className="inline-flex max-w-full items-center gap-1 text-primary underline"
              href={link}
              target="_blank"
              rel="noreferrer"
            >
              <span className="truncate">{link}</span>
              <ExternalLink className="size-3 shrink-0" aria-hidden="true" />
            </a>
          ))}
        </div>
      ) : null}

      {candidate.body_text ? (
        <details className="rounded-md bg-muted/40 p-3 text-sm">
          <summary className="cursor-pointer font-semibold">Source email</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-muted-foreground">
            {candidate.body_text}
          </pre>
        </details>
      ) : null}

      {canWrite ? (
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            disabled={loading || !name.trim() || !email.trim()}
            onClick={() => onReview(candidate, { decision: "approve", name, email })}
          >
            <UserCheck />
            Approve contact
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={loading}
            onClick={() => onReview(candidate, { decision: "dismiss" })}
          >
            <UserX />
            Dismiss
          </Button>
        </div>
      ) : null}
    </article>
  )
}

export function ContactEmailCandidatesPanel({
  candidates,
  canWrite,
  loading,
  onReview,
}: {
  candidates: ContactEmailCandidate[]
  canWrite: boolean
  loading: boolean
  onReview: (candidate: ContactEmailCandidate, decision: ContactEmailCandidateDecision) => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Contact candidates</CardTitle>
        <span className="text-sm text-muted-foreground">
          {loading ? "Loading" : `${candidates.length} awaiting review`}
        </span>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Empty hidden={candidates.length !== 0}>
          No forwarded contacts are waiting for review.
        </Empty>
        {candidates.map((candidate) => (
          <CandidateCard
            key={candidate.id}
            candidate={candidate}
            canWrite={canWrite}
            loading={loading}
            onReview={onReview}
          />
        ))}
      </CardContent>
    </Card>
  )
}
