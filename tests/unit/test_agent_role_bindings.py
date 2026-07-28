"""Regression tests for Discord role-ID agent authorization."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from five08.agent import AgentIdentityContext, AgentOrchestrator, PolicyEngine
from five08.agent.tools import ToolManifest
from five08.agent.models import AgentToolAction
from five08.backend import api
from five08.discord_bot.cogs.agent import AgentCog, AgentConfirmationView
from five08.settings import SharedSettings


def _production_policy() -> PolicyEngine:
    return PolicyEngine.from_settings(
        SharedSettings(
            environment="production",
            discord_server_id="1000",
            agent_discord_admin_role_ids="1001",
            agent_discord_steering_committee_role_ids="1002",
        )
    )


def _context(
    *,
    role_ids: list[str],
    roles: list[str] | None = None,
    guild_id: str | None = "1000",
    organization_id: str | None = "1000",
) -> AgentIdentityContext:
    return AgentIdentityContext(
        discord_user_id="user-1",
        organization_id=organization_id,
        guild_id=guild_id,
        role_ids=role_ids,
        roles=roles or [],
    )


def test_production_policy_ignores_spoofed_role_names_and_unknown_role_ids() -> None:
    policy = _production_policy()

    scopes = policy.scopes_for_context(_context(role_ids=["9999"], roles=["Admin"]))

    assert scopes == set()


def test_production_policy_grants_only_matching_role_ids_in_an_allowed_guild() -> None:
    policy = _production_policy()

    scopes = policy.scopes_for_context(_context(role_ids=["1001"], roles=["Member"]))

    assert "agent:chat" in scopes
    assert "user:manage" in scopes


def test_production_policy_uses_discord_server_id_for_agent_guild_binding() -> None:
    policy = PolicyEngine.from_settings(
        SharedSettings(
            environment="production",
            discord_server_id="1000",
            agent_discord_steering_committee_role_ids="1002",
        )
    )

    assert "agent:chat" in policy.scopes_for_context(_context(role_ids=["1002"]))


@pytest.mark.parametrize(
    ("guild_id", "organization_id"),
    [
        (None, "1000"),
        ("2000", "2000"),
        ("1000", "another-org"),
    ],
)
def test_production_policy_fails_closed_for_missing_unapproved_or_cross_tenant_guild(
    guild_id: str | None,
    organization_id: str | None,
) -> None:
    policy = _production_policy()

    assert (
        policy.scopes_for_context(
            _context(
                role_ids=["1001"],
                guild_id=guild_id,
                organization_id=organization_id,
            )
        )
        == set()
    )


def test_local_role_name_fallback_requires_explicit_opt_in_and_never_works_in_production() -> (
    None
):
    local_policy = PolicyEngine.from_settings(
        SharedSettings(environment="test", agent_allow_role_name_fallback=True)
    )
    production_policy = PolicyEngine.from_settings(
        SharedSettings(
            environment="production",
            agent_allow_role_name_fallback=True,
            discord_server_id="1000",
        )
    )
    context = _context(role_ids=[], roles=["Admin"])

    assert "user:manage" in local_policy.scopes_for_context(context)
    assert production_policy.scopes_for_context(context) == set()


def test_unapproved_guild_is_denied_before_any_planner_call() -> None:
    class PlannerMustNotRun:
        def plan(self, **_kwargs: object) -> object:
            raise AssertionError("the planner must not receive an unapproved guild")

    response = AgentOrchestrator(
        policy=_production_policy(),
        planner=PlannerMustNotRun(),  # type: ignore[arg-type]
    ).plan(
        "Explain the current plan",
        _context(role_ids=["1001"], guild_id="2000", organization_id="2000"),
    )

    assert response.status == "denied"


def test_role_ids_must_be_positive_decimal_discord_ids() -> None:
    with pytest.raises(ValidationError, match="role_ids"):
        AgentIdentityContext(discord_user_id="user-1", role_ids=["Admin"])


def test_guild_everyone_role_cannot_be_configured_as_an_agent_bundle() -> None:
    with pytest.raises(ValidationError, match="@everyone"):
        SharedSettings(
            environment="production",
            discord_server_id="1000",
            agent_discord_admin_role_ids="1000",
        )

    # Defense in depth for callers that construct a policy directly instead
    # of going through validated settings.
    policy = PolicyEngine(
        role_id_bindings={"admin": {"1000"}},
        allowed_guild_ids={"1000"},
        require_guild_binding=True,
        allow_role_name_fallback=False,
    )
    assert policy.scopes_for_context(_context(role_ids=["1000"])) == set()


def test_confirmation_reauthorization_uses_current_role_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api.settings, "environment", "production")
    monkeypatch.setattr(api.settings, "discord_server_id", "1000")
    monkeypatch.setattr(api.settings, "agent_discord_admin_role_ids", "1001")
    monkeypatch.setattr(api.settings, "agent_allow_role_name_fallback", True)
    original_context = _context(role_ids=["1001"], roles=["Admin"])
    revoked_context = _context(role_ids=["9999"], roles=["Admin"])

    assert (
        api._confirmation_execution_scopes(
            original_context=original_context,
            confirmation_context=revoked_context,
        )
        == set()
    )


def test_effective_scope_override_cannot_add_to_current_role_grants() -> None:
    policy = _production_policy()
    manifest = ToolManifest(
        name="task_write.create_task",
        risk="medium",
        required_scopes=("task:create",),
    )
    action = AgentToolAction(
        tool_name=manifest.name,
        required_scopes=["task:create"],
        summary="Create a task",
    )

    decision = policy.authorize_with_scopes(
        context=_context(role_ids=[]),
        manifest=manifest,
        action=action,
        effective_scopes={"task:create"},
    )

    assert not decision.allowed
    assert decision.reason == "Effective scopes exceed current Discord role grants"


@pytest.mark.asyncio
async def test_dm_confirmation_reloads_fresh_discord_role_ids() -> None:
    member = SimpleNamespace(
        roles=[SimpleNamespace(name="Admin", id=1001)],
    )
    guild = SimpleNamespace(
        get_member=lambda _user_id: None,
        fetch_member=AsyncMock(return_value=member),
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = cast(Any, SimpleNamespace(get_guild=lambda _guild_id: guild))
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "organization_id": "1000",
            "guild_id": "1000",
            "roles": ["Member"],
            "role_ids": ["9999"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=None,
        channel_id=111,
        message=None,
        user=SimpleNamespace(id=123),
    )

    context = await view._confirmation_context(cast(Any, interaction))

    assert context["roles"] == ["Admin"]
    assert context["role_ids"] == ["1001"]
    guild.fetch_member.assert_awaited_once_with(123)
