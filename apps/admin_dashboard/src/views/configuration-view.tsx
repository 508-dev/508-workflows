import { RefreshCw } from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import { Empty } from "@/components/empty"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
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

type ConfigurationItem = {
  key: string
  label: string
  category: string
  description: string
  value_type: "string" | "bool" | "int" | "float" | "url" | "csv"
  is_secret: boolean
  env_locked: boolean
  source: "env" | "database" | "default"
  configured: boolean
  restart_required: boolean
  secret_encryption_configured?: boolean | null
  value?: string | number | boolean | null
  masked_value?: string | null
}

type ConfigurationResponse = {
  items: ConfigurationItem[]
}

type ConfigurationGroupMetadata = {
  category: string
  label: string
  description: string
}

const configurationGroups: ConfigurationGroupMetadata[] = [
  {
    category: "CRM",
    label: "CRM",
    description: "EspoCRM connection settings used by the API, worker, and Discord bot.",
  },
  {
    category: "Projects",
    label: "Projects",
    description: "ERPNext credentials and project workflow settings.",
  },
  {
    category: "Onboarding",
    label: "Onboarding",
    description:
      "Editable onboarding integrations such as DocuSeal, Outline, and onboarding email SMTP.",
  },
  {
    category: "Newsletter",
    label: "Newsletter",
    description: "Brevo, Keila, and recurring 508 members audience sync settings.",
  },
  {
    category: "AI",
    label: "AI Providers",
    description: "Provider credentials, base URLs, and model defaults.",
  },
  {
    category: "Agent",
    label: "Agent Runtime",
    description: "Planner, fallback, and tiered model routing for agent workflows.",
  },
  {
    category: "Observability",
    label: "Observability",
    description: "Telemetry and request tracing integrations.",
  },
  {
    category: "Intake",
    label: "Intake",
    description: "Resume and mailbox intake limits and parser defaults.",
  },
  {
    category: "Operations",
    label: "Operations",
    description: "Queue, sync, GitHub, and notification behavior.",
  },
  {
    category: "Legacy",
    label: "Legacy",
    description: "Older integrations retained for compatibility.",
  },
]

const configurationGroupByCategory = new Map(
  configurationGroups.map((group, index) => [group.category, { ...group, index }]),
)

function configurationGroupId(category: string) {
  return `configurationGroup-${category.replace(/[^a-zA-Z0-9_-]+/g, "-")}`
}

function isPrimaryConfiguration(item: ConfigurationItem) {
  return (
    item.key.startsWith("ONBOARDING_EMAIL_") ||
    item.is_secret ||
    item.value_type === "url" ||
    item.key.endsWith("_MODEL") ||
    item.key.endsWith("_API_USER") ||
    item.key.endsWith("_BASE_URL")
  )
}

