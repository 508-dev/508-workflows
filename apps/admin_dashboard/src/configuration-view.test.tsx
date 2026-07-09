import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import type { ComponentProps } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { type ConfigurationItem, ConfigurationView } from "./views/configuration-view"

afterEach(() => {
  cleanup()
  window.history.replaceState({}, "", "/dashboard/configuration")
})

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

function renderConfigurationView(
  overrides: Partial<ComponentProps<typeof ConfigurationView>> = {},
) {
  return render(
    <ConfigurationView
      items={items}
      loading={{}}
      canWrite
      jobChannels={[]}
      availableJobChannels={[]}
      onRefresh={vi.fn()}
      onRefreshJobChannels={vi.fn()}
      onSave={vi.fn()}
      onClear={vi.fn()}
      onSaveJobChannel={vi.fn()}
      onDeleteJobChannel={vi.fn()}
      {...overrides}
    />,
  )
}

describe("ConfigurationView", () => {
  it("groups settings and keeps tuning rows behind advanced disclosure", () => {
    renderConfigurationView()

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
    renderConfigurationView({ onSave })

    fireEvent.click(screen.getByRole("button", { name: /AI Providers/ }))

    expect(screen.queryByRole("heading", { name: "Onboarding" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "AI Providers" })).toBeVisible()
    expect(screen.getByText("OpenAI API key")).toBeVisible()
    expect(screen.getByText("sec...lue")).toBeVisible()
    expect(screen.getByText("Encryption key missing")).toBeVisible()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
  })

  it("disables save for empty non-secret drafts so clear remains explicit", () => {
    renderConfigurationView({ items: items.slice(0, 3) })

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

  it("keeps onboarding email SMTP settings in the primary onboarding table", () => {
    renderConfigurationView({
      items: [
        configItem({
          key: "ONBOARDING_EMAIL_SMTP_USERNAME",
          label: "Onboarding email SMTP username",
          description: "SMTP username used to authenticate onboarding email sends.",
          configured: true,
          value: "onboarding@508.dev",
        }),
        configItem({
          key: "ONBOARDING_EMAIL_SMTP_PASSWORD",
          label: "Onboarding email SMTP password",
          description: "SMTP password or app password used to send onboarding emails.",
          is_secret: true,
          configured: true,
          masked_value: "sec...ret",
          secret_encryption_configured: true,
        }),
      ],
    })

    const onboardingTable = screen.getByRole("table", {
      name: "Onboarding configuration settings",
    })
    expect(within(onboardingTable).getByText("Onboarding email SMTP username")).toBeVisible()
    expect(within(onboardingTable).getByText("Onboarding email SMTP password")).toBeVisible()
    expect(screen.queryByText("Advanced")).not.toBeInTheDocument()
  })

  it("focuses the requested configuration category", async () => {
    renderConfigurationView({
      focusCategory: "Onboarding",
      focusNonce: 1,
    })

    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "AI Providers" })).not.toBeInTheDocument()
    })
    expect(screen.getByRole("heading", { name: "Onboarding" })).toBeVisible()
  })

  it("keeps the job channels tab selected from the hash and manages channels", async () => {
    window.history.replaceState({}, "", "/dashboard/configuration#job-channels")
    const onSaveJobChannel = vi.fn().mockResolvedValue(true)
    const onDeleteJobChannel = vi.fn()
    renderConfigurationView({
      jobChannels: [
        {
          channel_id: "123",
          channel_name: "gigs",
          posting_type: "part_time",
          requires_tag: true,
          available_tags: [{ name: "React" }],
        },
      ],
      availableJobChannels: [
        {
          channel_id: "123",
          channel_name: "gigs",
          posting_type: "part_time",
          registered: true,
        },
        {
          channel_id: "456",
          channel_name: "fulltime-roles",
          posting_type: "unknown",
          registered: false,
        },
      ],
      onSaveJobChannel,
      onDeleteJobChannel,
    })

    expect(screen.getByRole("table", { name: "Registered job channels" })).toBeVisible()
    expect(screen.getByRole("button", { name: /Job channels/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    )

    fireEvent.change(screen.getByLabelText("Job channel to register"), {
      target: { value: "456" },
    })
    fireEvent.change(screen.getByLabelText("New job channel posting type"), {
      target: { value: "full_time" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Register channel" }))

    await waitFor(() => {
      expect(onSaveJobChannel).toHaveBeenCalledWith("456", "full_time")
    })

    fireEvent.click(screen.getByRole("button", { name: "Deregister" }))
    expect(onDeleteJobChannel).toHaveBeenCalledWith("123")
  })
})
