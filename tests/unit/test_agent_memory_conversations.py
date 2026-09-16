"""Conversation trajectories for private memory and clarification state."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from five08.agent import (
    AgentContextSnippet,
    AgentIdentityContext,
    AgentOrchestrator,
    InMemoryMemoryStore,
    ToolRegistry,
)
from five08.agent.context import (
    ContextLoadBounds,
    PrivateMemoryContextLoader,
    context_sources_for_snippets,
)
from five08.agent.state import InMemoryAgentStateStore
from five08.knowledge.store import InMemoryKnowledgeStore


def context(user="alice", channel="100"):
    return AgentIdentityContext(
        discord_user_id=user,
        organization_id="guild",
        guild_id="guild",
        channel_id=channel,
        roles=["Member"],
    )


@pytest.mark.parametrize("store_type", [InMemoryMemoryStore, InMemoryKnowledgeStore])
def test_remember_correct_recall_forget_trajectory(store_type):
    store = store_type()
    agent = AgentOrchestrator(registry=ToolRegistry(memory_store=store))
    actor = context()
    for zone in ("Asia/Tokyo", "Europe/London"):
        draft = agent.plan(f"Remember that my timezone is {zone}", actor)
        assert draft.status == "requires_confirmation"
        assert (
            agent.execute_plan(draft.plan, actor, confirmed=True)[0].status
            == "succeeded"
        )
    response = agent.plan("Do you remember my timezone?", actor)
    assert response.status == "executed"
    facts = response.results[0].result["facts"]
    assert len(facts) == 1
    assert facts[0]["value_json"]["text"] == "my timezone is Europe/London"
    assert facts[0]["supersedes_id"]
    edit = agent.plan(
        f"Update memory fact {facts[0]['id']} to my timezone is UTC", actor
    )
    assert edit.status == "requires_confirmation"
    assert agent.execute_plan(edit.plan, actor, confirmed=True)[0].status == "succeeded"
    fact = (
        agent.plan("What do you remember about me?", actor)
        .results[0]
        .result["facts"][0]
    )
    deletion = agent.plan(f"Forget memory fact {fact['id']}", actor)
    assert deletion.status == "requires_confirmation"
    assert (
        agent.execute_plan(deletion.plan, actor, confirmed=True)[0].status
        == "succeeded"
    )
    assert (
        agent.plan("What do you remember about me?", actor).results[0].result["facts"]
        == []
    )


def test_followup_uses_persisted_missing_field_after_orchestrator_rebuild():
    state = InMemoryAgentStateStore()
    actor = context()
    actor.message_id = "500"
    first = AgentOrchestrator(state_store=state).plan("Show tasks", actor)
    assert first.clarification_question == "Which project should I search?"
    # Discord creates a response thread whose ID equals the original message ID.
    followup = actor.model_copy(
        update={"channel_id": "500", "thread_id": "500", "message_id": "501"}
    )
    result = AgentOrchestrator(state_store=state).plan("Atlas", followup)
    assert result.status == "executed"
    assert result.plan.actions[0].arguments["project"] == "Atlas"
    assert state.take_clarification(actor) is None


def test_other_actor_or_expired_state_cannot_complete_previous_request():
    state = InMemoryAgentStateStore()
    agent = AgentOrchestrator(state_store=state)
    agent.plan("Show tasks", context())
    assert agent.plan("Atlas", context("bob")).plan is None
    for key, value in state.clarifications.items():
        state.clarifications[key] = value.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
    assert agent.plan("Atlas", context()).plan is None


def test_new_command_does_not_fill_pending_project():
    agent = AgentOrchestrator()
    agent.plan("Show tasks", context())
    result = agent.plan("Remember that my timezone is UTC", context())
    assert result.plan.actions[0].tool_name == "memory_write.remember_fact"


def test_task_title_followup_preserves_the_original_project():
    agent = AgentOrchestrator()
    first = agent.plan("Create a task in project Atlas", context())
    assert first.clarification_field == "task_title"
    response = agent.plan("Refresh the docs", context())
    assert response.status == "requires_confirmation"
    assert response.plan.actions[0].arguments["project"] == "Atlas"
    assert response.plan.actions[0].arguments["title"] == "Refresh the docs"


@pytest.mark.parametrize(
    "title",
    ["Update Alice's profile", "Fix API: retry errors"],
)
def test_task_title_followup_accepts_normal_punctuation(title):
    agent = AgentOrchestrator()
    agent.plan("Create a task in project Atlas", context())

    response = agent.plan(title, context())

    assert response.status == "requires_confirmation"
    assert response.plan.actions[0].arguments["title"] == title


def test_invalid_clarification_reply_keeps_state_for_retry():
    state = InMemoryAgentStateStore()
    agent = AgentOrchestrator(state_store=state)
    agent.plan("Show tasks", context())

    agent.plan("Which project?", context())
    response = agent.plan("Atlas", context())

    assert response.status == "executed"
    assert response.plan.actions[0].arguments["project"] == "Atlas"


def test_planner_failure_keeps_clarification_state_for_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    state = InMemoryAgentStateStore()
    agent = AgentOrchestrator(state_store=state)
    agent.plan("Show tasks", context())
    original_plan = agent._plan
    monkeypatch.setattr(agent, "_plan", Mock(side_effect=RuntimeError("outage")))

    with pytest.raises(RuntimeError, match="outage"):
        agent.plan("Atlas", context())

    monkeypatch.setattr(agent, "_plan", original_plan)
    assert agent.plan("Atlas", context()).status == "executed"


def test_polite_remember_request_is_a_private_write():
    response = AgentOrchestrator().plan(
        "Could you remember that my timezone is Asia/Tokyo?",
        context(),
    )

    assert response.status == "requires_confirmation"
    assert response.plan.actions[0].tool_name == "memory_write.remember_fact"


def test_multiline_memory_edit_is_parsed():
    response = AgentOrchestrator().plan(
        "Update memory fact abcdefgh to first line\nsecond line",
        context(),
    )

    assert response.status == "requires_confirmation"
    assert response.plan.actions[0].arguments["replaces_id"] == "abcdefgh"
    assert response.plan.actions[0].arguments["value_json"]["text"] == (
        "first line\nsecond line"
    )


def test_expired_clarification_state_can_be_purged_without_another_write():
    state = InMemoryAgentStateStore()
    agent = AgentOrchestrator(state_store=state)
    agent.plan("Show tasks", context())
    for key, value in state.clarifications.items():
        state.clarifications[key] = value.model_copy(
            update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )

    assert state.purge_expired() == 1
    assert state.clarifications == {}


def test_personal_preference_suggestions_never_save_without_confirmation():
    agent = AgentOrchestrator(memory_suggestions_enabled=True)
    response = agent.plan("My timezone is Asia/Tokyo", context())
    assert response.status == "requires_confirmation"
    assert "privately" in response.message
    assert (
        agent.plan("What do you remember about me?", context())
        .results[0]
        .result["facts"]
        == []
    )
    assert (
        agent.execute_plan(response.plan, context(), confirmed=True)[0].status
        == "succeeded"
    )
    assert (
        len(
            agent.plan("What do you remember about me?", context())
            .results[0]
            .result["facts"]
        )
        == 1
    )


@pytest.mark.parametrize("store_type", [InMemoryMemoryStore, InMemoryKnowledgeStore])
def test_admin_cannot_read_edit_or_forget_another_users_private_fact(store_type):
    store = store_type()
    registry = ToolRegistry(memory_store=store)
    agent = AgentOrchestrator(registry=registry)
    draft = agent.plan("Remember that my timezone is UTC", context())
    fact = agent.execute_plan(draft.plan, context(), confirmed=True)[0].result["fact"]
    admin = context("bob").model_copy(update={"roles": ["Admin"]})
    edit = agent.plan(f"Update memory fact {fact['id']} to altered", admin)
    assert agent.execute_plan(edit.plan, admin, confirmed=True)[0].status == "denied"
    forget = agent.plan(f"Forget memory fact {fact['id']}", admin)
    assert agent.execute_plan(forget.plan, admin, confirmed=True)[0].status == "denied"
    with pytest.raises(PermissionError):
        registry.execute(
            "memory_read.get_user_facts",
            {"user_id": "alice"},
            actor_id="bob",
            actor_scopes={"memory:admin"},
            organization_id="guild",
        )


def test_saved_preferences_are_context_only_for_owner_and_private_destination():
    store = InMemoryKnowledgeStore()
    agent = AgentOrchestrator(registry=ToolRegistry(memory_store=store))
    draft = agent.plan("Remember that my timezone is Asia/Tokyo", context())
    fact = agent.execute_plan(draft.plan, context(), confirmed=True)[0].result["fact"]
    loader = PrivateMemoryContextLoader(store)
    snippets = loader.load(context=context(), bounds=ContextLoadBounds())
    assert "Asia/Tokyo" in snippets[0].text
    assert snippets[0].backend_loaded is True
    assert snippets[0].trusted is False
    source = context_sources_for_snippets(
        context=context(),
        snippets=snippets,
    )
    assert source[0].source_type == "memory_fact"
    assert source[0].source_ref == fact["id"]
    assert loader.load(context=context("bob"), bounds=ContextLoadBounds()) == []
    assert (
        loader.load(
            context=context().model_copy(
                update={"response_destination_visibility": "public"}
            ),
            bounds=ContextLoadBounds(),
        )
        == []
    )


def test_memory_lookup_outage_does_not_block_unrelated_agent_workflows() -> None:
    store = Mock()
    store.list_facts.side_effect = RuntimeError("memory unavailable")
    actor = context()
    actor.context_snippets = [
        AgentContextSnippet(
            source_type="discord_message",
            source_ref="current-thread",
            label="Current conversation",
            text="The current task is Atlas.",
            token_count=6,
        )
    ]
    agent = AgentOrchestrator(context_loader=PrivateMemoryContextLoader(store))

    response = agent.plan("Show tasks for project Atlas", actor)

    assert response.status == "executed"
    assert response.plan is not None
    assert response.plan.context_sources[0].source_type == "request"


def test_current_conversation_precedes_saved_memory_in_bounded_context() -> None:
    store = InMemoryMemoryStore()
    store.remember_fact(
        scope_type="user",
        scope_id="alice",
        key="long_note",
        value_json={"text": "memory " * 1000},
        visibility="private",
        source_type="request",
        source_ref="agent_request",
        source_excerpt=None,
        created_by="alice",
        verification_status="user_confirmed",
        organization_id="guild",
    )
    actor = context()
    actor.context_snippets = [
        AgentContextSnippet(
            source_type="discord_message",
            source_ref="current-thread",
            label="Current conversation",
            text="Use the current conversation.",
            token_count=6,
        )
    ]

    snippets = PrivateMemoryContextLoader(store).load(
        context=actor,
        bounds=ContextLoadBounds(max_messages=1, max_tokens=6),
    )

    assert [snippet.source_ref for snippet in snippets] == ["current-thread"]
