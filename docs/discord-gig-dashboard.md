# Discord Gig Dashboard

The operations dashboard tracks Discord forum posts that represent pending gigs.
It is intentionally a local workflow layer, not yet the system of record for
projects, CRM accounts, or ERP records.

## Data Model

Gig rows live in `engagements` with `lifecycle_stage = 'pending_gig'`.
Candidate and applicant rows live in `engagement_applications`.

The supported gig statuses are:

- `recruiting`
- `filled`
- `unknown`
- `lost`
- `outdated`

The bot parses visible status markers from Discord forum thread titles, such as
`[RECRUITING]`. Dashboard status updates are preserved during later bot
indexing and match upserts unless Discord explicitly provides a non-unknown
status transition. This avoids silently reverting dashboard-only updates.

## Channel Registration

Use `/register-jobs-channel` in a Discord forum channel to make the bot index
new and existing posts as gigs. Registered channels are stored in
`job_post_channels`.

Each registered channel has a posting type:

- `part_time`
- `full_time`
- `part_time_or_full_time`
- `unknown`

The default is `part_time`. Forum tags on a post can override the channel
default, so a part-time channel can still carry posts that are open to full-time
candidates.

## Dashboard Access

`GET /dashboard/api/gigs` returns only `pending_gig` rows.

Steering Committee+ dashboard users can see all gigs. Non-Steering users can
log in and see only gigs they originally posted, based on
`posted_by_discord_user_id`.

Dashboard gig mutations also require `lifecycle_stage = 'pending_gig'`, so the
gig controls cannot mutate future non-gig project engagement rows by id.

## Candidate Sources

`engagement_applications.source` records how a candidate entered the gig flow:

- `match_candidates`: `/match-candidates` or automatic matching
- `direct_interest`: a Discord reply that conservatively expresses interest
- `manual_add`: reserved for dashboard/manual workflows
- `discord`, `crm`, `erp`: reserved for later integrations

Matched candidates can be CRM-backed, Discord-only, or both. Discord-only
candidates must still be persisted so role-only matches remain visible in the
dashboard.

Direct-interest detection is intentionally conservative and includes negation
guards for phrases such as "not available" or "not interested".

## Stale Recruiting Notifications

`GIG_RECRUITING_STALE_DAYS` controls when a recruiting gig is considered stale.
The default is `7`.

The dashboard notification tray uses `GET /dashboard/api/notifications` to show
recruiting gigs whose latest known activity is older than the configured
threshold.

The Discord bot also runs a periodic reminder loop. When a stale recruiting gig
has a Discord thread and original poster, it replies in the thread and mentions
the poster asking for a status update. Sent reminders update
`last_recruiting_reminder_at` but do not advance `last_activity_at`, so passive
reminders do not make stale gigs look active.

Ordinary registered gig thread replies count as activity. This makes the stale
recruiting reminder instruction to "leave a thread reply if it is still active"
match the dashboard stale timer.

Dashboard status updates enqueue Discord title sync in the bot instead of
waiting for Discord thread rename calls inline. Discord can apply long
per-route/resource rate limits to thread edits, so the bot debounces repeated
status changes for the same thread and performs the latest title update in the
background.

## Matching Guardrails

The matching flow should not broaden hard requirements into soft hints.
Required languages remain hard gates in relaxed searches, including languages
extracted by the LLM beyond the static regex list.

Discord roles can help find candidates for role-only or broad searches, but they
should not admit candidates when concrete required skills are present and the
candidate lacks those skills.
