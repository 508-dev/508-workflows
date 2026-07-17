import { Check, Lightbulb, Plus, RefreshCw, X } from "lucide-react"
import { type FormEvent, useMemo, useState } from "react"

import { Empty } from "@/components/empty"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

export type PaymentAutomationProject = {
  id: string
  display_name: string
  source_status?: string
}

export type PaymentAutomationCondition = {
  fact: string
  operator: string
  value?: unknown
}

export type PaymentAutomationAction = {
  action_type: string
  payload: Record<string, unknown>
}

export type PaymentAutomationRule = {
  id: string
  project_id?: string | null
  origin?: "configured" | "learned" | string
  priority: number
  mode: "observe" | "suggest" | "automatic" | string
  enabled: boolean
  version: number
  conditions: PaymentAutomationCondition[]
  actions: PaymentAutomationAction[]
  created_by?: string | null
}

export type PaymentAutomationSuggestion = {
  id: string
  project_id?: string | null
  payload: Record<string, unknown>
  mode: string
  status: string
  attempts: number
  review_decision?: string | null
  subject_id?: string
  subject_snapshot?: Record<string, unknown>
}

export type PaymentAutomationRuleDraft = {
  project_id: string
  priority: number
  mode: "suggest" | "automatic"
  enabled: boolean
  conditions: PaymentAutomationCondition[]
  actions: PaymentAutomationAction[]
}

type IdentifierFact =
  | "transaction.counterparty"
  | "transaction.description"
  | "transaction.reference_number"

const identifierOptions: { value: IdentifierFact; label: string }[] = [
  { value: "transaction.counterparty", label: "Counterparty" },
  { value: "transaction.description", label: "Description" },
  { value: "transaction.reference_number", label: "Reference number" },
]

function conditionLabel(condition: PaymentAutomationCondition) {
  const label = condition.fact.replace("transaction.", "")
  const operator = condition.operator.replaceAll("_", " ")
  const value =
    typeof condition.value === "string"
      ? condition.value
      : condition.value === undefined
        ? ""
        : JSON.stringify(condition.value)
  return `${label} ${operator} ${value}`.trim()
}

function snapshotText(snapshot: Record<string, unknown>, key: string) {
  const value = snapshot[key]
  return typeof value === "string" && value.trim() ? value.trim() : ""
}

function currencyAmount(snapshot: Record<string, unknown>) {
  const amount = snapshotText(snapshot, "amount")
  const currency = snapshotText(snapshot, "currency")
  return [amount, currency].filter(Boolean).join(" ") || "Amount unavailable"
}

