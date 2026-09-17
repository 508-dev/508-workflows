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
  -> worker sends a bounded material bundle to isolated OMP authoring
  -> proposed title, summary, source references, and backend-computed diff
  -> Discord private review packet: full article + complete diff + safe links
  -> requester acknowledges that exact packet
  -> Discord: Publish / Revise / Cancel / Refresh
  -> current-role + ownership + stale-document checks
  -> one recorded Outline create/update attempt
  -> article link, conflict, or reconciliation state
```

The credentialed worker sends OMP a fixed, backend-approved material bundle:
the explicit request, opted-in organization-visible thread snapshot, frozen
target article when updating, a few full related articles verified in the same
shared Outline collection, and current high-authority organization-visible
knowledge. Outline search excerpts are candidate selectors only and never
leave the worker. The sandbox returns one typed draft; it has no database
connection, Discord token, Outline credential, publishing tool, or enabled
generic HTTP/shell/filesystem tool. The durable Postgres workflow is the source
of truth for request/proposal state.

The bundle has opaque source IDs. The sandbox may cite only IDs from that
bundle, and the worker rejects any other citation. It has no dynamic Outline or
knowledge tool: that avoids giving untrusted OMP an oracle against privileged
backend integrations.

Organization knowledge is supplemental and fail-closed. The worker supplies
only current organization-memory facts with a verified high-trust authority
(`admin_confirmed` or `authoritative`); private, project-scoped, stale,
lower-trust, or metadata-incomplete facts are omitted.

The bundle has a 32-source and 32,000-character admitted-source budget; a
target article is capped at 16,000 characters and an opted-in public Discord
snapshot at 12,000.

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

OMP never runs as a child of the API or worker. Clearing a child environment is
not a security boundary: a same-UID child can still inspect its parent's mounts,
network, and credentials. `WIKI_OMP_COMMAND` and `WIKI_OMP_LAUNCHER_PATH` are
therefore rejected rather than used as a fallback.

The optional `compose.wiki-omp.yaml` overlay builds the repository's separate
sandbox image. Its Dockerfile downloads a pinned, checksum-verified OMP release
and the sidecar starts OMP in RPC mode with tools, sessions, skills, rules,
extensions, LSP, and PTY disabled. It then rejects the run if OMP reports any
remaining tool through `get_state`. Each request receives a new empty
home/config/cache/cwd and a minimal OMP child environment containing only the
OpenRouter key, enforced internal proxy address, locale, and scratch paths;
the sandbox bearer token and provider-key file path are not inherited by OMP.

The sidecar has no `env_file`, host mounts, database/Redis/MinIO/API/Discord
credentials, Linux capabilities, or writable root filesystem. Its OpenRouter
credential is a Docker secret mounted only in the sidecar, not a worker
environment variable. The worker uses a distinct narrow
`WIKI_OMP_SANDBOX_TOKEN` to call `POST /v1/wiki-authoring/runs`; the versioned
request contains only the bounded material bundle and the response is strict
JSON with a single draft. Promote the resulting sandbox image by reviewed
digest in production.

The overlay places the sandbox only on Docker-internal control/proxy networks;
it has no direct external route. A separate, uncredentialed proxy is the sole
service attached to an external bridge. It accepts only HTTPS `CONNECT`
requests for `openrouter.ai:443`, rejects all other HTTP traffic, and refuses
DNS answers outside global address space before connecting. OMP receives that
proxy URL in its otherwise minimal child environment, so a failed/misconfigured
proxy stops authoring rather than silently granting the sandbox broad egress.

The v1 request uses `Authorization: Bearer <sandbox token>` and includes the
model/thinking selection, reserved action and target, opaque approved-material
IDs with their bounded content, and the draft size/citation contract. The
sandbox must return exactly `{"protocol_version":"v1","draft":{...}}`,
where `draft` contains the reserved action/target, title, text, summary, and
only material IDs it used. The worker disables redirects, bounds the response,
rejects extra response fields, and validates every cited ID before persisting a
proposal. A sandbox error cannot publish or modify Outline.

Do not set `WIKI_EDITING_ENABLED=true` until all required configuration is
present. See the [Configuration Reference](./configuration.md) for every
setting.
