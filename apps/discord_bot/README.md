# Discord Bot

This document captures Discord bot behavior, permissions, and slash command usage.

## Overview

- Bot package: `apps/discord_bot`
- Main entrypoint: `discord-bot` (`uv run --package discord_bot discord-bot`)
- Core command cogs: `apps/discord_bot/src/five08/discord_bot/cogs/`
- Bot settings: `apps/discord_bot/src/five08/discord_bot/config.py`

## Operation Model

The Discord bot is the user-facing gateway for human commands. It should stay
thin: receive a Discord interaction, resolve Discord context, call the backend
or an integration client, render the result, and emit best-effort audit events
for human-triggered writes.

At startup, `Bot508` automatically loads every cog module in
`five08.discord_bot.cogs` and then syncs slash commands with Discord. The bot
also starts a small aiohttp HTTP server for health checks and authenticated
internal callbacks such as member-agreement role application.

Typical command flow:

```text
Discord slash command
  -> cog validates Discord roles / request shape
  -> cog calls backend API or integration client
  -> backend/service performs durable work or synchronous operation
  -> cog renders ephemeral or channel-visible result
  -> cog emits best-effort audit event when appropriate
```

The bot uses `API_SHARED_SECRET` for protected backend/internal calls. Agent
routes use a separate `AGENT_SHARED_SECRET`, because they carry role context;
in deployed environments it must differ from `API_SHARED_SECRET` and be shared
only with the backend API. Command authorization still belongs in role checks,
backend policy checks, and resource-level checks.

In deployed environments, agent permissions are bound to the configured
`DISCORD_SERVER_ID` and immutable role IDs, not mutable role names. Configure
each capability bundle with the corresponding `AGENT_DISCORD_*_ROLE_IDS`
variable. If the same Billing / ERP Dev role grants
both bundles, list its ID in both variables. Missing mappings fail closed.

## Server Diagnostics

`/diagnostics` is a private, read-only role discovery panel for the one
configured `DISCORD_SERVER_ID`. The Discord server owner and members with the
native **Manage Server** permission can list role names and immutable IDs, view
agent role-binding health, refresh the role snapshot, and download a copyable
configuration export. It never grants a role, updates deployment configuration,
or reveals secret values. Native Discord permission is intentionally used for
this bootstrap utility so administrators can discover IDs before configuring
`AGENT_DISCORD_ADMIN_ROLE_IDS`.

The operations dashboard exposes the same snapshot at **Discord diagnostics**
for users with `configuration:read`; it retrieves data through the bot's
authenticated internal endpoint and remains read-only.

## Agent Gateway

The `/agent` command and explicit bot mentions send natural-language requests to
the backend agent gateway. The bot does not execute agent tool calls directly and
does not hold extra service credentials for agent actions.

Agent command flow:

```text
/agent request or @bot mention
  -> bot resolves Discord user/guild/channel/role context
  -> POST /agent/requests on the backend API
  -> backend uses a structured proposal planner when configured, with deterministic fallback
  -> backend validates the typed plan and authorizes every proposed tool
  -> deterministic backend policy authorizes each proposed tool
  -> read actions execute synchronously
  -> public web reads may return bounded observations to the planner for up to 3 turns
  -> write actions return a frozen confirmation plan
  -> Discord confirmation button calls POST /agent/confirmations/{plan_id}
  -> backend executes the exact frozen plan inline and returns the result
```

The backend agent package keeps read and write tools separate, applies
capability checks before every tool call, requires confirmation for writes, and
audits request/confirmation attempts. The model only drafts bounded tool calls;
it cannot authorize users or execute integrations. Supported workflows include
tasks, GitHub issues, CRM contacts, member agreements, account provisioning,
private memory, and bounded public-web research according to the requester's
roles. Only public web tool output can enter the bounded planner follow-up loop;
CRM, ERP, task, and private-memory results are never passed to it. Public web
queries reject obvious private identifiers before a provider is contacted.
Long-running service changes should be implemented as PR-based workflows rather
than direct production mutations. Task reads require an explicit project filter
to avoid guild-wide task enumeration.

Mention flow is opt-in by default: the bot runs the agent when directly
mentioned in a server channel or thread. The only unmentioned continuation path
is a bot-owned thread named `Agent response`, which is created for public-safe
mention clarifications. Other bot-created threads, including job forum posts,
still require an explicit bot mention. Mention-triggered agent results and
confirmation buttons are sent by DM to avoid leaking task or plan details into
public channels.

