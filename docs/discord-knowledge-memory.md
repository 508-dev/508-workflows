# Discord Knowledge Memory

The Discord knowledge surface turns an explicit, reviewed conversation into a
durable answer and can answer later questions from authorized organizational
sources. The backend owns extraction, policy, persistence, retrieval, and
answer validation; Discord is a thin context collector and renderer.

## Capture

These mention forms are supported:

- In a Discord thread: `@bot remember this thread`
- As a reply to an answer: `@bot remember this answer`
- To review candidates: `@bot suggest facts worth saving from this thread`

Capture defaults to **private**, owned by the requester. A public source does
not imply consent to create shared memory. Use `remember this thread for the
team` to request organization visibility, or `remember this thread for the
project` for a mapped project. The preview names the audience before saving.
The backend checks the requested audience against source visibility, roles,
and project membership. Detected secrets or contact details force private
visibility even when sharing was requested.

Personal facts are user-only through the application: admin and Steering
Committee roles cannot read, change, or delete another person's private
memory. Shared facts are explicitly approved team/project knowledge, such as
deployment procedures and project decisions. An uncertain audience stays
private; model output cannot choose a wider audience.

The agent can suggest saving simple personal statements such as “My timezone
is Asia/Tokyo” or “I prefer concise answers” in an addressed conversation.
Suggestions are previews, and all saves require confirmation. There is no
passive channel ingestion or automatic fact saving.

The bot sends at most the configured message and character bounds. Outside a
thread it follows only the reply chain (answer and its referenced question), so
an unrelated channel history is never swept into a capture.

```text
Discord mention
  -> bounded message snapshot + gateway-resolved visibility
  -> POST /knowledge/captures
  -> evidence-bound Q&A proposal
  -> private frozen preview
  -> POST /knowledge/captures/{draft_id}/confirmation
  -> current-role reauthorization
  -> atomic fact + provenance write
```

Only the requester can confirm the draft. Drafts expire after 10 minutes by
default. Repeated confirmation is idempotent; a changed answer to the same
normalized question supersedes the previous active answer instead of silently
overwriting its history. Secret-like text is never promoted to organization or
project visibility. Consumed previews are immediately stripped of raw Discord
messages and candidates; expired preview records are deleted automatically.

## Asking Questions

`/ask` is always private. Questions in direct bot mentions use the knowledge
path after local help and dedicated live-workflow routing, so a natural question
such as `@bot I forgot, does our main website auto deploy?` does not require
`/ask`. Search runs across:

- Active remembered facts visible to the caller
- The member-safe Outline integration account
- Recent messages from explicitly selected Discord channels/public threads
  the asking member can currently read
- ERPNext project cache rows filtered by the caller's CRM-linked project roster
- CRM people data only when the caller has the existing CRM read scope

Recall is hybrid. PostgreSQL full-text ranking handles keyword matches, a
conservative deterministic similarity score handles common typos, and the
optional model selects semantically relevant facts for paraphrases and synonyms
from a small candidate pool. Authorization and source visibility are applied
before that candidate pool is constructed. The model may propose an answer and
evidence IDs, but code rejects unknown citations and owns the final visibility
decision. An explicit model abstention remains an insufficient result. When the
model is unavailable, only positively matched keyword or fuzzy evidence can be
returned; unrelated semantic candidates are never used as fallback.

A mention answer is channel-visible only when all evidence shown to the model is
organization-visible and the answer contains no detected secrets, email
addresses, or phone numbers. Project, private, and CRM-backed results are sent
by DM. Discord output escapes markdown and mass mentions.

## Authorization And Provenance

Role-derived scopes are evaluated in the backend; model output never grants
access. Members can read and capture organization knowledge and query the
member-safe wiki. Project managers gain project knowledge scopes. CRM evidence
uses the existing admin CRM-read scope.

Every remembered answer stores:

- Organization and user/project/org scope
- Visibility and verification status
- Question, answer, aliases, confidence, and review date
- Discord source reference, title, excerpt hash, message IDs, author IDs, and
  source timestamp
- Supersession, deletion, expiry, and audit timestamps

`memory_facts` is the searchable source of truth, `memory_fact_sources` stores
provenance, and `knowledge_capture_drafts` stores immutable confirmation
previews until they are confirmed, canceled, or expire. PostgreSQL full-text
search and bounded semantic selection provide recall without a vector database.

Human-triggered capture, confirmation, and query attempts emit best-effort
audit metadata. Raw conversation text and answers are not copied into audit
metadata.

## Personal Memory And Follow-ups

`/agent What do you remember about me?` lists active private facts with IDs
and paginated Edit/Forget controls. Both changes require confirmation. Saving
a new value for a named preference supersedes the previous value and retains
revision history; separate free-form notes remain separate. Expired, deleted,
and superseded facts are excluded from recall and planner context.

The planner receives a bounded set of the requester's private facts only for
private responses. Retrieved facts and thread messages remain untrusted data
and cannot change permissions.

Missing task projects/titles are retained for ten minutes, scoped to the user,
organization, and conversation. “Show tasks” → “Which project?” → “Atlas” works
across API restarts and into the bot's newly created response thread. Recent
accessible thread messages also provide bounded context for model planning.
Frozen confirmation plans use the same Postgres TTL store and an atomic claim,
so concurrent clicks cannot execute the same plan twice. The separate MVP task
registry remains process-local; production task persistence is a separate
integration concern.

Discord question retrieval reads at most 100 recent messages per enabled
location within 30 days by default, with a total 20,000-character snapshot.
It is a bounded recent-history search, not an archive of the entire server.
Channels and threads must be selected by ID; selecting a parent does not
implicitly select its threads. Answers using Discord snapshots stay private.
Source outages are reported separately from absence of supporting evidence.

## Operations

The backend API applies the Alembic migration at startup. Relevant settings are
documented in [Configuration Reference](./configuration.md). `KNOWLEDGE_ENABLED`
is the rollback switch; disabling model use keeps deterministic extraction and
retrieval available.
