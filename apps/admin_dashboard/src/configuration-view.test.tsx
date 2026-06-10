import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { type ConfigurationItem, ConfigurationView } from "./main"

afterEach(() => cleanup())

function configItem(overrides: Partial<ConfigurationItem>): ConfigurationItem {
  return {
    key: "TEST_SETTING",
    label: "Test setting",
    category: "Onboarding",
    description: "Test setting description.",
    value_type: "string",
    is_secret: false,
    env_locked: false,
    source: "default",
    configured: false,
    restart_required: false,
    value: "",
    masked_value: null,
    secret_encryption_configured: null,
    ...overrides,
  }
}

const items: ConfigurationItem[] = [
  configItem({
    key: "DOCUSEAL_BASE_URL",
    label: "DocuSeal base URL",
    description: "DocuSeal API endpoint used for agreement workflows.",
    value_type: "url",
    configured: true,
    value: "https://docuseal.example.com",
  }),
  configItem({
    key: "DOCUSEAL_API_KEY",
    label: "DocuSeal API key",
    description: "DocuSeal API key for agreement workflows.",
    is_secret: true,
    configured: true,
    masked_value: "doc...key",
    secret_encryption_configured: true,
  }),
  configItem({
    key: "DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID",
    label: "DocuSeal member agreement template",
    description: "Template ID used to filter/sign member agreements.",
    value_type: "int",
    configured: true,
    value: 123,
  }),
  configItem({
    key: "OPENAI_API_KEY",
    label: "OpenAI API key",
    category: "AI",
    description: "Primary OpenAI-compatible API key.",
    is_secret: true,
    configured: true,
    masked_value: "sec...lue",
    secret_encryption_configured: false,
  }),
]

describe("ConfigurationView", () => {
  it("groups settings and keeps tuning rows behind advanced disclosure", () => {
    render(
      <ConfigurationView
        items={items}
        loading={{}}
        canWrite
        onRefresh={vi.fn()}
        onSave={vi.fn()}
        onClear={vi.fn()}
      />,
    )

    expect(screen.getByRole("heading", { name: "Onboarding" })).toBeVisible()
    expect(screen.getByRole("heading", { name: "AI Providers" })).toBeVisible()
    const onboardingTable = screen.getByRole("table", {
      name: "Onboarding configuration settings",
    })
    expect(within(onboardingTable).getByText("DocuSeal base URL")).toBeVisible()
    expect(within(onboardingTable).getByText("DocuSeal API key")).toBeVisible()
    expect(screen.getByText("DocuSeal member agreement template")).not.toBeVisible()

    fireEvent.click(screen.getByText("Advanced"))

    expect(screen.getByText("DocuSeal member agreement template")).toBeVisible()
  })

  it("filters to a selected group and disables impossible secret saves", () => {
    const onSave = vi.fn()
    render(
      <ConfigurationView
        items={items}
        loading={{}}
        canWrite
        onRefresh={vi.fn()}
        onSave={onSave}
        onClear={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: /AI Providers/ }))

    expect(screen.queryByRole("heading", { name: "Onboarding" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "AI Providers" })).toBeVisible()
    expect(screen.getByText("OpenAI API key")).toBeVisible()
    expect(screen.getByText("sec...lue")).toBeVisible()
    expect(screen.getByText("Encryption key missing")).toBeVisible()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("disables save for empty non-secret drafts so clear remains explicit", () => {
    render(
      <ConfigurationView
        items={items.slice(0, 3)}
        loading={{}}
        canWrite
        onRefresh={vi.fn()}
        onSave={vi.fn()}
        onClear={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText("DocuSeal base URL value"), {
      target: { value: "" },
    })
    const primaryTable = screen.getByRole("table", {
      name: "Onboarding configuration settings",
    })
    expect(within(primaryTable).getAllByRole("button", { name: "Save" })[0]).toBeDisabled()

    fireEvent.click(screen.getByText("Advanced"))
    fireEvent.change(screen.getByLabelText("DocuSeal member agreement template value"), {
      target: { value: "" },
    })

    const advancedTable = screen.getByRole("table", {
      name: "Onboarding advanced configuration settings",
    })
    expect(within(advancedTable).getByRole("button", { name: "Save" })).toBeDisabled()
  })
})