Production mention handling depends on Discord gateway and channel access:
The bot requests all intents in code, but the production Discord application
should have the Message Content privileged intent enabled or approved in the
Developer Portal. Direct mentions expose message content even without that
intent, but unmentioned follow-up messages in dedicated agent response threads
need it because they do not mention the bot. The bot also needs channel
permissions to view the channel, send messages, create public threads, and send
messages in threads.

Audit writes are best-effort and do not block command execution. If the audit
store is unavailable, treat the agent surface as temporarily untraced until the
audit pipeline is healthy again.

Pending confirmation plans and the MVP task store are currently process-local in
the backend API. Confirmation plans expire after 10 minutes with opportunistic
cleanup during agent requests and confirmations. A production multi-process
deployment should move pending plans to Redis or another shared TTL store and
swap the task registry for a durable task service before relying on cross-process
agent behavior.

Relevant configuration:

- `BACKEND_API_BASE_URL`: backend API used by the bot.
- `API_SHARED_SECRET`: shared service secret for non-agent protected backend calls.
- `AGENT_SHARED_SECRET`: dedicated credential for `/agent/*`; required outside
  explicit local/test migration mode and must differ from `API_SHARED_SECRET`.
- `AGENT_ALLOW_LEGACY_API_SECRET`: local/test-only opt-in to the old agent
  credential fallback; leave `false` for deployed environments.
- `DISCORD_SERVER_ID`: the single guild allowlist for agent requests and
  confirmations; production blocks an unset or mismatched guild before planning.
- `AGENT_DISCORD_ADMIN_ROLE_IDS`, `AGENT_DISCORD_STEERING_COMMITTEE_ROLE_IDS`,
  `AGENT_DISCORD_BILLING_ROLE_IDS`, `AGENT_DISCORD_ERP_DEVELOPER_ROLE_IDS`,
  `AGENT_DISCORD_PROJECT_MANAGER_ROLE_IDS`, `AGENT_DISCORD_ENGINEER_ROLE_IDS`:
  role-ID bindings for the corresponding agent capability bundles.
- `AGENT_ALLOW_ROLE_NAME_FALLBACK`: explicit local/test-only migration aid;
  role names never authorize deployed agent requests.
- `AGENT_API_TIMEOUT_SECONDS`: timeout for synchronous agent gateway requests.
- `AGENT_FAST_*`, `AGENT_STRONG_*`, `AGENT_REASONING_*`: backend model
  tier configuration for OpenAI-compatible providers. Credentials stay in the
  backend process; the bot only receives non-secret plan metadata.
- `AGENT_PLANNING_MAX_STEPS`: cap for public-web research/answer turns.
- `AGENT_REQUEST_RESPONSE_BUDGET_SECONDS`: backend caller-visible limit for
  synchronous agent planning/reads; keep it below `AGENT_API_TIMEOUT_SECONDS`.
- `AGENT_PUBLIC_WEB_DEADLINE_SECONDS`: best-effort public-web loop budget; keep
  it below the response budget.
- `AGENT_WEB_SEARCH_PROVIDER_ORDER`: configured fallback order for `searxng`,
  `brave`, and `firecrawl`.
- `SEARXNG_BASE_URL`, `BRAVE_SEARCH_API_KEY`, `FIRECRAWL_API_KEY`: configure
  one or more web providers. Brave and Firecrawl keys stay in the backend.
