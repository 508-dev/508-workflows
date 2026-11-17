"""Focused tests for the permissioned Discord wiki writer cog."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from five08.discord_bot.cogs import wiki_writer as wiki_writer_module
from five08.discord_bot.cogs.wiki_writer import (
    NO_MENTIONS,
    WikiProposalView,
    WikiRevisionModal,
    WikiReviewAcknowledgementButton,
    WikiUpdateDynamicButton,
    WikiWriterCog,
    setup,
)
from five08.tls import default_ca_bundle_path
from five08.wiki_editing.assertions import (
    WIKI_ASSERTION_HEADER,
    verify_wiki_action_assertion,
)
from five08.wiki_editing.models import WikiEditReviewArtifact, WikiSourceReference


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def _role(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _member(*role_names: str, user_id: int = 123) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, roles=[_role(name) for name in role_names])


def _interaction(
    *,
    role_names: tuple[str, ...] = ("Steering Committee",),
    guild_id: int | None = 123,
    user_id: int = 123,
    channel: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=999,
        guild_id=guild_id,
        channel_id=456,
        channel=channel,
        user=_member(*role_names, user_id=user_id),
        response=SimpleNamespace(
            defer=AsyncMock(),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            is_done=Mock(return_value=False),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=None,
        client=SimpleNamespace(get_cog=Mock()),
    )


def _cog_with_member(member: SimpleNamespace) -> tuple[WikiWriterCog, SimpleNamespace]:
    guild = SimpleNamespace(fetch_member=AsyncMock(return_value=member), me=None)
    bot = SimpleNamespace(get_guild=Mock(return_value=guild))
    cog = WikiWriterCog(bot)
    return cog, guild


def _review_payload() -> dict[str, object]:
    review = WikiEditReviewArtifact.from_output(
        proposed_title="Member guide",
        proposed_article="Full proposed article with @everyone preserved in the private file.",
        complete_diff="@@ -1 +1 @@\n-Old guide\n+Full proposed article with @everyone.",
        source_refs=[
            WikiSourceReference(
                source_type="outline_document",
                source_ref="doc-1",
                title="Existing member guide",
                source_url="https://outline.example/doc/member-guide",
            )
        ],
    )
    return review.model_dump(mode="json")


def test_failed_wiki_draft_shows_revision_controls() -> None:
    assert wiki_writer_module._controls_for_response({"status": "failed"}) == (
        "revise",
        "cancel",
        "refresh",
    )


def test_publishing_wiki_draft_only_shows_refresh() -> None:
    assert wiki_writer_module._controls_for_response({"status": "publishing"}) == (
        "refresh",
    )


def test_unacknowledged_proposal_only_shows_ack_after_a_complete_packet() -> None:
    assert wiki_writer_module._controls_for_response(
        {"status": "proposed", "review": _review_payload()}
    ) == ("ack", "revise", "cancel", "refresh")
    assert wiki_writer_module._controls_for_response({"status": "proposed"}) == (
        "revise",
        "cancel",
        "refresh",
    )


@pytest.mark.asyncio
async def test_wiki_inputs_honor_the_configured_instruction_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wiki_writer_module.settings,
        "wiki_editing_max_instruction_characters",
        100,
    )
    cog, _guild = _cog_with_member(_member("Steering Committee"))
    interaction = _interaction()
    cog._create_wiki_update = AsyncMock()

    await cog.wiki_update.callback(cog, interaction, "x" * 101)

    cog._create_wiki_update.assert_not_awaited()
    assert (
        interaction.response.send_message.await_args.args[0]
        == "Wiki update instructions must be 100 characters or fewer."
    )

    view = WikiProposalView(
        cog=cog,
        requester_id=123,
        proposal_id="11111111-1111-1111-1111-111111111111",
        guild_id="123",
    )
    modal = WikiRevisionModal(view=view)
    assert modal.instruction.max_length == 100

    revision_interaction = _interaction()
    cog._post_proposal_action = AsyncMock()
    await view.submit_revision(revision_interaction, "y" * 101)

    cog._post_proposal_action.assert_not_awaited()
    assert (
        revision_interaction.response.send_message.await_args.args[0]
        == "Revision instructions must be 100 characters or fewer."
    )


@pytest.fixture(autouse=True)
def configure_wiki_guild(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiki_writer_module.settings, "discord_server_id", "123")


@pytest.mark.asyncio
async def test_wiki_update_posts_typed_payload_and_sanitizes_private_response() -> None:
    cog, guild = _cog_with_member(_member("Steering Committee"))
    interaction = _interaction()
    proposal_id = "11111111-1111-1111-1111-111111111111"

    async def fetch_member_after_acknowledgement(_user_id: int) -> SimpleNamespace:
        assert interaction.response.defer.await_count == 1
        return _member("Steering Committee")

    guild.fetch_member.side_effect = fetch_member_after_acknowledgement
    cog._create_wiki_update = AsyncMock(
        return_value={
            "proposal_id": proposal_id,
            "status": "proposed",
            "audience": "shared_coop_wiki",
            "message": "Review @everyone **carefully**",
            "title": "<b>Member guide</b>",
            "target_document_id": "doc-1",
            "summary": "Add **clearer** guidance.",
            "review": _review_payload(),
            "review_acknowledged": False,
            "source_count": 0,
            "revision": 1,
        }
    )
    cog._audit_wiki_response = Mock()

    await cog.wiki_update.callback(
        cog,
        interaction,
        "  Clarify  the invoice process.  ",
        " doc-1 ",
        False,
    )

    cog._create_wiki_update.assert_awaited_once()
    payload = cog._create_wiki_update.await_args.args[0]
    assert payload["instruction"] == "Clarify the invoice process."
    assert payload["target_document_id"] == "doc-1"
    assert payload["selected_conversation"] == []
    assert payload["context"]["discord_user_id"] == "123"
    assert payload["context"]["guild_id"] == "123"
    assert payload["context"]["roles"] == ["Steering Committee"]
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)

    sent = interaction.followup.send.await_args
    assert sent.kwargs["ephemeral"] is True
    assert sent.kwargs["allowed_mentions"] is NO_MENTIONS
    assert "@everyone" not in sent.args[0]
    assert "@here" not in sent.args[0]
    assert "**" not in sent.args[0]
    assert "Audience: shared co-op wiki" in sent.args[0]
    assert "Full proposed article" not in sent.args[0]
    assert "Proposed diff:" not in sent.args[0]
    review_file = sent.kwargs["file"]
    packet = review_file.fp.getvalue().decode("utf-8")
    assert "Full proposed article with @everyone" in packet
    assert "@@ -1 +1 @@" in packet
    assert "https://outline.example/doc/member-guide" in packet
    view = sent.kwargs["view"]
    assert isinstance(view, WikiProposalView)
    assert {item.item.label for item in view.children} == {
        "Acknowledge review",
        "Revise",
        "Cancel",
        "Refresh",
    }
    assert all(proposal_id in item.item.custom_id for item in view.children)
    generic_ids = [
        item.item.custom_id
        for item in view.children
        if isinstance(item, WikiUpdateDynamicButton)
    ]
    assert all(custom_id.endswith(":123") for custom_id in generic_ids)


@pytest.mark.asyncio
async def test_wiki_update_rejects_unconfigured_guild_before_backend_request() -> None:
    cog, _guild = _cog_with_member(_member("Steering Committee"))
    interaction = _interaction(guild_id=456)
    cog._create_wiki_update = AsyncMock()

    await cog.wiki_update.callback(cog, interaction, "Clarify invoices")

    cog._create_wiki_update.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert (
        "configured co-op server"
        in interaction.response.send_message.await_args.args[0]
    )
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_wiki_update_requires_steering_committee_role() -> None:
    cog, _guild = _cog_with_member(_member("Steering Committee"))
    interaction = _interaction(role_names=())
    cog._create_wiki_update = AsyncMock()

    await cog.wiki_update.callback(cog, interaction, "Clarify invoices")

    cog._create_wiki_update.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert "Steering Committee" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_wiki_update_rechecks_revoked_role_after_acknowledgement() -> None:
    cog, _guild = _cog_with_member(_member())
    interaction = _interaction()
    acknowledgement_state = {"done": False}

    async def defer(*_args: object, **_kwargs: object) -> None:
        acknowledgement_state["done"] = True

    interaction.response.defer = AsyncMock(side_effect=defer)
    interaction.response.is_done = Mock(
        side_effect=lambda: acknowledgement_state["done"]
    )
    cog._create_wiki_update = AsyncMock()

    await cog.wiki_update.callback(cog, interaction, "Clarify invoices")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    cog._create_wiki_update.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    assert (
        "no longer have the Steering Committee role"
        in (interaction.followup.send.await_args.args[0])
    )


@pytest.mark.asyncio
async def test_dynamic_publish_rehydrates_encoded_owner_and_fresh_roles() -> None:
    cog, guild = _cog_with_member(_member("Admin"))
    cog._post_proposal_action = AsyncMock(
        return_value={
            "proposal_id": "11111111-1111-1111-1111-111111111111",
            "status": "published",
            "message": "Published.",
            "source_count": 1,
        }
    )
    cog._audit_wiki_response = Mock()
    cog._send_wiki_response = AsyncMock()
    interaction = _interaction(role_names=(), channel=None)

    async def fetch_member_after_acknowledgement(_user_id: int) -> SimpleNamespace:
        assert interaction.response.defer.await_count == 1
        return _member("Admin")

    guild.fetch_member.side_effect = fetch_member_after_acknowledgement
    interaction.client = SimpleNamespace(get_cog=Mock(return_value=cog))
    button = WikiUpdateDynamicButton(
        action="publish",
        proposal_id="11111111-1111-1111-1111-111111111111",
        guild_id="123",
        requester_id=123,
    )

    await button.callback(interaction)

    guild.fetch_member.assert_awaited_once_with(123)
    cog._post_proposal_action.assert_awaited_once()
    call = cog._post_proposal_action.await_args.kwargs
    assert call["action"] == "publish"
    assert call["context"]["roles"] == ["Admin"]
    assert call["context"]["guild_id"] == "123"
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)


@pytest.mark.asyncio
async def test_dynamic_review_acknowledgement_binds_the_rendered_packet() -> None:
    cog, guild = _cog_with_member(_member("Admin"))
    review = _review_payload()
    review_id = str(review["review_id"])
    cog._post_proposal_action = AsyncMock(
        return_value={
            "proposal_id": "11111111-1111-1111-1111-111111111111",
            "status": "proposed",
            "action": "publish",
            "review_acknowledged": True,
            "message": "Review acknowledged.",
            "source_count": 1,
        }
    )
    cog._audit_wiki_response = Mock()
    cog._send_wiki_response = AsyncMock()
    interaction = _interaction(role_names=(), channel=None)
    guild.fetch_member.return_value = _member("Admin")
    interaction.client = SimpleNamespace(get_cog=Mock(return_value=cog))
    button = WikiReviewAcknowledgementButton(
        proposal_id="11111111-1111-1111-1111-111111111111",
        requester_id=123,
        review_id=review_id,
    )

    await button.callback(interaction)

    call = cog._post_proposal_action.await_args.kwargs
    assert call["action"] == "ack"
    assert call["review_id"] == review_id
    assert call["context"]["roles"] == ["Admin"]
    assert button.item.custom_id.endswith(f":{review_id}")
    assert len(button.item.custom_id) <= 100


@pytest.mark.asyncio
async def test_dynamic_control_rejects_different_encoded_requester() -> None:
    cog, guild = _cog_with_member(_member("Steering Committee"))
    cog._post_proposal_action = AsyncMock()
    interaction = _interaction(user_id=123)
    interaction.client = SimpleNamespace(get_cog=Mock(return_value=cog))
    button = WikiUpdateDynamicButton(
        action="cancel",
        proposal_id="11111111-1111-1111-1111-111111111111",
        guild_id="123",
        requester_id=456,
    )

    await button.callback(interaction)

    guild.fetch_member.assert_not_awaited()
    cog._post_proposal_action.assert_not_awaited()
    assert "Only the requester" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_dynamic_item_rehydration_preserves_encoded_requester() -> None:
    interaction = _interaction()
    button = WikiUpdateDynamicButton(
        action="refresh",
        proposal_id="11111111-1111-1111-1111-111111111111",
        guild_id="123",
        requester_id=456,
    )
    match = wiki_writer_module._WIKI_UPDATE_COMPONENT_RE.fullmatch(
        button.item.custom_id
    )
    assert match is not None

    restored = await WikiUpdateDynamicButton.from_custom_id(
        interaction,
        button.item,
        match,
    )

    assert restored.action == "refresh"
    assert restored.proposal_id == "11111111-1111-1111-1111-111111111111"
    assert restored.guild_id == "123"
    assert restored.requester_id == 456


@pytest.mark.asyncio
async def test_revision_modal_posts_revision_endpoint_with_fresh_context() -> None:
    cog, guild = _cog_with_member(_member("Steering Committee"))
    cog._post_proposal_action = AsyncMock(
        return_value={
            "proposal_id": "11111111-1111-1111-1111-111111111111",
            "status": "authoring",
            "message": "Revision queued.",
            "source_count": 0,
        }
    )
    cog._audit_wiki_response = Mock()
    cog._send_wiki_response = AsyncMock()
    interaction = _interaction()
    view = WikiProposalView(
        cog=cog,
        requester_id=123,
        proposal_id="11111111-1111-1111-1111-111111111111",
        guild_id="123",
    )

    await view.submit_revision(interaction, "  Explain  the policy more clearly. ")

    guild.fetch_member.assert_awaited_once_with(123)
    call = cog._post_proposal_action.await_args.kwargs
    assert call["action"] == "revise"
    assert call["instruction"] == "Explain the policy more clearly."
    assert call["context"]["roles"] == ["Steering Committee"]


@pytest.mark.asyncio
async def test_selected_thread_context_is_bounded_org_visible_and_never_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeThread:
        def __init__(self, *, private: bool, parent_org_visible: bool = True) -> None:
            self._private = private
            self.id = 456
            self.name = "Invoice discussion"
            self.jump_url = "https://discord.com/channels/123/456"
            default_role = SimpleNamespace(id=0)
            self.guild = SimpleNamespace(
                id=123,
                me=SimpleNamespace(id=999),
                default_role=default_role,
            )
            self.parent = SimpleNamespace(
                permissions_for=lambda actor: SimpleNamespace(
                    view_channel=parent_org_visible or actor is not default_role,
                    read_message_history=parent_org_visible
                    or actor is not default_role,
                )
            )
            self.history_called = False

        def is_private(self) -> bool:
            return self._private

        def permissions_for(self, _actor: object) -> SimpleNamespace:
            return SimpleNamespace(view_channel=True, read_message_history=True)

        async def history(self, *, limit: int, oldest_first: bool):
            self.history_called = True
            message_indexes = (
                range(limit) if oldest_first else range(24, 24 - limit, -1)
            )
            for index in message_indexes:
                yield SimpleNamespace(
                    id=index,
                    author=SimpleNamespace(id=index + 100),
                    content="x" * 2_000,
                )

    monkeypatch.setattr(wiki_writer_module.discord, "Thread", FakeThread)
    cog, _guild = _cog_with_member(_member("Steering Committee"))
    public_thread = FakeThread(private=False)
    interaction = _interaction(channel=public_thread)

    sources = await cog._collect_current_thread(
        interaction,
        member=_member("Steering Committee"),
        guild_id="123",
    )

    assert len(sources) == 1
    source = sources[0]
    assert source["visibility"] == "org"
    assert source["provenance"]["source_type"] == "discord_thread"
    assert source["provenance"]["message_ids"] == [
        str(index) for index in range(19, 25)
    ]
    assert len(source["organization_visible_text"]) <= 12_000
    assert public_thread.history_called is True

    private_thread = FakeThread(private=True)
    private_interaction = _interaction(channel=private_thread)
    private_sources = await cog._collect_current_thread(
        private_interaction,
        member=_member("Steering Committee"),
        guild_id="123",
    )
    assert private_sources == []
    assert private_thread.history_called is False

    unresolved_parent_thread = FakeThread(private=False)
    unresolved_parent_thread.parent = SimpleNamespace(
        permissions_for=Mock(side_effect=discord.ClientException("parent missing"))
    )
    unresolved_parent_interaction = _interaction(channel=unresolved_parent_thread)
    unresolved_parent_sources = await cog._collect_current_thread(
        unresolved_parent_interaction,
        member=_member("Steering Committee"),
        guild_id="123",
    )
    assert unresolved_parent_sources == []
    assert unresolved_parent_thread.history_called is False

    restricted_parent_thread = FakeThread(private=False, parent_org_visible=False)
    restricted_parent_interaction = _interaction(channel=restricted_parent_thread)
    restricted_sources = await cog._collect_current_thread(
        restricted_parent_interaction,
        member=_member("Steering Committee"),
        guild_id="123",
    )
    assert restricted_sources == []
    assert restricted_parent_thread.history_called is False


@pytest.mark.asyncio
async def test_selected_thread_context_respects_configured_source_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeThread:
        id = 456
        name = "Current decision"
        jump_url = "https://discord.com/channels/123/456"

        def __init__(self) -> None:
            default_role = SimpleNamespace(id=0)
            self.guild = SimpleNamespace(
                id=123,
                me=SimpleNamespace(id=999),
                default_role=default_role,
            )
            self.parent = SimpleNamespace(
                permissions_for=lambda _actor: SimpleNamespace(
                    view_channel=True,
                    read_message_history=True,
                )
            )

        def is_private(self) -> bool:
            return False

        def permissions_for(self, _actor: object) -> SimpleNamespace:
            return SimpleNamespace(view_channel=True, read_message_history=True)

        async def history(self, *, limit: int, oldest_first: bool):
            assert limit == wiki_writer_module.WIKI_THREAD_MESSAGE_LIMIT
            assert oldest_first is False
            yield SimpleNamespace(
                id=24,
                author=SimpleNamespace(id=124),
                content="x" * 2_000,
            )

    monkeypatch.setattr(wiki_writer_module.discord, "Thread", FakeThread)
    monkeypatch.setattr(
        wiki_writer_module.settings,
        "knowledge_capture_max_characters",
        1_000,
    )
    cog, _guild = _cog_with_member(_member("Steering Committee"))

    sources = await cog._collect_current_thread(
        _interaction(channel=FakeThread()),
        member=_member("Steering Committee"),
        guild_id="123",
    )

    assert len(sources) == 1
    source = sources[0]
    assert source["provenance"]["message_ids"] == ["24"]
    assert len(source["organization_visible_text"]) == 1_000


@pytest.mark.asyncio
async def test_selected_thread_context_uses_newest_messages_in_chronological_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeThread:
        id = 456
        name = "Current decision"
        jump_url = "https://discord.com/channels/123/456"

        def __init__(self) -> None:
            default_role = SimpleNamespace(id=0)
            self.guild = SimpleNamespace(
                id=123,
                me=SimpleNamespace(id=999),
                default_role=default_role,
            )
            self.parent = SimpleNamespace(
                permissions_for=lambda _actor: SimpleNamespace(
                    view_channel=True,
                    read_message_history=True,
                )
            )

        def is_private(self) -> bool:
            return False

        def permissions_for(self, _actor: object) -> SimpleNamespace:
            return SimpleNamespace(view_channel=True, read_message_history=True)

        async def history(self, *, limit: int, oldest_first: bool):
            assert limit == wiki_writer_module.WIKI_THREAD_MESSAGE_LIMIT
            assert oldest_first is False
            # Discord yields its requested newest batch newest-first.
            for index in range(24, 4, -1):
                yield SimpleNamespace(
                    id=index,
                    author=SimpleNamespace(id=index + 100),
                    content=f"message {index}",
                )

    monkeypatch.setattr(wiki_writer_module.discord, "Thread", FakeThread)
    cog, _guild = _cog_with_member(_member("Steering Committee"))
    sources = await cog._collect_current_thread(
        _interaction(channel=FakeThread()),
        member=_member("Steering Committee"),
        guild_id="123",
    )

    assert len(sources) == 1
    source = sources[0]
    assert source["provenance"]["message_ids"] == [str(index) for index in range(5, 25)]
    assert source["organization_visible_text"].splitlines() == [
        f"{index + 100}: message {index}" for index in range(5, 25)
    ]


@pytest.mark.asyncio
async def test_proposal_action_posts_expected_endpoint_payloads() -> None:
    cog = WikiWriterCog.__new__(WikiWriterCog)
    cog._post_backend_json = Mock(return_value={"status": "proposed"})
    context = {"discord_user_id": "123"}
    proposal_id = "11111111-1111-1111-1111-111111111111"

    await cog._post_proposal_action(
        proposal_id=proposal_id,
        action="publish",
        context=context,
    )
    assert cog._post_backend_json.call_args.args == (
        f"/wiki/updates/{proposal_id}/publish",
        {"context": context},
    )
    await cog._post_proposal_action(
        proposal_id=proposal_id,
        action="ack",
        context=context,
        review_id="0123456789abcdef",
    )
    assert cog._post_backend_json.call_args.args == (
        f"/wiki/updates/{proposal_id}/acknowledge-review",
        {"context": context, "review_id": "0123456789abcdef"},
    )
    await cog._post_proposal_action(
        proposal_id=proposal_id,
        action="revise",
        context=context,
        instruction="Clarify scope",
    )
    assert cog._post_backend_json.call_args.args == (
        f"/wiki/updates/{proposal_id}/revise",
        {"instruction": "Clarify scope", "context": context},
    )
    await cog._post_proposal_action(
        proposal_id=proposal_id,
        action="cancel",
        context=context,
    )
    assert cog._post_backend_json.call_args.args == (
        f"/wiki/updates/{proposal_id}/cancel",
        {"context": context},
    )
    await cog._post_proposal_action(
        proposal_id=proposal_id,
        action="refresh",
        context=context,
    )
    assert cog._post_backend_json.call_args.args == (
        f"/wiki/updates/{proposal_id}/status",
        {"context": context},
    )


def test_backend_post_uses_authenticated_tls_verified_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cog = WikiWriterCog.__new__(WikiWriterCog)
    monkeypatch.setattr(
        wiki_writer_module,
        "settings",
        SimpleNamespace(
            backend_api_base_url="https://api.test",
            api_shared_secret="secret",
            wiki_editing_assertion_secret="wiki-assertion-secret",
            wiki_editing_request_timeout_seconds=45.0,
        ),
    )

    with patch("five08.discord_bot.cogs.wiki_writer.requests.post") as mock_post:
        mock_post.return_value = _FakeResponse(
            202,
            {"status": "queued", "message": "Accepted"},
        )
        response = cog._post_backend_json("/wiki/updates", {"instruction": "x"})

    assert response["http_status"] == 202
    assert mock_post.call_args.args[0] == "https://api.test/wiki/updates"
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-API-Secret"] == "secret"
    verify_wiki_action_assertion(
        headers[WIKI_ASSERTION_HEADER],
        "wiki-assertion-secret",
        method="POST",
        path="/wiki/updates",
        payload={"instruction": "x"},
    )
    assert mock_post.call_args.kwargs["timeout"] == 45.0
    assert mock_post.call_args.kwargs["verify"] == default_ca_bundle_path()
    assert mock_post.call_args.kwargs["allow_redirects"] is False


def test_backend_post_rejects_redirects_before_parsing_response() -> None:
    cog = WikiWriterCog.__new__(WikiWriterCog)

    with patch.object(
        wiki_writer_module,
        "settings",
        SimpleNamespace(
            backend_api_base_url="https://api.test",
            api_shared_secret="secret",
            wiki_editing_assertion_secret="wiki-assertion-secret",
            wiki_editing_request_timeout_seconds=45.0,
        ),
    ):
        with patch("five08.discord_bot.cogs.wiki_writer.requests.post") as mock_post:
            mock_post.return_value = _FakeResponse(307, {"detail": "redirect"})

            with pytest.raises(RuntimeError, match="redirect status=307"):
                cog._post_backend_json("/wiki/updates", {"instruction": "x"})

    assert mock_post.call_args.kwargs["allow_redirects"] is False


def test_wiki_response_surfaces_structured_backend_failures() -> None:
    message = WikiWriterCog._format_wiki_response(
        {"error": "forbidden", "http_status": 403}
    )

    assert message.splitlines()[0] == "Wiki update failed (HTTP 403): forbidden."


def test_audit_metadata_excludes_instruction_summary_and_raw_source_text() -> None:
    cog = WikiWriterCog.__new__(WikiWriterCog)
    cog._audit_command_safe = Mock()
    interaction = _interaction()

    cog._audit_wiki_response(
        interaction=interaction,
        action="wiki.update.request",
        proposal_id="11111111-1111-1111-1111-111111111111",
        response={
            "proposal_id": "11111111-1111-1111-1111-111111111111",
            "target_document_id": "doc-1",
            "status": "proposed",
            "operation_status": "pending",
            "source_count": 2,
            "revision": 3,
            "summary": "private source text",
            "diff": "+ private source text",
            "review": _review_payload(),
            "message": "private source text",
        },
    )

    metadata = cog._audit_command_safe.call_args.kwargs["metadata"]
    assert metadata == {
        "proposal_id": "11111111-1111-1111-1111-111111111111",
        "target_document_id": "doc-1",
        "status": "proposed",
        "operation_status": "pending",
        "source_count": 2,
        "revision": 3,
    }
    assert "private source text" not in str(metadata)
    assert "Full proposed article" not in str(metadata)


@pytest.mark.asyncio
async def test_setup_registers_restart_safe_dynamic_wiki_controls() -> None:
    bot = SimpleNamespace(add_dynamic_items=Mock(), add_cog=AsyncMock())

    await setup(bot)

    bot.add_dynamic_items.assert_called_once_with(
        WikiUpdateDynamicButton,
        WikiReviewAcknowledgementButton,
    )
    added_cog = bot.add_cog.await_args.args[0]
    assert isinstance(added_cog, WikiWriterCog)
