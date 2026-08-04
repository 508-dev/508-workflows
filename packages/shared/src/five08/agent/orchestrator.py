"""Agent gateway orchestration for English task requests."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Protocol, cast
from uuid import uuid4

from five08.agent.models import (
    AgentExecutionResult,
    AgentIdentityContext,
    AgentModelSelection,
    AgentPlan,
    AgentResponse,
    AgentToolAction,
    MemoryVisibility,
    ModelTier,
)
from five08.agent.context import (
    AgentContextLoader,
    ContextLoadBounds,
    RequestContextLoader,
    context_sources_for_snippets,
)
from five08.agent.model_routing import AgentModelConfig
from five08.agent.planner import AgentPlanner, AgentPlannerResult
from five08.agent.policy import PolicyEngine
from five08.agent.tools import ToolPartialSuccessError, ToolRegistry
from five08.clients.migadu import normalize_migadu_mailbox_domain

_TASK_ID_RE = re.compile(r"\bTASK-\d+\b", re.IGNORECASE)
_DATE_ISO_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_CONTACT_ID_REFERENCE_RE = re.compile(
    r"\b(?:crm\s+)?contact\s+([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b",
    re.IGNORECASE,
)
_CONTACT_QUERY_PREFIX_RE = re.compile(r"^(?:crm\s+)?contact\s+", re.IGNORECASE)
_MONTH_DATE_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(\d{1,2})(?:,\s*|\s+)(20\d{2})\b",
    re.IGNORECASE,
)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
LiteralPlanner = Literal["deterministic_regex", "live_model"]


class AgentIntentNormalizer(Protocol):
    """Optional fallback that rewrites loose phrasing into supported commands."""

    def normalize(self, message: str) -> str | None:
        """Return a supported command-shaped message, or None when uncertain."""


class AgentOrchestrator:
    """Convert English into frozen, policy-checked plans and execute them."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        model_config: AgentModelConfig | None = None,
        planner: AgentPlanner | None = None,
        intent_normalizer: AgentIntentNormalizer | None = None,
        context_loader: AgentContextLoader | None = None,
        context_bounds: ContextLoadBounds | None = None,
        today: date | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self._explicit_policy = policy
        self.model_config = model_config or AgentModelConfig()
        self.planner = planner
        self.intent_normalizer = intent_normalizer
        self.context_loader = context_loader or RequestContextLoader()
        self.context_bounds = context_bounds or ContextLoadBounds()
        self.today = today

    @property
    def policy(self) -> PolicyEngine:
        """Use live GitHub repository configuration unless a test overrides it."""

        return self._explicit_policy or PolicyEngine.from_runtime_config(
            self.registry.runtime_config
        )

    def plan(self, message: str, context: AgentIdentityContext) -> AgentResponse:
        text = message.strip()
        loaded_context = self.context_loader.load(
            context=context,
            bounds=self.context_bounds,
        )
        context = context.model_copy(update={"context_snippets": loaded_context})
        if not text:
            return AgentResponse(
                status="needs_clarification",
                message="I need a task request to work from.",
                clarification_question="What task or project action should I take?",
            )

        resolved_member_agreement = self._plan_member_agreement_from_crm(
            text,
            context,
            planner="deterministic_regex",
        )
        if resolved_member_agreement is not None:
            return resolved_member_agreement

        planner: LiteralPlanner = "deterministic_regex"
        planning_text = text
        action = self._parse_action(text)
        if action is not None:
            return self._response_for_deterministic_action(
                action=action,
                context=context,
                planning_text=planning_text,
                planner=planner,
            )

        planned_response = self._plan_with_model(text, context)
        if planned_response is not None:
            return planned_response

        if action is None and not re.search(
            r"\bcreate\s+(?:a\s+)?task\b", text, re.IGNORECASE
        ):
            normalized_text = self._normalize_intent(text)
            if normalized_text:
                normalized_action = self._parse_action(normalized_text)
                if normalized_action is not None:
                    action = normalized_action
                    planning_text = normalized_text
                    planner = "live_model"
        if action is None:
            if re.search(r"\bcreate\s+(?:a\s+)?task\b", text, re.IGNORECASE):
                return AgentResponse(
                    status="needs_clarification",
                    message="I need a task title before I can create it.",
                    clarification_question="What should the task be?",
                )
            return AgentResponse(
                status="needs_clarification",
                message="I could not map that to a supported workflow.",
                clarification_question=(
                    "Try asking me to manage a task, GitHub issue, CRM contact, or member account."
                ),
            )
        return self._response_for_deterministic_action(
            action=action,
            context=context,
            planning_text=planning_text,
            planner=planner,
        )

    def _response_for_deterministic_action(
        self,
        *,
        action: AgentToolAction,
        context: AgentIdentityContext,
        planning_text: str,
        planner: LiteralPlanner,
    ) -> AgentResponse:
        """Plan a known workflow before asking a model to infer an intent."""

        if action.tool_name == "task_read.search_tasks" and not action.arguments.get(
            "project"
        ):
            return AgentResponse(
                status="needs_clarification",
                message="Task search requires a project filter.",
                clarification_question="Which project should I search?",
            )

        return self._response_for_action(
            action=action,
            context=context,
            planning_text=planning_text,
            planner=planner,
        )

    def _plan_with_model(
        self,
        text: str,
        context: AgentIdentityContext,
    ) -> AgentResponse | None:
        """Use the structured planner when configured, preserving safe fallback."""

        if self.planner is None:
            return None
        model_tier = self._choose_model_tier_for_request(text)
        try:
            result = self.planner.plan(
                message=text,
                context=context,
                runtime_config=self.registry.runtime_config,
                model_tier=model_tier,
            )
        except Exception:
            # Provider errors fall through to deterministic parsing. Do not expose
            # provider internals in a Discord response.
            return None
        if result is None:
            return None
        return self._response_for_planner_result(
            result=result,
            context=context,
            planning_text=text,
        )

    def _response_for_planner_result(
        self,
        *,
        result: AgentPlannerResult,
        context: AgentIdentityContext,
        planning_text: str,
    ) -> AgentResponse:
        draft = result.draft
        if draft.status == "needs_clarification":
            question = draft.clarification_question or "What should I do next?"
            return AgentResponse(
                status="needs_clarification",
                message=question,
                clarification_question=question,
            )

        actions = [
            AgentToolAction(
                tool_name=draft_action.tool_name,
                arguments=draft_action.arguments,
                summary=draft_action.summary,
            )
            for draft_action in draft.actions
        ]
        for action in actions:
            try:
                self.registry.validate_planner_action(
                    action.tool_name,
                    action.arguments,
                )
            except ValueError:
                return AgentResponse(
                    status="needs_clarification",
                    message="I need a clearer request before I can safely continue.",
                    clarification_question=(
                        "What exact task, issue, contact, or account action should I run?"
                    ),
                )
            clarification = self._planner_action_clarification(action, context=context)
            if clarification is not None:
                return AgentResponse(
                    status="needs_clarification",
                    message=clarification,
                    clarification_question=clarification,
                )
        return self._response_for_actions(
            actions=actions,
            context=context,
            planning_text=planning_text,
            planner="live_model",
            model=result.model,
        )

    def _response_for_action(
        self,
        *,
        action: AgentToolAction,
        context: AgentIdentityContext,
        planning_text: str,
        planner: LiteralPlanner,
    ) -> AgentResponse:
        """Authorize, freeze, and optionally execute a single planned action."""

        return self._response_for_actions(
            actions=[action],
            context=context,
            planning_text=planning_text,
            planner=planner,
        )

    def _response_for_actions(
        self,
        *,
        actions: list[AgentToolAction],
        context: AgentIdentityContext,
        planning_text: str,
        planner: LiteralPlanner,
        model: AgentModelSelection | None = None,
    ) -> AgentResponse:
        """Authorize and execute a complete, schema-validated action proposal."""

        if not actions:
            return AgentResponse(
                status="needs_clarification",
                message="What should I do next?",
                clarification_question="What should I do next?",
            )

        for action in actions:
            manifest = self.registry.get(action.tool_name)
            if manifest is None:
                return AgentResponse(
                    status="needs_clarification",
                    message="I could not map that to an available workflow.",
                    clarification_question=(
                        "What task, issue, contact, or account workflow should I run?"
                    ),
                )
            action.arguments = self.registry.normalize_action_arguments(
                action.tool_name,
                action.arguments,
            )
            action.risk = manifest.risk
            action.requires_confirmation = manifest.requires_confirmation
            action.required_scopes = self.policy.required_scopes_for_action(
                manifest=manifest,
                action=action,
            )

        for action in actions:
            manifest = self.registry.get(action.tool_name)
            decision = self.policy.authorize(
                context=context,
                manifest=manifest,
                action=action,
            )
            if not decision.allowed:
                action.requires_confirmation = False
                plan = self._build_plan(
                    context=context,
                    intent=self._intent_for_tool(action.tool_name),
                    actions=actions,
                    model_tier=(
                        model.tier
                        if model is not None
                        else self._choose_model_tier(planning_text, actions)
                    ),
                    requires_confirmation=False,
                    planner=planner,
                    model=model,
                )
                return AgentResponse(
                    status="denied", plan=plan, message=decision.reason
                )
            action.requires_confirmation = decision.requires_confirmation

        requires_confirmation = any(action.requires_confirmation for action in actions)
        plan = self._build_plan(
            context=context,
            intent=self._intent_for_tool(actions[0].tool_name),
            actions=actions,
            model_tier=(
                model.tier
                if model is not None
                else self._choose_model_tier(planning_text, actions)
            ),
            requires_confirmation=requires_confirmation,
            planner=planner,
            model=model,
        )
        if plan.requires_confirmation:
            return AgentResponse(
                status="requires_confirmation",
                plan=plan,
                message="This action needs confirmation before execution.",
            )

        results = self.execute_plan(plan, context)
        if all(result.status == "succeeded" for result in results):
            return AgentResponse(
                status="executed",
                plan=plan,
                results=results,
                message=self._execution_message(results),
            )
        return AgentResponse(
            status="failed",
            plan=plan,
            results=results,
            message=self._execution_message(results),
        )

    def _plan_member_agreement_from_crm(
        self,
        text: str,
        context: AgentIdentityContext,
        *,
        planner: LiteralPlanner,
    ) -> AgentResponse | None:
        """Resolve a named member agreement recipient through CRM before writing."""
        recipient_name = self._extract_member_agreement_recipient(text)
        if recipient_name is None:
            return None

        search_action = AgentToolAction(
            tool_name="crm_read.search_contacts",
            arguments={"query": recipient_name, "limit": 5},
            summary=f"Search CRM contacts matching: {recipient_name}",
        )
        manifest = self.registry.get(search_action.tool_name)
        decision = self.policy.authorize(
            context=context,
            manifest=manifest,
            action=search_action,
        )
        if not decision.allowed:
            return AgentResponse(status="denied", message=decision.reason)

        try:
            payload = self.registry.execute(
                search_action.tool_name,
                search_action.arguments,
                organization_id=context.organization_id,
                actor_id=context.discord_user_id,
                actor_scopes=self.policy.scopes_for_context(context),
            )
        except Exception:
            question = "What email address should I use for the member agreement?"
            return AgentResponse(
                status="needs_clarification",
                message=question,
                clarification_question=question,
            )

        contacts = payload.get("contacts") if isinstance(payload, dict) else None
        if not isinstance(contacts, list) or not contacts:
            question = (
                f"I could not find a CRM contact for {recipient_name}. "
                "What email address should I use for the member agreement?"
            )
            return AgentResponse(
                status="needs_clarification",
                message=question,
                clarification_question=question,
            )

        usable_contacts = [
            contact
            for contact in contacts
            if isinstance(contact, dict)
            and isinstance(contact.get("emailAddress"), str)
            and contact["emailAddress"].strip()
        ]
        if len(usable_contacts) > 1:
            candidate_list = _format_contact_candidates(usable_contacts)
            question = (
                f"I found multiple CRM contacts for {recipient_name}: "
                f"{candidate_list}. Which one should I use?"
            )
            return AgentResponse(
                status="needs_clarification",
                message=question,
                clarification_question=question,
            )
        if len(usable_contacts) != 1:
            question = (
                f"I found CRM contacts for {recipient_name}, but none had a usable "
                "email address. What email address should I use for the member agreement?"
            )
            return AgentResponse(
                status="needs_clarification",
                message=question,
                clarification_question=question,
            )

        contact = usable_contacts[0]
        submitter_email = str(contact["emailAddress"]).strip()
        submitter_name = _clean_text(str(contact.get("name") or recipient_name))
        action = AgentToolAction(
            tool_name="docuseal_write.create_member_agreement_submission",
            arguments={
                "submitter_email": submitter_email,
                "submitter_name": submitter_name,
                "send_email": True,
            },
            summary=f"Create DocuSeal member agreement submission for {submitter_name}",
        )
        return self._response_for_action(
            action=action,
            context=context,
            planning_text=text,
            planner=planner,
        )

    @staticmethod
    def _extract_member_agreement_recipient(text: str) -> str | None:
        lowered = text.casefold()
        if re.search(r"\bcreate\s+(?:a\s+)?task\b", text, re.IGNORECASE):
            return None
        if "member agreement" not in lowered or not re.search(
            r"\b(?:send|create|submit)\b",
            text,
            re.IGNORECASE,
        ):
            return None
        if re.search(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            text,
            re.IGNORECASE,
        ):
            return None
        match = re.search(
            r"\b(?:to|for)\s+([A-Za-z][A-Za-z .'-]{0,80})\s*$",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        recipient = _clean_text(match.group(1))
        return recipient or None

    def execute_plan(
        self,
        plan: AgentPlan,
        context: AgentIdentityContext,
        *,
        confirmed: bool = False,
        effective_scopes: set[str] | None = None,
    ) -> list[AgentExecutionResult]:
        results: list[AgentExecutionResult] = []
        if plan.requires_confirmation and not confirmed:
            return [
                AgentExecutionResult(
                    tool_name=action.tool_name,
                    status="denied",
                    error="Plan requires confirmation before execution",
                )
                for action in plan.actions
            ]

        organization_id = context.organization_id
        actor_id = context.discord_user_id
        actor_scopes = effective_scopes or self.policy.scopes_for_context(context)
        for action in plan.actions:
            manifest = self.registry.get(action.tool_name)
            decision = self.policy.authorize_with_scopes(
                context=context,
                manifest=manifest,
                action=action,
                effective_scopes=actor_scopes,
            )
            if not decision.allowed:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="denied",
                        error=decision.reason,
                    )
                )
                continue
            try:
                payload = self.registry.execute(
                    action.tool_name,
                    action.arguments,
                    organization_id=organization_id,
                    actor_id=actor_id,
                    project_id=context.project_id,
                    actor_scopes=actor_scopes,
                )
            except PermissionError as exc:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="denied",
                        error=str(exc),
                    )
                )
            except KeyError as exc:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="failed",
                        error=str(exc.args[0]) if exc.args else str(exc),
                    )
                )
            except ToolPartialSuccessError as exc:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="failed",
                        result=exc.result,
                        error=str(exc),
                    )
                )
            except Exception as exc:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="failed",
                        error=str(exc),
                    )
                )
            else:
                if action.tool_name.startswith("memory_read."):
                    payload = self._filter_memory_payload_for_destination(
                        payload,
                        context,
                    )
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="succeeded",
                        result=payload,
                    )
                )
        return results

    def _filter_memory_payload_for_destination(
        self,
        payload: dict[str, object],
        context: AgentIdentityContext,
    ) -> dict[str, object]:
        facts = payload.get("facts")
        if not isinstance(facts, list):
            return payload
        visible_facts = [
            fact
            for fact in facts
            if isinstance(fact, dict)
            and self.policy.can_echo_memory_to_destination(
                context=context,
                visibility=cast(
                    MemoryVisibility,
                    str(fact.get("visibility") or "private"),
                ),
            )
        ]
        return {**payload, "facts": visible_facts}

    def _normalize_intent(self, text: str) -> str | None:
        if self.intent_normalizer is None:
            return None
        try:
            normalized = self.intent_normalizer.normalize(text)
        except Exception:
            return None
        normalized = _clean_text(normalized or "")
        if not normalized or normalized.casefold() == text.casefold():
            return None
        return normalized

    def _build_plan(
        self,
        *,
        context: AgentIdentityContext,
        intent: str,
        actions: list[AgentToolAction],
        model_tier: ModelTier,
        requires_confirmation: bool,
        planner: LiteralPlanner = "deterministic_regex",
        model: AgentModelSelection | None = None,
    ) -> AgentPlan:
        return AgentPlan(
            plan_id=str(uuid4()),
            operation_id=context.operation_id,
            intent=intent,
            planner=planner,
            model_tier=model_tier,
            model=model or self._resolve_model(model_tier),
            actions=actions,
            human_summary=self._human_summary(actions),
            requires_confirmation=requires_confirmation,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            context_sources=context_sources_for_snippets(
                context=context,
                snippets=context.context_snippets,
            ),
        )

    def _resolve_model(self, model_tier: ModelTier) -> AgentModelSelection:
        return self.model_config.resolve(model_tier)

    def _parse_action(self, text: str) -> AgentToolAction | None:
        lowered = text.casefold()
        if re.search(r"\bcreate\s+(?:a\s+)?task\b", text, re.IGNORECASE):
            return self._parse_create_task(text)
        if (
            "github project" in lowered
            or "gh project" in lowered
            or "project board" in lowered
        ):
            action = self._parse_github_project(text)
            if action is not None:
                return action
        if (
            "todo" in lowered
            or "github issue" in lowered
            or "gh issue" in lowered
            or ("issue" in lowered and "repo" in lowered)
        ):
            action = self._parse_github_issue(text)
            if action is not None:
                return action
        if re.search(r"\b(?:list|show)\s+(?:github|gh)\s+repositories\b", text, re.I):
            return AgentToolAction(
                tool_name="github_repository.list_repositories",
                arguments={},
                summary="List repositories selected for the GitHub App",
            )
        if any(
            keyword in lowered
            for keyword in ["find task", "search task", "list task", "show task"]
        ):
            return self._parse_search_task(text)
        memory_action = self._parse_memory_action(text)
        if memory_action is not None:
            return memory_action
        if self._is_crm_contact_update_request(lowered):
            action = self._parse_crm_contact_update(text)
            if action is not None:
                return action
        if any(
            keyword in lowered
            for keyword in [
                "search crm",
                "find contact",
                "lookup contact",
                "find member",
            ]
        ):
            return self._parse_crm_contact_search(text)
        if "member agreement" in lowered and any(
            keyword in lowered for keyword in ["send", "create", "submit"]
        ):
            return self._parse_member_agreement(text)
        if "sso" in lowered and any(
            keyword in lowered for keyword in ["create", "link", "provision"]
        ):
            return self._parse_sso_user_create(text)
        if self._is_user_accounts_create_request(lowered):
            return self._parse_user_accounts_create(text)
        if "outline" in lowered and any(
            keyword in lowered for keyword in ["invite", "add"]
        ):
            return self._parse_outline_invite(text)
        if "mailbox" in lowered and any(
            keyword in lowered for keyword in ["create", "provision"]
        ):
            return self._parse_mailbox_create(text)
        if any(
            keyword in lowered
            for keyword in ["update task", "change task", "assign task", "close task"]
        ):
            return self._parse_update_task(text)
        task_id = _TASK_ID_RE.search(text)
        if task_id and any(
            keyword in lowered
            for keyword in ["assign", "due", "status", "close", "complete", "completed"]
            + ["mark", "marked", "rename", "title"]
        ):
            return self._parse_update_task(text)
        return None

    def _parse_memory_action(self, text: str) -> AgentToolAction | None:
        lowered = text.casefold()
        if re.search(r"\bwhat\s+do\s+you\s+remember\s+about\s+me\b", lowered):
            return AgentToolAction(
                tool_name="memory_read.get_user_facts",
                arguments={},
                summary="List durable facts remembered about the requester",
            )
        forget_match = re.search(
            r"\bforget\s+(?:memory\s+)?(?:fact\s+)?([A-Za-z0-9_-]{8,})\b",
            text,
            re.IGNORECASE,
        )
        if forget_match is not None:
            return AgentToolAction(
                tool_name="memory_write.forget_fact",
                arguments={"fact_id": forget_match.group(1)},
                summary=f"Forget memory fact {forget_match.group(1)}",
            )
        remember_match = re.search(
            r"\bremember\s+(?:that\s+)?(.+?)(?:\s+for\s+me)?$",
            text,
            re.IGNORECASE,
        )
        if remember_match is not None:
            fact_text = _clean_text(remember_match.group(1))
            if fact_text:
                key = self._memory_key_from_fact_text(fact_text)
                return AgentToolAction(
                    tool_name="memory_write.remember_fact",
                    arguments={
                        "scope_type": "user",
                        "key": key,
                        "value_json": {"text": fact_text},
                        "visibility": "private",
                        "source_type": "request",
                        "source_ref": "agent_request",
                        "source_excerpt": fact_text,
                    },
                    summary=f"Remember private user fact: {key}",
                )
        return None

    @staticmethod
    def _memory_key_from_fact_text(text: str) -> str:
        match = re.search(
            r"\b(?:my\s+)?([A-Za-z][A-Za-z0-9 _-]{1,40})\s+is\s+",
            text,
        )
        if match is None:
            return "note"
        key = re.sub(r"\s+", "_", match.group(1).strip().lower())
        return key[:64] or "note"

    def _parse_search_task(self, text: str) -> AgentToolAction:
        project = self._extract_project(text)
        query = self._extract_search_query(text)
        return AgentToolAction(
            tool_name="task_read.search_tasks",
            arguments={"query": query, "project": project},
            summary=f"Search tasks matching: {query}",
        )

    def _parse_github_issue(self, text: str) -> AgentToolAction | None:
        lowered = text.casefold()
        repository = self._extract_repository(text)
        issue_number = self._extract_github_issue_number(text)
        if issue_number is not None and re.search(
            r"\b(?:comment|reply)\b",
            text,
            re.IGNORECASE,
        ):
            body = self._extract_github_comment_body(text)
            if body is None:
                return None
            args = {
                "repository": repository,
                "issue_number": issue_number,
                "body": body,
            }
            return AgentToolAction(
                tool_name="github_issue.comment_on_issue",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"Comment on GitHub issue #{issue_number}",
            )

        if issue_number is not None and re.search(
            r"\b(?:close|complete|reopen|open|update|edit|rename|mark)\b",
            text,
            re.IGNORECASE,
        ):
            state: str | None = None
            state_reason: str | None = None
            if re.search(
                r"\b(?:close|complete|mark(?:ed)?\s+as\s+(?:done|complete))\b",
                text,
                re.I,
            ):
                state = "closed"
                state_reason = "completed"
            elif re.search(r"\b(?:reopen|open)\b", text, re.I):
                state = "open"
            title = self._extract_github_issue_update_title(text)
            body = self._extract_github_issue_body_update(text)
            args: dict[str, object] = {
                "repository": repository,
                "issue_number": issue_number,
                "title": title,
                "state": state,
                "state_reason": state_reason,
            }
            if body is not None:
                args["body"] = body
            if not any(
                value is not None
                for key, value in args.items()
                if key not in {"repository", "issue_number"}
            ):
                return None
            return AgentToolAction(
                tool_name="github_issue.update_issue",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"Update GitHub issue #{issue_number}",
            )

        if issue_number is not None and re.search(
            r"\b(?:show|view|get|display)\b",
            text,
            re.IGNORECASE,
        ):
            args = {"repository": repository, "issue_number": issue_number}
            return AgentToolAction(
                tool_name="github_issue.get_issue",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"Show GitHub issue #{issue_number}",
            )

        if re.search(
            r"\b(?:create|open)\s+(?:a\s+)?(?:(?:github|gh)\s+)?(?:issue|todo)\b",
            text,
            re.IGNORECASE,
        ):
            title = self._extract_github_issue_title(text)
            if not title:
                return None
            body = self._extract_body(text)
            args = {"title": title, "repository": repository, "body": body}
            return AgentToolAction(
                tool_name="github_issue.create_issue",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f'Create GitHub issue: "{title}"',
            )

        state_list_match = re.search(
            r"\b(?:search|find|list|show)\s+(open|closed|all)\s+"
            r"(?:(?:github|gh)\s+)?(?:issues?|todos?)\b",
            text,
            re.IGNORECASE,
        )
        if state_list_match is not None:
            args = {
                "query": "",
                "repository": repository,
                "state": state_list_match.group(1).casefold(),
            }
            return AgentToolAction(
                tool_name="github_issue.search_issues",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"List {state_list_match.group(1).casefold()} GitHub issues",
            )

        if any(keyword in lowered for keyword in ["search", "find", "list", "show"]):
            query = self._extract_github_issue_query(text)
            args = {"query": query, "repository": repository, "state": "open"}
            return AgentToolAction(
                tool_name="github_issue.search_issues",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"Search GitHub issues matching: {query}",
            )
        return None

    def _parse_github_project(self, text: str) -> AgentToolAction | None:
        lowered = text.casefold()
        project_number = self._extract_github_project_number(text)
        if project_number is not None and re.search(
            r"\badd\s+(?:github\s+)?(?:issue|todo)\s*#?\d+\s+to\b",
            text,
            re.IGNORECASE,
        ):
            issue_number = self._extract_github_issue_number(text)
            if issue_number is None:
                return None
            repository = self._extract_repository(text)
            args = {
                "project_number": project_number,
                "issue_number": issue_number,
                "repository": repository,
            }
            return AgentToolAction(
                tool_name="github_project.add_issue_to_project",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"Add GitHub issue #{issue_number} to project #{project_number}",
            )
        if project_number is not None:
            item_number = self._extract_github_project_item_number(text)
            field_match = re.search(
                r"\b(?:set|update)\s+(?:github\s+)?project(?:\s+board)?\s*#?\d+"
                r"\s+item\s*#?\d+\s+field\s*#?(\d+)\s+(?:to|as)\s+(.+)$",
                text,
                re.IGNORECASE,
            )
            if item_number is not None and field_match is not None:
                value = _clean_text(field_match.group(2))
                if value is not None:
                    return AgentToolAction(
                        tool_name="github_project.update_project_item",
                        arguments={
                            "project_number": project_number,
                            "item_id": item_number,
                            "fields": [
                                {
                                    "id": int(field_match.group(1)),
                                    "value": value,
                                }
                            ],
                        },
                        summary=(
                            f"Update field #{field_match.group(1)} on GitHub project "
                            f"#{project_number} item #{item_number}"
                        ),
                    )
        if (
            project_number is not None
            and "field" in lowered
            and any(keyword in lowered for keyword in ["show", "list", "view"])
        ):
            return AgentToolAction(
                tool_name="github_project.list_project_fields",
                arguments={"project_number": project_number},
                summary=f"List fields for GitHub project #{project_number}",
            )
        if (
            project_number is not None
            and "item" in lowered
            and any(keyword in lowered for keyword in ["show", "list", "view"])
        ):
            return AgentToolAction(
                tool_name="github_project.list_project_items",
                arguments={"project_number": project_number},
                summary=f"List items in GitHub project #{project_number}",
            )
        if project_number is not None and any(
            keyword in lowered for keyword in ["show", "view", "get"]
        ):
            return AgentToolAction(
                tool_name="github_project.get_project",
                arguments={"project_number": project_number},
                summary=f"Show GitHub project #{project_number}",
            )
        if any(keyword in lowered for keyword in ["list", "show", "view"]):
            return AgentToolAction(
                tool_name="github_project.list_projects",
                arguments={},
                summary="List GitHub Projects",
            )
        return None

    def _parse_crm_contact_search(self, text: str) -> AgentToolAction | None:
        match = re.search(
            r"\b(?:search crm(?: contacts)?|find contact|lookup contact|find member)"
            r"\s+(?:for\s+)?(.+)",
            text,
            re.IGNORECASE,
        )
        query = _clean_text(match.group(1)) if match else None
        if not query:
            return None
        return AgentToolAction(
            tool_name="crm_read.search_contacts",
            arguments={"query": query, "limit": 5},
            summary=f"Search CRM contacts matching: {query}",
        )

    def _is_crm_contact_update_request(self, lowered: str) -> bool:
        has_update_intent = bool(
            re.search(r"\b(?:update|change|set|mark|approve|reject)\b", lowered)
        )
        has_contact_target = "contact" in lowered or "crm" in lowered
        return has_update_intent and has_contact_target

    def _parse_crm_contact_update(self, text: str) -> AgentToolAction | None:
        contact_id_match = re.search(
            r"\b(?:crm\s+)?contact\s+([A-Za-z0-9_-]{2,})\b",
            text,
            re.IGNORECASE,
        )
        if contact_id_match is None:
            return None
        contact_id = contact_id_match.group(1)
        updates: dict[str, object] = {}

        onboarding_match = re.search(
            r"\bonboarding(?:\s+state)?\s+(?:to|as)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if onboarding_match:
            value = _clean_text(onboarding_match.group(1))
            if value:
                updates["cOnboardingState"] = value
        elif re.search(r"\bapprove\b", text, re.IGNORECASE):
            updates["cOnboardingState"] = "approved"
        elif re.search(r"\breject\b", text, re.IGNORECASE):
            updates["cOnboardingState"] = "rejected"

        if not updates:
            return None
        return AgentToolAction(
            tool_name="crm_write.update_contact",
            arguments={"contact_id": contact_id, "updates": updates},
            summary=f"Update CRM contact {contact_id}",
        )

    def _parse_member_agreement(self, text: str) -> AgentToolAction | None:
        email_match = re.search(
            r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        if email_match is None:
            return None
        before_email = re.sub(
            r"\s+(?:at|with\s+(?:email|e-mail)|email)\s*$",
            "",
            text[: email_match.start()],
            flags=re.IGNORECASE,
        )
        name_match = re.search(
            r"\b(?:to|for)\s+([A-Za-z][A-Za-z .'-]{0,80})\s*$",
            before_email,
            re.IGNORECASE,
        )
        submitter_name = _clean_text(name_match.group(1)) if name_match else None
        return AgentToolAction(
            tool_name="docuseal_write.create_member_agreement_submission",
            arguments={
                "submitter_email": email_match.group(1),
                "submitter_name": submitter_name,
                "send_email": True,
            },
            summary=(
                "Create DocuSeal member agreement submission for "
                f"{submitter_name or email_match.group(1)}"
            ),
        )

    def _parse_mailbox_create(self, text: str) -> AgentToolAction | None:
        mailbox_match = re.search(
            r"\bmailbox\s+(?:for\s+)?([A-Za-z0-9._%+-]+)"
            r"(?:@([A-Za-z0-9.-]+\.[A-Za-z]{2,}))?(?=\s|$)",
            text,
            re.IGNORECASE,
        )
        if mailbox_match is None:
            return None

        supplied_domain = mailbox_match.group(2)
        configured_domain = normalize_migadu_mailbox_domain(
            self.registry.runtime_config.migadu_mailbox_domain
        ).casefold()
        if supplied_domain and supplied_domain.casefold() != configured_domain:
            return None

        local_part = mailbox_match.group(1).strip().lower()
        name_match = re.search(
            r"\b(?:named|name)\s+([A-Za-z][A-Za-z .'-]{0,80})",
            text,
            re.IGNORECASE,
        )
        if name_match is None:
            name_match = re.search(
                r"\bfor\s+([A-Za-z][A-Za-z .'-]{0,80})",
                text[mailbox_match.end() :],
                re.IGNORECASE,
            )
        if name_match is None:
            return None
        name = _clean_text(
            re.split(
                r"\s+\b(?:with\s+)?(?:backup|recovery|forward(?:ing)?|forward)\b",
                name_match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
        )
        backup_email = self._extract_mailbox_backup_email(text)
        if not local_part or not name or not backup_email:
            return None
        return AgentToolAction(
            tool_name="mail_write.create_mailbox",
            arguments={
                "local_part": local_part,
                "backup_email": backup_email,
                "name": name,
            },
            summary=f"Create mailbox {local_part} for {name}",
        )

    @staticmethod
    def _is_user_accounts_create_request(lowered: str) -> bool:
        has_create = any(keyword in lowered for keyword in ["create", "provision"])
        has_account_target = any(
            keyword in lowered
            for keyword in [
                "508 account",
                "508 accounts",
                "user account",
                "user accounts",
                "account bundle",
            ]
        )
        return has_create and has_account_target

    def _parse_user_accounts_create(self, text: str) -> AgentToolAction | None:
        mailbox_username = self._extract_mailbox_username(text)
        contact_arguments = self._extract_contact_reference(text)
        if mailbox_username is None or contact_arguments is None:
            return None
        arguments = dict(contact_arguments)
        arguments["mailbox_username"] = mailbox_username
        contact_label = contact_arguments.get("contact_id") or contact_arguments.get(
            "contact_query"
        )
        return AgentToolAction(
            tool_name="account_write.create_user_accounts",
            arguments=arguments,
            summary=f"Create 508 accounts for {contact_label}",
        )

    def _parse_sso_user_create(self, text: str) -> AgentToolAction | None:
        contact_arguments = self._extract_contact_reference(text)
        if contact_arguments is None:
            return None
        contact_label = contact_arguments.get("contact_id") or contact_arguments.get(
            "contact_query"
        )
        return AgentToolAction(
            tool_name="sso_write.create_user",
            arguments=contact_arguments,
            summary=f"Create or link SSO user for {contact_label}",
        )

    def _parse_outline_invite(self, text: str) -> AgentToolAction | None:
        email_match = re.search(
            r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        if email_match is not None:
            email = email_match.group(1)
            return AgentToolAction(
                tool_name="outline_write.invite_user",
                arguments={"email": email},
                summary=f"Invite {email} to Outline",
            )

        contact_arguments = self._extract_outline_contact_reference(text)
        if contact_arguments is None:
            return None
        contact_label = contact_arguments.get("contact_id") or contact_arguments.get(
            "contact_query"
        )
        return AgentToolAction(
            tool_name="outline_write.invite_user",
            arguments=contact_arguments,
            summary=f"Invite {contact_label} to Outline",
        )

    @staticmethod
    def _extract_mailbox_username(text: str) -> str | None:
        match = re.search(
            r"\b(?:mailbox\s+username|mailbox|508\s+(?:email|address)|email|username)"
            r"\s+(?:for\s+)?([A-Za-z0-9._%+-]+"
            r"(?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?)\b",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        return match.group(1).strip()

    @staticmethod
    def _extract_contact_reference(text: str) -> dict[str, str] | None:
        contact_id_match = _CONTACT_ID_REFERENCE_RE.search(text)
        if contact_id_match is not None:
            return {"contact_id": contact_id_match.group(1)}

        query_match = re.search(
            r"\bfor\s+(.+?)(?=\s+(?:with|using|and)\s+"
            r"(?:mailbox|508\s+(?:email|address)|email|username)\b|$)",
            text,
            re.IGNORECASE,
        )
        if query_match is None:
            return None
        query = _clean_text(
            _CONTACT_QUERY_PREFIX_RE.sub("", query_match.group(1), count=1)
        )
        if not query:
            return None
        return {"contact_query": query}

    @classmethod
    def _extract_outline_contact_reference(cls, text: str) -> dict[str, str] | None:
        contact_id_match = _CONTACT_ID_REFERENCE_RE.search(text)
        if contact_id_match is not None:
            return {"contact_id": contact_id_match.group(1)}

        invite_match = re.search(
            r"\b(?:invite|add)\s+(.+?)\s+to\s+outline\b",
            text,
            re.IGNORECASE,
        )
        if invite_match is not None:
            query = _clean_text(
                _CONTACT_QUERY_PREFIX_RE.sub("", invite_match.group(1), count=1)
            )
            if query:
                return {"contact_query": query}
        return cls._extract_contact_reference(text)

    @staticmethod
    def _extract_mailbox_backup_email(text: str) -> str | None:
        email_pattern = r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})"
        for pattern in [
            rf"\b(?:with\s+)?(?:backup|recovery)(?:\s+(?:email|address))?\s+{email_pattern}\b",
            rf"\bforward(?:ing)?(?:\s+to)?\s+{email_pattern}\b",
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match is not None:
                return match.group(1)

        mailbox_match = re.search(
            rf"\bmailbox\s+(?:for\s+)?{email_pattern}\b",
            text,
            re.IGNORECASE,
        )
        mailbox_email = mailbox_match.group(1).casefold() if mailbox_match else None
        for email_match in re.finditer(rf"\b{email_pattern}\b", text, re.IGNORECASE):
            email = email_match.group(1)
            if mailbox_email is None or email.casefold() != mailbox_email:
                return email
        return None

    def _parse_create_task(self, text: str) -> AgentToolAction | None:
        title = self._extract_title(text)
        if not title:
            return None
        assignee = self._extract_assignee(text)
        project = self._extract_project(text)
        due_date = self._extract_due_date(text)
        args = {
            "title": title,
            "assignee": assignee,
            "project": project,
            "due_date": due_date,
        }
        return AgentToolAction(
            tool_name="task_write.create_task",
            arguments={key: value for key, value in args.items() if value is not None},
            summary=self._create_task_summary(title, assignee, project, due_date),
        )

    def _parse_update_task(self, text: str) -> AgentToolAction | None:
        task_match = _TASK_ID_RE.search(text)
        if task_match is None:
            return None
        task_id = task_match.group(0).upper()
        title = self._extract_update_title(text)
        assignee = self._extract_assignee(text)
        project = self._extract_project(text)
        due_date = self._extract_due_date(text)
        status = self._extract_status(text)
        args = {
            "task_id": task_id,
            "title": title,
            "assignee": assignee,
            "project": project,
            "due_date": due_date,
            "status": status,
        }
        changed = {key: value for key, value in args.items() if value is not None}
        if set(changed) == {"task_id"}:
            return None
        return AgentToolAction(
            tool_name="task_write.update_task",
            arguments=changed,
            summary=f"Update {task_id}: {', '.join(key for key in changed if key != 'task_id')}",
        )

    def _extract_title(self, text: str) -> str | None:
        match = re.search(
            r"\btask\s+(?:(?:for|in)\s+.+?\s+)?to\s+(.+)",
            text,
            re.IGNORECASE,
        )
        if match is None:
            match = re.search(
                r"\bcreate\s+(?:a\s+)?task[:\s]+(.+)", text, re.IGNORECASE
            )
        if match is None:
            return None
        title = match.group(1)
        title = self._trim_parseable_due_clause(title)
        title = self._trim_trailing_assignee_clause(title)
        title = re.split(
            r"\s+\b(?:and assign(?: it)? to|assign(?: it)? to|and link(?: it)? to|in project|for project)\b",
            title,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        title = _clean_text(title)
        if title and self._is_target_only_task_title(title):
            return None
        return title

    def _is_target_only_task_title(self, title: str) -> bool:
        return bool(
            re.match(
                r"^(?:for\b|in\s+project\b|assign(?:ed)?\b|assign(?:ed)?\s+to\b)",
                title,
                re.IGNORECASE,
            )
        )

    def _extract_update_title(self, text: str) -> str | None:
        match = re.search(
            r"\b(?:title|rename(?:\s+(?:task|TASK-\d+))?)\s+to\s+(.+)",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        title = self._trim_parseable_due_clause(match.group(1))
        title = re.split(
            r"\s+\b(?:and assign(?: it)? to|assign(?: it)? to|and link(?: it)? to|in project|for project|status to)\b",
            title,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(title)

    def _trim_parseable_due_clause(self, title: str) -> str:
        for match in re.finditer(r"\b(?:by|before|due)\b", title, re.IGNORECASE):
            candidate = title[match.start() :]
            if self._extract_due_date_from_clause_start(candidate) is not None:
                return title[: match.start()].rstrip()
        return title

    def _trim_trailing_assignee_clause(self, title: str) -> str:
        match = re.search(
            r"(?i:\bfor)\s+([A-Z][A-Za-z .'-]{0,80}?)"
            r"(?=\s+(?i:in\s+project|for\s+project|by|before|due)\b|[ .,:;!?]*$)",
            title,
        )
        if match is None:
            return title
        assignee = _clean_text(match.group(1))
        if not assignee or assignee.casefold().startswith("project "):
            return title
        return title[: match.start()].rstrip()

    def _extract_assignee(self, text: str) -> str | None:
        match = re.search(
            r"\bfor\s+([A-Za-z][A-Za-z .'-]{0,80}?)\s+to\b",
            text,
            re.IGNORECASE,
        )
        if match is not None:
            assignee = _clean_text(match.group(1))
            if assignee and not assignee.casefold().startswith("project "):
                return assignee

        if match is None or match.group(1).casefold().startswith("project "):
            match = re.search(
                r"\bassign(?:ed)?\s+(?:(?:it|task|TASK-\d+)\s+)?to\s+([A-Za-z][A-Za-z .'-]{0,80})",
                text,
                re.IGNORECASE,
            )
        if match is None:
            trailing_match = re.search(
                r"(?i:\bfor)\s+([A-Z][A-Za-z .'-]{0,80}?)"
                r"(?=\s+(?i:in\s+project|for\s+project|by|before|due)\b|[ .,:;!?]*$)",
                text,
            )
            if trailing_match is None:
                return None
            trailing_assignee = _clean_text(trailing_match.group(1))
            if trailing_assignee and not trailing_assignee.casefold().startswith(
                "project "
            ):
                return trailing_assignee
            return None
        assignee = re.split(
            r"\s+\b(?:by|before|due|and|in project|for project)\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(assignee)

    def _extract_project(self, text: str) -> str | None:
        match = re.search(
            r"\b(?:link(?: it)? to project|in project|for project)\s+([A-Za-z0-9][A-Za-z0-9 ._-]{0,80})",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        project = self._trim_parseable_due_clause(match.group(1))
        project = re.split(
            r"\s+\b(?:and assign(?: it)? to|assign(?:ed)?(?:\s+(?:it|task|TASK-\d+))?\s+to|matching|about)\b",
            project,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        project = re.split(
            r"\s+\bto\s+(?=[a-z])",
            project,
            maxsplit=1,
        )[0]
        return _clean_text(project)

    def _extract_due_date_from_clause_start(self, text: str) -> str | None:
        due_cue = r"^\s*(?:by|before|due(?:\s+(?:by|on))?)\s+"
        iso_match = re.search(rf"{due_cue}({_DATE_ISO_RE.pattern})", text, re.I)
        if iso_match:
            try:
                return date.fromisoformat(iso_match.group(2)).isoformat()
            except ValueError:
                return None
        month_match = re.search(rf"{due_cue}({_MONTH_DATE_RE.pattern})", text, re.I)
        if month_match:
            month = _MONTHS[month_match.group(2).casefold()]
            day = int(month_match.group(3))
            year = int(month_match.group(4))
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
        lowered = text.casefold()
        today = self.today or date.today()
        if re.search(rf"{due_cue}tomorrow\b", lowered):
            return (today + timedelta(days=1)).isoformat()
        for weekday_name, weekday_index in _WEEKDAYS.items():
            if re.search(rf"{due_cue}(?:next\s+)?{weekday_name}\b", lowered):
                days_ahead = (weekday_index - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                return (today + timedelta(days=days_ahead)).isoformat()
        return None

    def _extract_due_date(self, text: str) -> str | None:
        due_cue = r"\b(?:by|before|due)\s+(?:on\s+)?"
        iso_match = re.search(rf"{due_cue}({_DATE_ISO_RE.pattern})", text, re.I)
        if iso_match:
            try:
                return date.fromisoformat(iso_match.group(2)).isoformat()
            except ValueError:
                return None
        month_match = re.search(rf"{due_cue}({_MONTH_DATE_RE.pattern})", text, re.I)
        if month_match:
            month = _MONTHS[month_match.group(2).casefold()]
            day = int(month_match.group(3))
            year = int(month_match.group(4))
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                return None
        lowered = text.casefold()
        today = self.today or date.today()
        if re.search(rf"{due_cue}tomorrow\b", lowered):
            return (today + timedelta(days=1)).isoformat()
        for weekday_name, weekday_index in _WEEKDAYS.items():
            if re.search(rf"{due_cue}(?:next\s+)?{weekday_name}\b", lowered):
                days_ahead = (weekday_index - today.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                return (today + timedelta(days=days_ahead)).isoformat()
        return None

    @staticmethod
    def _extract_search_query(text: str) -> str:
        match = re.search(
            r"\b(?:find|search|list|show)\s+tasks?\b(?:\s+(?:for|matching|about)?\s*(.*))?",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return text.strip()
        raw_query = (match.group(1) or "").strip()
        if not raw_query:
            return ""
        has_explicit_project_clause = (
            re.search(r"\b(?:for|in)\s+project\s+", text, re.IGNORECASE) is not None
        )
        leading_project = re.match(
            r"^(?:in\s+project|for\s+project)\s+(.+)",
            raw_query,
            re.IGNORECASE,
        )
        if leading_project is not None:
            trailing_query = re.search(
                r"\b(?:matching|about)\s+(.+)",
                leading_project.group(1),
                re.IGNORECASE,
            )
            return (
                (_clean_text(trailing_query.group(1)) or "") if trailing_query else ""
            )

        bare_project_filter = (
            re.match(r"^project\s+(.+)", raw_query, re.IGNORECASE)
            if has_explicit_project_clause
            else None
        )
        if (
            bare_project_filter is not None
            and re.search(
                r"\b(?:in|for)\s+project\s+",
                raw_query,
                re.IGNORECASE,
            )
            is None
        ):
            trailing_query = re.search(
                r"\b(?:matching|about)\s+(.+)",
                bare_project_filter.group(1),
                re.IGNORECASE,
            )
            return (
                (_clean_text(trailing_query.group(1)) or "") if trailing_query else ""
            )

        if re.match(r"^(?:in\s+project|for\s+project)\b", raw_query, re.I):
            return ""
        query = re.split(
            r"\s+\b(?:in project|for project)\b",
            raw_query,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(query) or ""

    @staticmethod
    def _extract_status(text: str) -> str | None:
        lowered = text.casefold()
        if re.search(r"\b(?:close|complete)\s+(?:task\s+)?task-\d+\b", lowered):
            return "done"
        if re.search(
            r"\bmark(?:ed)?\s+(?:task\s+)?task-\d+\s+(?:as\s+)?(?:done|completed)\b",
            lowered,
        ):
            return "done"
        if re.search(
            r"\b(?:close|complete|mark)\s+(?:task\s+)?(?:as\s+)?(?:done|completed)\b",
            lowered,
        ):
            return "done"
        if re.search(r"\bstatus\s+to\s+(?:done|completed)\b", lowered):
            return "done"
        if re.search(r"\bstatus\s+to\s+blocked\b", lowered):
            return "blocked"
        if re.search(r"\bstatus\s+to\s+in progress\b", lowered):
            return "in_progress"
        return None

    @staticmethod
    def _extract_repository(text: str) -> str | None:
        match = re.search(
            r"\b(?:in|for)\s+(?:repo|repository)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
            text,
            re.IGNORECASE,
        )
        return match.group(1) if match else None

    @staticmethod
    def _extract_github_issue_number(text: str) -> int | None:
        matches = re.findall(
            r"\b(?:(?:github|gh)\s+)?(?:issue|todo)\s*#?\s*(\d+)\b",
            text,
            re.IGNORECASE,
        )
        if not matches:
            return None
        try:
            return int(matches[-1])
        except ValueError:
            return None

    @staticmethod
    def _extract_github_project_number(text: str) -> int | None:
        matches = re.findall(
            r"\b(?:(?:github|gh)\s+)?project(?:\s+board)?\s*#?\s*(\d+)\b",
            text,
            re.IGNORECASE,
        )
        if not matches:
            return None
        try:
            return int(matches[-1])
        except ValueError:
            return None

    @staticmethod
    def _extract_github_project_item_number(text: str) -> int | None:
        matches = re.findall(r"\bitem\s*#?\s*(\d+)\b", text, re.IGNORECASE)
        if not matches:
            return None
        try:
            return int(matches[-1])
        except ValueError:
            return None

    @staticmethod
    def _extract_github_comment_body(text: str) -> str | None:
        match = re.search(
            r"\b(?:comment|reply)(?:\s+(?:on|to))?\s+"
            r"(?:(?:github|gh)\s+)?(?:issue|todo)\s*#?\s*\d+"
            r"(?:\s*(?::|with|saying)\s*|\s+)(.+)$",
            text,
            re.IGNORECASE,
        )
        return _clean_text(match.group(1)) if match else None

    @staticmethod
    def _extract_github_issue_update_title(text: str) -> str | None:
        match = re.search(
            r"\b(?:title|rename(?:\s+(?:issue|todo))?)\s+to\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        title = re.split(
            r"\s+\b(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(title)

    @staticmethod
    def _extract_github_issue_body_update(text: str) -> str | None:
        match = re.search(r"\bbody\s+to\s+(.+)$", text, re.IGNORECASE)
        if match is None:
            return None
        body = re.split(
            r"\s+\b(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(body)

    @staticmethod
    def _extract_body(text: str) -> str | None:
        match = re.search(r"\bwith\s+body\s+(.+)", text, re.IGNORECASE)
        return _clean_text(match.group(1)) if match else None

    @staticmethod
    def _extract_github_issue_title(text: str) -> str | None:
        match = re.search(
            r"\b(?:create|open)\s+(?:a\s+)?(?:(?:github|gh)\s+)?(?:issue|todo)"
            r"(?:\s+(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?"
            r"(?:\s+(?:titled|for|to)\s+|:\s*|\s+)(.+)",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        title = re.split(
            r"\s+\bwith\s+body\b",
            match.group(1),
            maxsplit=1,
            flags=re.I,
        )[0]
        title = re.split(
            r"\s+\b(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
            title,
            maxsplit=1,
            flags=re.I,
        )[0]
        return _clean_text(title)

    @staticmethod
    def _extract_github_issue_query(text: str) -> str:
        match = re.search(
            r"\b(?:search|find|list|show)\s+(?:(?:github|gh)\s+)?(?:issues?|todos?)"
            r"(?:\s+(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?"
            r"(?:\s+(?:for|matching|about)\s+)?(.+)?",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return ""
        query = match.group(1) or ""
        query = re.split(
            r"\s+\b(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b",
            query,
            maxsplit=1,
            flags=re.I,
        )[0]
        return _clean_text(query) or ""

    @staticmethod
    def _choose_model_tier(text: str, actions: list[AgentToolAction]) -> ModelTier:
        if len(actions) > 1:
            return "strong"
        if any(action.risk in {"medium", "high", "critical"} for action in actions):
            return "strong"
        if len(text.split()) > 18:
            return "strong"
        return "fast"

    @staticmethod
    def _choose_model_tier_for_request(text: str) -> ModelTier:
        """Choose a real planner tier before model invocation.

        This intentionally uses only coarse, deterministic request signals. The
        model does not decide which quality tier or authority boundary applies.
        """

        lowered = text.casefold()
        write_markers = (
            "create",
            "update",
            "approve",
            "reject",
            "send",
            "invite",
            "provision",
            "set up",
            "remember",
            "forget",
        )
        if len(text.split()) > 18 or any(
            re.search(rf"\b{marker}\b", lowered) for marker in write_markers
        ):
            return "strong"
        return "fast"

    def _planner_action_clarification(
        self,
        action: AgentToolAction,
        *,
        context: AgentIdentityContext,
    ) -> str | None:
        """Reject incomplete model proposals before authorization or execution."""

        args = action.arguments
        tool_name = action.tool_name
        if tool_name == "task_read.search_tasks":
            if not _non_empty_arg(args, "project"):
                return "Which project should I search?"
            return None
        if tool_name == "task_write.create_task":
            if not _non_empty_arg(args, "title"):
                return "What should the task be?"
            return None
        if tool_name == "task_write.update_task":
            if not _non_empty_arg(args, "task_id"):
                return "Which task should I update?"
            if not any(
                _non_empty_arg(args, key)
                for key in ("title", "project", "assignee", "due_date", "status")
            ):
                return "What should I change on that task?"
            return None
        if tool_name == "github_issue.search_issues":
            if not _non_empty_arg(args, "repository") and not _non_empty_text(
                self.registry.runtime_config.github_default_repo
            ):
                return "Which GitHub repository should I search?"
            return None
        if tool_name == "github_issue.get_issue" and not _non_empty_arg(
            args, "issue_number"
        ):
            return "Which GitHub issue should I show?"
        if tool_name == "github_issue.create_issue":
            if not _non_empty_arg(args, "title"):
                return "What should be the title of the GitHub issue?"
            if not _non_empty_arg(args, "repository") and not _non_empty_text(
                self.registry.runtime_config.github_default_repo
            ):
                return "Which GitHub repository should I create the issue in?"
            return None
        if tool_name == "github_issue.update_issue":
            if not _non_empty_arg(args, "issue_number"):
                return "Which GitHub issue should I update?"
            if not any(
                key in args and _non_empty_arg(args, key)
                for key in ("title", "body", "state")
            ):
                return "What should I change on that GitHub issue?"
            return None
        if tool_name == "github_issue.comment_on_issue":
            if not _non_empty_arg(args, "issue_number"):
                return "Which GitHub issue should I comment on?"
            if not _non_empty_arg(args, "body"):
                return "What comment should I add to that GitHub issue?"
            return None
        if tool_name in {
            "github_project.get_project",
            "github_project.list_project_fields",
            "github_project.list_project_items",
        } and not _non_empty_arg(args, "project_number"):
            return "Which GitHub Project should I use?"
        if tool_name == "github_project.add_issue_to_project":
            if not _non_empty_arg(args, "project_number"):
                return "Which GitHub Project should I add the issue to?"
            if not _non_empty_arg(args, "issue_number"):
                return "Which GitHub issue should I add to the project?"
            if not _non_empty_arg(args, "repository") and not _non_empty_text(
                self.registry.runtime_config.github_default_repo
            ):
                return "Which GitHub repository contains that issue?"
            return None
        if tool_name == "github_project.update_project_item":
            if not _non_empty_arg(args, "project_number"):
                return "Which GitHub Project should I update?"
            if not _non_empty_arg(args, "item_id"):
                return "Which GitHub Project item should I update?"
            if not isinstance(args.get("fields"), list) or not args["fields"]:
                return "Which GitHub Project field values should I update?"
            return None
        if tool_name == "crm_read.search_contacts" and not _non_empty_arg(
            args, "query"
        ):
            return "Who should I look up?"
        if tool_name == "crm_write.update_contact":
            if not _non_empty_arg(args, "contact_id"):
                return "Which CRM contact should I update?"
            if not isinstance(args.get("updates"), dict) or not args["updates"]:
                return "What should I update on that CRM contact?"
        if (
            tool_name == "docuseal_write.create_member_agreement_submission"
            and not _non_empty_arg(args, "submitter_email")
        ):
            return "What email address should I use for the member agreement?"
        if tool_name == "mail_write.create_mailbox":
            if not _non_empty_arg(args, "local_part"):
                return "What mailbox should I create?"
            if not _non_empty_arg(args, "backup_email"):
                return "What backup email should I use?"
            if not _non_empty_arg(args, "name"):
                return "What display name should I use?"
        if tool_name == "sso_write.create_user" and not _has_contact_reference(args):
            return "Which CRM contact should I create the SSO user for?"
        if tool_name == "outline_write.invite_user" and not (
            _non_empty_arg(args, "email") or _has_contact_reference(args)
        ):
            return "Who should I invite to Outline?"
        if tool_name == "account_write.create_user_accounts":
            if not _has_contact_reference(args):
                return "Which CRM contact should I create accounts for?"
            if not _non_empty_arg(args, "mailbox_username"):
                return "What 508 mailbox username should I create?"
        if tool_name == "memory_read.get_project_facts" and not context.project_id:
            return "I need a project context before I can read project memory."
        if tool_name == "memory_write.remember_fact":
            if not _non_empty_arg(args, "key") or not isinstance(
                args.get("value_json"), dict
            ):
                return "What fact should I remember?"
        if tool_name == "memory_write.forget_fact" and not _non_empty_arg(
            args, "fact_id"
        ):
            return "Which remembered fact should I forget?"
        return None

    @staticmethod
    def _intent_for_tool(tool_name: str) -> str:
        return {
            "task_read.search_tasks": "search_tasks",
            "task_write.create_task": "create_task",
            "task_write.update_task": "update_task",
            "github_issue.search_issues": "search_github_issues",
            "github_issue.get_issue": "get_github_issue",
            "github_issue.create_issue": "create_github_issue",
            "github_issue.update_issue": "update_github_issue",
            "github_issue.comment_on_issue": "comment_on_github_issue",
            "github_repository.list_repositories": "list_github_repositories",
            "github_project.list_projects": "list_github_projects",
            "github_project.get_project": "get_github_project",
            "github_project.list_project_fields": "list_github_project_fields",
            "github_project.list_project_items": "list_github_project_items",
            "github_project.add_issue_to_project": "add_github_issue_to_project",
            "github_project.update_project_item": "update_github_project_item",
            "crm_read.search_contacts": "search_crm_contacts",
            "crm_write.update_contact": "update_crm_contact",
            "docuseal_write.create_member_agreement_submission": "send_member_agreement",
            "mail_write.create_mailbox": "create_mailbox",
            "sso_write.create_user": "create_sso_user",
            "outline_write.invite_user": "invite_outline_user",
            "account_write.create_user_accounts": "create_user_accounts",
            "memory_read.get_user_facts": "read_user_memory",
            "memory_read.get_project_facts": "read_project_memory",
            "memory_read.search_context": "search_context",
            "memory_write.remember_fact": "remember_fact",
            "memory_write.forget_fact": "forget_fact",
        }.get(tool_name, "unknown")

    @staticmethod
    def _human_summary(actions: list[AgentToolAction]) -> str:
        if not actions:
            return "No actions."
        return "\n".join(
            f"{index}. {action.summary}"
            for index, action in enumerate(actions, start=1)
        )

    @staticmethod
    def _create_task_summary(
        title: str,
        assignee: str | None,
        project: str | None,
        due_date: str | None,
    ) -> str:
        parts = [f'Create task: "{title}"']
        if assignee:
            parts.append(f"assign to {assignee}")
        if project:
            parts.append(f"project {project}")
        if due_date:
            parts.append(f"due {due_date}")
        return ", ".join(parts)

    @staticmethod
    def _execution_message(results: list[AgentExecutionResult]) -> str:
        if not results:
            return "No actions were executed."
        if all(result.status == "succeeded" for result in results):
            return "Executed the approved agent plan."
        return "One or more agent actions failed."


def _clean_text(value: str) -> str | None:
    cleaned = value.strip(" .,:;\"'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_arg(arguments: dict[str, object], key: str) -> bool:
    value = arguments.get(key)
    if _non_empty_text(value):
        return True
    return isinstance(value, int | float) and not isinstance(value, bool) and value > 0


def _has_contact_reference(arguments: dict[str, object]) -> bool:
    return _non_empty_arg(arguments, "contact_id") or _non_empty_arg(
        arguments, "contact_query"
    )


def _format_contact_candidates(contacts: list[dict[str, object]]) -> str:
    labels: list[str] = []
    for contact in contacts[:5]:
        name = _clean_text(str(contact.get("name") or "Unknown")) or "Unknown"
        email = _clean_text(str(contact.get("emailAddress") or "")) or ""
        contact_id = _clean_text(str(contact.get("id") or "")) or ""
        label = name
        if email:
            label = f"{label} <{email}>"
        if contact_id:
            label = f"{label} ({contact_id})"
        labels.append(label)
    return "; ".join(labels)