- `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `AGENT_ERP_ORGANIZATION_ID`: enable
  bounded Billing/ERP reads. The organization ID must exactly match the one
  Discord guild/organization authorized to use the configured ERP credentials;
  reads fail closed when it is missing or does not match.
- `RESUME_AI_*`: optional resume-specific extraction provider for direct CRM
  resume parsing in the bot; falls back to the normal `OPENAI_*` settings.

## Permissions

- **Member**: receives no privileged agent scopes.
- **Steering Committee**: project/task/CRM/member-agreement and scoped memory
  workflows, plus agent chat and public-web research; it cannot provision
  accounts, manage integrations, deploy, or inherit Admin automatically.
- **Billing**: may read narrowly whitelisted Sales/Purchase invoice summaries
  and supplier lookups, plus agent chat/public-web research. Billing writes,
  CRM, provisioning, and Admin authority are not exposed through the agent.
- **ERP Developer**: may read narrowly whitelisted ERP project summaries, plus
  agent chat/public-web research. ERP writes, billing, CRM, provisioning, and
  Admin authority are not exposed through the agent.
- **Admin / Owner**: receives the full administrative and specialist scope set.

## Wiki Search

`/wiki` is a private, read-only Outline search surface for members. With no
query it shows the starred quick links from the dedicated Outline integration
account; with `query:` it searches that account's published, member-safe
documents and returns short excerpts plus Outline links.

Configure `OUTLINE_CONTENTS_API_KEY` separately from
`OUTLINE_ADMIN_API_KEY`. The contents key must belong to a regular account that
has access only to collections safe for every Discord `Member`, and should be
scoped to `documents.search`, `documents.info`, and `stars.list`.
`DISCORD_SERVER_ID` is required: `/wiki` refuses DMs and other guilds. The same
member-safe key supports project wiki matching in the dashboard. Search queries
and result snippets are not audit logged.

## Slash Commands

- `/agent`
  - Description: Send a natural-language task request through the backend agent gateway.
  - Behavior:
    - Sends Discord user, guild, channel, role, and interaction context to the backend.
    - Executes allowed read-only task lookups synchronously.
    - Shows a frozen confirmation plan for task writes before execution.
    - Confirms or cancels writes through backend confirmation endpoints.
  - Guardrails:
    - The bot does not authorize agent tool calls itself.
    - Backend policy checks scopes and tenant context before every tool execution.
    - Agent write actions are audited and require confirmation.

- `@bot ...`
  - Description: Run the same agent gateway from a normal channel or thread message.
  - Behavior:
    - Strips the bot mention and sends the remaining text as the agent request.
    - Replies in the same channel or thread.
    - Supports the same confirmation buttons for writes.
  - Example: `@508.dev Bot show tasks for project Atlas`

- `/dashboard-login`
  - Description: Generate a one-time operations dashboard login link.
  - Required role: validated by the backend against active CRM-linked Discord roles. Steering Committee+ can access CRM/onboarding dashboard views; Admin/Owner roles can receive admin permissions, with sensitive job/audit/sync views requiring SSO validation.
  - Behavior:
    - Calls backend `POST /auth/discord/links` using `API_SHARED_SECRET`.
    - Sends the invoking member's Discord role names so local/dev/test backends can authorize links when the local people cache is empty.
    - Returns an ephemeral one-time URL with expiry.
    - Opening the URL loads a short browser page that automatically continues with a POST. The link is not consumed by the initial GET, so normal Discord link previews and security scanners do not burn the token.

- `/wiki`
  - Description: Search member-safe Outline documents or show quick links.
  - Required role: Member.
  - Args: `query` (optional). Omit it to show quick links curated by the
    integration account's Outline stars.
  - Behavior: Returns up to five published search results, each with a short
    excerpt, last-updated date, and an Outline link. Responses are ephemeral.
  - Search syntax: supports quoted phrases, `OR`, and `-exclude`.

- `/payment-info`
  - Description: View masked ERPNext Supplier Details payment info and open a modal to update your own payment details.
  - Behavior:
    - Resolves only the invoking Discord user through CRM `cDiscordUserID`.
    - Requires the linked CRM contact to have a matching `@508.dev` email and ERPNext User/Supplier identity.
    - Stores updates in the Supplier `supplier_details` field.
    - Sends all responses ephemerally.
    - Does not accept a user/contact/supplier argument or provide an admin override path.

- `/create-mailbox`
  - Description: Create a Migadu mailbox for a 508 user, optionally link it to a CRM contact, and sync `c508Email`.
  - Prerequisites: `MIGADU_API_USER` and `MIGADU_API_KEY` must be configured (configured in env; command will fail if missing).
  - Newsletter sync: if Brevo and/or Keila are configured, the new 508 mailbox and backup email are added to the configured 508 members audience. Brevo uses `BREVO_508_MEMBERS_NEWSLETTER_LIST_ID`, or the list named by `BREVO_508_MEMBERS_NEWSLETTER_LIST_NAME` (default `508 members`) when the explicit ID is unset. Newsletter failures are shown as warnings and do not block mailbox creation.
  - Required role: Admin
  - Args:
    - `mailbox_username` (required): 508 mailbox username or address. If the domain is omitted, `@508.dev` is added automatically.
    - `search_term` (optional): CRM lookup by email, name, Discord username, or contact ID. Bare terms first search contact name and Discord username, then fall back to `c508Email = {term}@508.dev` if needed.
    - `name` (optional): Full mailbox name. Defaults from the matched CRM contact when available.
    - `backup_email` (optional unless `search_term` is omitted): Full backup email where the invite should be sent. Defaults from the matched CRM contact when available.
  - Behavior:
    - Rejects explicit mailbox domains other than `@508.dev`.
    - Aborts before creation if the matched CRM contact already has a `c508Email`.
    - Prompts for contact selection when multiple eligible CRM matches are found.
    - Updates the linked CRM contact `c508Email` after mailbox creation and reports partial failure if that sync fails.

- `/mark-id-verified`
  - Description: Mark a contact as ID verified.
  - Required role: Admin
  - Args:
    - `search_term` (required): Email, 508 username, or name.
    - `verified_by` (required): Verifier 508 username or Discord mention.
    - `id_type` (optional): ID type used (example: `passport`, `driver's license`).
    - `verified_at` (optional): Date verified (defaults to today).
  - CRM fields updated:
    - `cIdVerifiedAt` ← `verified_at`
    - `cIdVerifiedBy` ← `verified_by`
    - `cVerifiedIdType` ← `id_type`

