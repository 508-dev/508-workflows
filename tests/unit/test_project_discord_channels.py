"""Tests for durable private project Discord channel mappings."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

import five08.project_discord_channels as project_channels


class _CursorStub:
    def __init__(self, rows: list[dict | None]) -> None:
        self.rows = list(rows)
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_CursorStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def execute(self, query: str, params: tuple) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[dict]:
        rows = [row for row in self.rows if row is not None]
        self.rows.clear()
        return rows


class _ConnectionStub:
    def __init__(self, cursor: _CursorStub) -> None:
        self._cursor = cursor

    def cursor(self, row_factory=None) -> _CursorStub:  # noqa: ARG002
        return self._cursor

    def __enter__(self) -> "_ConnectionStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _install_connection_stub(monkeypatch, cursor: _CursorStub) -> None:
    @contextmanager
    def _connection():
        yield _ConnectionStub(cursor)

    monkeypatch.setattr(
        project_channels, "get_postgres_connection", lambda _: _connection()
    )


def _mapping(*, project_id: str = "project-1") -> dict:
    return {
        "id": "mapping-1",
        "project_id": project_id,
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "channel_name": "private-project",
        "active": True,
        "registered_by_discord_user_id": "user-1",
        "last_verified_at": None,
        "last_verification_error": None,
    }


def test_register_project_channel_creates_new_mapping(monkeypatch) -> None:
    cursor = _CursorStub([_mapping()])
    _install_connection_stub(monkeypatch, cursor)

    mapping, created = project_channels.register_project_discord_channel(
        project_channels.SharedSettings(),
        project_id="project-1",
        guild_id="guild-1",
        channel_id="channel-1",
        channel_name="private-project",
        registered_by_discord_user_id="user-1",
    )

    assert created is True
    assert mapping.project_id == "project-1"
    assert "INSERT INTO project_discord_channels" in cursor.executed[0][0]
    assert (
        "ON CONFLICT (guild_id, channel_id) WHERE active IS TRUE"
        in cursor.executed[0][0]
    )


def test_register_project_channel_does_not_reassign_other_project(monkeypatch) -> None:
    cursor = _CursorStub([None, _mapping(project_id="other-project")])
    _install_connection_stub(monkeypatch, cursor)

    with pytest.raises(project_channels.ProjectDiscordChannelConflict):
        project_channels.register_project_discord_channel(
            project_channels.SharedSettings(),
            project_id="project-1",
            guild_id="guild-1",
            channel_id="channel-1",
            channel_name="private-project",
            registered_by_discord_user_id="user-1",
        )

    assert len(cursor.executed) == 2


def test_register_project_channel_is_idempotent_for_same_project(monkeypatch) -> None:
    cursor = _CursorStub([None, _mapping(), _mapping()])
    _install_connection_stub(monkeypatch, cursor)

    mapping, created = project_channels.register_project_discord_channel(
        project_channels.SharedSettings(),
        project_id="project-1",
        guild_id="guild-1",
        channel_id="channel-1",
        channel_name="renamed-private-project",
        registered_by_discord_user_id="user-2",
    )

    assert created is False
    assert mapping.channel_name == "private-project"
    assert "UPDATE project_discord_channels" in cursor.executed[2][0]


def test_list_active_project_channels_returns_only_rows_from_query(monkeypatch) -> None:
    cursor = _CursorStub([_mapping(), _mapping(project_id="project-1")])
    _install_connection_stub(monkeypatch, cursor)

    result = project_channels.list_active_project_discord_channels(
        project_channels.SharedSettings(), project_id="project-1"
    )

    assert [channel.channel_id for channel in result] == ["channel-1", "channel-1"]
    assert "active IS TRUE" in cursor.executed[0][0]


def _delivery_row(
    *,
    project_discord_channel_id: str = "00000000-0000-0000-0000-000000000011",
    status: str = "sent",
    discord_message_id: str | None = "message-1",
) -> dict:
    return {
        "project_discord_channel_id": project_discord_channel_id,
        "status": status,
        "discord_message_id": discord_message_id,
    }


def test_claim_project_payment_discord_delivery_creates_a_new_lease(
    monkeypatch,
) -> None:
    cursor = _CursorStub([{"discord_message_id": None}])
    _install_connection_stub(monkeypatch, cursor)

    result = project_channels.claim_project_payment_discord_delivery(
        project_channels.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        project_discord_channel_id="00000000-0000-0000-0000-000000000011",
        worker_lease_token="00000000-0000-0000-0000-000000000021",
    )

    assert (
        result.status
        is project_channels.ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED
    )
    assert result.lease_token is not None
    assert "ON CONFLICT (notification_id) DO NOTHING" in cursor.executed[0][0]
    assert "project_payment_notification_outbox" in cursor.executed[0][0]
    assert "outbox.lease_token = %s::uuid" in cursor.executed[0][0]


def test_claim_project_payment_discord_delivery_returns_durable_sent_receipt(
    monkeypatch,
) -> None:
    cursor = _CursorStub([None, None, _delivery_row()])
    _install_connection_stub(monkeypatch, cursor)

    result = project_channels.claim_project_payment_discord_delivery(
        project_channels.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        project_discord_channel_id="00000000-0000-0000-0000-000000000011",
        worker_lease_token="00000000-0000-0000-0000-000000000021",
    )

    assert (
        result.status
        is project_channels.ProjectPaymentDiscordDeliveryClaimStatus.ALREADY_DELIVERED
    )
    assert result.discord_message_id == "message-1"
    assert "status = 'failed'" in cursor.executed[1][0]


def test_claim_project_payment_discord_delivery_reclaims_failed_lease(
    monkeypatch,
) -> None:
    cursor = _CursorStub([None, {"discord_message_id": None}])
    _install_connection_stub(monkeypatch, cursor)

    result = project_channels.claim_project_payment_discord_delivery(
        project_channels.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        project_discord_channel_id="00000000-0000-0000-0000-000000000011",
        worker_lease_token="00000000-0000-0000-0000-000000000021",
    )

    assert (
        result.status
        is project_channels.ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED
    )
    assert "status = 'sending'" in cursor.executed[1][0]


def test_failed_project_payment_discord_delivery_cannot_overwrite_sent_receipt(
    monkeypatch,
) -> None:
    cursor = _CursorStub([])
    _install_connection_stub(monkeypatch, cursor)

    project_channels.mark_project_payment_discord_delivery_failed(
        project_channels.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        project_discord_channel_id="00000000-0000-0000-0000-000000000011",
        error="temporary failure",
        lease_token="00000000-0000-0000-0000-000000000001",
    )

    assert "AND lock_token = %s::uuid" in cursor.executed[0][0]


def test_renew_project_payment_discord_delivery_lease_requires_mapping_and_token(
    monkeypatch,
) -> None:
    cursor = _CursorStub([{"notification_id": "notification-1"}])
    _install_connection_stub(monkeypatch, cursor)

    renewed = project_channels.renew_project_payment_discord_delivery_lease(
        project_channels.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        project_discord_channel_id="00000000-0000-0000-0000-000000000011",
        worker_lease_token="00000000-0000-0000-0000-000000000022",
        lease_token="00000000-0000-0000-0000-000000000021",
    )

    assert renewed is True
    query, params = cursor.executed[0]
    assert "project_payment_discord_deliveries.project_discord_channel_id" in query
    assert "project_payment_discord_deliveries.lock_token" in query
    assert "outbox.lease_token = %s::uuid" in query
    assert params[-1] == "00000000-0000-0000-0000-000000000022"
