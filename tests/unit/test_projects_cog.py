"""Focused unit tests for project payment channel command helpers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from five08.discord_bot.cogs.projects import ProjectsCog
from five08.project_discord_channels import (
    ProjectPaymentDiscordDeliveryClaim,
    ProjectPaymentDiscordDeliveryClaimStatus,
)


class _PaymentChannel:
    def __init__(self, messages: list[object] | None = None) -> None:
        self.messages = messages or []
        self.history_calls = 0
        self.send_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def history(self, **_kwargs: object):  # noqa: ANN201
        self.history_calls += 1
        for message in self.messages:
            yield message

    async def send(self, *args: object, **kwargs: object) -> object:
        self.send_calls.append((args, kwargs))
        return SimpleNamespace(id="sent-message-1")


class _GuildSequence(Sequence[SimpleNamespace]):
    """Small stand-in for discord.py's non-list ``SequenceProxy``."""

    def __init__(self, values: list[SimpleNamespace]) -> None:
        self.values = values

    def __getitem__(self, index: int) -> SimpleNamespace:
        return self.values[index]

    def __len__(self) -> int:
        return len(self.values)


def _payment_context() -> dict[str, object]:
    return {
        "notification_id": "00000000-0000-0000-0000-000000000001",
        "project_discord_channel_id": "00000000-0000-0000-0000-000000000002",
        "project_id": "00000000-0000-0000-0000-000000000003",
        "guild_id": "1",
        "channel_id": "2",
        "allocation_id": "00000000-0000-0000-0000-000000000004",
        "amount": Decimal("1250.00"),
        "currency": "USD",
        "posted_at": datetime(2026, 7, 16, tzinfo=timezone.utc),
        "bank_transaction_id": "00000000-0000-0000-0000-000000000005",
    }


def _payment_delivery_kwargs() -> dict[str, str]:
    return {
        "notification_id": "00000000-0000-0000-0000-000000000001",
        "worker_lease_token": "00000000-0000-0000-0000-000000000010",
    }


def _payment_cog(
    monkeypatch,
    channel: _PaymentChannel,
    *,
    contexts: list[SimpleNamespace | None] | None = None,
) -> ProjectsCog:
    bot = Mock()
    bot.get_guild.return_value = SimpleNamespace()
    bot.guilds = [SimpleNamespace(id=1)]
    cog = ProjectsCog(bot)
    default_context = SimpleNamespace(**_payment_context())
    remaining_contexts = list(
        contexts or [default_context, default_context, default_context]
    )

    def _get_context(*_args: object, **_kwargs: object) -> SimpleNamespace | None:
        if remaining_contexts:
            return remaining_contexts.pop(0)
        return default_context

    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.get_project_payment_notification_delivery_context",
        _get_context,
    )
    monkeypatch.setattr(cog, "_private_channel_error", lambda *_args: None)

    async def _resolve_channel(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[_PaymentChannel, bool]:
        return channel, False

    monkeypatch.setattr(cog, "_resolve_text_channel", _resolve_channel)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.record_project_discord_channel_verification",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.renew_project_payment_discord_delivery_lease",
        lambda *_args, **_kwargs: True,
    )
    return cog


def test_project_payment_message_has_stable_hidden_idempotency_marker() -> None:
    content = ProjectsCog._payment_message_content(
        notification_id="00000000-0000-0000-0000-000000000001",
        amount="1250",
        currency="USD",
        posted_at="2026-07-16T00:00:00+00:00",
    )

    assert "USD 1,250.00" in content
    assert (
        "<!-- project-payment-notification:00000000-0000-0000-0000-000000000001 -->"
        in content
    )
    assert "Payment received" in content


def test_project_channel_validation_requires_read_message_history() -> None:
    default_role = object()
    bot_member = object()
    guild = SimpleNamespace(id=1, default_role=default_role, me=bot_member)

    class _Channel:
        guild = SimpleNamespace(id=1)

        @staticmethod
        def permissions_for(member: object) -> SimpleNamespace:
            if member is default_role:
                return SimpleNamespace(view_channel=False)
            return SimpleNamespace(
                view_channel=True,
                send_messages=True,
                read_message_history=False,
            )

    assert (
        ProjectsCog._private_channel_error(cast(Any, guild), cast(Any, _Channel()))
        == "missing_read_message_history_permission"
    )


def test_project_channel_guild_fallback_accepts_a_discord_sequence_proxy_shape(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.settings.discord_server_id", ""
    )
    bot = Mock()
    bot.guilds = _GuildSequence([SimpleNamespace(id=1)])

    assert ProjectsCog(bot)._is_configured_guild(1) is True
    assert ProjectsCog(bot)._is_configured_guild(2) is False


