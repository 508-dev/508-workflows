"""Best-effort audit event writer for Discord user actions."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Final

import discord
import requests

from five08.discord_webhook import DiscordWebhookLogger

logger = logging.getLogger(__name__)
DEFAULT_WEBHOOK_USERNAME = "508 Workflows"
_WEBHOOK_SUCCESS_COLOR = 0x2ECC71
_WEBHOOK_ERROR_COLOR = 0xE74C3C
_WEBHOOK_WARNING_COLOR = 0xF1C40F
_WEBHOOK_INFO_COLOR = 0x3498DB
_FAILURE_RESULTS: Final[frozenset[str]] = frozenset(
    {"error", "failed", "failure", "denied"}
)
_SUCCESS_RESULTS: Final[frozenset[str]] = frozenset(
    {"success", "ok", "created", "queued", "succeeded"}
)
_WARNING_RESULTS: Final[frozenset[str]] = frozenset({"retrying", "warning"})
_NON_MUTATING_COMMAND_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "crm.resume_download_button",
        "crm.status",
        "crm.unlinked_discord_users",
    }
)
_NON_MUTATING_COMMAND_PREFIXES: Final[tuple[str, ...]] = (
    "crm.get_",
    "crm.search_",
    "crm.view_",
)


class DiscordAuditLogger:
    """Write human audit events to the backend API without breaking commands."""

    def __init__(
        self,
        *,
        base_url: str | None,
        shared_secret: str | None,
        timeout_seconds: float,
        discord_logs_webhook_url: str | None = None,
        discord_logs_webhook_wait: bool = True,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.shared_secret = (shared_secret or "").strip()
        self.timeout_seconds = timeout_seconds
        self.webhook_logger = DiscordWebhookLogger(
            webhook_url=discord_logs_webhook_url,
            timeout_seconds=timeout_seconds,
            wait_for_response=discord_logs_webhook_wait,
        )

    @property
    def enabled(self) -> bool:
        """Return whether audit writes are configured and enabled."""
        return bool(self.base_url and self.shared_secret)

    @property
    def webhook_enabled(self) -> bool:
        """Return whether Discord channel logging is configured."""
        return self.webhook_logger.enabled

    def log_command(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        result: str,
        metadata: dict[str, Any] | None = None,
        resource_type: str | None = "discord_command",
        resource_id: str | None = None,
    ) -> None:
        """Queue a best-effort audit write in the background."""
        if not (self.enabled or self.webhook_enabled):
            return
        if not self._should_log_command_event(action=action, result=result):
            return

        event_payload = self._build_discord_payload(
            interaction=interaction,
            action=action,
            result=result,
            metadata=metadata,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        self._queue_event(event_payload)

    @staticmethod
    def _is_failure_result(result: str) -> bool:
        """Return True when command result should always be audited."""
        return result.strip().lower() in _FAILURE_RESULTS

    @staticmethod
    def _is_non_mutating_action(action: str) -> bool:
        """Return True when the command action is treated as non-mutating."""
        normalized_action = action.strip().lower()
        if normalized_action in _NON_MUTATING_COMMAND_ACTIONS:
            return True
        return any(
            normalized_action.startswith(prefix)
            for prefix in _NON_MUTATING_COMMAND_PREFIXES
        )

    @staticmethod
    def _should_log_command_event(*, action: str, result: str) -> bool:
        """Return whether a command event should be emitted."""
        if DiscordAuditLogger._is_failure_result(result):
            return True
        return not DiscordAuditLogger._is_non_mutating_action(action)

    def log_admin_sso_action(
        self,
        *,
        action: str,
        result: str,
        actor_email: str,
        actor_display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Queue best-effort audit write for non-Discord human actions."""
        if not (self.enabled or self.webhook_enabled):
            return

        normalized_email = actor_email.strip().lower()
        if not normalized_email:
            return

        event_payload = {
            "source": "admin_dashboard",
            "action": action,
            "result": result,
            "actor_provider": "admin_sso",
            "actor_subject": normalized_email,
            "actor_display_name": actor_display_name,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "correlation_id": correlation_id,
            "metadata": metadata or {},
        }

        self._queue_event(event_payload)

    def _queue_event(self, event_payload: dict[str, Any]) -> None:
        try:
            task = asyncio.create_task(self._post_event(event_payload))
        except RuntimeError:
            thread = threading.Thread(
                target=self._run_event_in_thread,
                args=(event_payload,),
                daemon=True,
            )
            thread.start()
            return

        task.add_done_callback(self._on_task_done)

    def _run_event_in_thread(self, event_payload: dict[str, Any]) -> None:
        try:
            asyncio.run(self._post_event(event_payload))
        except Exception as exc:
            self._on_task_done(error=exc)
        else:
            self._on_task_done()

    async def _post_event(self, event_payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self._send_event_sync, event_payload)

    def _send_event_sync(self, event_payload: dict[str, Any]) -> None:
        if self.enabled:
            self._send_audit_event_sync(event_payload)
        if self.webhook_enabled:
            self._send_webhook_event(event_payload)

    def _send_audit_event_sync(self, event_payload: dict[str, Any]) -> None:
        if not self.enabled:
            return

        headers = {
            "X-API-Secret": self.shared_secret,
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/audit/events"

        try:
            response = requests.post(
                url,
                headers=headers,
                json=event_payload,
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                logger.warning(
                    "Audit write failed status=%s action=%s body=%s",
                    response.status_code,
                    event_payload.get("action"),
                    response.text[:300],
                )
        except Exception as exc:
            logger.warning(
                "Audit write exception action=%s error=%s",
                event_payload.get("action"),
                exc,
            )

    def _send_webhook_event(self, event_payload: dict[str, Any]) -> None:
        if not self.webhook_enabled:
            return
        self.webhook_logger.send(
            username=DEFAULT_WEBHOOK_USERNAME,
            embeds=[self._build_webhook_embed(event_payload)],
        )

    @staticmethod
    def _result_emoji(result: str) -> str:
        normalized = result.strip().lower()
        if normalized in _SUCCESS_RESULTS:
            return "✅"
        if normalized in _FAILURE_RESULTS:
            return "❌"
        return "ℹ️"

    @staticmethod
    def _shorten(text: str, max_length: int = 180) -> str:
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 3]}..."

    @staticmethod
    def _webhook_color(result: str) -> int:
        normalized = result.strip().lower()
        if normalized in _SUCCESS_RESULTS:
            return _WEBHOOK_SUCCESS_COLOR
        if normalized in _FAILURE_RESULTS:
            return _WEBHOOK_ERROR_COLOR
        if normalized in _WARNING_RESULTS:
            return _WEBHOOK_WARNING_COLOR
        return _WEBHOOK_INFO_COLOR

    def _build_webhook_embed(self, event_payload: dict[str, Any]) -> dict[str, Any]:
        source = str(event_payload.get("source") or "unknown")
        action = str(event_payload.get("action") or "unknown")
        result = str(event_payload.get("result") or "unknown")
        service = "Discord Bot"
        actor = (
            str(event_payload.get("actor_display_name"))
            if event_payload.get("actor_display_name")
            else str(event_payload.get("actor_subject"))
        )
        actor_subject = str(event_payload.get("actor_subject") or actor)
        resource = (
            f"{event_payload.get('resource_type')}:{event_payload.get('resource_id')}"
            if event_payload.get("resource_type")
            else None
        )
        metadata = (
            event_payload.get("metadata")
            if isinstance(event_payload.get("metadata"), dict)
            else {}
        )
        correlation_id = str(event_payload.get("correlation_id") or "") or None
        error = (
            metadata.get("error")
            if isinstance(metadata, dict) and metadata.get("error") is not None
            else None
        )
        command = metadata.get("command") if isinstance(metadata, dict) else None
        actor_value = f"{actor} ({actor_subject})" if actor != actor_subject else actor
        description = (
            f"{self._result_emoji(result)} {source} {action}\nresult: **{result}**"
        )
        fields: list[dict[str, Any]] = [
            {"name": "Service", "value": service, "inline": True},
            {"name": "Actor", "value": self._shorten(actor_value, 64), "inline": True},
            {"name": "Action", "value": self._shorten(action, 128), "inline": True},
        ]

        if resource:
            fields.append(
                {
                    "name": "Resource",
                    "value": self._shorten(str(resource), 128),
                    "inline": True,
                },
            )
        if correlation_id:
            fields.append(
                {
                    "name": "Correlation ID",
                    "value": self._shorten(correlation_id, 128),
                    "inline": False,
                },
            )
        if command:
            fields.append(
                {
                    "name": "Command",
                    "value": self._shorten(str(command), 128),
                    "inline": False,
                },
            )
        if error:
            fields.append(
                {
                    "name": "Error",
                    "value": self._shorten(str(error), 1024),
                    "inline": False,
                },
            )

        return {
            "title": f"{self._result_emoji(result)} {source}:{action}",
            "description": description,
            "color": self._webhook_color(result),
            "fields": fields,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }

    def _on_task_done(
        self,
        task: asyncio.Task[None] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        if error is None and task is not None:
            try:
                task.result()
            except Exception as exc:  # pragma: no cover - defensive fallback
                error = exc

        if error is not None:
            logger.warning("Unexpected audit task failure: %s", error)

    def _build_discord_payload(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        result: str,
        metadata: dict[str, Any] | None,
        resource_type: str | None,
        resource_id: str | None,
    ) -> dict[str, Any]:
        command_name = None
        if interaction.command is not None:
            command_name = interaction.command.qualified_name

        actor_display_name = getattr(interaction.user, "display_name", None)
        if not actor_display_name:
            actor_display_name = getattr(interaction.user, "name", None)

        base_metadata: dict[str, Any] = {
            "command": command_name,
            "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
            "channel_id": (
                str(interaction.channel_id)
                if interaction.channel_id is not None
                else None
            ),
            "interaction_id": str(interaction.id),
        }
        if metadata:
            base_metadata.update(metadata)

        return {
            "source": "discord",
            "action": action,
            "result": result,
            "actor_provider": "discord",
            "actor_subject": str(interaction.user.id),
            "actor_display_name": actor_display_name,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "correlation_id": str(interaction.id),
            "metadata": base_metadata,
        }