- `/create-sso-user`
  - Description: Create or link an Authentik SSO user for a CRM contact.
  - Required role: Admin
  - Prerequisites: `AUTHENTIK_API_BASE_URL` and `AUTHENTIK_API_TOKEN` must be configured. Recovery emails resolve the Authentik Email Stage by `AUTHENTIK_RECOVERY_EMAIL_STAGE_NAME` (defaults to `default-recovery-email`), with `AUTHENTIK_RECOVERY_EMAIL_STAGE_ID` available as an override.
  - Args:
    - `search_term` (required): Discord mention, email, 508 username, or contact ID.
  - Behavior:
    - Derives `username` from the contact's `@508.dev` email.
    - Supports Discord mentions by resolving the contact from `cDiscordUserID`.
    - If `cSsoID` is already populated, retrieves that Authentik user and validates it still matches the CRM-derived username/email.
    - If `cSsoID` is blank, searches Authentik by exact username and exact `@508.dev` email before creating anything.
    - Updates CRM `cSsoID` with the Authentik numeric user id (`pk`).
    - Sends the Authentik recovery email only when the user was newly created, auto-resolving the Email Stage by name unless a UUID override is configured.

- `/send-member-agreement`
  - Description: Send the member agreement for signature through DocuSeal.
  - Required role: Steering Committee
  - Prerequisites: `DOCUSEAL_BASE_URL`, `DOCUSEAL_API_KEY`, and `DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID` must be configured. For DocuSeal Cloud, set `DOCUSEAL_BASE_URL=https://api.docuseal.com`. For self-hosted DocuSeal, this is usually `https://your-host/api`.
  - Args:
    - `search_term` (required): Email, 508 email, Discord username, name, or contact ID.
  - Guardrails:
    - Does not send when the contact already has `cMemberAgreementSignedAt` set.
    - Requires a CRM email address on the contact.

- `/search-members`
  - Description: Search for candidates/members in the CRM.
  - Args:
    - `query` (required; accepts `skills:python,sql` for skills-only search, `john skills:python,sql` for combined search, and `me`/`self`/`myself` to look up your own CRM profile)
    - `show_skills` (optional; explicit detailed skills output)
  - Notes:
    - Use `query: me`, `query: self`, or `query: myself` to replace the old self-lookup flow from `/view-skills`.
    - Add `show_skills:true` to return the detailed skills embed for a single match.
  - Examples:
    - `/search-members query:me`
    - `/search-members query:myself`
    - `/search-members query:me show_skills:true`
    - `/search-members query:"john skills:python,sql"`

- `/crm-status`
  - Description: Check CRM API accessibility.

- `/get-resume`
  - Description: Download and send a contact's resume.
  - Args:
    - `query` (required)

- `/link-discord-user`
  - Description: Link a Discord user to a CRM contact.
  - Args:
    - `user` (required)
    - `search_term` (required)

- `/unlinked-discord-users`
  - Description: List Discord members with `Member` role not linked in CRM.

- `/set-github-username`
  - Description: Set GitHub username on a CRM contact.
  - Args:
    - `github_username` (required)
    - `search_term` (optional)

- `/upload-resume`
  - Description: Upload resume, extract profile fields, and preview CRM updates.
  - Args:
    - `file` (required)
    - `search_term` (optional)
    - `overwrite` (optional)
    - `link_user` (optional)