@pytest.mark.asyncio
async def test_project_channel_autocomplete_uses_local_open_project_ids(
    monkeypatch,
) -> None:
    bot = Mock()
    bot.guilds = [SimpleNamespace(id=1)]
    cog = ProjectsCog(bot)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.list_dashboard_projects",
        lambda *_args, **_kwargs: [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "display_name": "Acme Design Sprint",
                "customer": "Acme",
                "erpnext_project_id": "PROJ-001",
            }
        ],
    )

    choices = await cog.project_channel_autocomplete(
        SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(roles=[SimpleNamespace(name="Steering Committee")]),
        ),
        "acme",
    )

    assert [(choice.name, choice.value) for choice in choices] == [
        ("Acme Design Sprint — Acme", "00000000-0000-0000-0000-000000000001")
    ]


@pytest.mark.asyncio
async def test_project_channel_autocomplete_does_not_leak_project_names_to_members(
    monkeypatch,
) -> None:
    bot = Mock()
    bot.guilds = [SimpleNamespace(id=1)]
    cog = ProjectsCog(bot)
    list_projects = Mock()
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.list_dashboard_projects",
        list_projects,
    )

    choices = await cog.project_channel_autocomplete(
        SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(roles=[SimpleNamespace(name="Member")]),
        ),
        "acme",
    )

    assert choices == []
    list_projects.assert_not_called()


@pytest.mark.asyncio
async def test_project_channel_registration_rejects_a_different_guild(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.settings.discord_server_id", "1"
    )
    bot = Mock()
    bot.guilds = [SimpleNamespace(id=1)]
    cog = ProjectsCog(bot)
    target_channel = SimpleNamespace(id=99, name="private-payments")
    monkeypatch.setattr(cog, "_target_text_channel", lambda *_args: target_channel)
    list_projects = Mock()
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.list_dashboard_projects",
        list_projects,
    )
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=999),
        response=SimpleNamespace(send_message=AsyncMock()),
        user=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="Steering Committee")],
        ),
    )

    await ProjectsCog.register_project_channel.callback(
        cog,
        interaction,
        project="00000000-0000-0000-0000-000000000001",
    )

    interaction.response.send_message.assert_awaited_once()
    assert "configured server" in interaction.response.send_message.call_args.args[0]
    list_projects.assert_not_called()


@pytest.mark.asyncio
async def test_project_payment_delivery_rejects_a_cross_guild_mapping(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.settings.discord_server_id", "2"
    )
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel)
    claim = Mock()
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        claim,
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 403
    assert result == {"error": "payment_channel_wrong_guild"}
    claim.assert_not_called()


@pytest.mark.asyncio
async def test_project_payment_delivery_returns_durable_bot_receipt_without_sending(
    monkeypatch,
) -> None:
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        lambda *_args, **_kwargs: ProjectPaymentDiscordDeliveryClaim(
            status=ProjectPaymentDiscordDeliveryClaimStatus.ALREADY_DELIVERED,
            discord_message_id="existing-message-1",
        ),
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 200
    assert result == {
        "status": "already_delivered",
        "message_id": "existing-message-1",
    }
    assert channel.history_calls == 0
    assert channel.send_calls == []


@pytest.mark.asyncio
async def test_project_payment_delivery_persists_marker_recovery_receipt(
    monkeypatch,
) -> None:
    channel = _PaymentChannel(
        [
            SimpleNamespace(
                id="existing-message-1",
                content=(
                    "<!-- project-payment-notification:"
                    "00000000-0000-0000-0000-000000000001 -->"
                ),
            )
        ]
    )
    cog = _payment_cog(monkeypatch, channel)
    persisted: dict[str, object] = {}
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        lambda *_args, **_kwargs: ProjectPaymentDiscordDeliveryClaim(
            status=ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED,
            lease_token="lease-1",
        ),
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.mark_project_payment_discord_delivery_sent",
        lambda *_args, **kwargs: persisted.update(kwargs) or True,
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 200
    assert result["status"] == "already_delivered"
    assert persisted["discord_message_id"] == "existing-message-1"
    assert (
        persisted["project_discord_channel_id"]
        == "00000000-0000-0000-0000-000000000002"
    )
    assert channel.send_calls == []


