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

## Hacker News Job Lead Review

The Gigs page can scrape employer posts from Hacker News “Who is hiring?”
threads into a review queue. Each lead shows an explicit employment type such
as `Full-time`, `Part-time / contract`, or `Full-time or part-time / contract`,
plus whether the result came from the LLM classifier or keyword fallback.

**Post to #unqualified-leads** is the default screening path: it creates a
holding thread without matching candidates. It is optional, however; an
operator may **Qualify lead** directly from the dashboard and then **Promote to
Discord**. Promotion always creates a fresh thread in a registered gigs forum.
Discord forum threads cannot be moved between forums, so a staged lead is
reposted from its source content; only the promoted thread becomes a dashboard
gig and is eligible for automatic matching.

If Discord creates a holding thread but its database marker cannot be saved and
the bot cannot delete that orphaned thread, the lead is intentionally blocked
from restaging. Delete the orphaned thread in Discord, then use **Clear holding
recovery block** on the lead in the dashboard. This Steering-only action clears
only the recovery block and is audited; it does not qualify or promote the lead.

The Leads tab retains the latest scrape result so operators can confirm that a
monthly thread was found. It links to the discovered HN thread and reports HN's
comment count, direct top-level posts scanned as potential gigs, included leads,
filtered posts (with reasons), and records created or refreshed. A found thread
with zero top-level posts is reported separately from a missing thread; re-run
the scrape as new employers add comments.

When a post contains them, the review card exposes the role-specific
application page and a direct contact email as separate actions. Model-proposed
links and email addresses are accepted only when they appear verbatim in the
source post; deterministic scoring prefers application pages over company
homepages. Re-scraping can correct pending or rejected leads that were
previously admitted by a keyword false positive, while approved or posted leads
remain unchanged.

Lead cards keep a compact excerpt by default and can expand to show the full
normalized HN comment. Rejected leads can be restored to `pending` for another
review; the restoration clears the prior review marker while the dashboard audit
trail records the action. Posting a lead keeps the reviewer on the Leads tab,
marks that card `Posted`, and exposes links to the resulting Discord thread and
dashboard gig when available.

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
