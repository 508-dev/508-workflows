"""Read-only Discord server diagnostics for role-ID configuration."""

from __future__ import annotations

import io
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable

import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin

logger = logging.getLogger(__name__)

ROLE_PAGE_SIZE = 10
DIAGNOSTICS_VIEW_TIMEOUT_SECONDS = 600.0
EVERYONE_DISPLAY_NAME = "@\u200beveryone"
ROLE_BINDINGS: tuple[tuple[str, str, str], ...] = (
    ("admin", "Admin", "AGENT_DISCORD_ADMIN_ROLE_IDS"),
    (
        "steering_committee",
        "Steering committee",
        "AGENT_DISCORD_STEERING_COMMITTEE_ROLE_IDS",
    ),
    ("billing", "Billing", "AGENT_DISCORD_BILLING_ROLE_IDS"),
    ("erp_developer", "ERP developer", "AGENT_DISCORD_ERP_DEVELOPER_ROLE_IDS"),
    (
        "project_manager",
        "Project manager",
        "AGENT_DISCORD_PROJECT_MANAGER_ROLE_IDS",
    ),
    ("engineer", "Engineer", "AGENT_DISCORD_ENGINEER_ROLE_IDS"),
)


def _safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _configured_guild_id() -> int | None:
    configured_guild_id = str(settings.discord_server_id or "").strip()
    if not configured_guild_id.isdecimal():
        return None
    parsed_guild_id = _safe_int(configured_guild_id)
    return parsed_guild_id if parsed_guild_id > 0 else None


def _sorted_role_ids(role_ids: Iterable[str]) -> list[str]:
    return sorted(
        {str(role_id).strip() for role_id in role_ids if str(role_id).strip()},
        key=_safe_int,
    )


def _safe_role_name(value: object) -> str:
    """Normalize a role name for embeds, exports, and dashboard JSON."""
    return " ".join(str(value or "").split()) or "Unnamed role"


def _display_role_name(value: object) -> str:
    """Prevent diagnostics output from creating Discord mentions or markdown."""
    return discord.utils.escape_markdown(
        discord.utils.escape_mentions(_safe_role_name(value))
    )


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 1)].rstrip()}…"


def _role_payload(
    role: object,
    *,
    guild_id: str,
    bot_top_role_position: int | None,
    bot_can_manage_roles: bool,
) -> dict[str, Any]:
    role_id = str(getattr(role, "id", "")).strip()
    position = _safe_int(getattr(role, "position", 0))
    is_default = role_id == guild_id
    managed = bool(getattr(role, "managed", False))
    manageable_by_bot = bool(
        bot_can_manage_roles
        and bot_top_role_position is not None
        and position < bot_top_role_position
        and not is_default
        and not managed
    )
    return {
        "id": role_id,
        "name": _safe_role_name(getattr(role, "name", "")),
        "position": position,
        "managed": managed,
        "is_default": is_default,
        "manageable_by_bot": manageable_by_bot,
    }


def _agent_shared_secret_status() -> str:
    """Return a safe status marker without revealing a credential value."""
    agent_secret = str(settings.agent_shared_secret or "").strip()
    if not agent_secret:
        return "missing"

    api_secret = str(settings.api_shared_secret or "").strip()
    if api_secret and secrets.compare_digest(agent_secret, api_secret):
        return "matches_api_shared_secret"
    if api_secret:
        return "separate"
    return "configured"


