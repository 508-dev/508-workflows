# Discord Knowledge Memory

The Discord knowledge surface turns an explicit, reviewed conversation into a
durable answer and can answer later questions from authorized organizational
sources. The backend owns extraction, policy, persistence, retrieval, and
answer validation; Discord is a thin context collector and renderer.

## Capture

Two mention forms are supported:

- In a Discord thread: `@bot remember this thread`
- As a reply to an answer: `@bot remember this answer`

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

A mention answer is channel-visible only when all cited evidence is
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

## Operations

The backend API applies the Alembic migration at startup. Relevant settings are
documented in [Configuration Reference](./configuration.md). `KNOWLEDGE_ENABLED`
is the rollback switch; disabling model use keeps deterministic extraction and
retrieval available.
