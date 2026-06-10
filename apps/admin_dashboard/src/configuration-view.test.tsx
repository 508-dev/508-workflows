import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { type ConfigurationItem, ConfigurationView } from "./main"

afterEach(() => cleanup())

function configItem(overrides: Partial<ConfigurationItem>): ConfigurationItem {
  return {
    key: "TEST_SETTING",
    label: "Test setting",
    category: "CRM",
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
    key: "ESPO_BASE_URL",
    label: "EspoCRM base URL",
    description: "Base URL used for CRM API calls.",
    value_type: "url",
    configured: true,
    restart_required: true,
    value: "https://crm.example.com",
  }),
  configItem({
    key: "ESPO_API_KEY",
    label: "EspoCRM API key",
    description: "API key used by CRM clients.",
    is_secret: true,
    configured: true,
    restart_required: true,
    masked_value: "esp...key",
    secret_encryption_configured: true,
  }),
  configItem({
    key: "CRM_SYNC_INTERVAL_SECONDS",
    label: "CRM sync interval",
    description: "Background CRM sync interval.",
    value_type: "int",
    configured: true,
    value: 900,
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

    expect(screen.getByRole("heading", { name: "CRM" })).toBeVisible()
    expect(screen.getByRole("heading", { name: "AI Providers" })).toBeVisible()
    const crmTable = screen.getByRole("table", { name: "CRM configuration settings" })
    expect(within(crmTable).getByText("EspoCRM base URL")).toBeVisible()
    expect(within(crmTable).getByText("EspoCRM API key")).toBeVisible()
    expect(screen.getByText("CRM sync interval")).not.toBeVisible()

    fireEvent.click(screen.getByText("Advanced"))

    expect(screen.getByText("CRM sync interval")).toBeVisible()
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

    expect(screen.queryByRole("heading", { name: "CRM" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "AI Providers" })).toBeVisible()
    expect(screen.getByText("OpenAI API key")).toBeVisible()
    expect(screen.getByText("sec...lue")).toBeVisible()
    expect(screen.getByText("Encryption key missing")).toBeVisible()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })
})
