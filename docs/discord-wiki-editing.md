# Discord Wiki Editing

`/wiki-update` prepares a reviewable shared Outline wiki change from an
explicit Discord request. It is intentionally separate from member-safe
`/wiki` search: the bot never receives a credential that can mutate Outline.

## Workflow

Only Workflows Engineer, Steering Committee, Admin, and Owner roles can start
an update. The backend re-evaluates those current role-derived scopes whenever
a request is created, revised, canceled, viewed, or published.

```text
/wiki-update (explicit request)
  -> durable request + immutable queued proposal revision
  -> worker starts bounded OMP authoring with read-only host tools
  -> proposed title, summary, source references, and backend-computed diff
  -> Discord private review packet: full article + complete diff + safe links
  -> requester acknowledges that exact packet
  -> Discord: Publish / Revise / Cancel / Refresh
  -> current-role + ownership + stale-document checks
  -> one recorded Outline create/update attempt
  -> article link, conflict, or reconciliation state
```

The authoring runtime can search and read shared Outline pages, read a selected
organization-visible thread snapshot, search organization-visible knowledge,
and submit one typed draft. It has no database connection, Discord token,
Outline credential, generic HTTP tool, shell, filesystem tool, or publishing
tool. OMP runs with no persistent session in the initial release; the durable
Postgres workflow is the source of truth for request/proposal state.

All Outline reads are server-filtered to `WIKI_OUTLINE_COLLECTION_ID` before a
title, excerpt, or document body reaches OMP. Search excerpts are
discovery-only: they provide a document ID but never a citable source ID. A
complete read is permitted only for the prevalidated update target or an ID
from that filtered search, and private, deleted, or nonexistent IDs receive
the same unavailable result. Sources must be opened through a read-only tool
before the model may cite them.

Organization knowledge is supplemental and fail-closed. The worker supplies
only current organization-memory facts with a verified high-trust authority
(`admin_confirmed` or `authoritative`); private, project-scoped, stale,
lower-trust, or metadata-incomplete facts are omitted.

The loop has a 32-call, 32-source, and 32,000-character admitted-source
budget; a target article is capped at 16,000 characters and an opted-in public
Discord snapshot at 12,000.

`include_current_thread` is opt-in. The bot rejects private threads from this
path. Private memories are never selected automatically and are not shared with
the authoring model.

## Review and provenance

The response card shows compact target metadata, organization audience, source
count, and authoring summary. The complete proposed article, complete
server-computed unified diff, and only safe HTTP(S) source links are sent to the
requester in one ephemeral Discord attachment. The attachment is bounded at
1 MB; an over-limit draft is rejected before it becomes reviewable rather than
being silently shortened. Raw selected Discord text is not returned by the API
or copied into audit logs.

The requester must press **Acknowledge review** for that exact immutable packet
before the backend will allocate an Outline write attempt. The acknowledgement
records the requester, timestamp, and packet hash in Postgres; it cannot be
replaced or cleared. A new revision has a fresh proposal ID and must be
reviewed and acknowledged again. Each proposal revision is immutable: revision
feedback starts a new queued revision with the same durable request and a fresh
document snapshot.

For an update, the service records the complete target article's content hash
and Outline revision before authoring. Immediately before publishing it fetches
the article again and compares both. A changed article becomes a conflict that
must be revised; it is never overwritten. The configured article-size limit is
also a data-boundary: a larger article is rejected, not truncated and sent to
the model.

## Publishing safety

Publishing is a backend side effect, not an OMP tool call or a worker retry.
The service and persistence layer both reject publish attempts unless the
proposal owner has durably acknowledged the current complete review packet.
The backend records a unique publish operation and its `write_started` state
before making the single Outline request. Repeated Publish clicks see that
operation and cannot issue a second request.

- A known Outline 409 becomes a reviewable conflict.
- A successful provider response stores the article ID, URL, content hash, and
  revision.
- A timeout, lost response, or other ambiguous outcome becomes
  `publish_unknown`. It is never retried automatically; an operator must
  inspect Outline and use a newly reviewed update if a follow-up write is
  required. The workflow never guesses that an ambiguous create/update was
  safe to repeat.

## OMP deployment boundary

Install the pinned `omp-rpc` package with the worker and set
`WIKI_OMP_COMMAND` to a single trusted OMP executable. The worker invokes it
through `scripts/wiki-omp-launcher.sh`, which clears the process environment,
uses a newly created empty scratch directory, and forwards only
`OPENROUTER_API_KEY`. Run the executable in an isolated container/sidecar with
controlled egress and no project or home-directory mount. The launcher adds
RPC, no-session, no-native-tools, no-skills, no-rules, no-extensions, no-LSP,
and no-PTY flags; the backend still enforces the host-tool allowlist.

The base worker image contains the launcher but deliberately does not download
an OMP binary at build time. Enable this feature only from a reviewed custom
worker image (or a controlled mounted binary) that pins the OMP release, then
set `WIKI_OMP_COMMAND` to that executable's absolute path.

Do not set `WIKI_EDITING_ENABLED=true` until all required configuration is
present. See the [Configuration Reference](./configuration.md) for every
setting.