function PaymentAutomationView(props: {
  projects: PaymentAutomationProject[]
  rules: PaymentAutomationRule[]
  suggestions: PaymentAutomationSuggestion[]
  loading: Record<string, boolean>
  canWrite: boolean
  onRefresh: () => void
  onCreateRule: (draft: PaymentAutomationRuleDraft) => Promise<boolean>
  onDisableRule: (rule: PaymentAutomationRule) => Promise<boolean>
  onApproveSuggestion: (suggestion: PaymentAutomationSuggestion) => Promise<boolean>
  onRejectSuggestion: (suggestion: PaymentAutomationSuggestion) => Promise<boolean>
}) {
  const [selectedProjectId, setSelectedProjectId] = useState("")
  const [identifierFact, setIdentifierFact] = useState<IdentifierFact>("transaction.counterparty")
  const [identifierValue, setIdentifierValue] = useState("")
  const [currency, setCurrency] = useState("")
  const [amount, setAmount] = useState("")
  const [mode, setMode] = useState<"suggest" | "automatic">("suggest")
  const [priority, setPriority] = useState("0")
  const [formError, setFormError] = useState("")
  const [pendingDecision, setPendingDecision] = useState<{
    suggestion: PaymentAutomationSuggestion
    decision: "approve" | "reject"
  } | null>(null)

  const projectById = useMemo(
    () => new Map(props.projects.map((project) => [project.id, project])),
    [props.projects],
  )
  const visibleRules = selectedProjectId
    ? props.rules.filter((rule) => rule.project_id === selectedProjectId)
    : props.rules
  const visibleSuggestions = selectedProjectId
    ? props.suggestions.filter((suggestion) => suggestion.project_id === selectedProjectId)
    : props.suggestions
  const normalizedCurrency = currency.trim().toUpperCase()
  const normalizedAmount = amount.trim()
  const canSubmit =
    props.canWrite &&
    Boolean(selectedProjectId && identifierValue.trim() && normalizedCurrency) &&
    (mode !== "automatic" || Boolean(normalizedAmount))

  function projectName(projectId?: string | null) {
    if (!projectId) return "Unknown project"
    return projectById.get(projectId)?.display_name || projectId
  }

  async function submitRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) {
      setFormError(
        mode === "automatic"
          ? "Automatic rules require a project, identity match, exact currency, and exact amount."
          : "Choose a project and provide an identity match and currency.",
      )
      return
    }
    const parsedPriority = Number(priority)
    if (!Number.isInteger(parsedPriority) || parsedPriority < -10_000 || parsedPriority > 10_000) {
      setFormError("Priority must be a whole number between -10,000 and 10,000.")
      return
    }
    const conditions: PaymentAutomationCondition[] = [
      { fact: "transaction.direction", operator: "equals", value: "inbound" },
      { fact: identifierFact, operator: "contains", value: identifierValue.trim() },
      { fact: "transaction.currency", operator: "equals", value: normalizedCurrency },
    ]
    const payload: Record<string, unknown> = { project_id: selectedProjectId }
    if (normalizedAmount) {
      conditions.push({ fact: "transaction.amount", operator: "equals", value: normalizedAmount })
      payload.amount = normalizedAmount
    }
    const created = await props.onCreateRule({
      project_id: selectedProjectId,
      priority: parsedPriority,
      mode,
      enabled: true,
      conditions,
      actions: [{ action_type: "project_payment.route", payload }],
    })
    if (!created) return
    setFormError("")
    setIdentifierValue("")
    setCurrency("")
    setAmount("")
    setMode("suggest")
    setPriority("0")
  }

  async function confirmSuggestionDecision() {
    if (!pendingDecision) return
    const { suggestion, decision } = pendingDecision
    const completed =
      decision === "approve"
        ? await props.onApproveSuggestion(suggestion)
        : await props.onRejectSuggestion(suggestion)
    if (completed) setPendingDecision(null)
  }

  return (
    <div className="grid gap-5">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Project payment automation</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Categorize canonical ERP bank receipts, review uncertain matches, and notify only
              registered private project channels.
            </p>
          </div>
          <Button
            id="refreshPaymentAutomation"
            type="button"
            variant="outline"
            onClick={props.onRefresh}
            disabled={props.loading.paymentAutomation}
          >
            <RefreshCw />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="rounded-md border bg-secondary p-3 text-sm text-muted-foreground">
            An approval can create a low-priority learned suggestion when its canonical ERP evidence
            has a payer identity and currency. Learned rules always require review; only configured
            automatic rules with exact identity, currency, and amount may route without review.
          </div>
          <Label className="max-w-md">
            Filter project
            <Select
              aria-label="Filter payment automation by project"
              value={selectedProjectId}
              onChange={(event) => setSelectedProjectId(event.target.value)}
            >
              <option value="">All active projects</option>
              {props.projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.display_name}
                </option>
              ))}
            </Select>
          </Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Create routing rule</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Start with a suggestion. Automatic routing is intentionally stricter.
            </p>
          </div>
          <Badge variant={mode === "automatic" ? "running" : "neutral"}>
            {mode === "automatic" ? "Automatic needs exact proof" : "Human review first"}
          </Badge>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={(event) => void submitRule(event)}>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <Label>
                Project
                <Select
                  aria-label="Rule project"
                  value={selectedProjectId}
                  onChange={(event) => setSelectedProjectId(event.target.value)}
                  disabled={!props.canWrite}
                >
                  <option value="">Choose an active project</option>
                  {props.projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.display_name}
                    </option>
                  ))}
                </Select>
              </Label>
              <Label>
                Rule mode
                <Select
                  aria-label="Rule mode"
                  value={mode}
                  onChange={(event) => setMode(event.target.value as "suggest" | "automatic")}
                  disabled={!props.canWrite}
                >
                  <option value="suggest">Suggest for review</option>
                  <option value="automatic">Route automatically</option>
                </Select>
              </Label>
              <Label>
                Priority
                <Input
                  aria-label="Rule priority"
                  inputMode="numeric"
                  value={priority}
                  onChange={(event) => setPriority(event.target.value)}
                  disabled={!props.canWrite}
                />
              </Label>
              <Label>
                Identity field
                <Select
                  aria-label="Rule identity field"
                  value={identifierFact}
                  onChange={(event) => setIdentifierFact(event.target.value as IdentifierFact)}
                  disabled={!props.canWrite}
                >
                  {identifierOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </Label>
              <Label>
                Identity contains
                <Input
                  aria-label="Rule identity value"
                  value={identifierValue}
                  onChange={(event) => setIdentifierValue(event.target.value)}
                  placeholder="e.g. Acme Ltd"
                  disabled={!props.canWrite}
                />
              </Label>
              <Label>
                Currency
                <Input
                  aria-label="Rule currency"
                  value={currency}
                  onChange={(event) => setCurrency(event.target.value)}
                  placeholder="GBP"
                  maxLength={12}
                  disabled={!props.canWrite}
                />
              </Label>
              <Label>
                Exact amount {mode === "automatic" ? "(required)" : "(optional)"}
                <Input
                  aria-label="Rule exact amount"
                  inputMode="decimal"
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                  placeholder="1250.00"
                  disabled={!props.canWrite}
                />
              </Label>
            </div>
            {formError ? <p className="text-sm text-destructive">{formError}</p> : null}
            {!props.canWrite ? (
              <p className="text-sm text-muted-foreground">
                You can inspect rules and suggestions, but configuration write permission is
                required to change routing.
              </p>
            ) : null}
            <div>
              <Button
                id="createPaymentRule"
                type="submit"
                disabled={!canSubmit || props.loading.paymentRuleCreate}
              >
                <Plus />
                Create rule
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Configured rules</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Disabling a rule retains its event and decision history.
            </p>
          </div>
          <Badge variant="neutral">{visibleRules.length} shown</Badge>
        </CardHeader>
        <Empty hidden={visibleRules.length !== 0}>No payment rules match this filter.</Empty>
        <div className={cn("overflow-x-auto", visibleRules.length === 0 && "hidden")}>
          <Table aria-label="Configured payment routing rules" className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead>Project</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Match conditions</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>Created by</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleRules.map((rule) => (
                <TableRow key={rule.id}>
                  <TableCell>
                    <div className="font-semibold">{projectName(rule.project_id)}</div>
                    <div className="font-mono text-xs text-muted-foreground">{rule.id}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      <Badge variant={rule.mode === "automatic" ? "running" : "neutral"}>
                        {rule.mode}
                      </Badge>
                      <Badge variant={rule.enabled ? "succeeded" : "failed"}>
                        {rule.enabled ? "enabled" : "disabled"}
                      </Badge>
                      <Badge variant={rule.origin === "learned" ? "queued" : "neutral"}>
                        {rule.origin === "learned" ? "learned" : "configured"}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[360px]">
                    <div className="flex flex-wrap gap-1">
                      {rule.conditions.map((condition) => (
                        <Badge key={conditionLabel(condition)} variant="neutral">
                          {conditionLabel(condition)}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>v{rule.version}</TableCell>
                  <TableCell className="max-w-48 truncate text-muted-foreground">
                    {rule.created_by || "Unknown"}
                  </TableCell>
                  <TableCell className="text-right">
                    {rule.enabled && props.canWrite ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => void props.onDisableRule(rule)}
                        disabled={props.loading[`paymentRule:${rule.id}`]}
                      >
                        <X />
                        Disable
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Payment suggestions</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Review the canonical ERP receipt before authorizing a project allocation.
            </p>
          </div>
          <Badge variant="neutral">{visibleSuggestions.length} awaiting review</Badge>
        </CardHeader>
        <Empty hidden={visibleSuggestions.length !== 0}>
          No payment suggestions are waiting for review.
        </Empty>
        <CardContent className={cn("grid gap-3", visibleSuggestions.length === 0 && "hidden")}>
          {visibleSuggestions.map((suggestion) => {
            const snapshot = suggestion.subject_snapshot || {}
            const counterparty = snapshotText(snapshot, "counterparty")
            const description = snapshotText(snapshot, "description")
            const reference = snapshotText(snapshot, "reference_number")
            return (
              <article
                key={suggestion.id}
                className="grid gap-3 rounded-md border bg-background p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h3 className="font-extrabold">{projectName(suggestion.project_id)}</h3>
                    <p className="text-sm text-muted-foreground">
                      {currencyAmount(snapshot)} ·{" "}
                      {counterparty || description || "Unidentified payer"}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <Badge variant="neutral">{suggestion.mode}</Badge>
                    <Badge variant="neutral">{suggestion.status.replaceAll("_", " ")}</Badge>
                  </div>
                </div>
                <dl className="grid gap-2 text-sm md:grid-cols-3">
                  <div>
                    <dt className="font-bold text-muted-foreground">Description</dt>
                    <dd>{description || "—"}</dd>
                  </div>
                  <div>
                    <dt className="font-bold text-muted-foreground">Reference</dt>
                    <dd>{reference || "—"}</dd>
                  </div>
                  <div>
                    <dt className="font-bold text-muted-foreground">ERP transaction</dt>
                    <dd className="font-mono text-xs">{suggestion.subject_id || "—"}</dd>
                  </div>
                </dl>
                {props.canWrite ? (
                  pendingDecision?.suggestion.id === suggestion.id ? (
                    <div className="grid gap-2 rounded-md border border-amber-400/40 bg-amber-500/10 p-3 text-sm">
                      <p>
                        {pendingDecision.decision === "approve"
                          ? "Approve this project allocation and queue the channel notification?"
                          : "Reject this project allocation suggestion?"}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          onClick={() => void confirmSuggestionDecision()}
                          disabled={props.loading[`paymentSuggestion:${suggestion.id}`]}
                        >
                          <Check />
                          Confirm {pendingDecision.decision}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => setPendingDecision(null)}
                          disabled={props.loading[`paymentSuggestion:${suggestion.id}`]}
                        >
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        onClick={() => setPendingDecision({ suggestion, decision: "approve" })}
                        disabled={props.loading[`paymentSuggestion:${suggestion.id}`]}
                      >
                        <Check />
                        Approve allocation
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => setPendingDecision({ suggestion, decision: "reject" })}
                        disabled={props.loading[`paymentSuggestion:${suggestion.id}`]}
                      >
                        <X />
                        Reject
                      </Button>
                    </div>
                  )
                ) : null}
              </article>
            )
          })}
          <div className="flex items-start gap-2 rounded-md border border-dashed p-3 text-sm text-muted-foreground">
            <Lightbulb className="mt-0.5 size-4 shrink-0" />
            Approval and rejection decisions are retained with the source evidence. An eligible
            approval may teach a future suggestion, but it never silently turns into automatic
            payment routing.
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export { PaymentAutomationView }
