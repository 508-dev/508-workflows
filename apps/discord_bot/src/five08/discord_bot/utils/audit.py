"""Best-effort audit event writer for Discord user actions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
import requests

logger = logging.getLogger(__name__)


class DiscordAuditLogger:
    """Write human audit events to the worker API without breaking commands."""

    def __init__(
        self,
        *,
        base_url: str | None,
        shared_secret: str | None,
        timeout_seconds: float,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.shared_secret = (shared_secret or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        """Return whether audit writes are configured and enabled."""
        return bool(self.base_url and self.shared_secret)

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
        if not self.enabled:
            return

        event_payload = self._build_payload(
            interaction=interaction,
            action=action,
            result=result,
            metadata=metadata,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        task = asyncio.create_task(self._post_event(event_payload))
        task.add_done_callback(self._on_task_done)

    async def _post_event(self, event_payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self._send_event_sync, event_payload)

    def _send_event_sync(self, event_payload: dict[str, Any]) -> None:
        if not self.enabled:
            return

        url = f"{self.base_url}/audit/events"
        headers = {
            "X-API-Secret": self.shared_secret,
            "Content-Type": "application/json",
        }

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

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("Unexpected audit task failure: %s", exc)

    def _build_payload(
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
