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

- `/create-mailbox`
  - Description: Create a Migadu mailbox for a 508 user and email the invitation to a backup address.
  - Prerequisites: `MIGADU_API_USER` and `MIGADU_API_KEY` must be configured (configured in env; command will fail if missing).
  - Required role: Admin
  - Args:
    - `mailbox_username` (required): 508 mailbox address (e.g. `alice@508.dev`).
    - `backup_email` (required): Full backup email where invite should be sent (e.g. `alice@gmail.com`).

- `/mark-id-verified`
  - Description: Mark a contact as ID verified.
  - Required role: Admin
  - Args:
    - `search_term` (required): Email, 508 username, or name.
    - `verified_by` (required): Verifier 508 username or Discord mention.
    - `id_type` (required): ID type used (example: `passport`, `driver's license`).
    - `verified_at` (optional): Date verified (defaults to today).
  - CRM fields updated:
    - `cIdVerifiedAt` ← `verified_at`
    - `cIdVerifiedBy` ← `verified_by`
    - `cVerifiedIdType` ← `id_type`

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
