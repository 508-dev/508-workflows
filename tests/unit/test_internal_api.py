"""Unit tests for bot internal automation routes."""

import asyncio
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock, Mock, call

import discord
import pytest

from five08.engagements import EngagementStatus
from five08.discord_bot.utils.internal_api import (
    AgentScheduleChannelRequest,
    AgentScheduleMemberSnapshotRequest,
    GigThreadStatusRequest,
    InternalAPIRoutes,
    MemberAgreementRoleRequest,
    PostJobLeadRequest,
    StageJobLeadRequest,
)


class TestInternalAPIRoutes:
    """Unit tests for internal bot API route handlers."""

    @pytest.fixture
    def mock_bot(self):
        bot = Mock()
        bot.guilds = [Mock(), Mock()]
        return bot

    @pytest.fixture
    def internal_api_routes(self, mock_bot):
        return InternalAPIRoutes(mock_bot)

    @pytest.mark.asyncio
    async def test_grant_member_role_applies_member_role(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Signed linked users should receive the Member role."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "123",
        )

        member_role = Mock()
        member_role.name = "Member"

        bot_top_role = Mock()
        bot_top_role.__gt__ = Mock(return_value=True)

        target_member = Mock()
        target_member.roles = []
        target_member.add_roles = AsyncMock()
        target_member.top_role = Mock()

        guild = Mock()
        guild.id = 123
        guild.roles = [member_role]
        guild.get_member.return_value = target_member
        guild.fetch_member = AsyncMock(return_value=target_member)
        guild.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=bot_top_role,
        )
        internal_api_routes.bot.get_guild.return_value = guild

        payload = MemberAgreementRoleRequest(
            discord_user_id="456",
            contact_id="contact-1",
            submission_id=4200,
        )

        result, status_code = await internal_api_routes._grant_member_role(payload)

        assert status_code == 200
        assert result["status"] == "applied"
        target_member.add_roles.assert_awaited_once_with(
            member_role,
            reason="Member agreement signed via Docuseal (contact contact-1, submission 4200)",
        )

    @pytest.mark.asyncio
    async def test_grant_member_role_returns_already_present(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Users who already have Member should not be modified."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "123",
        )

        member_role = Mock()
        member_role.name = "Member"

        target_member = Mock()
        target_member.roles = [member_role]

        guild = Mock()
        guild.id = 123
        guild.roles = [member_role]
        guild.get_member.return_value = target_member
        internal_api_routes.bot.get_guild.return_value = guild

        payload = MemberAgreementRoleRequest(discord_user_id="456")
        result, status_code = await internal_api_routes._grant_member_role(payload)

        assert status_code == 200
        assert result["status"] == "already_present"

    @pytest.mark.asyncio
    async def test_member_agreement_role_handler_rejects_unauthorized(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Internal role grant endpoint should require the shared API secret."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.api_shared_secret",
            "top-secret",
        )
        request = Mock()
        request.headers = {"X-API-Secret": "wrong"}

        response = await internal_api_routes.member_agreement_role_handler(request)

        assert response.status == 401
        assert json.loads(response.body.decode("utf-8")) == {"error": "unauthorized"}

    @pytest.mark.asyncio
    async def test_post_job_lead_delegates_to_jobs_cog(self, internal_api_routes):
        """Internal lead posting should reuse the jobs cog posting flow."""
        jobs_cog = Mock()
        jobs_cog.post_job_lead_to_discord = AsyncMock(
            return_value=(
                {
                    "status": "posted",
                    "lead_id": "lead-1",
                    "thread_id": "thread-1",
                },
                200,
            )
        )
        internal_api_routes.bot.get_cog.return_value = jobs_cog

        result, status_code = await internal_api_routes._post_job_lead(
            PostJobLeadRequest(
                lead_id="lead-1",
                reviewer_discord_user_id="admin-1",
                channel_id="channel-1",
                tags="Remote",
                engagement_status="recruiting",
            )
        )

        assert status_code == 200
        assert result["thread_id"] == "thread-1"
        jobs_cog.post_job_lead_to_discord.assert_awaited_once_with(
            lead_id="lead-1",
            reviewer_discord_user_id="admin-1",
            channel_id="channel-1",
            tags="Remote",
            engagement_status=EngagementStatus.RECRUITING,
            reason="Dashboard promoted qualified job lead",
        )

    @pytest.mark.asyncio
    async def test_stage_job_lead_delegates_to_jobs_cog(self, internal_api_routes):
        """Internal lead staging should reuse the jobs cog holding-forum flow."""
        jobs_cog = Mock()
        jobs_cog.stage_job_lead_to_discord = AsyncMock(
            return_value=(
                {
                    "status": "staged",
                    "lead_id": "lead-1",
                    "thread_id": "thread-1",
                },
                200,
            )
        )
        internal_api_routes.bot.get_cog.return_value = jobs_cog

        result, status_code = await internal_api_routes._stage_job_lead(
            StageJobLeadRequest(
                lead_id="lead-1",
                reviewer_discord_user_id="admin-1",
            )
        )

        assert status_code == 200
        assert result["thread_id"] == "thread-1"
        jobs_cog.stage_job_lead_to_discord.assert_awaited_once_with(
            lead_id="lead-1",
            reviewer_discord_user_id="admin-1",
            reason="Dashboard staged unqualified job lead",
        )

    @pytest.mark.asyncio
    async def test_post_job_lead_returns_unavailable_without_jobs_cog(
        self, internal_api_routes
    ):
        """The internal route should fail loudly when the jobs cog is unavailable."""
        internal_api_routes.bot.get_cog.return_value = None

        result, status_code = await internal_api_routes._post_job_lead(
            PostJobLeadRequest(lead_id="lead-1", reviewer_discord_user_id="admin-1")
        )

        assert status_code == 503
        assert result == {"error": "jobs_cog_unavailable"}

    @pytest.mark.asyncio
    async def test_list_job_channels_delegates_to_jobs_cog(self, internal_api_routes):
        """Internal channel metadata should come from the jobs cog."""
        jobs_cog = Mock()
        jobs_cog.list_registered_job_post_forums = AsyncMock(
            return_value=(
                {
                    "channels": [
                        {
                            "channel_id": "123",
                            "posting_type": "part_time",
                            "requires_tag": True,
                            "available_tags": [{"name": "Contract"}],
                        }
                    ]
                },
                200,
            )
        )
        internal_api_routes.bot.get_cog.return_value = jobs_cog

        result, status_code = await internal_api_routes._list_job_channels()

        assert status_code == 200
        assert result["channels"][0]["requires_tag"] is True
        jobs_cog.list_registered_job_post_forums.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_discord_diagnostics_delegates_to_diagnostics_cog(
        self,
        internal_api_routes,
    ):
        """The dashboard role catalog must come from the read-only diagnostics cog."""
        diagnostics_cog = Mock()
        diagnostics_cog.get_diagnostics_snapshot = AsyncMock(
            return_value=(
                {
                    "guild": {"id": "123", "name": "508.dev"},
                    "roles": [{"id": "456", "name": "Admin"}],
                },
                200,
            )
        )
        internal_api_routes.bot.get_cog.return_value = diagnostics_cog

        result, status_code = await internal_api_routes._get_discord_diagnostics(
            refresh=True,
        )

        assert status_code == 200
        assert result["roles"][0]["id"] == "456"
        diagnostics_cog.get_diagnostics_snapshot.assert_awaited_once_with(refresh=True)

    @pytest.mark.asyncio
    async def test_discord_diagnostics_reports_unavailable_without_cog(
        self,
        internal_api_routes,
    ):
        """No fallback may fabricate a role catalog when diagnostics is unavailable."""
        internal_api_routes.bot.get_cog.return_value = None

        result, status_code = await internal_api_routes._get_discord_diagnostics()

        assert status_code == 503
        assert result == {"error": "diagnostics_cog_unavailable"}

    @pytest.mark.asyncio
    async def test_agent_schedule_member_snapshot_fetches_current_role_ids(
        self,
        internal_api_routes,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Recurring work must refresh roles instead of trusting a saved grant."""

        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "1000",
        )
        member = SimpleNamespace(
            roles=[
                SimpleNamespace(id=1000, name="@everyone"),
                SimpleNamespace(id=1001, name="Admin"),
            ]
        )
        guild = Mock()
        guild.id = 1000
        guild.fetch_member = AsyncMock(return_value=member)
        internal_api_routes.bot.get_guild.return_value = guild

        result, status_code = await internal_api_routes._agent_schedule_member_snapshot(
            AgentScheduleMemberSnapshotRequest(
                guild_id="1000",
                discord_user_id="2000",
            )
        )

        assert status_code == 200
        assert result["role_ids"] == ["1000", "1001"]
        assert result["roles"] == ["@everyone", "Admin"]
        guild.fetch_member.assert_awaited_once_with(2000)

    @pytest.mark.asyncio
    async def test_agent_schedule_member_snapshot_rejects_cross_guild_request(
        self,
        internal_api_routes,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A bot-internal caller cannot use the role lookup across guilds."""

        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "1000",
        )
        guild = Mock()
        guild.id = 1000
        internal_api_routes.bot.get_guild.return_value = guild

        result, status_code = await internal_api_routes._agent_schedule_member_snapshot(
            AgentScheduleMemberSnapshotRequest(
                guild_id="other-guild",
                discord_user_id="2000",
            )
        )

        assert status_code == 403
        assert result == {"error": "guild_mismatch"}
        guild.fetch_member.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_schedule_channel_validation_requires_a_messageable_target(
        self,
        internal_api_routes,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A schedule is not persisted until the bot can post in its channel."""

        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "1000",
        )

        class FakeMessageable:
            def __init__(self, guild: object) -> None:
                self.guild = guild

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(view_channel=True, send_messages=True)

        guild = SimpleNamespace(id=1000, me=object())
        channel = FakeMessageable(guild)
        internal_api_routes.bot.get_guild.return_value = guild
        internal_api_routes.bot.get_channel.return_value = channel
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.abc.Messageable",
            FakeMessageable,
        )

        (
            result,
            status_code,
        ) = await internal_api_routes._validate_agent_schedule_channel(
            AgentScheduleChannelRequest(guild_id="1000", channel_id="2000")
        )

        assert status_code == 200
        assert result == {
            "status": "ready",
            "guild_id": "1000",
            "channel_id": "2000",
        }

    @pytest.mark.asyncio
    async def test_update_gig_thread_status_rewrites_title_marker(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Dashboard gig status changes should update the Discord thread title."""

        class FakeThread:
            id = 123
            name = "[RECRUITING] Old gig"
            archived = False
            locked = False
            guild = SimpleNamespace(me=object())

            def __init__(self) -> None:
                self.edit = AsyncMock()

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(
                    manage_threads=True,
                    view_channel=True,
                    send_messages_in_threads=True,
                )

        thread = FakeThread()
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.Thread",
            FakeThread,
        )
        internal_api_routes.bot.get_channel.return_value = thread

        result, status_code = await internal_api_routes._update_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="outdated")
        )

        assert status_code == 200
        assert result["status"] == "updated"
        assert result["title"] == "[OUTDATED] Old gig"
        thread.edit.assert_awaited_once_with(
            name="[OUTDATED] Old gig",
            reason="Dashboard gig status update",
        )

    @pytest.mark.asyncio
    async def test_update_gig_thread_status_uses_fallback_for_marker_only_title(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Dashboard status sync should not stack markers for marker-only titles."""

        class FakeThread:
            id = 123
            name = "[RECRUITING]"
            archived = False
            locked = False
            guild = SimpleNamespace(me=object())

            def __init__(self) -> None:
                self.edit = AsyncMock()

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(
                    manage_threads=True,
                    view_channel=True,
                    send_messages_in_threads=True,
                )

        thread = FakeThread()
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.Thread",
            FakeThread,
        )
        internal_api_routes.bot.get_channel.return_value = thread

        result, status_code = await internal_api_routes._update_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="filled")
        )

        assert status_code == 200
        assert result["title"] == "[FILLED] Discord gig 123"
        thread.edit.assert_awaited_once_with(
            name="[FILLED] Discord gig 123",
            reason="Dashboard gig status update",
        )

    @pytest.mark.asyncio
    async def test_update_gig_thread_status_closes_lost_thread(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Dashboard lost status changes should lock and archive the Discord thread."""

        class FakeThread:
            id = 123
            name = "[RECRUITING] Old gig"
            archived = False
            locked = False
            guild = SimpleNamespace(me=object())

            def __init__(self) -> None:
                self.edit = AsyncMock()

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(
                    manage_threads=True,
                    view_channel=True,
                    send_messages_in_threads=True,
                )

        thread = FakeThread()
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.Thread",
            FakeThread,
        )
        internal_api_routes.bot.get_channel.return_value = thread

        result, status_code = await internal_api_routes._update_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="lost")
        )

        assert status_code == 200
        assert result["status"] == "updated"
        assert result["title"] == "[LOST] Old gig"
        assert result["closed"] is True
        assert thread.edit.await_args_list == [
            call(name="[LOST] Old gig", reason="Dashboard gig status update"),
            call(
                locked=True,
                archived=True,
                reason="Dashboard gig status update",
            ),
        ]

    @pytest.mark.asyncio
    async def test_update_gig_thread_status_reopens_non_lost_thread(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Moving a closed lost thread away from lost should make it usable again."""

        class FakeThread:
            id = 123
            name = "[LOST] Old gig"
            archived = True
            locked = True
            guild = SimpleNamespace(me=object())

            def __init__(self) -> None:
                self.edit = AsyncMock()

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(
                    manage_threads=True,
                    view_channel=True,
                    send_messages_in_threads=True,
                )

        thread = FakeThread()
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.Thread",
            FakeThread,
        )
        internal_api_routes.bot.get_channel.return_value = thread

        result, status_code = await internal_api_routes._update_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="recruiting")
        )

        assert status_code == 200
        assert result["status"] == "updated"
        assert result["title"] == "[RECRUITING] Old gig"
        assert result["closed"] is False
        assert thread.edit.await_args_list == [
            call(
                locked=False,
                archived=False,
                reason="Dashboard gig status update",
            ),
            call(name="[RECRUITING] Old gig", reason="Dashboard gig status update"),
        ]

    @pytest.mark.asyncio
    async def test_update_gig_thread_status_preserves_moderator_lock(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Non-lost status sync should not clear locks unrelated to lost closure."""

        class FakeThread:
            id = 123
            name = "[RECRUITING] Old gig"
            archived = False
            locked = True
            guild = SimpleNamespace(me=object())

            def __init__(self) -> None:
                self.edit = AsyncMock()

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(
                    manage_threads=True,
                    view_channel=True,
                    send_messages_in_threads=True,
                )

        thread = FakeThread()
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.Thread",
            FakeThread,
        )
        internal_api_routes.bot.get_channel.return_value = thread

        result, status_code = await internal_api_routes._update_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="filled")
        )

        assert status_code == 200
        assert result["title"] == "[FILLED] Old gig"
        thread.edit.assert_awaited_once_with(
            name="[FILLED] Old gig",
            reason="Dashboard gig status update",
        )

    @pytest.mark.asyncio
    async def test_update_gig_thread_status_reports_missing_manage_threads(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Thread title sync should explain missing Discord permissions."""

        class FakeThread:
            id = 123
            name = "[RECRUITING] Old gig"
            archived = False
            locked = False
            guild = SimpleNamespace(me=object())

            def permissions_for(self, _member: object) -> SimpleNamespace:
                return SimpleNamespace(
                    manage_threads=False,
                    view_channel=True,
                    send_messages_in_threads=True,
                )

        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.discord.Thread",
            FakeThread,
        )
        internal_api_routes.bot.get_channel.return_value = FakeThread()

        result, status_code = await internal_api_routes._update_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="outdated")
        )

        assert status_code == 403
        assert result["error"] == "missing_manage_threads_permission"
        assert result["manage_threads"] is False

    @pytest.mark.asyncio
    async def test_enqueue_gig_thread_status_coalesces_latest_status(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Dashboard thread title sync should run async and keep the latest status."""

        async def immediate_sleep(_seconds: float) -> None:
            return None

        apply_status = AsyncMock()
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.asyncio.sleep",
            immediate_sleep,
        )
        monkeypatch.setattr(
            internal_api_routes,
            "_apply_gig_thread_status_with_retries",
            apply_status,
        )

        result, status_code = await internal_api_routes._enqueue_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="recruiting")
        )
        (
            second_result,
            second_status_code,
        ) = await internal_api_routes._enqueue_gig_thread_status(
            GigThreadStatusRequest(thread_id="123", status="outdated")
        )

        assert status_code == 202
        assert result["status"] == "queued"
        assert second_status_code == 202
        assert second_result["target_status"] == "outdated"

        task = internal_api_routes._gig_thread_status_tasks[123]
        await asyncio.wait_for(task, timeout=1)

        apply_status.assert_awaited_once()
        applied_payload = apply_status.await_args.args[0]
        assert applied_payload.status == "outdated"
        assert 123 not in internal_api_routes._gig_thread_status_tasks

    @pytest.mark.asyncio
    async def test_grant_member_role_returns_forbidden_when_fetch_forbidden(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Discord permission failures during member fetch should stay distinct."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "123",
        )

        guild = Mock()
        guild.id = 123
        member_role = Mock()
        member_role.name = "Member"
        guild.roles = [member_role]
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(
            side_effect=discord.Forbidden(
                response=Mock(status=403, reason="Forbidden"),
                message="forbidden",
            )
        )
        internal_api_routes.bot.get_guild.return_value = guild

        payload = MemberAgreementRoleRequest(
            discord_user_id="456",
            contact_id="contact-1",
        )

        result, status_code = await internal_api_routes._grant_member_role(payload)

        assert status_code == 403
        assert result["error"] == "member_lookup_forbidden"

    @pytest.mark.asyncio
    async def test_grant_member_role_returns_bad_gateway_when_fetch_http_error(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Discord API failures during member fetch should not become 404s."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "123",
        )

        guild = Mock()
        guild.id = 123
        member_role = Mock()
        member_role.name = "Member"
        guild.roles = [member_role]
        guild.get_member.return_value = None
        guild.fetch_member = AsyncMock(
            side_effect=discord.HTTPException(
                response=Mock(status=503, reason="Service Unavailable"),
                message="discord unavailable",
            )
        )
        internal_api_routes.bot.get_guild.return_value = guild

        payload = MemberAgreementRoleRequest(
            discord_user_id="456",
            contact_id="contact-1",
        )

        result, status_code = await internal_api_routes._grant_member_role(payload)

        assert status_code == 502
        assert result["error"] == "member_lookup_failed"

    def test_resolve_target_guild_uses_only_connected_guild_when_unconfigured(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Without a configured guild id, one connected guild is unambiguous."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            None,
        )
        only_guild = Mock()
        internal_api_routes.bot.guilds = [only_guild]

        assert internal_api_routes._resolve_target_guild() is only_guild

    def test_resolve_target_guild_returns_none_when_unconfigured_and_ambiguous(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Without a configured guild id, multiple connected guilds should fail closed."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            None,
        )
        internal_api_routes.bot.guilds = [Mock(), Mock()]

        assert internal_api_routes._resolve_target_guild() is None
