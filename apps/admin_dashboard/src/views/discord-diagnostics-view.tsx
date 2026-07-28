import { Clipboard, RefreshCw } from "lucide-react"
import { useMemo, useState } from "react"

import { Empty } from "@/components/empty"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export type DiscordDiagnosticRole = {
  id: string
  name: string
  position: number
  managed: boolean
  is_default: boolean
  manageable_by_bot: boolean
}

export type DiscordDiagnosticBindingRole = {
  id: string
  name?: string | null
  status: "resolved" | "missing" | "everyone" | string
  managed?: boolean | null
  manageable_by_bot?: boolean | null
}

export type DiscordDiagnosticBinding = {
  bundle: string
  label: string
  environment_variable: string
  role_ids: string[]
  roles: DiscordDiagnosticBindingRole[]
  status: "resolved" | "attention" | "unconfigured" | string
}

export type DiscordDiagnosticsResponse = {
  guild: {
    id: string
    name: string
    configured_server_matches: boolean
  }
  snapshot: {
    created_at: string
    source: string
    refresh_error?: string | null
  }
  bot: {
    manage_roles: boolean
    top_role?: {
      id: string
      name: string
      position: number | null
    } | null
  }
  agent: {
    configured_role_count: number
    resolved_role_count: number
    missing_role_count: number
    unconfigured_binding_count: number
    agent_shared_secret_status: string
    role_bindings: DiscordDiagnosticBinding[]
  }
  roles: DiscordDiagnosticRole[]
}

function snapshotSourceLabel(source: string) {
  return source === "discord_api" ? "Refreshed from Discord" : "Gateway cache"
}

function secretStatusLabel(status: string) {
  if (status === "separate") return "Configured separately"
  if (status === "configured") return "Configured"
  if (status === "matches_api_shared_secret") return "Matches API credential"
  return "Missing"
}

function roleCatalogText(diagnostics: DiscordDiagnosticsResponse) {
  const header = [
    `# Discord roles for ${diagnostics.guild.name} (${diagnostics.guild.id})`,
    "# name\trole ID\tmetadata",
  ]
  const roles = diagnostics.roles.map((role) => {
    const flags = [
      role.is_default ? "@everyone" : "",
      role.managed ? "managed" : "",
      role.manageable_by_bot ? "manageable-by-bot" : "",
    ].filter(Boolean)
    return `${role.name}\t${role.id}\t${flags.join(", ") || "standard"}`
  })
  return [...header, ...roles].join("\n")
}

function configurationText(diagnostics: DiscordDiagnosticsResponse) {
  const lines = [
    "# Review before adding these values to deployment configuration.",
    "# This text does not modify Discord roles or agent permissions.",
    `DISCORD_SERVER_ID=${diagnostics.guild.id}`,
  ]
  for (const binding of diagnostics.agent.role_bindings) {
    lines.push(`${binding.environment_variable}=${binding.role_ids.join(",")}`)
  }
  return lines.join("\n")
}

async function copyToClipboard(
  value: string,
  successMessage: string,
  onNotice: (message: string, tone?: "ok" | "error") => void,
) {
  if (!navigator.clipboard) {
    onNotice("Clipboard access is unavailable in this browser.", "error")
    return
  }
  try {
    await navigator.clipboard.writeText(value)
    onNotice(successMessage)
  } catch {
    onNotice("Unable to copy diagnostics text.", "error")
  }
}

function roleFlags(role: DiscordDiagnosticRole) {
  return [
    role.is_default ? "@everyone" : "",
    role.managed ? "Managed" : "",
    role.manageable_by_bot ? "Manageable by bot" : "",
  ].filter(Boolean)
}

