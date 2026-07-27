"""Deterministic authorization policy for agent tool calls."""

from __future__ import annotations

from dataclasses import dataclass

from five08.agent.models import AgentIdentityContext, AgentToolAction, MemoryVisibility
from five08.agent.tools import ToolManifest, ToolRuntimeConfig


@dataclass(frozen=True)
class PolicyDecision:
    """Result of deterministic policy evaluation."""

    allowed: bool
    reason: str
    requires_confirmation: bool = False


_ROLE_SCOPES: dict[str, frozenset[str]] = {
    "member": frozenset(
        {
            "project:read",
            "task:create",
            "task:update_own",
            "context:read_current_thread",
            "memory:read_self",
            "memory:write_self",
            "github:repository:member:read",
            "github:repository:member:write",
        }
    ),
    "project_manager": frozenset(
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
    ),
    "engineer": frozenset(
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
            "github:issue:read",
            "github:issue:create",
            "github:repository:all:read",
            "github:repository:all:write",
            "github:project:read",
            "github:project:write",
            "github:pr:create",
            "crm:contact:read",
            "crm:contact:update",
            "docuseal:submission:create",
            "mailbox:create",
            "deploy:request",
            "user:manage",
            "integration:manage",
            "context:read_current_thread",
            "context:read_channel_recent",
            "context:read_user_recent_self",
            "context:read_user_recent_any",
            "memory:read_self",
            "memory:read_project",
            "memory:write_self",
            "memory:write_project",
            "memory:admin",
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
        "workflows engineer",
        *_ADMIN_ROLE_NAMES,
    }
)


class PolicyEngine:
    """Authorize each proposed tool call without model involvement."""

    def __init__(
        self,
        *,
        github_default_repo: str = "508-dev/todos",
        github_member_extra_repos: str = "",
        github_allowed_repos: str = "",
        github_steering_all_installed_repos: bool = True,
        github_steering_extra_repos: str = "",
        github_app_configured: bool = False,
    ) -> None:
        self.github_default_repo = github_default_repo
        self.github_member_repositories = _repository_set(
            github_default_repo,
            github_member_extra_repos,
        )
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
    def from_runtime_config(cls, config: ToolRuntimeConfig) -> "PolicyEngine":
        """Build repository-aware authorization rules from live tool config."""

        return cls(
            github_default_repo=config.github_default_repo,
            github_member_extra_repos=config.github_member_extra_repos,
            github_allowed_repos=config.github_allowed_repos,
            github_steering_all_installed_repos=(
                config.github_steering_all_installed_repos
            ),
            github_steering_extra_repos=config.github_steering_extra_repos,
            github_app_configured=all(
                bool(str(value or "").strip())
                for value in (
                    config.github_app_id,
                    config.github_app_installation_id,
                    config.github_app_private_key,
                )
            ),
        )

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
        if "engineer" in normalized_roles or "workflows engineer" in normalized_roles:
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
        return self.authorize_with_scopes(
            context=context,
            manifest=manifest,
            action=action,
            effective_scopes=self.scopes_for_context(context),
        )

    def authorize_with_scopes(
        self,
        *,
        context: AgentIdentityContext,
        manifest: ToolManifest | None,
        action: AgentToolAction,
        effective_scopes: set[str],
    ) -> PolicyDecision:
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
        normalized_repository = _repository_key(repository) or _repository_key(
            self.github_default_repo
        )
        suffix = "write" if write else "read"
        if normalized_repository in self.github_member_repositories:
            return f"github:repository:member:{suffix}"
        if (
            not self.github_app_configured
            and normalized_repository in self.github_configured_repositories
        ):
            return f"github:repository:configured:{suffix}"
        if (
            self.github_app_configured and self.github_steering_all_installed_repos
        ) or normalized_repository in self.github_steering_repositories:
            return f"github:repository:all:{suffix}"
        # During migration, preserve existing Engineer behavior for legacy
        # token-backed installs. Execution still enforces GITHUB_ALLOWED_REPOS.
        if not self.github_app_configured:
            return "github:issue:create" if write else "github:issue:read"
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
