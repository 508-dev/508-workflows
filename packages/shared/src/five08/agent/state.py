"""Shared TTL state for clarification turns and frozen confirmations."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from five08.agent.models import AgentIdentityContext, AgentPlan
from five08.queue import get_postgres_connection
from five08.settings import SharedSettings

PendingPlan = tuple[AgentPlan, AgentIdentityContext]


class ClarificationState(BaseModel):
    """The missing field and original request, bound to one actor and location."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    field: Literal["task_project", "task_title"]
    request: str
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=10)
    )


def conversation_keys(context: AgentIdentityContext) -> list[str]:
    location = context.thread_id or context.channel_id
    if not location or not context.organization_id:
        return []
    locations = [location]
    # A response thread's ID is the triggering message ID in Discord.
    if context.message_id and not context.thread_id:
        locations.append(context.message_id)
    return [
        json.dumps([context.organization_id, context.discord_user_id, item])
        for item in dict.fromkeys(locations)
    ]


class AgentStateStore(Protocol):
    def take_clarification(
        self, context: AgentIdentityContext
    ) -> ClarificationState | None: ...
    def save_clarification(
        self, context: AgentIdentityContext, state: ClarificationState
    ) -> None: ...
    def save_plan(
        self,
        plan: AgentPlan,
        context: AgentIdentityContext,
        *,
        maximum: int,
        per_actor: int,
    ) -> bool: ...
    def claim_plan(
        self, plan_id: str, actor_id: str
    ) -> tuple[str, PendingPlan | None]: ...


class InMemoryAgentStateStore:
    """Test adapter with the same single-consumer behavior as Postgres."""

    def __init__(self) -> None:
        self.plans: dict[str, PendingPlan] = {}
        self.clarifications: dict[str, ClarificationState] = {}
        self._lock = threading.RLock()

    def take_clarification(
        self, context: AgentIdentityContext
    ) -> ClarificationState | None:
        with self._lock:
            state = next(
                (
                    self.clarifications[key]
                    for key in conversation_keys(context)
                    if key in self.clarifications
                ),
                None,
            )
            if state is None:
                return None
            self.clarifications = {
                key: value
                for key, value in self.clarifications.items()
                if value.id != state.id
                and value.expires_at > datetime.now(timezone.utc)
            }
            return state if state.expires_at > datetime.now(timezone.utc) else None

    def save_clarification(
        self, context: AgentIdentityContext, state: ClarificationState
    ) -> None:
        with self._lock:
            self.clarifications = {
                key: value
                for key, value in self.clarifications.items()
                if value.expires_at > datetime.now(timezone.utc)
            }
            for key in conversation_keys(context):
                self.clarifications[key] = state

    def save_plan(
        self,
        plan: AgentPlan,
        context: AgentIdentityContext,
        *,
        maximum: int,
        per_actor: int,
    ) -> bool:
        with self._lock:
            now = datetime.now(timezone.utc)
            self.plans = {
                key: value
                for key, value in self.plans.items()
                if value[0].expires_at is None or value[0].expires_at > now
            }
            if (
                len(self.plans) >= maximum
                or sum(
                    value[1].discord_user_id == context.discord_user_id
                    for value in self.plans.values()
                )
                >= per_actor
            ):
                return False
            self.plans[plan.plan_id] = (plan, context)
            return True

    def claim_plan(self, plan_id: str, actor_id: str) -> tuple[str, PendingPlan | None]:
        with self._lock:
            pending = self.plans.get(plan_id)
            if pending is None:
                return "not_found", None
            if pending[1].discord_user_id != actor_id:
                return "actor_mismatch", pending
            del self.plans[plan_id]
            expired = pending[0].expires_at
            return (
                "expired"
                if expired and expired <= datetime.now(timezone.utc)
                else "claimed"
            ), pending


class PostgresAgentStateStore:
    """Durable TTL state shared by API processes; claims are atomic."""

    def __init__(self, settings: SharedSettings) -> None:
        self.settings = settings

    def _connection(self):
        return get_postgres_connection(
            self.settings, connect_timeout_seconds=3, statement_timeout_seconds=3
        )

    def take_clarification(
        self, context: AgentIdentityContext
    ) -> ClarificationState | None:
        keys = conversation_keys(context)
        if not keys:
            return None
        with self._connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (
                    f"agent-conversation:{context.organization_id}:{context.discord_user_id}",
                ),
            )
            cursor.execute(
                "SELECT * FROM agent_states WHERE kind = 'clarification' AND key = ANY(%s) ORDER BY expires_at DESC LIMIT 1 FOR UPDATE",
                (keys,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "DELETE FROM agent_states WHERE kind = 'clarification' AND reference_id = %s",
                (row["reference_id"],),
            )
            return (
                ClarificationState.model_validate(row["payload"])
                if row["expires_at"] > datetime.now(timezone.utc)
                else None
            )

    def save_clarification(
        self, context: AgentIdentityContext, state: ClarificationState
    ) -> None:
        keys = conversation_keys(context)
        if not keys:
            return
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (
                    f"agent-conversation:{context.organization_id}:{context.discord_user_id}",
                ),
            )
            cursor.execute("DELETE FROM agent_states WHERE expires_at <= NOW()")
            for key in keys:
                cursor.execute(
                    """INSERT INTO agent_states (key, kind, actor_id, reference_id, payload, expires_at)
                       VALUES (%s, 'clarification', %s, %s, %s, %s)
                       ON CONFLICT (key) DO UPDATE SET reference_id = EXCLUDED.reference_id,
                           payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at""",
                    (
                        key,
                        context.discord_user_id,
                        state.id,
                        Jsonb(state.model_dump(mode="json")),
                        state.expires_at,
                    ),
                )

    def save_plan(
        self,
        plan: AgentPlan,
        context: AgentIdentityContext,
        *,
        maximum: int,
        per_actor: int,
    ) -> bool:
        with self._connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('agent-plan-capacity', 0))"
            )
            cursor.execute("DELETE FROM agent_states WHERE expires_at <= NOW()")
            cursor.execute(
                "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE actor_id = %s) AS actor_count FROM agent_states WHERE kind = 'plan'",
                (context.discord_user_id,),
            )
            counts = cursor.fetchone()
            if counts["total"] >= maximum or counts["actor_count"] >= per_actor:
                return False
            cursor.execute(
                """INSERT INTO agent_states (key, kind, actor_id, reference_id, payload, expires_at)
                   VALUES (%s, 'plan', %s, %s, %s, %s) ON CONFLICT (key) DO NOTHING""",
                (
                    "plan:" + plan.plan_id,
                    context.discord_user_id,
                    plan.plan_id,
                    Jsonb(
                        {
                            "plan": plan.model_dump(mode="json"),
                            "context": context.model_dump(mode="json"),
                        }
                    ),
                    plan.expires_at
                    or datetime.now(timezone.utc) + timedelta(minutes=10),
                ),
            )
        return True

    def claim_plan(self, plan_id: str, actor_id: str) -> tuple[str, PendingPlan | None]:
        with self._connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM agent_states WHERE kind = 'plan' AND key = %s FOR UPDATE",
                ("plan:" + plan_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return "not_found", None
            pending = (
                AgentPlan.model_validate(row["payload"]["plan"]),
                AgentIdentityContext.model_validate(row["payload"]["context"]),
            )
            if row["actor_id"] != actor_id:
                return "actor_mismatch", pending
            cursor.execute("DELETE FROM agent_states WHERE key = %s", (row["key"],))
            return (
                "expired"
                if row["expires_at"] <= datetime.now(timezone.utc)
                else "claimed"
            ), pending
