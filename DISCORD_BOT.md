# Discord Bot

This document captures Discord bot behavior, permissions, and slash command usage.

## Overview

- Bot package: `apps/discord_bot`
- Main entrypoint: `discord-bot` (`uv run --package discord-bot-app discord-bot`)
- Core command cogs: `apps/discord_bot/src/five08/discord_bot/cogs/`
- Bot settings: `apps/discord_bot/src/five08/discord_bot/config.py`

## Permissions

Access for key command groups:

- `Admin`:
  - `/mark-id-verified`
- `Steering Committee`:
  - `/kimai-status`, `/link-discord-user`, `/unlinked-discord-users`, `/set-github-username` (for others), `/upload-resume` (for others), `mark-id-verified` requires `Admin`.
- `Member`:
  - `/search-members`, `/get-resume`, `/view-skills`

## Slash Commands

### Kimai Commands

- `kimai-project-hours` (Steering Committee+ or project team lead)
  - Description: Get hours logged for a project with team-member breakdown.
  - Args:
    - `project_name` (required)
    - `month` (optional, `YYYY-MM`)
    - `start_date` (optional, `YYYY-MM-DD`, overrides `month`)
    - `end_date` (optional, `YYYY-MM-DD`)

- `kimai-projects`
  - Description: List available projects in Kimai.

- `kimai-status`
  - Description: Check Kimai API connection status.

### CRM Commands

- `search-members`
  - Description: Search for candidates/members in the CRM.
  - Args:
    - `query` (optional)
    - `skills` (optional, comma-separated)

- `crm-status`
  - Description: Check CRM API accessibility.

- `get-resume`
  - Description: Download and send a contact's resume.
  - Args:
    - `query` (required)

- `mark-id-verified`
  - Description: Mark a contact as ID verified.
  - Args:
    - `search_term` (required): Email, 508 username, or name.
    - `verified_by` (required): Verifier 508 username or Discord mention.
    - `id_type` (required): ID type used (example values: `passport`, `driver's license`).
    - `verified_at` (optional): Date verified (defaults to today).
  - CRM fields updated:
    - `cIdVerifiedAt` ← `verified_at`
    - `cIdVerifiedBy` ← `verified_by`
    - `cVerifiedIdType` ← `id_type`

- `link-discord-user`
  - Description: Link a Discord user to a CRM contact.
  - Args:
    - `user` (required)
    - `search_term` (required)

- `unlinked-discord-users`
  - Description: List Discord members with `Member` role not linked in CRM.

- `view-skills`
  - Description: View structured skills for yourself or a specific member.
  - Args:
    - `search_term` (optional)

- `set-github-username`
  - Description: Set GitHub username on a CRM contact.
  - Args:
    - `github_username` (required)
    - `search_term` (optional)

- `upload-resume`
  - Description: Upload resume, extract profile fields, and preview CRM updates.
  - Args:
    - `file` (required)
    - `search_term` (optional)
    - `overwrite` (optional)
    - `link_user` (optional)
