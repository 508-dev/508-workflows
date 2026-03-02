# Discord Slash Commands

This document lists all slash commands currently available in the Discord bot.

## Kimai Commands

- `/kimai-project-hours` (Steering Committee+ or team lead)
  - Description: Get hours logged for a project with breakdown by team members.
  - Arguments:
    - `project_name` (required) — Name of the project to query.
    - `month` (optional) — Month in `YYYY-MM` format; leave empty for current month.
    - `start_date` (optional) — Custom start date (`YYYY-MM-DD`), overrides `month`.
    - `end_date` (optional) — Custom end date (`YYYY-MM-DD`).

- `/kimai-projects`
  - Description: List available projects in Kimai.
  - Access: Steering Committee+ users can see all projects; team leads see their projects.

- `/kimai-status` (Steering Committee)
  - Description: Check Kimai API connection status.

## CRM Commands

- `/search-members` (Member)
  - Description: Search for candidates/members in the CRM.
  - Arguments:
    - `query` (optional) — Name, Discord username, email, or 508 email.
    - `skills` (optional) — Comma-separated skills (AND match).

- `/crm-status`
  - Description: Check CRM API accessibility.

- `/get-resume` (Member)
  - Description: Download and send a contact's resume.
  - Arguments:
    - `query` (required) — Email, 508 email, or Discord username.

- `/mark-id-verified` (Admin)
  - Description: Mark a contact as ID verified and record verifier details.
  - Arguments:
    - `search_term` (required): Email, 508 username, or name.
    - `verified_by` (required): Verifier 508 username or Discord mention.
    - `id_type` (required): Type of ID used for verification (example: `passport`, `driver's license`).
    - `verified_at` (optional): Date verified (defaults to today).
  - Writes to CRM fields:
    - `cIdVerifiedAt` from `verified_at`
    - `cIdVerifiedBy` from `verified_by`
    - `cVerifiedIdType` from `id_type`

- `/link-discord-user` (Steering Committee+)
  - Description: Link a Discord user to a CRM contact.
  - Arguments:
    - `user` (required) — Discord user mention.
    - `search_term` (required) — Email, 508 email, name, or contact ID.

- `/unlinked-discord-users` (Steering Committee+)
  - Description: List Discord users with Member role who aren't linked in CRM.

- `/view-skills`
  - Description: View structured skills for yourself or a specific member.
  - Arguments:
    - `search_term` (optional) — @mention, email, 508 username, name, or contact ID.

- `/set-github-username`
  - Description: Set GitHub username for a CRM contact.
  - Arguments:
    - `github_username` (required) — GitHub username.
    - `search_term` (optional) — If omitted, sets your own.
  - Note: Setting another person requires Steering Committee+.

- `/upload-resume`
  - Description: Upload resume, extract profile fields, and preview CRM updates.
  - Arguments:
    - `file` (required) — Resume file (PDF, DOC, DOCX, TXT).
    - `search_term` (optional) — Email, name, or contact ID to target.
    - `overwrite` (optional) — Replace existing resumes instead of appending.
    - `link_user` (optional) — Discord user to link to the contact.

