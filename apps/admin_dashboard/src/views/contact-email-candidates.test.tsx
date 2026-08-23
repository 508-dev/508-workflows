import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ContactEmailCandidatesPanel } from "./contact-email-candidates"

afterEach(cleanup)

describe("ContactEmailCandidatesPanel", () => {
  it("keeps the candidate editable and dispatches an explicit approval action", () => {
    const onReview = vi.fn()
    render(
      <ContactEmailCandidatesPanel
        canWrite
        loading={false}
        onReview={onReview}
        candidates={[
          {
            id: "candidate-1",
            status: "pending",
            delivered_to: "contacts@508.dev",
            proposed_name: "Ada Lovelace",
            proposed_email: "ada@example.com",
            subject: "Introduction",
            body_text: "See https://example.com/ada",
            links: ["https://example.com/ada"],
            extraction_method: "inline_forward",
          },
        ]}
      />,
    )

    fireEvent.change(screen.getByLabelText("Contact name for candidate-1"), {
      target: { value: "Ada Byron" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Approve contact" }))

    expect(onReview).toHaveBeenCalledWith(expect.objectContaining({ id: "candidate-1" }), {
      decision: "approve",
      name: "Ada Byron",
      email: "ada@example.com",
    })
    expect(screen.getByRole("link", { name: /https:\/\/example.com\/ada/ })).toHaveAttribute(
      "href",
      "https://example.com/ada",
    )
  })
})