export function DiscordDiagnosticsView({
  diagnostics,
  loading,
  onRefresh,
  onNotice,
}: {
  diagnostics: DiscordDiagnosticsResponse | null
  loading?: boolean
  onRefresh: () => void
  onNotice: (message: string, tone?: "ok" | "error") => void
}) {
  const [query, setQuery] = useState("")
  const filteredRoles = useMemo(() => {
    if (!diagnostics) return []
    const normalizedQuery = query.trim().toLowerCase()
    if (!normalizedQuery) return diagnostics.roles
    return diagnostics.roles.filter(
      (role) =>
        role.name.toLowerCase().includes(normalizedQuery) || role.id.includes(normalizedQuery),
    )
  }, [diagnostics, query])

  if (!diagnostics) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Discord diagnostics</CardTitle>
          <Button onClick={onRefresh} disabled={loading}>
            <RefreshCw className={loading ? "animate-spin" : ""} />
            Load diagnostics
          </Button>
        </CardHeader>
        <Empty hidden={false}>Load the configured server's read-only role catalog.</Empty>
      </Card>
    )
  }

  const snapshotWarning = diagnostics.snapshot.refresh_error
  const topRole = diagnostics.bot.top_role
  return (
    <div className="grid gap-5">
      <Card>
        <CardHeader className="items-start">
          <div className="grid gap-1">
            <CardTitle>Discord diagnostics</CardTitle>
            <p className="text-sm text-muted-foreground">
              Read-only server role discovery and agent role-ID configuration health.
            </p>
          </div>
          <Button onClick={onRefresh} disabled={loading}>
            <RefreshCw className={loading ? "animate-spin" : ""} />
            Refresh from Discord
          </Button>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <div>
            <div className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Server
            </div>
            <div className="mt-1 font-semibold">{diagnostics.guild.name}</div>
            <code className="text-xs text-muted-foreground">{diagnostics.guild.id}</code>
          </div>
          <div>
            <div className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Catalog
            </div>
            <div className="mt-1 font-semibold">{diagnostics.roles.length} roles</div>
            <div className="text-xs text-muted-foreground">
              {snapshotSourceLabel(diagnostics.snapshot.source)}
            </div>
          </div>
          <div>
            <div className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Agent bindings
            </div>
            <div className="mt-1 font-semibold">
              {diagnostics.agent.resolved_role_count} resolved ·{" "}
              {diagnostics.agent.missing_role_count} missing
            </div>
            <div className="text-xs text-muted-foreground">
              {diagnostics.agent.unconfigured_binding_count} unconfigured bundle(s)
            </div>
          </div>
          <div>
            <div className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
              Bot role access
            </div>
            <div className="mt-1 font-semibold">
              {diagnostics.bot.manage_roles ? "Can manage roles" : "Cannot manage roles"}
            </div>
            <div className="text-xs text-muted-foreground">
              {topRole ? `Top role: ${topRole.name}` : "Bot member unavailable"}
            </div>
          </div>
          <div className="md:col-span-2 xl:col-span-4">
            <Badge
              variant={
                diagnostics.agent.agent_shared_secret_status === "separate" ||
                diagnostics.agent.agent_shared_secret_status === "configured"
                  ? "succeeded"
                  : "missing"
              }
            >
              Agent credential: {secretStatusLabel(diagnostics.agent.agent_shared_secret_status)}
            </Badge>
            <span className="ml-2 text-xs text-muted-foreground">
              Secret values are never displayed.
            </span>
          </div>
          {snapshotWarning ? (
            <div className="md:col-span-2 xl:col-span-4 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              {snapshotWarning}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Copyable configuration</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              These actions only copy text. Apply role IDs through deployment configuration after
              review.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() =>
                void copyToClipboard(
                  configurationText(diagnostics),
                  "Copied role-ID configuration.",
                  onNotice,
                )
              }
            >
              <Clipboard />
              Copy configuration
            </Button>
            <Button
              variant="outline"
              onClick={() =>
                void copyToClipboard(roleCatalogText(diagnostics), "Copied role catalog.", onNotice)
              }
            >
              <Clipboard />
              Copy role catalog
            </Button>
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table aria-label="Agent role ID mappings">
            <TableHeader>
              <TableRow>
                <TableHead>Agent bundle</TableHead>
                <TableHead>Environment variable</TableHead>
                <TableHead>Configured role IDs</TableHead>
                <TableHead>Resolution</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {diagnostics.agent.role_bindings.map((binding) => (
                <TableRow key={binding.bundle}>
                  <TableCell className="font-semibold">{binding.label}</TableCell>
                  <TableCell>
                    <code className="text-xs">{binding.environment_variable}</code>
                  </TableCell>
                  <TableCell>
                    {binding.role_ids.length ? (
                      <code className="break-all text-xs">{binding.role_ids.join(",")}</code>
                    ) : (
                      <span className="text-muted-foreground">Not configured</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-1">
                      {binding.roles.map((role) => (
                        <span key={`${binding.bundle}-${role.id}`} className="text-xs">
                          <Badge variant={role.status === "resolved" ? "succeeded" : "missing"}>
                            {role.status}
                          </Badge>{" "}
                          <code>{role.id}</code>
                          {role.name ? ` — ${role.name}` : ""}
                        </span>
                      ))}
                      {!binding.roles.length ? (
                        <span className="text-xs text-muted-foreground">No roles selected.</span>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Server role catalog</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Names are for discovery only; use immutable role IDs when configuring agent access.
            </p>
          </div>
          <Label className="w-full sm:w-72">
            Search roles
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Role name or ID"
            />
          </Label>
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          <Table aria-label="Discord server roles">
            <TableHeader>
              <TableRow>
                <TableHead>Role</TableHead>
                <TableHead>Role ID</TableHead>
                <TableHead>Position</TableHead>
                <TableHead>Metadata</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredRoles.map((role) => (
                <TableRow key={role.id}>
                  <TableCell className="font-semibold">{role.name}</TableCell>
                  <TableCell>
                    <code className="text-xs">{role.id}</code>
                  </TableCell>
                  <TableCell>{role.position}</TableCell>
                  <TableCell>
                    {roleFlags(role).length ? (
                      <div className="flex flex-wrap gap-1">
                        {roleFlags(role).map((flag) => (
                          <Badge key={flag} variant="neutral">
                            {flag}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <span className="text-muted-foreground">Standard</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Empty hidden={filteredRoles.length > 0}>No roles match that name or ID.</Empty>
        </CardContent>
      </Card>
    </div>
  )
}
