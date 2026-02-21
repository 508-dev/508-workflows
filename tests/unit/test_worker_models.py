"""Unit tests for worker models."""

from five08.worker.models import AuditEventPayload, EspoCRMWebhookPayload


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
