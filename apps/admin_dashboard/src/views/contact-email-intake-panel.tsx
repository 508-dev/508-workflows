import { ArrowRight, Mail, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

const intakeAddress = "contacts@508.dev"
const workflowMailboxAddress = "workflows@508.dev"

export function ContactEmailIntakePanel() {
  return (
    <Card aria-labelledby="contactEmailIntakeTitle">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle id="contactEmailIntakeTitle">Contact email intake</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Forward a conversation to create a reviewed contact candidate, never an unreviewed CRM
              contact.
            </p>
          </div>
          <Badge variant="neutral">Assisted review</Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid items-center gap-2 rounded-md border bg-muted/30 p-3 text-sm md:grid-cols-[1fr_auto_1fr_auto_1fr]">
          <span className="font-semibold">Forwarded email</span>
          <ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />
          <code className="rounded bg-background px-2 py-1 font-semibold">{intakeAddress}</code>
          <ArrowRight className="size-4 text-muted-foreground" aria-hidden="true" />
          <code className="rounded bg-background px-2 py-1 font-semibold">
            {workflowMailboxAddress}
          </code>
        </div>

        <div className="grid gap-3 text-sm md:grid-cols-2">
          <div className="grid gap-1 rounded-md border p-3">
            <span className="flex items-center gap-2 font-semibold">
              <Mail className="size-4" aria-hidden="true" />
              Mail setup
            </span>
            <p className="text-muted-foreground">
              Create <code>{intakeAddress}</code> as an alias that delivers to the existing service
              mailbox <code>{workflowMailboxAddress}</code>. The worker reuses that mailbox&apos;s
              server-side IMAP credentials; it never needs a staff member&apos;s inbox password.
            </p>
          </div>
          <div className="grid gap-1 rounded-md border p-3">
            <span className="flex items-center gap-2 font-semibold">
              <ShieldCheck className="size-4" aria-hidden="true" />
              Recipient guard
            </span>
            <p className="text-muted-foreground">
              Preserve the original recipient in a <code>Delivered-To</code> or{" "}
              <code>X-Original-To</code> header. Intake should accept only messages delivered to{" "}
              <code>{intakeAddress}</code>, even though the mailbox ultimately receives them at{" "}
              <code>{workflowMailboxAddress}</code>.
            </p>
          </div>
        </div>

        <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
          After parsing, each message enters a dashboard review queue with editable name, email,
          links, and duplicate matches. Approving a candidate is the only action that creates or
          updates an EspoCRM contact.
        </p>
      </CardContent>
    </Card>
  )
}