function ConfigurationView({
  items,
  loading,
  canWrite,
  onRefresh,
  onSave,
  onClear,
  focusCategory,
  focusNonce,
}: {
  items: ConfigurationItem[]
  loading: Record<string, boolean>
  canWrite: boolean
  onRefresh: () => void
  onSave: (key: string, value: string) => Promise<boolean>
  onClear: (key: string) => void
  focusCategory?: string
  focusNonce?: number
}) {
  const [selectedCategory, setSelectedCategory] = useState("All")
  const [highlightedCategory, setHighlightedCategory] = useState("")
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [generatedSecrets, setGeneratedSecrets] = useState<Record<string, string>>({})
  const categories = useMemo(() => {
    const present = new Set(items.map((item) => item.category))
    const known = configurationGroups.filter((group) => present.has(group.category))
    const unknown = Array.from(present)
      .filter((category) => !configurationGroupByCategory.has(category))
      .sort()
      .map((category) => ({
        category,
        label: category,
        description: "Additional runtime settings.",
      }))
    return known.concat(unknown)
  }, [items])
  const groupedItems = useMemo(() => {
    const groups = new Map<string, ConfigurationItem[]>()
    for (const item of items) {
      if (selectedCategory !== "All" && item.category !== selectedCategory) continue
      const current = groups.get(item.category) ?? []
      current.push(item)
      groups.set(item.category, current)
    }
    return Array.from(groups.entries())
      .map(([category, groupItems]) => {
        const known = configurationGroupByCategory.get(category)
        const sortedItems = groupItems.sort((left, right) => {
          const priorityOrder =
            Number(!isPrimaryConfiguration(left)) - Number(!isPrimaryConfiguration(right))
          return priorityOrder || left.label.localeCompare(right.label)
        })
        const primaryItems = sortedItems.filter(isPrimaryConfiguration)
        const advancedItems = sortedItems.filter((item) => !isPrimaryConfiguration(item))
        return {
          category,
          label: known?.label ?? category,
          description: known?.description ?? "Additional runtime settings.",
          order: known?.index ?? configurationGroups.length,
          primaryItems: primaryItems.length ? primaryItems : sortedItems,
          advancedItems: primaryItems.length ? advancedItems : [],
          items: sortedItems,
        }
      })
      .sort((left, right) => left.order - right.order || left.label.localeCompare(right.label))
  }, [items, selectedCategory])
  const summary = useMemo(
    () => ({
      configured: items.filter((item) => item.configured).length,
      envLocked: items.filter((item) => item.env_locked).length,
      missing: items.filter((item) => !item.configured).length,
    }),
    [items],
  )
  const visibleItemCount = groupedItems.reduce((count, group) => count + group.items.length, 0)

  useEffect(() => {
    setDrafts(
      Object.fromEntries(
        items.map((item) => [item.key, item.is_secret ? "" : String(item.value ?? "")]),
      ),
    )
  }, [items])

  useEffect(() => {
    if (selectedCategory !== "All" && !items.some((item) => item.category === selectedCategory)) {
      setSelectedCategory("All")
    }
  }, [items, selectedCategory])

  useEffect(() => {
    if (!focusCategory || !items.some((item) => item.category === focusCategory)) return
    void focusNonce
    setSelectedCategory(focusCategory)
    setHighlightedCategory(focusCategory)
    const frame = window.requestAnimationFrame?.(() => {
      const element = document.getElementById(configurationGroupId(focusCategory))
      if (typeof element?.scrollIntoView === "function") {
        element.scrollIntoView({
          block: "start",
          behavior: "smooth",
        })
      }
    })
    const timeout = window.setTimeout(() => setHighlightedCategory(""), 4000)
    return () => {
      if (frame !== undefined) window.cancelAnimationFrame?.(frame)
      window.clearTimeout(timeout)
    }
  }, [focusCategory, focusNonce, items])

  function sourceBadge(item: ConfigurationItem) {
    if (item.source === "env") return "ENV"
    if (item.source === "database") return "DB"
    return "Default"
  }

  function generateSigningSecret() {
    const bytes = new Uint8Array(32)
    window.crypto.getRandomValues(bytes)
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
  }

  async function generateTallySigningSecret(item: ConfigurationItem) {
    const secret = generateSigningSecret()
    setDrafts((current) => ({ ...current, [item.key]: secret }))
    const saved = await onSave(item.key, secret)
    if (saved) {
      setGeneratedSecrets((current) => ({ ...current, [item.key]: secret }))
    }
  }

  async function copyGeneratedSecret(key: string) {
    const secret = generatedSecrets[key]
    if (!secret) return
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(secret)
        return
      } catch {
        // Fall through to selecting the visible field.
      }
    }
    const element = document.getElementById(`generatedSecret-${key}`) as HTMLInputElement | null
    element?.select()
  }

  function valueInput(item: ConfigurationItem) {
    const value = drafts[item.key] ?? ""
    const disabled = !canWrite || item.env_locked || loading[`configuration:${item.key}`]
    if (item.value_type === "bool") {
      return (
        <Select
          aria-label={`${item.label} value`}
          value={value}
          disabled={disabled}
          onChange={(event) =>
            setDrafts((current) => ({ ...current, [item.key]: event.target.value }))
          }
        >
          <option value="">Default</option>
          <option value="true">True</option>
          <option value="false">False</option>
        </Select>
      )
    }
    return (
      <Input
        aria-label={`${item.label} value`}
        value={value}
        type={item.is_secret ? "password" : item.value_type === "int" ? "number" : "text"}
        inputMode={item.value_type === "int" || item.value_type === "float" ? "numeric" : "text"}
        placeholder={item.is_secret ? "Set new value" : ""}
        autoComplete="off"
        disabled={disabled}
        onChange={(event) =>
          setDrafts((current) => ({ ...current, [item.key]: event.target.value }))
        }
      />
    )
  }

  function configurationTable(groupLabel: string, tableItems: ConfigurationItem[], suffix: string) {
    return (
      <div className="overflow-x-auto">
        <Table
          id={`configurationTable-${suffix}`}
          className="min-w-[980px]"
          aria-label={`${groupLabel} configuration settings`}
        >
          <TableHeader>
            <TableRow>
              <TableHead className="w-[26%]">Setting</TableHead>
              <TableHead className="w-[12%]">Source</TableHead>
              <TableHead className="w-[18%]">Active</TableHead>
              <TableHead className="w-[29%]">Value</TableHead>
              <TableHead className="w-[15%]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody id={`configurationBody-${suffix}`}>
            {tableItems.map((item) => renderConfigurationRow(item))}
          </TableBody>
        </Table>
      </div>
    )
  }

  function renderConfigurationRow(item: ConfigurationItem) {
    const busy = loading[`configuration:${item.key}`]
    const writable = canWrite && !item.env_locked && !busy
    const draft = drafts[item.key] ?? ""
    const emptyNonSecretDraft = !item.is_secret && !draft.trim()
    const generatedSecret = generatedSecrets[item.key] || ""
    const showTallySecretGenerator = item.key === "ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET"
    return (
      <TableRow key={item.key}>
        <TableCell>
          <div className="grid gap-1">
            <strong>{item.label}</strong>
            <span className="font-mono text-xs text-muted-foreground">{item.key}</span>
            <span className="text-xs text-muted-foreground">{item.description}</span>
            {item.restart_required ? (
              <div>
                <Badge variant="running">Restart</Badge>
              </div>
            ) : null}
          </div>
        </TableCell>
        <TableCell>
          <div className="grid gap-1.5">
            <Badge variant={item.source === "env" ? "running" : "neutral"}>
              {sourceBadge(item)}
            </Badge>
            {item.env_locked ? (
              <span className="text-xs text-muted-foreground">Environment locked</span>
            ) : null}
          </div>
        </TableCell>
        <TableCell>
          <div className="grid gap-1">
            <Badge variant={item.configured ? "succeeded" : "missing"}>
              {item.configured ? "Configured" : "Missing"}
            </Badge>
            {item.is_secret ? (
              <span className="font-mono text-xs text-muted-foreground">
                {item.masked_value || (item.configured ? "Hidden" : "No secret")}
              </span>
            ) : (
              <span className="break-words text-xs text-muted-foreground">
                {String(item.value ?? "") || "Default"}
              </span>
            )}
            {item.is_secret && item.secret_encryption_configured === false ? (
              <span className="text-xs text-red-300">Encryption key missing</span>
            ) : null}
          </div>
        </TableCell>
        <TableCell>
          <div className="grid gap-2">
            {valueInput(item)}
            {showTallySecretGenerator && generatedSecret ? (
              <div className="grid gap-2 rounded-md border bg-secondary/30 p-2 text-xs">
                <span className="font-extrabold">Copy this secret into Tally now.</span>
                <Input
                  id={`generatedSecret-${item.key}`}
                  value={generatedSecret}
                  readOnly
                  className="font-mono"
                  aria-label="Generated Tally webhook signing secret"
                />
                <span className="text-muted-foreground">
                  It is only shown until this page refreshes or you dismiss it.
                </span>
              </div>
            ) : null}
          </div>
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap justify-end gap-2">
            {showTallySecretGenerator ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void generateTallySigningSecret(item)}
                disabled={
                  !writable ||
                  item.secret_encryption_configured === false ||
                  !window.crypto?.getRandomValues
                }
              >
                Generate
              </Button>
            ) : null}
            {showTallySecretGenerator && generatedSecret ? (
              <>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => void copyGeneratedSecret(item.key)}
                >
                  Copy
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setGeneratedSecrets((current) => {
                      const next = { ...current }
                      delete next[item.key]
                      return next
                    })
                  }
                >
                  Hide
                </Button>
              </>
            ) : null}
            <Button
              type="button"
              size="sm"
              onClick={() => onSave(item.key, draft)}
              disabled={
                !writable ||
                (item.is_secret && !draft.trim()) ||
                emptyNonSecretDraft ||
                (item.is_secret && item.secret_encryption_configured === false)
              }
            >
              Save
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => onClear(item.key)}
              disabled={!writable || item.source !== "database"}
            >
              Clear
            </Button>
          </div>
        </TableCell>
      </TableRow>
    )
  }

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
          <Button
            id="refreshConfiguration"
            type="button"
            variant="outline"
            onClick={onRefresh}
            disabled={loading.configuration}
          >
            <RefreshCw />
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="grid gap-4">
          <section
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
            aria-label="Configuration summary"
          >
            {[
              ["Total", items.length],
              ["Configured", summary.configured],
              ["Missing", summary.missing],
              ["Env locked", summary.envLocked],
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border bg-background p-3">
                <span className="text-[11px] font-extrabold uppercase text-muted-foreground">
                  {label}
                </span>
                <strong className="mt-1 block text-xl">{value}</strong>
              </div>
            ))}
          </section>
          <section className="flex flex-wrap gap-2" aria-label="Configuration groups">
            <Button
              type="button"
              size="sm"
              variant={selectedCategory === "All" ? "default" : "outline"}
              aria-pressed={selectedCategory === "All"}
              onClick={() => setSelectedCategory("All")}
            >
              All groups
              <span className="font-mono text-[11px]">{items.length}</span>
            </Button>
            {categories.map((group) => {
              const count = items.filter((item) => item.category === group.category).length
              return (
                <Button
                  key={group.category}
                  type="button"
                  size="sm"
                  variant={selectedCategory === group.category ? "default" : "outline"}
                  aria-pressed={selectedCategory === group.category}
                  onClick={() => setSelectedCategory(group.category)}
                >
                  {group.label}
                  <span className="font-mono text-[11px]">{count}</span>
                </Button>
              )
            })}
          </section>
        </CardContent>
      </Card>

      <Empty hidden={visibleItemCount !== 0}>No configuration entries found.</Empty>
      {groupedItems.map((group) => {
        const configured = group.items.filter((item) => item.configured).length
        const missing = group.items.length - configured
        const restartRequired = group.items.some((item) => item.restart_required)
        return (
          <Card
            key={group.category}
            id={configurationGroupId(group.category)}
            className={cn(
              "scroll-mt-4 transition-shadow",
              highlightedCategory === group.category &&
                "ring-2 ring-primary ring-offset-2 ring-offset-background",
            )}
          >
            <CardHeader className="items-start">
              <div className="grid gap-1">
                <CardTitle>{group.label}</CardTitle>
                <span className="text-sm text-muted-foreground">{group.description}</span>
              </div>
              <div className="flex flex-wrap justify-end gap-1.5">
                <Badge variant="neutral">{group.items.length} settings</Badge>
                <Badge variant={missing ? "missing" : "succeeded"}>{configured} configured</Badge>
                {restartRequired ? <Badge variant="running">Restart</Badge> : null}
              </div>
            </CardHeader>
            {configurationTable(group.label, group.primaryItems, group.category)}
            {group.advancedItems.length ? (
              <details className="border-t bg-background/40">
                <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-3 px-4 py-3 text-sm font-extrabold">
                  <span>Advanced</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {group.advancedItems.length} settings
                  </span>
                </summary>
                <div className="border-t">
                  {configurationTable(
                    `${group.label} advanced`,
                    group.advancedItems,
                    `${group.category}-advanced`,
                  )}
                </div>
              </details>
            ) : null}
          </Card>
        )
      })}
    </div>
  )
}

export type { ConfigurationItem, ConfigurationResponse }
export { ConfigurationView }
