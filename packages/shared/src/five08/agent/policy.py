"""Deterministic authorization policy for agent tool calls."""

from __future__ import annotations

from dataclasses import dataclass

from five08.agent.models import AgentIdentityContext, AgentToolAction
from five08.agent.tools import ToolManifest


@dataclass(frozen=True)
class PolicyDecision:
    """Result of deterministic policy evaluation."""

    allowed: bool
    reason: str
    requires_confirmation: bool = False


_ROLE_SCOPES: dict[str, frozenset[str]] = {
    "member": frozenset({"project:read", "task:create", "task:update_own"}),
    "project_manager": frozenset(
        {
            "project:read",
            "project:write",
            "task:create",
            "task:update",
            "task:update_own",
            "task:assign",
        }
    ),
    "engineer": frozenset(
        {
            "project:read",
            "github:issue:create",
            "github:pr:create",
            "worker:job:rerun_dev",
        }
    ),
    "admin": frozenset(
        {
            "project:read",
            "project:write",
            "task:create",
            "task:update",
            "task:update_own",
            "task:assign",
            "task:delete",
            "github:issue:create",
            "github:pr:create",
            "deploy:request",
            "user:manage",
            "integration:manage",
        }
    ),
}
_ADMIN_ROLE_NAMES = frozenset({"admin", "owner", "steering committee"})
_MEMBER_ROLE_NAMES = frozenset(
    {
        "member",
        "project manager",
        "project_manager",
        "engineer",
        *_ADMIN_ROLE_NAMES,
    }
)


class PolicyEngine:
    """Authorize each proposed tool call without model involvement."""

    def scopes_for_context(self, context: AgentIdentityContext) -> set[str]:
        scopes: set[str] = set()
        normalized_roles = {role.strip().casefold() for role in context.roles}
        if normalized_roles & _ADMIN_ROLE_NAMES:
            scopes.update(_ROLE_SCOPES["admin"])
        if (
            "project manager" in normalized_roles
            or "project_manager" in normalized_roles
        ):
            scopes.update(_ROLE_SCOPES["project_manager"])
        if "engineer" in normalized_roles:
            scopes.update(_ROLE_SCOPES["engineer"])
        if normalized_roles & _MEMBER_ROLE_NAMES:
            scopes.update(_ROLE_SCOPES["member"])
        return scopes

    def authorize(
        self,
        *,
        context: AgentIdentityContext,
        manifest: ToolManifest | None,
        action: AgentToolAction,
    ) -> PolicyDecision:
        if manifest is None:
            return PolicyDecision(False, f"Unknown tool {action.tool_name}")
        if context.impersonation:
            return PolicyDecision(
                False, "Impersonated Discord requests cannot use agent tools"
            )
        if manifest.tenant_scoped and not (
            context.organization_id or context.guild_id or context.workspace_id
        ):
            return PolicyDecision(False, "Tool requires resolved tenant context")

        effective_scopes = self.scopes_for_context(context)
        missing_scopes = [
            scope for scope in manifest.required_scopes if scope not in effective_scopes
        ]
        if missing_scopes:
            return PolicyDecision(
                False,
                f"Missing required scopes: {', '.join(missing_scopes)}",
            )

        return PolicyDecision(
            True,
            "allowed",
            requires_confirmation=manifest.requires_confirmation,
        )
