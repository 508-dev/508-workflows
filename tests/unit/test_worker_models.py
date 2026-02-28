"""Unit tests for worker models."""

from five08.worker.models import (
    AuditEventPayload,
    DocusealWebhookPayload,
    EspoCRMWebhookPayload,
)


def test_espocrm_webhook_payload_from_list() -> None:
    """from_list should parse list payload into events."""
    payload = EspoCRMWebhookPayload.from_list(
        [{"id": "contact-1", "name": "Jane"}, {"id": "contact-2"}]
    )
    assert len(payload.events) == 2
    assert payload.events[0].id == "contact-1"
    assert payload.events[0].name == "Jane"


def test_audit_event_payload_defaults_metadata() -> None:
    """Audit payload should default metadata to an empty object."""
    payload = AuditEventPayload(
        source="discord",
        action="crm.search",
        result="success",
        actor_provider="discord",
        actor_subject="12345",
    )
    assert payload.metadata == {}


def test_docuseal_webhook_payload_parses_completed_event() -> None:
    """Docuseal payload should parse form.completed event with submitter data."""
    payload = DocusealWebhookPayload.model_validate(
        {
            "event_type": "form.completed",
            "timestamp": "2026-02-25T12:00:00Z",
            "data": {
                "id": 42,
                "email": "member@508.dev",
                "status": "completed",
                "completed_at": "2026-02-25T12:00:00Z",
                "name": "Jane Doe",
            },
        }
    )
    assert payload.event_type == "form.completed"
    assert payload.data.id == 42
    assert payload.data.email == "member@508.dev"
    assert payload.data.completed_at == "2026-02-25T12:00:00Z"
    assert payload.data.name == "Jane Doe"
