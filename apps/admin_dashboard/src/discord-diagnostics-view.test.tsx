import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  type DiscordDiagnosticsResponse,
  DiscordDiagnosticsView,
} from "./views/discord-diagnostics-view"

afterEach(cleanup)

const diagnostics: DiscordDiagnosticsResponse = {
  guild: {
    id: "123",
    name: "508.dev",
    configured_server_matches: true,
  },
  snapshot: {
    created_at: "2026-07-28T00:00:00Z",
    source: "discord_api",
  },
  bot: {
    manage_roles: true,
    top_role: { id: "900", name: "Bot", position: 10 },
  },
  agent: {
    configured_role_count: 2,
    resolved_role_count: 1,
    missing_role_count: 1,
    unconfigured_binding_count: 5,
    api_shared_secret_status: "configured",
    role_bindings: [
      {
        bundle: "admin",
        label: "Admin",
        environment_variable: "AGENT_DISCORD_ADMIN_ROLE_IDS",
        role_ids: ["456"],
        roles: [
          {
            id: "456",
            name: "Admin",
            status: "resolved",
            managed: false,
            manageable_by_bot: true,
          },
        ],
        status: "resolved",
      },
      {
        bundle: "billing",
        label: "Billing",
        environment_variable: "AGENT_DISCORD_BILLING_ROLE_IDS",
        role_ids: ["999"],
        roles: [{ id: "999", status: "missing" }],
        status: "attention",
      },
    ],
  },
  roles: [
    {
      id: "456",
      name: "Admin",
      position: 9,
      managed: false,
      is_default: false,
      manageable_by_bot: true,
    },
    {
      id: "123",
      name: "@everyone",
      position: 0,
      managed: false,
      is_default: true,
      manageable_by_bot: false,
    },
  ],
}

describe("DiscordDiagnosticsView", () => {
  it("shows copyable role IDs, binding health, and filters the role catalog", () => {
    render(
      <DiscordDiagnosticsView diagnostics={diagnostics} onRefresh={vi.fn()} onNotice={vi.fn()} />,
    )

    expect(screen.getByText("508.dev")).toBeVisible()
    expect(screen.getByText("AGENT_DISCORD_ADMIN_ROLE_IDS")).toBeVisible()
    expect(screen.getByRole("table", { name: "Discord server roles" })).toBeVisible()
    expect(screen.getAllByText("456").length).toBeGreaterThan(0)
    expect(screen.getByText("Secret values are never displayed.")).toBeVisible()

    fireEvent.change(screen.getByLabelText("Search roles"), {
      target: { value: "everyone" },
    })

    const catalog = screen.getByRole("table", { name: "Discord server roles" })
    expect(within(catalog).getAllByText("@everyone")[0]).toBeVisible()
    expect(within(catalog).queryByRole("cell", { name: "Admin" })).not.toBeInTheDocument()
  })
})
