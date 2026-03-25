# Discord Bot

This document captures Discord bot behavior, permissions, and slash command usage.

## Overview

- Bot package: `apps/discord_bot`
- Main entrypoint: `discord-bot` (`uv run --package discord_bot discord-bot`)
- Core command cogs: `apps/discord_bot/src/five08/discord_bot/cogs/`
- Bot settings: `apps/discord_bot/src/five08/discord_bot/config.py`

## Permissions

- **Everyone**: can see and invoke non-restricted commands.
- **Member**: has member-only command access in addition to everyone commands.
- **Steering Committee**: includes member permissions and adds additional moderation/admin-assist commands.
- **Admin**: can run sensitive writes such as ID verification updates.

## Slash Commands

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
  - Prerequisites: `DOCUSEAL_BASE_URL`, `DOCUSEAL_API_KEY`, and `DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID` must be configured.
  - Args:
    - `search_term` (required): Email, 508 email, Discord username, name, or contact ID.
  - Guardrails:
    - Does not send when the contact already has `cMemberAgreementSignedAt` set.
    - Requires a CRM email address on the contact.

- `/search-members`
  - Description: Search for candidates/members in the CRM.
  - Args:
    - `query` (optional)
    - `skills` (optional, comma-separated)

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

- `/view-skills`
  - Description: View structured skills for yourself or a specific member.
  - Args:
    - `search_term` (optional)

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
