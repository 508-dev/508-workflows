"""Unit tests for the read-only Discord diagnostics cog."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

import five08.discord_bot.cogs.diagnostics as diagnostics_module
from five08.discord_bot.cogs.diagnostics import (
    DiagnosticsCog,
    DiagnosticsView,
    build_diagnostics_snapshot,
    diagnostics_export_text,
)


def _role(
    role_id: int,
    name: str,
    position: int,
    *,
    managed: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=role_id,
        name=name,
        position=position,
        managed=managed,
    )


def _guild() -> SimpleNamespace:
    top_role = _role(900, "Bot", 10)
    return SimpleNamespace(
        id=100,
        name="508.dev",
        me=SimpleNamespace(
            top_role=top_role,
            guild_permissions=SimpleNamespace(manage_roles=True),
        ),
        roles=[
            _role(100, "@everyone", 0),
            _role(400, "Managed integration", 4, managed=True),
            _role(300, "Billing", 5),
            _role(200, "Admin", 9),
        ],
    )


@pytest.fixture(autouse=True)
def _configure_role_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics_module.settings, "discord_server_id", "100")
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_admin_role_ids",
        "200",
    )
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_steering_committee_role_ids",
        "",
    )
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_billing_role_ids",
        "300,999",
    )
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_erp_developer_role_ids",
        "",
    )
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_project_manager_role_ids",
        "",
    )
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_engineer_role_ids",
        "",
    )
    monkeypatch.setattr(diagnostics_module.settings, "api_shared_secret", "api-secret")


def test_snapshot_resolves_role_bindings_and_never_returns_secret_values() -> None:
    snapshot = build_diagnostics_snapshot(
        _guild(),
        _guild().roles,
        source="gateway_cache",
        snapshot_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )

    assert [role["id"] for role in snapshot["roles"]] == ["200", "300", "400", "100"]
    assert snapshot["agent"]["resolved_role_count"] == 2
    assert snapshot["agent"]["missing_role_count"] == 1
    assert snapshot["agent"]["api_shared_secret_status"] == "configured"

    bindings = {
        binding["bundle"]: binding for binding in snapshot["agent"]["role_bindings"]
    }
    assert bindings["admin"]["roles"] == [
        {
            "id": "200",
            "name": "Admin",
            "status": "resolved",
            "managed": False,
            "manageable_by_bot": True,
        }
    ]
    assert bindings["billing"]["status"] == "attention"
    assert bindings["billing"]["roles"][1]["status"] == "missing"

    exported = diagnostics_export_text(snapshot)
    assert "AGENT_DISCORD_BILLING_ROLE_IDS=300,999" in exported
    assert "Admin\t200" in exported
    assert "api-secret" not in exported


def test_snapshot_marks_everyone_as_attention_when_configuration_is_corrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diagnostics_module.settings,
        "agent_discord_admin_role_ids",
        "100",
    )

    guild = _guild()
    snapshot = build_diagnostics_snapshot(guild, guild.roles, source="gateway_cache")
    admin_binding = next(
        binding
        for binding in snapshot["agent"]["role_bindings"]
        if binding["bundle"] == "admin"
    )

    assert admin_binding["status"] == "attention"
    assert admin_binding["roles"][0]["status"] == "everyone"


@pytest.mark.asyncio
async def test_cog_refresh_falls_back_to_gateway_cache() -> None:
    guild = _guild()
    guild.fetch_roles = AsyncMock(side_effect=RuntimeError("Discord unavailable"))
    bot = Mock()
    bot.get_guild.return_value = guild
    cog = DiagnosticsCog(bot)

    snapshot, status_code = await cog.get_diagnostics_snapshot(refresh=True)

    assert status_code == 200
    assert snapshot["snapshot"]["source"] == "gateway_cache"
    assert "showing the gateway cache" in snapshot["snapshot"]["refresh_error"]


@pytest.mark.asyncio
async def test_role_catalog_pagination_enables_only_for_the_roles_panel() -> None:
    guild = _guild()
    guild.roles.extend(
        _role(1_000 + index, f"Role {index}", 20 + index) for index in range(11)
    )
    snapshot = build_diagnostics_snapshot(guild, guild.roles, source="gateway_cache")
    view = DiagnosticsView(cog=Mock(), owner_id=123, snapshot=snapshot)
    buttons = {
        child.custom_id: child
        for child in view.children
        if getattr(child, "custom_id", None)
        in {
            "discord_diagnostics_previous_roles",
            "discord_diagnostics_next_roles",
        }
    }

    assert buttons["discord_diagnostics_previous_roles"].disabled is True
    assert buttons["discord_diagnostics_next_roles"].disabled is True

    view.active_panel = "roles"
    view._sync_role_navigation()
    assert buttons["discord_diagnostics_previous_roles"].disabled is True
    assert buttons["discord_diagnostics_next_roles"].disabled is False

    view.role_page = 1
    view._sync_role_navigation()
    assert buttons["discord_diagnostics_previous_roles"].disabled is False
    assert buttons["discord_diagnostics_next_roles"].disabled is True


def _interaction(
    guild: object,
    *,
    manage_guild: bool,
    administrator: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        guild_id=getattr(guild, "id"),
        user=SimpleNamespace(
            id=123,
            guild_permissions=SimpleNamespace(
                manage_guild=manage_guild,
                administrator=administrator,
            ),
        ),
        response=SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_diagnostics_command_requires_native_manage_guild_permission() -> None:
    guild = _guild()
    bot = Mock()
    bot.get_guild.return_value = guild
    cog = DiagnosticsCog(bot)
    interaction = _interaction(guild, manage_guild=False)

    with patch.object(cog, "_audit_snapshot_action") as audit:
        await cast(Any, cog.diagnostics_command.callback)(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    assert "Manage Server" in interaction.response.send_message.call_args.args[0]
    audit.assert_called_once()


@pytest.mark.asyncio
async def test_diagnostics_command_returns_ephemeral_panel_for_native_server_admin() -> (
    None
):
    guild = _guild()
    bot = Mock()
    bot.get_guild.return_value = guild
    cog = DiagnosticsCog(bot)
    interaction = _interaction(guild, manage_guild=True)
    snapshot = build_diagnostics_snapshot(guild, guild.roles, source="gateway_cache")

    with (
        patch.object(
            cog,
            "get_diagnostics_snapshot",
            new=AsyncMock(return_value=(snapshot, 200)),
        ),
        patch.object(cog, "_audit_snapshot_action") as audit,
    ):
        await cast(Any, cog.diagnostics_command.callback)(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True
    audit.assert_called_once()
