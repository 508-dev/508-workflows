# Discord Gig Dashboard

The operations dashboard tracks Discord forum posts that represent pending gigs.
It is intentionally a local workflow layer, not yet the system of record for
projects, CRM accounts, or ERP records.

## Data Model

Gig rows live in `engagements` with `lifecycle_stage = 'pending_gig'`.
Candidate and applicant rows live in `engagement_applications`.

The supported gig statuses are:

- `lead`
- `recruiting`
- `contacted`
- `filled`
- `unknown`
- `lost`
- `outdated`
- `duplicate`

The bot parses visible status markers from Discord forum thread titles, such as
`[RECRUITING]`. Dashboard status updates are preserved during later bot
indexing and match upserts unless Discord explicitly provides a non-unknown
status transition. This avoids silently reverting dashboard-only updates.

## Channel Registration

Use `/register-jobs-channel` in a Discord forum channel to make the bot index
new and existing posts as gigs. Registered channels are stored in
`job_post_channels`. On bot startup, configured existing forum channels are
auto-registered and backfilled so existing server gigs appear in the dashboard
after launch. `DISCORD_DEFAULT_JOB_FORUM_CHANNELS` defaults to
`gigs:part_time,fulltime-roles:full_time`; use a comma-separated
`channel-name:posting_type` list to override it.

Each registered channel has a posting type:

- `part_time`
- `full_time`
- `part_time_or_full_time`
- `unknown`

The default is `part_time`. Forum tags on a post can override the channel
default, so a part-time channel can still carry posts that are open to full-time
candidates.

`#unqualified-leads` is a separate holding forum for externally sourced leads.
It is configured by `DISCORD_UNQUALIFIED_LEADS_FORUM_CHANNEL`, which defaults to
`unqualified-leads` and accepts either the exact forum name or its Discord ID.
Do not register this forum with `/register-jobs-channel` or include it in
`DISCORD_DEFAULT_JOB_FORUM_CHANNELS`: staging posts there intentionally create
neither a gig record nor automatic candidate matches.

## Dashboard Access

`GET /dashboard/api/gigs` returns only `pending_gig` rows.

Steering Committee+ dashboard users can see all gigs. Non-Steering users can
log in and see only gigs they originally posted, based on
`posted_by_discord_user_id`.

Dashboard gig mutations also require `lifecycle_stage = 'pending_gig'`, so the
gig controls cannot mutate future non-gig project engagement rows by id.

## Sourced Job Lead Review

Open **Leads** from the dashboard sidebar. Each lead title opens a detail page
you can bookmark or share with another Steering operator.

- **To post** shows pending leads without a saved holding thread. Post straight
  from a row or open the title to read the full listing, see application links,
  and reject the lead if it does not belong in the holding forum.
- **Qualification** defaults to pending leads in the holding forum. Open a lead
  to qualify or reject it. The status filter also offers **Unstaged** for direct
  qualification, **Qualified** for promotion, **Rejected** for restoration,
  and **Posted** for the resulting Discord and dashboard gig links.
- **Sources** holds the manual sync controls and the latest results by source.
  For Hacker News, this includes the monthly thread link and counts for
  comments, posts scanned, included leads, and filtered posts.

Posting to the holding forum creates a Discord thread for review. Promoting a
qualified lead creates a fresh thread in a registered gigs forum, creates the
dashboard gig, and makes it eligible for automatic matching.

If a holding thread was deleted, open the affected lead, choose **Holding
thread deleted**, and confirm the thread is gone in Discord. This works for
both a saved holding post and a post left behind by an interrupted attempt.
The reset is audited and returns a pending lead to **To post**. For an
interrupted attempt without a saved thread ID, check the forum for the lead
before confirming.

A lead detail includes the employment type and classification method, the full
normalized listing, and application links and contact email when available.
Model-proposed links and email addresses must appear verbatim in the source
post. Re-scraping can correct pending or rejected leads while preserving
approved and posted leads.

## External Job Lead Sources

The **Sources** tab offers manual syncs for TokyoDev, Japan Dev, Himalayas, a16z,
and a limited Sequoia portfolio pilot. a16z sync reads up to the first 25 public search
results for each of five gig terms, deduplicates jobs, and keeps only roles
whose titles explicitly mention contract, contractor, freelance, fractional,
or part-time work. It stores job metadata and the direct application link,
without copying full job descriptions. Search result coverage is limited by
the board's public first page. Titles that describe a contract domain (such as
smart contracts or contract administration) or explicitly say full-time are
excluded from this title-only pilot.

TokyoDev and Japan Dev import active co-op listings across employment types.
They use the employment type published on the listing when available and fall
back to the existing classifier when that field is absent or unspecified.
The **Employment type** filter shows full-time, part-time or contract, roles
offering both, and listings with unknown terms.
The [employment classification benchmark](job-lead-classification-benchmark.md)
records the evaluation cases and the Jev-first routing results.

All sources require a clear engineering, product, data, design, marketing,
or ecommerce role in the job title or HN header before admitting a lead. The
filter excludes recruiting, sales, clinical, language, and data annotation
roles even when their descriptions mention technical skills. A sync also marks
previously unreviewed, unstaged off-topic leads from that source as rejected so
they leave the review queue. Reviewed, posted, and staged leads remain untouched.
The source status reports role mismatches and this cleanup separately. Operators
can restore a rejected lead if the title filter missed a suitable role; later
syncs preserve that manual override.

Operators can run all sources together or trigger any source alone. Each
source has its own latest-run status and counts, and leads show a source label.
The source and employment-type filters apply before the 50-lead page limit.
Himalayas leads show country and UTC-offset eligibility where supplied by the
API. If a later Himalayas page fails, completed pages are saved and that
source's status card labels the sync as partial so an operator can retry it.

The Sequoia pilot reads Reddit's public Greenhouse job feed. Reddit is a
Sequoia portfolio company; this is **not** a complete Sequoia job board sync.
The Consider-powered Sequoia board's terms require written permission for
automated access, so this adapter uses the portfolio company's public ATS
feed. Both sources enter the existing human review queue. Title matches are
signals to verify during review, not proof that the role is open to contractors.

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

## Gig Status Reminders

`GIG_RECRUITING_STALE_DAYS` controls when a recruiting gig is considered stale.
The default is `7`.
`GIG_CONTACTED_REMINDER_DAYS` controls how long a gig may remain in `CONTACTED`
before a follow-up reminder. The default is `5`.
`GIG_RECRUITING_REMINDER_MAX_AGE_DAYS` bounds reminders and stale notifications
to recently posted gigs. The default is `90`, so old backfilled Discord posts are
not treated as active recruiting work.

The dashboard notification tray uses `GET /dashboard/api/notifications` to show
recruiting gigs whose latest known activity is older than the configured
threshold, and contacted gigs that have remained in that status for the
configured follow-up interval.
The main gig list hides historical terminal statuses (`LOST`, `DUPLICATE`, and
`OUTDATED`) by default. Steering/admin viewers can opt into historical gigs when
they need those records.

The Discord bot also runs a periodic reminder loop. When a stale recruiting or
contacted gig has a Discord thread and original poster, it replies in the thread
and mentions the poster asking for a status update. Sent reminders update
`last_status_reminder_at` but do not advance `last_activity_at`, so passive
reminders do not make stale gigs look active. Contacted-gig reminders use the
time the status changed, rather than later thread activity, to preserve the
five-day follow-up cadence.
Locked or archived Discord gig threads are treated as done and marked outdated
instead of receiving a reminder.

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
