import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ContactEmailIntakePanel } from "./contact-email-intake-panel"

afterEach(cleanup)

describe("ContactEmailIntakePanel", () => {
  it("documents the alias flow and approval boundary without exposing mailbox credentials", () => {
    render(<ContactEmailIntakePanel />)

    expect(screen.getByRole("heading", { name: "Contact email intake" })).toBeVisible()
    expect(screen.getAllByText("contacts@508.dev")).toHaveLength(3)
    expect(screen.getAllByText("workflows@508.dev")).toHaveLength(3)
    expect(screen.getByText("Delivered-To")).toBeVisible()
    expect(screen.getByText(/never needs a staff member's inbox password/i)).toBeVisible()
    expect(
      screen.getByText(/only action that creates or updates an EspoCRM contact/i),
    ).toBeVisible()
  })
})