@pytest.mark.asyncio
async def test_project_payment_delivery_persists_receipt_after_discord_send(
    monkeypatch,
) -> None:
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel)
    claimed: dict[str, object] = {}
    persisted: dict[str, object] = {}
    renewals: list[dict[str, str]] = []

    def renew_worker_bound_delivery_lease(
        _settings: object,
        *,
        notification_id: str,
        project_discord_channel_id: str,
        worker_lease_token: str,
        lease_token: str,
    ) -> bool:
        renewals.append(
            {
                "notification_id": notification_id,
                "project_discord_channel_id": project_discord_channel_id,
                "worker_lease_token": worker_lease_token,
                "lease_token": lease_token,
            }
        )
        return True

    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.renew_project_payment_discord_delivery_lease",
        renew_worker_bound_delivery_lease,
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        lambda *_args, **kwargs: (
            claimed.update(kwargs)
            or ProjectPaymentDiscordDeliveryClaim(
                status=ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED,
                lease_token="lease-1",
            )
        ),
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.mark_project_payment_discord_delivery_sent",
        lambda *_args, **kwargs: persisted.update(kwargs) or True,
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 200
    assert result == {"status": "sent", "message_id": "sent-message-1"}
    assert (
        claimed["project_discord_channel_id"] == "00000000-0000-0000-0000-000000000002"
    )
    assert claimed["worker_lease_token"] == "00000000-0000-0000-0000-000000000010"
    assert len(renewals) == 2
    assert all(
        renewal["worker_lease_token"] == "00000000-0000-0000-0000-000000000010"
        for renewal in renewals
    )
    assert persisted["discord_message_id"] == "sent-message-1"
    assert (
        persisted["project_discord_channel_id"]
        == "00000000-0000-0000-0000-000000000002"
    )
    assert len(channel.send_calls) == 1
    assert "USD 1,250.00" in str(channel.send_calls[0][0][0])
    nonce = str(channel.send_calls[0][1]["nonce"])
    assert nonce.startswith("pp-")
    assert len(nonce) <= 25


@pytest.mark.asyncio
async def test_project_payment_delivery_rejects_missing_or_revoked_canonical_context(
    monkeypatch,
) -> None:
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel, contexts=[None])
    claim = Mock()
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        claim,
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 409
    assert result == {"error": "payment_notification_not_eligible"}
    claim.assert_not_called()
    assert channel.send_calls == []


@pytest.mark.asyncio
async def test_project_payment_delivery_blocks_when_mapping_revokes_after_claim(
    monkeypatch,
) -> None:
    initial_context = SimpleNamespace(**_payment_context())
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel, contexts=[initial_context, None])
    released: dict[str, object] = {}
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        lambda *_args, **_kwargs: ProjectPaymentDiscordDeliveryClaim(
            status=ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED,
            lease_token="lease-1",
        ),
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.mark_project_payment_discord_delivery_failed",
        lambda *_args, **kwargs: released.update(kwargs) or True,
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 409
    assert result == {"error": "payment_notification_not_eligible"}
    assert (
        released["project_discord_channel_id"]
        == initial_context.project_discord_channel_id
    )
    assert released["error"] == "notification_no_longer_eligible"
    assert channel.history_calls == 0
    assert channel.send_calls == []


@pytest.mark.asyncio
async def test_project_payment_delivery_returns_retryable_status_when_guild_is_unready(
    monkeypatch,
) -> None:
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel)
    cog.bot.get_guild.return_value = None

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 503
    assert result == {"error": "guild_not_ready"}
    assert channel.send_calls == []


@pytest.mark.asyncio
async def test_project_payment_delivery_returns_retryable_status_when_lease_is_lost(
    monkeypatch,
) -> None:
    channel = _PaymentChannel()
    cog = _payment_cog(monkeypatch, channel)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.claim_project_payment_discord_delivery",
        lambda *_args, **_kwargs: ProjectPaymentDiscordDeliveryClaim(
            status=ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED,
            lease_token="lease-1",
        ),
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.projects.renew_project_payment_discord_delivery_lease",
        lambda *_args, **_kwargs: False,
    )

    result, status_code = await cog.post_project_payment_notification(
        **_payment_delivery_kwargs()
    )

    assert status_code == 503
    assert result == {"error": "payment_notification_delivery_lease_lost"}
    assert channel.history_calls == 0
    assert channel.send_calls == []


@pytest.mark.asyncio
async def test_project_payment_delivery_accepts_only_the_notification_and_lease(
    monkeypatch,
) -> None:
    cog = _payment_cog(monkeypatch, _PaymentChannel(), contexts=[None])
    notification_method = cast(Any, cog.post_project_payment_notification)

    with pytest.raises(TypeError, match="project_id"):
        await notification_method(
            **_payment_delivery_kwargs(),
            project_id="forged-project",
        )
