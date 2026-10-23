"""Shared contracts for the isolated wiki authoring boundary.

OMP is untrusted provider-facing code. It must not execute as a subprocess of
the credentialed API/worker process: a child sharing that process identity can
inspect inherited mounts, network access, and credentials. The sole supported
implementation is the worker's remote sandbox adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Protocol

from five08.wiki_editing.models import (
    WikiAuthoringWorkItem,
    WikiOmpRunMetadata,
    WikiSourceReference,
)


# ``KnowledgeEvidence.authority`` is deterministically derived from the
# verification status in the knowledge store. Only admin-confirmed and
# authoritative organization facts meet this threshold.
WIKI_AUTHORING_MIN_KNOWLEDGE_AUTHORITY = 0.9


class WikiAuthoringError(RuntimeError):
    """An authoring run could not produce a valid reviewable draft."""


class WikiAuthoringUnavailableError(WikiAuthoringError):
    """The isolated authoring runtime is not configured or cannot be reached."""


class WikiAuthoringTransientError(WikiAuthoringUnavailableError):
    """A retryable sandbox transport or capacity failure.

    This is deliberately distinct from a malformed draft or an invalid sandbox
    configuration. Retrying a draft phase is safe because it has no publishing
    capability, but callers must release the durable authoring lease first.
    """


@dataclass(frozen=True, slots=True)
class WikiAuthoringMaterial:
    """One backend-approved read-only item eligible for sandbox authoring."""

    source: WikiSourceReference
    text: str
    # Knowledge sources must explicitly carry the only visibility eligible for
    # an external authoring run. Request text is separately supplied by the
    # requesting user and is never accepted from the knowledge callback.
    visibility: Literal["org", "request"] = "org"
    # Organization-memory metadata crosses the callback boundary rather than
    # being inferred from a source label, allowing the remote adapter to fail
    # closed when a future source omits trust or freshness state.
    knowledge_authority: float | None = None
    knowledge_stale: bool | None = None
    knowledge_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WikiOmpDraft:
    """Validated draft returned by the sandbox before backend diff/publish."""

    title: str
    text: str
    summary: str
    source_refs: tuple[WikiSourceReference, ...]
    metadata: WikiOmpRunMetadata


class WikiAuthoringRunner(Protocol):
    """Injectable authoring boundary used by the service and focused tests."""

    def author(
        self,
        work_item: WikiAuthoringWorkItem,
        *,
        metadata: WikiOmpRunMetadata,
    ) -> WikiOmpDraft:
        """Produce one validated draft from a frozen authoring work item."""


KnowledgeSearch = Callable[[str, WikiAuthoringWorkItem], list[WikiAuthoringMaterial]]


class OmpWikiAuthoringRunner:
    """Legacy local-RPC entry point deliberately disabled for safety.

    Retaining the name lets old deployment code fail loudly instead of falling
    back to a same-worker child process. It never imports or starts ``omp_rpc``.
    """

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise WikiAuthoringUnavailableError(
            "Local OMP launching is disabled. Configure the isolated wiki OMP "
            "sandbox instead."
        )
