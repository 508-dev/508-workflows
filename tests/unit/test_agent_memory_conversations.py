"""Conversation trajectories for private memory and clarification state."""

from datetime import datetime, timedelta, timezone

import pytest

from five08.agent import (
    AgentIdentityContext,
    AgentOrchestrator,
    InMemoryMemoryStore,
    ToolRegistry,
)
from five08.agent.context import PrivateMemoryContextLoader, ContextLoadBounds
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
    agent.execute_plan(draft.plan, context(), confirmed=True)
    loader = PrivateMemoryContextLoader(store)
    assert (
        "Asia/Tokyo"
        in loader.load(context=context(), bounds=ContextLoadBounds())[0].text
    )
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