def build_diagnostics_snapshot(
    guild: object,
    roles: Iterable[object],
    *,
    source: str,
    refresh_error: str | None = None,
    snapshot_at: datetime | None = None,
) -> dict[str, Any]:
    """Build JSON-safe diagnostics data from a Discord guild role snapshot."""
    guild_id = str(getattr(guild, "id", "")).strip()
    bot_member = getattr(guild, "me", None)
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    bot_can_manage_roles = bool(getattr(bot_permissions, "manage_roles", False))
    bot_top_role = getattr(bot_member, "top_role", None)
    bot_top_role_position = (
        _safe_int(getattr(bot_top_role, "position", 0))
        if bot_top_role is not None
        else None
    )
    role_payloads = [
        _role_payload(
            role,
            guild_id=guild_id,
            bot_top_role_position=bot_top_role_position,
            bot_can_manage_roles=bot_can_manage_roles,
        )
        for role in roles
    ]
    role_payloads.sort(
        key=lambda role: (-int(role["position"]), _safe_int(role["id"])),
    )
    roles_by_id = {role["id"]: role for role in role_payloads if role["id"]}

    bindings: list[dict[str, Any]] = []
    configured_role_count = 0
    resolved_role_count = 0
    missing_role_count = 0
    unconfigured_binding_count = 0
    for bundle, label, environment_variable in ROLE_BINDINGS:
        role_ids = _sorted_role_ids(
            settings.agent_discord_role_id_bindings.get(bundle, frozenset())
        )
        configured_role_count += len(role_ids)
        resolved_roles: list[dict[str, Any]] = []
        for role_id in role_ids:
            role = roles_by_id.get(role_id)
            if role_id == guild_id:
                status = "everyone"
            elif role is None:
                status = "missing"
            else:
                status = "resolved"

            if status == "resolved":
                resolved_role_count += 1
            elif status == "missing":
                missing_role_count += 1
            resolved_roles.append(
                {
                    "id": role_id,
                    "name": role["name"] if role is not None else None,
                    "status": status,
                    "managed": role["managed"] if role is not None else None,
                    "manageable_by_bot": (
                        role["manageable_by_bot"] if role is not None else None
                    ),
                }
            )

        if not role_ids:
            binding_status = "unconfigured"
            unconfigured_binding_count += 1
        elif all(role["status"] == "resolved" for role in resolved_roles):
            binding_status = "resolved"
        else:
            binding_status = "attention"
        bindings.append(
            {
                "bundle": bundle,
                "label": label,
                "environment_variable": environment_variable,
                "role_ids": role_ids,
                "roles": resolved_roles,
                "status": binding_status,
            }
        )

    now = snapshot_at or datetime.now(tz=timezone.utc)
    return {
        "guild": {
            "id": guild_id,
            "name": _safe_role_name(getattr(guild, "name", "")),
            "configured_server_matches": guild_id == str(_configured_guild_id() or ""),
        },
        "snapshot": {
            "created_at": now.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "source": source,
            "refresh_error": refresh_error,
        },
        "bot": {
            "manage_roles": bot_can_manage_roles,
            "top_role": (
                {
                    "id": str(getattr(bot_top_role, "id", "")).strip(),
                    "name": _safe_role_name(getattr(bot_top_role, "name", "")),
                    "position": bot_top_role_position,
                }
                if bot_top_role is not None
                else None
            ),
        },
        "agent": {
            "configured_role_count": configured_role_count,
            "resolved_role_count": resolved_role_count,
            "missing_role_count": missing_role_count,
            "unconfigured_binding_count": unconfigured_binding_count,
            "agent_shared_secret_status": _agent_shared_secret_status(),
            "role_bindings": bindings,
        },
        "roles": role_payloads,
    }


def diagnostics_export_text(snapshot: dict[str, Any]) -> str:
    """Produce a human-copyable, non-applying configuration and role export."""
    guild = snapshot.get("guild") if isinstance(snapshot.get("guild"), dict) else {}
    agent = snapshot.get("agent") if isinstance(snapshot.get("agent"), dict) else {}
    role_bindings = agent.get("role_bindings") if isinstance(agent, dict) else []
    roles = snapshot.get("roles") if isinstance(snapshot.get("roles"), list) else []
    guild_id = str(guild.get("id") or "").strip()
    guild_name = _safe_role_name(guild.get("name"))

    lines = [
        "# 508 Discord role diagnostics (read-only export)",
        f"# Server: {guild_name} ({guild_id})",
        "# Copy these values into deployment configuration only after review.",
        "# This file never changes Discord roles or agent permissions.",
        "",
        f"DISCORD_SERVER_ID={guild_id}",
    ]
    if isinstance(role_bindings, list):
        for binding in role_bindings:
            if not isinstance(binding, dict):
                continue
            environment_variable = str(
                binding.get("environment_variable") or ""
            ).strip()
            role_ids = binding.get("role_ids")
            if environment_variable:
                values = (
                    ",".join(str(role_id) for role_id in role_ids)
                    if isinstance(role_ids, list)
                    else ""
                )
                lines.append(f"{environment_variable}={values}")

    lines.extend(("", "# Role catalog: name<TAB>role ID<TAB>metadata"))
    if isinstance(roles, list):
        for role in roles:
            if not isinstance(role, dict):
                continue
            flags: list[str] = []
            if role.get("is_default"):
                flags.append("@everyone")
            if role.get("managed"):
                flags.append("managed")
            if role.get("manageable_by_bot"):
                flags.append("manageable-by-bot")
            lines.append(
                "\t".join(
                    (
                        _safe_role_name(role.get("name")),
                        str(role.get("id") or ""),
                        ", ".join(flags) or "standard",
                    )
                )
            )
    return "\n".join(lines) + "\n"


