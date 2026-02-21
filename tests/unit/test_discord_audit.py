"""Unit tests for Discord audit helper."""

from unittest.mock import Mock, patch

from five08.discord_bot.utils.audit import DiscordAuditLogger


def _mock_interaction() -> Mock:
    interaction = Mock()
    interaction.id = 123456789
    interaction.guild_id = 987654321
    interaction.channel_id = 555
    interaction.command = Mock()
    interaction.command.qualified_name = "search-members"
    interaction.user = Mock()
    interaction.user.id = 42
    interaction.user.display_name = "Test User"
    interaction.user.name = "testuser"
    return interaction


def test_audit_logger_disabled_without_config() -> None:
    """Logger should no-op when base URL/secret are not configured."""
    logger = DiscordAuditLogger(base_url=None, shared_secret=None, timeout_seconds=1.0)
    interaction = _mock_interaction()

    logger.log_command(
        interaction=interaction,
        action="crm.search_members",
        result="success",
    )

    assert logger.enabled is False


def test_send_event_sync_logs_warning_on_request_error() -> None:
    """Request exceptions should be logged as warnings and not raised."""
    logger = DiscordAuditLogger(
        base_url="http://backend-api:8090",
        shared_secret="secret",
        timeout_seconds=1.0,
    )
    payload = logger._build_discord_payload(
        interaction=_mock_interaction(),
        action="crm.search_members",
        result="success",
        metadata={"query": "python"},
        resource_type="discord_command",
        resource_id=None,
    )

    with patch("five08.discord_bot.utils.audit.requests.post") as mock_post:
        with patch("five08.discord_bot.utils.audit.logger.warning") as mock_warning:
            mock_post.side_effect = RuntimeError("network down")
            logger._send_event_sync(payload)

    mock_warning.assert_called_once()


def test_log_admin_sso_action_normalizes_actor_email() -> None:
    """Admin SSO audit should normalize actor email and queue the event."""
    logger = DiscordAuditLogger(
        base_url="http://backend-api:8090",
        shared_secret="secret",
        timeout_seconds=1.0,
    )

    with patch.object(logger, "_queue_event") as mock_queue:
        logger.log_admin_sso_action(
            action="crm.resume_mailbox_ingest",
            result="success",
            actor_email=" Admin@Example.COM ",
            actor_display_name="Admin User",
            metadata={"processed_attachments": 1},
            resource_type="mailbox_message",
            resource_id="<msg-id>",
            correlation_id="<msg-id>",
        )

    mock_queue.assert_called_once()
    payload = mock_queue.call_args.args[0]
    assert payload["source"] == "admin_dashboard"
    assert payload["actor_provider"] == "admin_sso"
    assert payload["actor_subject"] == "admin@example.com"
