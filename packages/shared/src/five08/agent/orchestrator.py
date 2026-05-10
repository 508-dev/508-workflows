"""Agent gateway orchestration for English task requests."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
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


class AgentOrchestrator:
    """Convert English into frozen, policy-checked plans and execute them."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        policy: PolicyEngine | None = None,
        model_config: AgentModelConfig | None = None,
        today: date | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.policy = policy or PolicyEngine()
        self.model_config = model_config or AgentModelConfig()
        self.today = today

    def plan(self, message: str, context: AgentIdentityContext) -> AgentResponse:
        text = message.strip()
        if not text:
            return AgentResponse(
                status="needs_clarification",
                message="I need a task request to work from.",
                clarification_question="What task or project action should I take?",
            )

        action = self._parse_action(text)
        if action is None:
            return AgentResponse(
                status="needs_clarification",
                message="I could not turn that into a supported task action.",
                clarification_question=(
                    "Try asking me to create, update, or search for a task."
                ),
            )

        manifest = self.registry.get(action.tool_name)
        if manifest is not None:
            action.risk = manifest.risk
            action.requires_confirmation = manifest.requires_confirmation
            action.required_scopes = list(manifest.required_scopes)

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
                model_tier=self._choose_model_tier(text, [action]),
                requires_confirmation=False,
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
            model_tier=self._choose_model_tier(text, [action]),
            requires_confirmation=decision.requires_confirmation,
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

        organization_id = (
            context.organization_id or context.guild_id or context.workspace_id
        )
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

    def _build_plan(
        self,
        *,
        intent: str,
        actions: list[AgentToolAction],
        model_tier: ModelTier,
        requires_confirmation: bool,
    ) -> AgentPlan:
        return AgentPlan(
            plan_id=str(uuid4()),
            intent=intent,
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
        if "create" in lowered and "task" in lowered:
            return self._parse_create_task(text)
        if any(
            keyword in lowered
            for keyword in ["find task", "search task", "list task", "show task"]
        ):
            return self._parse_search_task(text)
        if "github issue" in lowered or "gh issue" in lowered:
            return self._parse_github_issue(text)
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

        if any(keyword in lowered for keyword in ["create", "open"]):
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

    def _parse_member_agreement(self, text: str) -> AgentToolAction | None:
        email_match = re.search(
            r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        if email_match is None:
            return None
        before_email = text[: email_match.start()]
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
            args["begin"] = f"{month_match.group(1)}-01"
        return AgentToolAction(
            tool_name="kimai_read.project_hours",
            arguments=args,
            summary=f"Read Kimai hours for project {project_name}",
        )

    def _parse_mailbox_create(self, text: str) -> AgentToolAction | None:
        email_match = re.search(
            r"\b([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        local_match = re.search(
            r"\bmailbox\s+(?:for\s+)?([A-Za-z0-9._%+-]+)(?:@|\s)",
            text,
            re.IGNORECASE,
        )
        name_match = re.search(
            r"\b(?:named|name)\s+([A-Za-z][A-Za-z .'-]{0,80})",
            text,
            re.IGNORECASE,
        )
        if email_match is None or local_match is None or name_match is None:
            return None
        local_part = local_match.group(1).strip().lower()
        name = _clean_text(name_match.group(1))
        if not local_part or not name:
            return None
        return AgentToolAction(
            tool_name="mail_write.create_mailbox",
            arguments={
                "local_part": local_part,
                "backup_email": email_match.group(1),
                "name": name,
            },
            summary=f"Create mailbox {local_part} for {name}",
        )

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
        title = re.split(
            r"\s+\b(?:and assign(?: it)? to|assign(?: it)? to|and link(?: it)? to|in project|for project)\b",
            title,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(title)

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
            if self._extract_due_date(candidate) is not None:
                return title[: match.start()].rstrip()
        return title

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
        project = re.split(
            r"\s+\b(?:by|before|due|and|assign|to|matching|about)\b",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return _clean_text(project)

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
        leading_project_pattern = (
            r"^(?:in\s+project|for\s+project|project)\s+(.+)"
            if has_explicit_project_clause
            else r"^(?:in\s+project|for\s+project)\s+(.+)"
        )
        leading_project = re.match(leading_project_pattern, raw_query, re.IGNORECASE)
        if leading_project is not None:
            trailing_query = re.search(
                r"\b(?:matching|about)\s+(.+)",
                leading_project.group(1),
                re.IGNORECASE,
            )
            return (
                (_clean_text(trailing_query.group(1)) or "") if trailing_query else ""
            )
        project_only_pattern = (
            r"^(?:in\s+project|for\s+project|project)\b"
            if has_explicit_project_clause
            else r"^(?:in\s+project|for\s+project)\b"
        )
        if re.match(project_only_pattern, raw_query, re.I):
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
        title = re.split(r"\s+\bwith\s+body\b", match.group(1), maxsplit=1, flags=re.I)[
            0
        ]
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
