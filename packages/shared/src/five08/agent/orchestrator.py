"""Agent gateway orchestration for English task requests."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import uuid4

from five08.agent.models import (
    AgentExecutionResult,
    AgentIdentityContext,
    AgentModelSelection,
    AgentPlan,
    AgentResponse,
    AgentToolAction,
    ModelTier,
)
from five08.agent.model_routing import AgentModelConfig
from five08.agent.policy import PolicyEngine
from five08.agent.tools import ToolRegistry
from five08.clients.migadu import normalize_migadu_mailbox_domain

_TASK_ID_RE = re.compile(r"\bTASK-\d+\b", re.IGNORECASE)
_DATE_ISO_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
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
        intent_normalizer: AgentIntentNormalizer | None = None,
        today: date | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.policy = policy or PolicyEngine()
        self.model_config = model_config or AgentModelConfig()
        self.intent_normalizer = intent_normalizer
        self.today = today

    def plan(self, message: str, context: AgentIdentityContext) -> AgentResponse:
        text = message.strip()
        if not text:
            return AgentResponse(
                status="needs_clarification",
                message="I need a task request to work from.",
                clarification_question="What task or project action should I take?",
            )

        planner: LiteralPlanner = "deterministic_regex"
        planning_text = text
        action = self._parse_action(text)
        if action is None:
            resolved_member_agreement = self._plan_member_agreement_from_crm(
                text,
                context,
                planner=planner,
            )
            if resolved_member_agreement is not None:
                return resolved_member_agreement
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
        if action is None and planner == "live_model":
            resolved_member_agreement = self._plan_member_agreement_from_crm(
                planning_text,
                context,
                planner=planner,
            )
            if resolved_member_agreement is not None:
                return resolved_member_agreement
        if action is None:
            if re.search(r"\bcreate\s+(?:a\s+)?task\b", text, re.IGNORECASE):
                return AgentResponse(
                    status="needs_clarification",
                    message="I need a task title before I can create it.",
                    clarification_question="What should the task be?",
                )
            return AgentResponse(
                status="needs_clarification",
                message="I could not turn that into a supported task action.",
                clarification_question=(
                    "Try asking me to create, update, or search for a task."
                ),
            )
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

    def _response_for_action(
        self,
        *,
        action: AgentToolAction,
        context: AgentIdentityContext,
        planning_text: str,
        planner: LiteralPlanner,
    ) -> AgentResponse:
        """Authorize, freeze, and optionally execute a single planned action."""

        manifest = self.registry.get(action.tool_name)
        if manifest is not None:
            action.risk = manifest.risk
            action.requires_confirmation = manifest.requires_confirmation
            action.required_scopes = self.policy.required_scopes_for_action(
                manifest=manifest,
                action=action,
            )

        decision = self.policy.authorize(
            context=context,
            manifest=manifest,
            action=action,
        )
        if not decision.allowed:
            action.requires_confirmation = False
            plan = self._build_plan(
                intent=self._intent_for_tool(action.tool_name),
                actions=[action],
                model_tier=self._choose_model_tier(planning_text, [action]),
                requires_confirmation=False,
                planner=planner,
            )
            return AgentResponse(
                status="denied",
                plan=plan,
                message=decision.reason,
            )

        action.requires_confirmation = decision.requires_confirmation
        plan = self._build_plan(
            intent=self._intent_for_tool(action.tool_name),
            actions=[action],
            model_tier=self._choose_model_tier(planning_text, [action]),
            requires_confirmation=decision.requires_confirmation,
            planner=planner,
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
        if "member agreement" not in lowered or not re.search(
            r"\b(?:send|create)\b",
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
            except Exception as exc:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="failed",
                        error=str(exc),
                    )
                )
            else:
                results.append(
                    AgentExecutionResult(
                        tool_name=action.tool_name,
                        status="succeeded",
                        result=payload,
                    )
                )
        return results

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
        intent: str,
        actions: list[AgentToolAction],
        model_tier: ModelTier,
        requires_confirmation: bool,
        planner: LiteralPlanner = "deterministic_regex",
    ) -> AgentPlan:
        return AgentPlan(
            plan_id=str(uuid4()),
            intent=intent,
            planner=planner,
            model_tier=model_tier,
            model=self._resolve_model(model_tier),
            actions=actions,
            human_summary=self._human_summary(actions),
            requires_confirmation=requires_confirmation,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )

    def _resolve_model(self, model_tier: ModelTier) -> AgentModelSelection:
        return self.model_config.resolve(model_tier)

    def _parse_action(self, text: str) -> AgentToolAction | None:
        lowered = text.casefold()
        if re.search(r"\bcreate\s+(?:a\s+)?task\b", text, re.IGNORECASE):
            return self._parse_create_task(text)
        if "github issue" in lowered or "gh issue" in lowered:
            action = self._parse_github_issue(text)
            if action is not None:
                return action
        if any(
            keyword in lowered
            for keyword in ["find task", "search task", "list task", "show task"]
        ):
            return self._parse_search_task(text)
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
            keyword in lowered for keyword in ["send", "create"]
        ):
            return self._parse_member_agreement(text)
        if "kimai" in lowered and "hour" in lowered:
            return self._parse_kimai_project_hours(text)
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
        if re.search(
            r"\b(?:create|open)\s+(?:a\s+)?(?:github|gh)\s+issue\b",
            text,
            re.IGNORECASE,
        ):
            title = self._extract_github_issue_title(text)
            if not title:
                return None
            repository = self._extract_repository(text)
            body = self._extract_body(text)
            args = {"title": title, "repository": repository, "body": body}
            return AgentToolAction(
                tool_name="github_issue.create_issue",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f'Create GitHub issue: "{title}"',
            )

        if any(keyword in lowered for keyword in ["search", "find", "list", "show"]):
            query = self._extract_github_issue_query(text)
            repository = self._extract_repository(text)
            args = {"query": query, "repository": repository, "state": "open"}
            return AgentToolAction(
                tool_name="github_issue.search_issues",
                arguments={
                    key: value for key, value in args.items() if value is not None
                },
                summary=f"Search GitHub issues matching: {query}",
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

    def _parse_kimai_project_hours(self, text: str) -> AgentToolAction | None:
        match = re.search(
            r"\b(?:kimai\s+)?(?:project\s+)?hours\s+(?:for|on)\s+project\s+(.+)",
            text,
            re.IGNORECASE,
        )
        if match is None:
            return None
        project = re.split(
            r"\s+\b(?:in|for|during)\s+\d{4}-\d{2}\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        project_name = _clean_text(project)
        if not project_name:
            return None
        args: dict[str, str] = {"project": project_name}
        month_match = re.search(r"\b(20\d{2}-\d{2})\b", text)
        if month_match:
            year, month = (int(part) for part in month_match.group(1).split("-"))
            if month < 1 or month > 12:
                return None
            begin = date(year, month, 1)
            if month == 12:
                next_month = date(year + 1, 1, 1)
            else:
                next_month = date(year, month + 1, 1)
            end = datetime.combine(next_month, datetime.min.time()) - timedelta(
                seconds=1
            )
            args["begin"] = begin.isoformat()
            args["end"] = end.isoformat()
        return AgentToolAction(
            tool_name="kimai_read.project_hours",
            arguments=args,
            summary=f"Read Kimai hours for project {project_name}",
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
    def _extract_body(text: str) -> str | None:
        match = re.search(r"\bwith\s+body\s+(.+)", text, re.IGNORECASE)
        return _clean_text(match.group(1)) if match else None

    @staticmethod
    def _extract_github_issue_title(text: str) -> str | None:
        match = re.search(
            r"\b(?:create|open)\s+(?:a\s+)?(?:github|gh)\s+issue"
            r"(?:\s+(?:in|for)\s+(?:repo|repository)\s+[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?"
            r"(?:\s+(?:titled|for|to)\s+|:\s*)(.+)",
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
            r"\b(?:search|find|list|show)\s+(?:github|gh)\s+issues?"
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
    def _intent_for_tool(tool_name: str) -> str:
        return {
            "task_read.search_tasks": "search_tasks",
            "task_write.create_task": "create_task",
            "task_write.update_task": "update_task",
            "github_issue.search_issues": "search_github_issues",
            "github_issue.create_issue": "create_github_issue",
            "crm_read.search_contacts": "search_crm_contacts",
            "crm_write.update_contact": "update_crm_contact",
            "docuseal_write.create_member_agreement_submission": "send_member_agreement",
            "kimai_read.project_hours": "read_kimai_project_hours",
            "mail_write.create_mailbox": "create_mailbox",
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
