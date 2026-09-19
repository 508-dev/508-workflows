"""Real Postgres tests; set AGENT_TEST_POSTGRES_URL to a disposable database."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import os
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from five08.agent import AgentIdentityContext, AgentOrchestrator, ToolRegistry
from five08.agent.state import PostgresAgentStateStore
from five08.knowledge.store import PostgresKnowledgeStore
from five08.settings import SharedSettings, normalize_sqlalchemy_postgres_url


@pytest.fixture
def database():
    url = os.environ.get("AGENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("AGENT_TEST_POSTGRES_URL must name a disposable test database")
    database_name = "agent_test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )
    try:
        engine = create_engine(
            make_url(normalize_sqlalchemy_postgres_url(url)).set(database=database_name)
        )
        with (
            engine.begin() as conn,
            Operations.context(MigrationContext.configure(conn)),
        ):
            for name in (
                "20260916_0100_create_knowledge_memory",
                "20260917_0100_create_agent_states",
            ):
                importlib.import_module(
                    "five08.worker.migrations.versions." + name
                ).upgrade()
        engine.dispose()
        yield SharedSettings(
            _env_file=None, postgres_url=make_conninfo(url, dbname=database_name)
        )
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )


def actor(user="alice", org="guild"):
    return AgentIdentityContext(
        discord_user_id=user,
        organization_id=org,
        guild_id=org,
        channel_id="channel",
        roles=["Member"],
    )


def make_agent(settings):
    return AgentOrchestrator(
        registry=ToolRegistry(memory_store=PostgresKnowledgeStore(settings)),
        state_store=PostgresAgentStateStore(settings),
    )


def test_fact_survives_rebuild_correction_retains_history_and_org_isolation(database):
    for zone in ("Asia/Tokyo", "Europe/London"):
        agent = make_agent(database)
        response = agent.plan("Remember that my timezone is " + zone, actor())
        assert (
            agent.execute_plan(response.plan, actor(), confirmed=True)[0].status
            == "succeeded"
        )
    agent = make_agent(database)
    facts = (
        agent.plan("What do you remember about me?", actor()).results[0].result["facts"]
    )
    assert len(facts) == 1
    assert facts[0]["value_json"]["text"] == "my timezone is Europe/London"
    assert facts[0]["supersedes_id"]
    assert (
        agent.plan("What do you remember about me?", actor(org="other"))
        .results[0]
        .result["facts"]
        == []
    )
    with psycopg.connect(database.postgres_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_facts WHERE status = 'superseded'"
            ).fetchone()[0]
            == 1
        )
    edit = agent.plan(
        f"Update memory fact {facts[0]['id']} to my timezone is UTC", actor()
    )
    assert (
        agent.execute_plan(edit.plan, actor(), confirmed=True)[0].status == "succeeded"
    )
    current = (
        make_agent(database)
        .plan("What do you remember about me?", actor())
        .results[0]
        .result["facts"][0]
    )
    deletion = agent.plan(f"Forget memory fact {current['id']}", actor())
    assert (
        agent.execute_plan(deletion.plan, actor(), confirmed=True)[0].status
        == "succeeded"
    )
    assert (
        make_agent(database)
        .plan("What do you remember about me?", actor())
        .results[0]
        .result["facts"]
        == []
    )


def test_concurrent_preference_corrections_leave_one_active_revision(database):
    def save(index):
        agent = make_agent(database)
        draft = agent.plan(f"Remember that my timezone is zone-{index}", actor())
        return agent.execute_plan(draft.plan, actor(), confirmed=True)[0].status

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(save, range(8))) == ["succeeded"] * 8
    with psycopg.connect(database.postgres_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_facts WHERE status = 'active'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM memory_facts WHERE status = 'superseded'"
            ).fetchone()[0]
            == 7
        )


def test_shared_confirmation_is_claimed_once_and_wrong_actor_does_not_consume(database):
    response = make_agent(database).plan("Remember that my timezone is UTC", actor())
    store = PostgresAgentStateStore(database)
    assert store.save_plan(response.plan, actor(), maximum=100, per_actor=5)
    assert (
        PostgresAgentStateStore(database).claim_plan(response.plan.plan_id, "bob")[0]
        == "actor_mismatch"
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: PostgresAgentStateStore(database).claim_plan(
                    response.plan.plan_id, "alice"
                )[0],
                range(8),
            )
        )
    assert results.count("claimed") == 1
    assert results.count("not_found") == 7


def test_clarification_survives_rebuild(database):
    assert (
        make_agent(database).plan("Show tasks", actor()).status == "needs_clarification"
    )
    response = make_agent(database).plan("Atlas", actor())
    assert response.status == "executed"
    assert response.plan.actions[0].arguments["project"] == "Atlas"