def _snapshot_source_label(snapshot: dict[str, Any]) -> str:
    snapshot_metadata = snapshot.get("snapshot")
    if not isinstance(snapshot_metadata, dict):
        return "Unknown source"
    source = str(snapshot_metadata.get("source") or "")
    if source == "discord_api":
        return "Refreshed from Discord"
    return "Gateway cache"


def _overview_embed(snapshot: dict[str, Any]) -> discord.Embed:
    guild = snapshot["guild"]
    agent = snapshot["agent"]
    bot = snapshot["bot"]
    snapshot_metadata = snapshot["snapshot"]
    secret_status = str(agent["agent_shared_secret_status"])
    secret_label = {
        "separate": "Configured and separate from API credential",
        "configured": "Configured (API credential status unavailable)",
        "matches_api_shared_secret": "Needs attention: matches API credential",
        "missing": "Needs attention: not configured",
    }.get(secret_status, "Unknown")
    refresh_error = snapshot_metadata.get("refresh_error")
    description = "Read-only role catalog and agent configuration health. No role grants or settings are changed."
    if refresh_error:
        description += f"\n\nRefresh fallback: {_truncate(str(refresh_error), 180)}"
    embed = discord.Embed(
        title="Discord diagnostics",
        description=description,
        colour=discord.Colour.blurple(),
    )
    embed.add_field(
        name="Server",
        value=f"{_display_role_name(guild['name'])}\n`{guild['id']}`",
        inline=False,
    )
    embed.add_field(
        name="Role catalog",
        value=(
            f"{len(snapshot['roles'])} roles • {_snapshot_source_label(snapshot)}\n"
            f"Snapshot: {snapshot_metadata['created_at']}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Agent role bindings",
        value=(
            f"{agent['resolved_role_count']} resolved • "
            f"{agent['missing_role_count']} missing • "
            f"{agent['unconfigured_binding_count']} unconfigured"
        ),
        inline=False,
    )
    embed.add_field(
        name="Agent credential",
        value=secret_label,
        inline=False,
    )
    top_role = bot.get("top_role")
    if isinstance(top_role, dict):
        bot_status = (
            f"Top role: {_display_role_name(top_role['name'])} `#{top_role['position']}`\n"
            f"Manage roles: {'yes' if bot['manage_roles'] else 'no'}"
        )
    else:
        bot_status = "Bot member is not currently resolved."
    embed.add_field(name="Bot role access", value=bot_status, inline=False)
    return embed


def _mapping_embed(snapshot: dict[str, Any]) -> discord.Embed:
    agent = snapshot["agent"]
    embed = discord.Embed(
        title="Discord diagnostics • agent mappings",
        description=(
            "Configured IDs are resolved against this server only. Role names are display "
            "metadata; production authorization remains bound to immutable role IDs."
        ),
        colour=discord.Colour.blurple(),
    )
    for binding in agent["role_bindings"]:
        status = str(binding["status"])
        if not binding["roles"]:
            value = "Not configured"
        else:
            lines = []
            for role in binding["roles"]:
                if role["status"] == "resolved":
                    label = f"{_display_role_name(role['name'])} ✓"
                elif role["status"] == "everyone":
                    label = f"{EVERYONE_DISPLAY_NAME} ⚠"
                else:
                    label = "Missing from this server ⚠"
                lines.append(f"`{role['id']}` — {label}")
            value = _truncate("\n".join(lines), 1000)
        embed.add_field(
            name=f"{binding['label']} • {status}",
            value=value,
            inline=False,
        )
    return embed


def _roles_embed(snapshot: dict[str, Any], *, page: int) -> discord.Embed:
    roles = snapshot["roles"]
    page_count = max(1, (len(roles) + ROLE_PAGE_SIZE - 1) // ROLE_PAGE_SIZE)
    normalized_page = min(max(page, 0), page_count - 1)
    start = normalized_page * ROLE_PAGE_SIZE
    visible_roles = roles[start : start + ROLE_PAGE_SIZE]
    if visible_roles:
        lines = []
        for role in visible_roles:
            flags = []
            if role["is_default"]:
                flags.append(EVERYONE_DISPLAY_NAME)
            if role["managed"]:
                flags.append("managed")
            if role["manageable_by_bot"]:
                flags.append("manageable by bot")
            suffix = f" • {', '.join(flags)}" if flags else ""
            lines.append(
                f"**{_display_role_name(role['name'])}** — `{role['id']}` "
                f"• position {role['position']}{suffix}"
            )
        description = "\n".join(lines)
    else:
        description = "No roles are currently available from the bot gateway cache."
    return discord.Embed(
        title=f"Discord diagnostics • roles ({normalized_page + 1}/{page_count})",
        description=_truncate(description, 4000),
        colour=discord.Colour.blurple(),
    )


class DiagnosticsView(discord.ui.View):
    """Private, short-lived navigation for the read-only diagnostic panel."""

    def __init__(
        self,
        *,
        cog: "DiagnosticsCog",
        owner_id: int,
        snapshot: dict[str, Any],
    ) -> None:
        super().__init__(timeout=DIAGNOSTICS_VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = owner_id
        self.snapshot = snapshot
        self.active_panel = "overview"
        self.role_page = 0
        self.message: discord.WebhookMessage | None = None
        self._sync_role_navigation()

    def _role_page_count(self) -> int:
        return max(
            1,
            (len(self.snapshot["roles"]) + ROLE_PAGE_SIZE - 1) // ROLE_PAGE_SIZE,
        )

    def _sync_role_navigation(self) -> None:
        """Disable role pagination controls outside the role catalog bounds."""
        page_count = self._role_page_count()
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "discord_diagnostics_previous_roles":
                child.disabled = self.active_panel != "roles" or self.role_page <= 0
            elif child.custom_id == "discord_diagnostics_next_roles":
                child.disabled = (
                    self.active_panel != "roles" or self.role_page >= page_count - 1
                )

    def _embed(self) -> discord.Embed:
        if self.active_panel == "roles":
            return _roles_embed(self.snapshot, page=self.role_page)
        if self.active_panel == "mappings":
            return _mapping_embed(self.snapshot)
        return _overview_embed(self.snapshot)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if getattr(interaction.user, "id", None) != self.owner_id:
            await interaction.response.send_message(
                "This diagnostics panel belongs to the administrator who opened it.",
                ephemeral=True,
            )
            return False
        if self.cog._can_view_diagnostics(interaction):
            return True
        await interaction.response.send_message(
            "You no longer have Discord's Manage Server permission for this panel.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug("Diagnostics panel expired before it could be disabled")

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.secondary)
    async def overview_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        self.active_panel = "overview"
        self._sync_role_navigation()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Roles", style=discord.ButtonStyle.secondary)
    async def roles_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        self.active_panel = "roles"
        self.role_page = 0
        self._sync_role_navigation()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Mappings", style=discord.ButtonStyle.secondary)
    async def mappings_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        self.active_panel = "mappings"
        self._sync_role_navigation()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Export", style=discord.ButtonStyle.secondary)
    async def export_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        guild_id = str(self.snapshot["guild"]["id"] or "unknown")
        payload = diagnostics_export_text(self.snapshot).encode("utf-8")
        await interaction.response.send_message(
            "Read-only role catalog and configuration export:",
            file=discord.File(
                io.BytesIO(payload),
                filename=f"discord-role-diagnostics-{guild_id}.txt",
            ),
            ephemeral=True,
        )
        self.cog._audit_snapshot_action(
            interaction,
            action="discord.diagnostics.export",
            snapshot=self.snapshot,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        snapshot, status_code = await self.cog.get_diagnostics_snapshot(refresh=True)
        if status_code != 200:
            await interaction.response.send_message(
                "Unable to refresh diagnostics right now. The bot cannot resolve the configured server.",
                ephemeral=True,
            )
            self.cog._audit_snapshot_action(
                interaction,
                action="discord.diagnostics.refresh",
                result="error",
            )
            return
        self.snapshot = snapshot
        self.role_page = 0
        self._sync_role_navigation()
        await interaction.response.edit_message(embed=self._embed(), view=self)
        self.cog._audit_snapshot_action(
            interaction,
            action="discord.diagnostics.refresh",
            snapshot=snapshot,
        )

    @discord.ui.button(
        label="Previous roles",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="discord_diagnostics_previous_roles",
    )
    async def previous_roles_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        self.role_page = max(0, self.role_page - 1)
        self._sync_role_navigation()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(
        label="Next roles",
        style=discord.ButtonStyle.secondary,
        row=1,
        custom_id="discord_diagnostics_next_roles",
    )
    async def next_roles_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button["DiagnosticsView"],
    ) -> None:
        self.role_page = min(self._role_page_count() - 1, self.role_page + 1)
        self._sync_role_navigation()
        await interaction.response.edit_message(embed=self._embed(), view=self)


class DiagnosticsCog(DiscordAuditCogMixin, commands.Cog):
    """Expose a private, no-write diagnostic role catalog to server admins."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()

    def _configured_guild(self) -> discord.Guild | None:
        guild_id = _configured_guild_id()
        if guild_id is None:
            return None
        return self.bot.get_guild(guild_id)

    @staticmethod
    def _can_view_diagnostics(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            return False
        user = interaction.user
        if getattr(user, "id", None) == getattr(guild, "owner_id", None):
            return True
        permissions = getattr(user, "guild_permissions", None)
        return bool(
            getattr(permissions, "manage_guild", False)
            or getattr(permissions, "administrator", False)
        )

    async def get_diagnostics_snapshot(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """Return the configured guild's safe role catalog for internal consumers."""
        guild = self._configured_guild()
        if guild is None:
            return {"error": "configured_guild_unavailable"}, 503

        roles = list(guild.roles)
        source = "gateway_cache"
        refresh_error: str | None = None
        if refresh:
            try:
                roles = list(await guild.fetch_roles())
                source = "discord_api"
            except discord.Forbidden:
                refresh_error = (
                    "Discord denied a fresh role lookup; showing the gateway cache."
                )
            except discord.HTTPException as exc:
                logger.warning(
                    "Failed refreshing Discord diagnostics role catalog guild_id=%s: %s",
                    guild.id,
                    exc,
                )
                refresh_error = (
                    "Discord role refresh failed; showing the gateway cache."
                )
            except Exception:
                logger.exception(
                    "Unexpected Discord diagnostics role refresh failure guild_id=%s",
                    guild.id,
                )
                refresh_error = (
                    "Discord role refresh failed; showing the gateway cache."
                )

        return (
            build_diagnostics_snapshot(
                guild,
                roles,
                source=source,
                refresh_error=refresh_error,
            ),
            200,
        )

    def _audit_snapshot_action(
        self,
        interaction: discord.Interaction,
        *,
        action: str,
        result: str = "success",
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        agent = snapshot.get("agent") if isinstance(snapshot, dict) else {}
        role_count = (
            len(snapshot.get("roles", [])) if isinstance(snapshot, dict) else None
        )
        self._audit_command_safe(
            interaction=interaction,
            action=action,
            result=result,
            metadata={
                "guild_id": str(getattr(interaction, "guild_id", "") or ""),
                "role_count": role_count,
                "resolved_role_count": agent.get("resolved_role_count")
                if isinstance(agent, dict)
                else None,
                "missing_role_count": agent.get("missing_role_count")
                if isinstance(agent, dict)
                else None,
            },
            resource_type="discord_server",
            resource_id=str(getattr(interaction, "guild_id", "") or ""),
        )

    @app_commands.command(
        name="diagnostics",
        description="View read-only server role and agent configuration diagnostics.",
    )
    async def diagnostics_command(self, interaction: discord.Interaction) -> None:
        """Show role IDs and configuration health without changing any access."""
        configured_guild_id = _configured_guild_id()
        guild = interaction.guild
        if (
            configured_guild_id is None
            or guild is None
            or getattr(guild, "id", None) != configured_guild_id
        ):
            await interaction.response.send_message(
                "Diagnostics are only available in the configured Discord server.",
                ephemeral=True,
            )
            return
        if not self._can_view_diagnostics(interaction):
            await interaction.response.send_message(
                "You need Discord's Manage Server permission to view diagnostics.",
                ephemeral=True,
            )
            self._audit_snapshot_action(
                interaction,
                action="discord.diagnostics.view",
                result="denied",
            )
            return

        await interaction.response.defer(ephemeral=True)
        snapshot, status_code = await self.get_diagnostics_snapshot()
        if status_code != 200:
            await interaction.followup.send(
                "Diagnostics are temporarily unavailable because the bot cannot resolve the configured server.",
                ephemeral=True,
            )
            self._audit_snapshot_action(
                interaction,
                action="discord.diagnostics.view",
                result="error",
            )
            return

        view = DiagnosticsView(
            cog=self,
            owner_id=int(interaction.user.id),
            snapshot=snapshot,
        )
        message = await interaction.followup.send(
            embed=_overview_embed(snapshot),
            view=view,
            ephemeral=True,
            wait=True,
        )
        if isinstance(message, discord.WebhookMessage):
            view.message = message
        self._audit_snapshot_action(
            interaction,
            action="discord.diagnostics.view",
            snapshot=snapshot,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the Discord diagnostics cog."""
    await bot.add_cog(DiagnosticsCog(bot))
