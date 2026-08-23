"""Deterministic authorization policy for agent tool calls."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from five08.agent.models import AgentIdentityContext, AgentToolAction, MemoryVisibility
from five08.agent.tools import ToolManifest, ToolRuntimeConfig

if TYPE_CHECKING:
    from five08.settings import SharedSettings


@dataclass(frozen=True)
class PolicyDecision:
    """Result of deterministic policy evaluation."""

    allowed: bool
    reason: str
    requires_confirmation: bool = False


_MEMBER_SCOPES = frozenset()

# These bundles are deliberately independent. A Discord role is a grant of a
# narrowly-scoped capability set, rather than a rung in a hierarchy. This keeps
# finance and ERP operations separate from steering and administrative powers.
_PROJECT_MANAGER_SCOPES = frozenset(
    {
        "project:read",
        "project:write",
        "task:create",
        "task:update",
        "task:update_own",
        "task:assign",
        "context:read_current_thread",
        "context:read_channel_recent",
        "memory:read_self",
        "memory:read_project",
        "memory:write_self",
        "memory:write_project",
    }
)
_ENGINEER_SCOPES = frozenset(
    {
        "project:read",
        "github:issue:read",
        "github:issue:create",
        "github:repository:configured:read",
        "github:repository:configured:write",
        "github:pr:create",
        "worker:job:rerun_dev",
        "context:read_current_thread",
        "memory:read_self",
        "memory:write_self",
    }
)
_STEERING_COMMITTEE_SCOPES = frozenset(
    {
        "project:read",
        "project:write",
        "task:create",
        "task:update",
        "task:update_own",
        "task:assign",
        "github:issue:read",
        "github:issue:create",
        "github:pr:create",
        "github:repository:all:read",
        "github:repository:all:write",
        "github:project:read",
        "github:project:write",
        "crm:contact:read",
        "crm:contact:update",
        "docuseal:submission:create",
        "context:read_current_thread",
        "context:read_channel_recent",
        "memory:read_self",
        "memory:read_project",
        "memory:write_self",
        "memory:write_project",
        "agent:chat",
        "web:research",
    }
)
_BILLING_SCOPES = frozenset(
    {
        "billing:invoice:read",
        "billing:supplier:read",
        "agent:chat",
        "web:research",
    }
)
_ERP_DEVELOPER_SCOPES = frozenset(
    {
        "erp:project:read",
        "agent:chat",
        "web:research",
    }
)
_ADMIN_SCOPES = frozenset(
    {
        *_PROJECT_MANAGER_SCOPES,
        *_ENGINEER_SCOPES,
        *_STEERING_COMMITTEE_SCOPES,
        *_BILLING_SCOPES,
        *_ERP_DEVELOPER_SCOPES,
        "task:delete",
        "mailbox:create",
        "deploy:request",
        "user:manage",
        "integration:manage",
        # Persistent prompts can repeatedly invoke otherwise safe reads and
        # publish to a channel, so they require a distinct admin-only scope.
        "agent:schedule:manage",
        "context:read_user_recent_self",
        "context:read_user_recent_any",
        "memory:admin",
    }
)

_ROLE_SCOPES: dict[str, frozenset[str]] = {
    # Members receive no privileged agent capabilities. Public-chat or public
    # web capabilities, if offered, must be explicitly granted elsewhere.
    "member": _MEMBER_SCOPES,
    "project_manager": _PROJECT_MANAGER_SCOPES,
    "engineer": _ENGINEER_SCOPES,
    "steering_committee": _STEERING_COMMITTEE_SCOPES,
    "billing": _BILLING_SCOPES,
    "erp_developer": _ERP_DEVELOPER_SCOPES,
    # Owners are aliases for this bundle, so they intentionally receive every
    # currently defined administrative and specialist capability.
    "admin": _ADMIN_SCOPES,
}


def _normalize_role_name(role: str) -> str:
    """Normalize role spelling without doing fuzzy or partial matching."""

    return " ".join(
        role.strip()
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace("&", " ")
        .split()
    )


# A role name can intentionally resolve to more than one bundle for the
# combined Billing / ERP Dev Discord role. Exact aliases avoid treating a
# vaguely similar role name as authority.
_ROLE_ALIASES: dict[str, frozenset[str]] = {
    "member": frozenset({"member"}),
    "members": frozenset({"member"}),
    "project manager": frozenset({"project_manager"}),
    "project managers": frozenset({"project_manager"}),
    "engineer": frozenset({"engineer"}),
    "engineers": frozenset({"engineer"}),
    "workflows engineer": frozenset({"engineer"}),
    "workflows engineers": frozenset({"engineer"}),
    "steering": frozenset({"steering_committee"}),
    "steering committee": frozenset({"steering_committee"}),
    "steering committee member": frozenset({"steering_committee"}),
    "billing": frozenset({"billing"}),
    "billing team": frozenset({"billing"}),
    "finance": frozenset({"billing"}),
    "finance team": frozenset({"billing"}),
    "erp developer": frozenset({"erp_developer"}),
    "erp developers": frozenset({"erp_developer"}),
    "erp dev": frozenset({"erp_developer"}),
    "erp devs": frozenset({"erp_developer"}),
    "erpnext developer": frozenset({"erp_developer"}),
    "erpnext developers": frozenset({"erp_developer"}),
    "erpnext dev": frozenset({"erp_developer"}),
    "erpnext devs": frozenset({"erp_developer"}),
    "billing erp developer": frozenset({"billing", "erp_developer"}),
    "billing erp dev": frozenset({"billing", "erp_developer"}),
    "admin": frozenset({"admin"}),
    "admins": frozenset({"admin"}),
    "administrator": frozenset({"admin"}),
    "administrators": frozenset({"admin"}),
    "owner": frozenset({"admin"}),
    "owners": frozenset({"admin"}),
}


class PolicyEngine:
    """Authorize each proposed tool call without model involvement.

    Deployed callers must construct this through :meth:`from_settings`, which
    binds scopes to configured Discord role IDs and guild IDs. The permissive
    defaults retain deterministic, isolated-library test behavior only; they
    are not used by the API or Discord gateway runtime.
    """

    def __init__(
        self,
        *,
        role_id_bindings: Mapping[str, Collection[str]] | None = None,
        allowed_guild_ids: Collection[str] = (),
        require_guild_binding: bool = False,
        allow_role_name_fallback: bool = True,
        github_default_repo: str = "508-dev/todos",
        github_allowed_repos: str = "",
        github_steering_all_installed_repos: bool = True,
        github_steering_extra_repos: str = "",
        github_app_configured: bool = False,
    ) -> None:
        self._allowed_guild_ids = frozenset(
            str(guild_id).strip() for guild_id in allowed_guild_ids
        )
        self._role_id_bindings = {
            # A Discord guild's @everyone role uses the guild snowflake. Strip
            # it even for directly-constructed policies so a configuration
            # bypass cannot turn every member into an agent administrator.
            bundle_name: frozenset(str(role_id).strip() for role_id in role_ids)
            - self._allowed_guild_ids
            for bundle_name, role_ids in (role_id_bindings or {}).items()
            if bundle_name in _ROLE_SCOPES
        }
        self._require_guild_binding = require_guild_binding
        self._allow_role_name_fallback = allow_role_name_fallback
        self.github_default_repo = github_default_repo
        self.github_configured_repositories = _repository_set(
            github_default_repo,
            github_allowed_repos,
        )
        self.github_steering_repositories = _repository_set(
            github_default_repo,
            github_steering_extra_repos,
        )
        self.github_steering_all_installed_repos = github_steering_all_installed_repos
        self.github_app_configured = github_app_configured

    @classmethod
    def from_settings(
        cls,
        settings: SharedSettings,
        *,
        runtime_config: ToolRuntimeConfig | None = None,
    ) -> PolicyEngine:
        """Build the production policy from immutable Discord ID bindings."""

        environment = settings.environment.strip().casefold()
        local_environments = {"local", "development", "dev", "test", "testing"}
        configured_guild_id = str(settings.discord_server_id or "").strip()
        allowed_guild_ids = (
            frozenset({configured_guild_id})
            if configured_guild_id.isdecimal() and int(configured_guild_id) > 0
            else frozenset()
        )
        config = runtime_config or ToolRuntimeConfig.from_settings(settings)
        return cls(
            role_id_bindings=settings.agent_discord_role_id_bindings,
            allowed_guild_ids=allowed_guild_ids,
            # Production is intentionally unusable until an explicit guild
            # allowlist is configured alongside the role-ID bundle mappings.
            require_guild_binding=environment not in local_environments,
            allow_role_name_fallback=settings.agent_role_name_fallback_enabled,
            github_default_repo=config.github_default_repo,
            github_allowed_repos=config.github_allowed_repos,
            github_steering_all_installed_repos=(
                config.github_steering_all_installed_repos
            ),
            github_steering_extra_repos=config.github_steering_extra_repos,
            github_app_configured=_github_app_is_configured(config),
        )

    @classmethod
    def from_runtime_config(cls, config: ToolRuntimeConfig) -> PolicyEngine:
        """Build repository-aware authorization rules from live tool config."""

        return cls(
            github_default_repo=config.github_default_repo,
            github_allowed_repos=config.github_allowed_repos,
            github_steering_all_installed_repos=(
                config.github_steering_all_installed_repos
            ),
            github_steering_extra_repos=config.github_steering_extra_repos,
            github_app_configured=_github_app_is_configured(config),
        )

    def guild_is_allowed(self, guild_id: str | None) -> bool:
        """Return whether a Discord guild may reach this policy instance."""

        normalized_guild_id = (guild_id or "").strip()
        if self._allowed_guild_ids:
            return normalized_guild_id in self._allowed_guild_ids
        return not self._require_guild_binding

    def scopes_for_context(self, context: AgentIdentityContext) -> set[str]:
        if not self.guild_is_allowed(context.guild_id):
            return set()
        if (
            context.organization_id is not None
            and context.guild_id is not None
            and context.organization_id != context.guild_id
        ):
            # The Discord guild is the tenant boundary for this gateway. Do
            # not let a supplied guild authorization be paired with another
            # organization, especially for tenant-bound integrations.
            return set()

        scopes: set[str] = set()
        context_role_ids = frozenset(context.role_ids)
        for bundle_name, configured_role_ids in self._role_id_bindings.items():
            if context_role_ids & configured_role_ids:
                scopes.update(_ROLE_SCOPES[bundle_name])

        if not self._allow_role_name_fallback:
            return scopes

        for role in context.roles:
            for bundle_name in _ROLE_ALIASES.get(_normalize_role_name(role), ()):
                scopes.update(_ROLE_SCOPES[bundle_name])
        return scopes

    def authorize(
        self,
        *,
        context: AgentIdentityContext,
        manifest: ToolManifest | None,
        action: AgentToolAction,
    ) -> PolicyDecision:
        return self.authorize_with_scopes(
            context=context,
            manifest=manifest,
            action=action,
            effective_scopes=self.scopes_for_context(context),
        )

    def authorize_chat(self, *, context: AgentIdentityContext) -> PolicyDecision:
        """Authorize a no-tool conversational answer under the same boundary."""

        if context.impersonation:
            return PolicyDecision(
                False, "Impersonated Discord requests cannot use agent tools"
            )
        if "agent:chat" not in self.scopes_for_context(context):
            return PolicyDecision(False, "Missing required scopes: agent:chat")
        return PolicyDecision(True, "allowed")

    def authorize_with_scopes(
        self,
        *,
        context: AgentIdentityContext,
        manifest: ToolManifest | None,
        action: AgentToolAction,
        effective_scopes: set[str],
    ) -> PolicyDecision:
        # Callers may use ``effective_scopes`` to narrow an existing grant at
        # confirmation time. It must never be an alternate authority channel
        # that can add a scope absent from the caller's current Discord roles.
        current_scopes = self.scopes_for_context(context)
        if not effective_scopes.issubset(current_scopes):
            return PolicyDecision(
                False,
                "Effective scopes exceed current Discord role grants",
            )
        if manifest is None:
            return PolicyDecision(False, f"Unknown tool {action.tool_name}")
        if context.impersonation:
            return PolicyDecision(
                False, "Impersonated Discord requests cannot use agent tools"
            )
        if manifest.tenant_scoped and not context.organization_id:
            return PolicyDecision(False, "Tool requires resolved tenant context")

        required_scopes = self.required_scopes_for_action(
            manifest=manifest,
            action=action,
        )
        missing_scopes = [
            scope for scope in required_scopes if scope not in effective_scopes
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

    def required_scopes_for_action(
        self,
        *,
        manifest: ToolManifest,
        action: AgentToolAction,
    ) -> list[str]:
        required_scopes = list(manifest.required_scopes)
        if action.tool_name.startswith("github_issue."):
            required_scopes.append(
                self._github_issue_scope(
                    repository=action.arguments.get("repository"),
                    write=manifest.write,
                )
            )
        if action.tool_name == "task_write.update_task" and action.arguments.get(
            "assignee"
        ):
            required_scopes.append("task:assign")
        if action.tool_name == "memory_write.remember_fact":
            scope_type = str(action.arguments.get("scope_type") or "user")
            if scope_type == "project":
                required_scopes.append("memory:write_project")
            elif scope_type == "org":
                required_scopes.append("memory:admin")
        if action.tool_name == "memory_write.forget_fact" and action.arguments.get(
            "admin"
        ):
            required_scopes.append("memory:admin")
        return required_scopes

    def _github_issue_scope(self, *, repository: object, write: bool) -> str:
        """Return the narrowest repository scope required for an issue action."""

        normalized_repository = _repository_key(repository) or _repository_key(
            self.github_default_repo
        )
        suffix = "write" if write else "read"
        # Legacy token installs have an explicit repository allowlist. Keep the
        # existing Engineer grants working during the GitHub App migration.
        if not self.github_app_configured:
            if normalized_repository in self.github_configured_repositories:
                return "github:issue:create" if write else "github:issue:read"
            return f"github:repository:configured:{suffix}"
        # App installations are selected at execution time. Only Steering and
        # Admin may use the installation-wide grant; a member never receives
        # it merely by belonging to the Discord server.
        if (
            self.github_steering_all_installed_repos
            or normalized_repository in self.github_steering_repositories
        ):
            return f"github:repository:all:{suffix}"
        return f"github:repository:steering:{suffix}"

    def authorize_context_read(
        self,
        *,
        context: AgentIdentityContext,
        source_visibility: str,
        required_scope: str = "context:read_current_thread",
    ) -> PolicyDecision:
        """Authorize retrieval of already source-visible context."""

        if context.impersonation:
            return PolicyDecision(
                False, "Impersonated Discord requests cannot load context"
            )
        effective_scopes = self.scopes_for_context(context)
        if required_scope not in effective_scopes:
            return PolicyDecision(False, f"Missing required scopes: {required_scope}")
        if (
            source_visibility in {"private", "restricted"}
            and "memory:admin" not in effective_scopes
        ):
            return PolicyDecision(False, "Context source is private or restricted")
        return PolicyDecision(True, "allowed")

    @staticmethod
    def can_echo_memory_to_destination(
        *,
        context: AgentIdentityContext,
        visibility: MemoryVisibility,
    ) -> bool:
        """Return whether memory of this visibility can be rendered here."""

        if visibility == "private":
            return context.response_destination_visibility == "private"
        if visibility == "project":
            return context.response_destination_visibility in {"private", "restricted"}
        return True


def _github_app_is_configured(config: ToolRuntimeConfig) -> bool:
    return all(
        bool(str(value or "").strip())
        for value in (
            config.github_app_client_id,
            config.github_app_installation_id,
            config.github_app_private_key,
        )
    )


def _repository_set(*values: str | None) -> set[str]:
    repositories: set[str] = set()
    for value in values:
        for repository in (value or "").split(","):
            normalized = _repository_key(repository)
            if normalized is not None:
                repositories.add(normalized)
    return repositories


def _repository_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().strip("/").casefold()
    return normalized or None
