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

The bot uses `API_SHARED_SECRET` for protected backend/internal calls. That
shared secret authenticates service-to-service traffic; command authorization
still belongs in role checks, backend policy checks, and resource-level checks.

## Agent Gateway

The `/agent` command sends natural-language requests to the backend agent
gateway. The bot does not execute agent tool calls directly and does not hold
extra service credentials for agent actions.

Agent command flow:

```text
/agent request
  -> bot resolves Discord user/guild/channel/role context
  -> POST /agent/requests on the backend API
  -> backend parses the request into a typed plan
  -> deterministic backend policy authorizes each proposed tool
  -> read actions execute synchronously
  -> write actions return a frozen confirmation plan
  -> Discord confirmation button calls POST /agent/confirmations/{plan_id}
  -> backend executes the exact frozen plan inline and returns the result
```

Current MVP scope is task-style commands only. The backend agent package keeps
read and write tools separate, applies capability checks before every tool call,
requires confirmation for writes, and audits request/confirmation attempts.
Long-running service changes should be implemented as PR-based workflows rather
than direct production mutations.

Relevant configuration:

- `BACKEND_API_BASE_URL`: backend API used by the bot.
- `API_SHARED_SECRET`: shared service secret for protected backend calls.
- `AGENT_API_TIMEOUT_SECONDS`: timeout for synchronous agent gateway requests.

## Permissions

- **Everyone**: can see and invoke non-restricted commands.
- **Member**: has member-only command access in addition to everyone commands.
- **Steering Committee**: includes member permissions and adds additional moderation/admin-assist commands.
- **Admin**: can run sensitive writes such as ID verification updates.

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

- `/login`
  - Description: Generate a one-time admin dashboard login link.
  - Required role: any role listed in `DISCORD_ADMIN_ROLES` (`Admin,Owner` by default).
  - Behavior:
    - Calls backend `POST /auth/discord/links` using `API_SHARED_SECRET`.
    - Returns an ephemeral one-time URL with expiry.

- `/create-mailbox`
  - Description: Create a Migadu mailbox for a 508 user, optionally link it to a CRM contact, and sync `c508Email`.
  - Prerequisites: `MIGADU_API_USER` and `MIGADU_API_KEY` must be configured (configured in env; command will fail if missing).
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
