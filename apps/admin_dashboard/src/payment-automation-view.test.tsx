import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  type PaymentAutomationRule,
  type PaymentAutomationSuggestion,
  PaymentAutomationView,
} from "./views/payment-automation-view"

afterEach(cleanup)

const projects = [
  { id: "project-1", display_name: "Acme launch", source_status: "Open" },
  { id: "project-2", display_name: "Northstar rollout", source_status: "Open" },
]

const rules: PaymentAutomationRule[] = [
  {
    id: "rule-1",
    project_id: "project-1",
    priority: 10,
    mode: "automatic",
    enabled: true,
    version: 2,
    conditions: [
      { fact: "transaction.direction", operator: "equals", value: "inbound" },
      { fact: "transaction.amount", operator: "equals", value: "1500.00" },
    ],
    actions: [{ action_type: "project_payment.route", payload: { project_id: "project-1" } }],
  },
]

const suggestions: PaymentAutomationSuggestion[] = [
  {
    id: "suggestion-1",
    project_id: "project-1",
    payload: { project_id: "project-1" },
    mode: "suggest",
    status: "awaiting_review",
    attempts: 0,
    subject_id: "BT-0001",
    subject_snapshot: {
      amount: "1500.00",
      currency: "GBP",
      counterparty: "Acme Ltd",
      description: "Launch deposit",
      reference_number: "ACME-42",
    },
  },
]

function renderPaymentAutomationView(
  overrides: Partial<ComponentProps<typeof PaymentAutomationView>> = {},
) {
  return render(
    <PaymentAutomationView
      projects={projects}
      rules={rules}
      suggestions={suggestions}
      loading={{}}
      canWrite
      onRefresh={vi.fn()}
      onCreateRule={vi.fn().mockResolvedValue(true)}
      onDisableRule={vi.fn().mockResolvedValue(true)}
      onApproveSuggestion={vi.fn().mockResolvedValue(true)}
      onRejectSuggestion={vi.fn().mockResolvedValue(true)}
      {...overrides}
    />,
  )
}

describe("PaymentAutomationView", () => {
  it("builds a safely constrained automatic payment routing rule", async () => {
    const onCreateRule = vi.fn().mockResolvedValue(true)
    renderPaymentAutomationView({ onCreateRule })

    fireEvent.change(screen.getByLabelText("Rule project"), { target: { value: "project-1" } })
    fireEvent.change(screen.getByLabelText("Rule mode"), { target: { value: "automatic" } })
    fireEvent.change(screen.getByLabelText("Rule identity value"), {
      target: { value: "Acme Ltd" },
    })
    fireEvent.change(screen.getByLabelText("Rule currency"), { target: { value: "gbp" } })
    fireEvent.change(screen.getByLabelText("Rule exact amount"), { target: { value: "1500.00" } })
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }))

    await waitFor(() => expect(onCreateRule).toHaveBeenCalledTimes(1))
    expect(onCreateRule).toHaveBeenCalledWith({
      project_id: "project-1",
      priority: 0,
      mode: "automatic",
      enabled: true,
      conditions: [
        { fact: "transaction.direction", operator: "equals", value: "inbound" },
        { fact: "transaction.counterparty", operator: "contains", value: "Acme Ltd" },
        { fact: "transaction.currency", operator: "equals", value: "GBP" },
        { fact: "transaction.amount", operator: "equals", value: "1500.00" },
      ],
      actions: [
        {
          action_type: "project_payment.route",
          payload: { project_id: "project-1", amount: "1500.00" },
        },
      ],
    })
  })

  it("requires explicit confirmation before an allocation decision", () => {
    const onApproveSuggestion = vi.fn().mockResolvedValue(true)
    renderPaymentAutomationView({ onApproveSuggestion })

    fireEvent.click(screen.getByRole("button", { name: "Approve allocation" }))
    expect(onApproveSuggestion).not.toHaveBeenCalled()
    expect(
      screen.getByText("Approve this project allocation and queue the channel notification?"),
    ).toBeVisible()

    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }))
    expect(onApproveSuggestion).toHaveBeenCalledWith(suggestions[0])
  })

  it("filters rules and suggestions to the selected project", () => {
    renderPaymentAutomationView()

    fireEvent.change(screen.getByLabelText("Filter payment automation by project"), {
      target: { value: "project-2" },
    })

    expect(screen.getByText("No payment rules match this filter.")).toBeVisible()
    expect(screen.getByText("No payment suggestions are waiting for review.")).toBeVisible()
  })

  it("labels feedback-derived rules so an operator can distinguish them", () => {
    renderPaymentAutomationView({
      rules: [{ ...rules[0], origin: "learned", mode: "suggest" }],
    })

    expect(screen.getByText("learned")).toBeVisible()
    expect(screen.queryByText("configured")).not.toBeInTheDocument()
  })
})
