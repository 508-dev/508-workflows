"""Job posting and candidate matching cog for the 508.dev Discord bot."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import re
import socket
from collections import OrderedDict
from dataclasses import dataclass, is_dataclass, replace
from typing import Any, Awaitable, Callable, Literal, cast
from urllib.parse import urljoin, urlsplit

import discord
from discord import app_commands
from discord.ext import commands
from curl_cffi import CurlOpt
from curl_cffi import requests as curl_requests
from curl_cffi.requests import BrowserTypeLiteral
from curl_cffi.requests import RequestsError

from five08.audit import update_person_discord_roles, upsert_discord_member
from five08.candidate_search import search_candidates
from five08.crm_normalization import format_seniority_label
from five08.document_text import document_file_extension, extract_document_text
from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import (
    check_user_roles_with_hierarchy,
    require_role,
)
from five08.engagements import (
    DiscordEngagementInput,
    EngagementApplicationSource,
    add_engagement_event,
    list_due_recruiting_reminders,
    mark_recruiting_reminder_sent,
    parse_status_from_title,
    requirements_to_payload,
    upsert_discord_interest_application,
    strip_status_from_title,
    upsert_discord_engagement,
    upsert_suggested_applications,
)
from five08.job_channels import (
    JobPostingType,
    infer_job_posting_type_from_labels,
    list_registered_job_post_channel_configs,
    normalize_job_posting_type,
    register_job_post_channel,
    unregister_job_post_channel,
)
from five08.job_match import (
    DISCORD_ROLES_EXCLUDE_FROM_SYNC,
    JobRequirements,
    extract_job_requirements,
    is_language_requirement_skill,
    rerank_shortlisted_candidates,
)

logger = logging.getLogger(__name__)

MATCH_CANDIDATES_MAX_ATTACHMENT_SCAN = 5
MATCH_CANDIDATES_MAX_LINK_SCAN = 3
MATCH_CANDIDATES_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MATCH_CANDIDATES_MAX_ATTACHMENT_TEXT_CHARS = 10000
MATCH_CANDIDATES_MAX_LINK_TEXT_CHARS = 12000
MATCH_CANDIDATES_MAX_LINK_BYTES = 2 * 1024 * 1024
MATCH_CANDIDATES_MAX_LINK_REDIRECTS = 2
MATCH_CANDIDATES_MAX_POSTING_CHARS = 36000
MATCH_CANDIDATES_FETCH_TIMEOUT_SECONDS = 12.0
MATCH_CANDIDATES_BROWSER_TIMEOUT_MS = 20_000
MATCH_CANDIDATES_BROWSER_POST_NAV_WAIT_MS = 5_000
MATCH_CANDIDATES_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
MATCH_CANDIDATES_FETCH_IMPERSONATE: BrowserTypeLiteral = "chrome131_android"
MATCH_CANDIDATES_BLOCKED_STATUS_CODES = frozenset({401, 403, 429, 503})
MATCH_CANDIDATES_BLOCKED_PATTERNS = (
    re.compile(r"access denied", re.IGNORECASE),
    re.compile(r"attention required", re.IGNORECASE),
    re.compile(r"\bcaptcha\b", re.IGNORECASE),
    re.compile(r"_cf_chl_opt", re.IGNORECASE),
    re.compile(r"/cdn-cgi/challenge-platform", re.IGNORECASE),
    re.compile(r"checking your browser", re.IGNORECASE),
    re.compile(r"enable javascript and cookies(?: to continue)?", re.IGNORECASE),
    re.compile(r"unusual traffic", re.IGNORECASE),
    re.compile(r"verify you are human", re.IGNORECASE),
)
MATCH_CANDIDATES_JS_SPARSE_TEXT_MAX_CHARS = 250
MATCH_CANDIDATES_JS_SPARSE_TEXT_MAX_WORDS = 40
MATCH_CANDIDATES_JS_RENDER_MARKERS = (
    'id="__next"',
    "id='__next'",
    'id="__nuxt"',
    "id='__nuxt'",
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    "data-reactroot",
    "__next_data__",
    "__next_f",
    "__nuxt__",
    "window.__next_data__",
    "window.__nuxt__",
    "enable javascript",
    "requires javascript",
    "javascript to run this app",
    "please turn on javascript",
)
MATCH_CANDIDATES_SUPPORTED_ATTACHMENT_EXTENSIONS = frozenset(
    {".txt", ".md", ".pdf", ".docx", ".html", ".htm", ".rtf"}
)
MATCH_CANDIDATES_URL_PATTERN = re.compile(r"(?i)\bhttps?://[^\s<>()\[\]\"']+")
MATCH_CANDIDATES_JD_URL_HINTS = (
    "job",
    "jobs",
    "jd",
    "job-description",
    "position",
    "role",
    "career",
    "careers",
    "hiring",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "smartrecruiters.com",
    "linkedin.com/jobs",
    "docs.google.com/document",
    "notion.site",
)
AUTO_MATCH_DEDUPE_MAX = 10_000
MATCH_CANDIDATES_PRIVATE_TRUTHY = frozenset({"true", "1", "yes", "y", "on"})
# Exclude known-bad resume artifact from auto-match rendering.
AUTO_MATCH_EXCLUDED_RESUME_NAMES = frozenset({"Vladyslav_Stryzhak.pdf"})
GIG_FORUM_BACKFILL_ARCHIVED_LIMIT = 200
GIG_RECRUITING_REMINDER_CHECK_SECONDS = 6 * 60 * 60
GIG_INTEREST_PATTERNS = (
    re.compile(r"\b(?:i'?m|i am)\s+interested\b", re.IGNORECASE),
    re.compile(r"\binterested\s+(?:in|for)\s+(?:this|the)\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+can\s+help\b", re.IGNORECASE),
    re.compile(
        r"\b(?:i|we)\s+(?:could|can)\s+(?:do|take|handle)\s+this\b", re.IGNORECASE
    ),
    re.compile(r"\bcount\s+me\s+in\b", re.IGNORECASE),
    re.compile(r"\bavailable\s+(?:for|to)\b", re.IGNORECASE),
    re.compile(r"\b(?:happy|open)\s+to\s+(?:help|chat|talk|take)\b", re.IGNORECASE),
)
GIG_INTEREST_NEGATION_RE = re.compile(
    r"\b(?:not|n't|no\s+longer|unavailable)\s+(?:currently\s+)?(?:available|interested)"
    r"|\bavailable\s+(?:for|to)\b.{0,40}\b(?:not|n't)\b",
    re.IGNORECASE,
)
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
JobWatchChannel = discord.ForumChannel


def _format_curl_resolve_address(value: IPAddress) -> str:
    if value.version == 6:
        return f"[{value.compressed}]"
    return value.compressed


def _parse_match_candidates_private(private_flag: str | None) -> bool | None:
    """Parse the `private` arg into a bool, or return None for invalid values."""
    if private_flag is None:
        return False

    normalized = private_flag.strip().lower()
    if not normalized:
        return None

    if normalized in MATCH_CANDIDATES_PRIVATE_TRUTHY:
        return True

    return None


class MatchResumeSelectView(discord.ui.View):
    """View containing a resume download select for match results."""

    def __init__(self, options: list[tuple[str, str, str]]) -> None:
        super().__init__(timeout=600)  # 10 minute timeout
        self.add_item(MatchResumeSelect(options))


class MatchResumeSelect(discord.ui.Select):
    """Select menu for downloading a resume from match results."""

    def __init__(self, options: list[tuple[str, str, str]]) -> None:
        discord_options: list[discord.SelectOption] = []
        self._resume_lookup: dict[str, str] = {}

        for contact_name, resume_id, resume_name in options[:25]:
            label = contact_name.strip() or "Unknown"
            if len(label) > 100:
                label = label[:97] + "..."
            description = resume_name.strip() or "Resume"
            if len(description) > 100:
                description = description[:97] + "..."
            discord_options.append(
                discord.SelectOption(
                    label=label,
                    value=resume_id,
                    description=description,
                )
            )
            self._resume_lookup[resume_id] = contact_name

        super().__init__(
            placeholder="Download a resume...",
            min_values=1,
            max_values=1,
            options=discord_options,
            custom_id="match_resume_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        jobs_cog = interaction.client.get_cog("JobsCog")  # type: ignore[attr-defined]
        crm_cog = interaction.client.get_cog("CRMCog")  # type: ignore[attr-defined]
        try:
            if jobs_cog is None or not hasattr(jobs_cog, "_audit_command"):
                await interaction.response.send_message(
                    "❌ Job matching functionality not available.",
                    ephemeral=True,
                )
                return
            download_method = getattr(crm_cog, "_download_and_send_resume", None)
            if crm_cog is None or not callable(download_method):
                await interaction.response.send_message(
                    "❌ CRM functionality not available.",
                    ephemeral=True,
                )
                return

            resume_id = self.values[0]
            contact_name = self._resume_lookup.get(resume_id, "Unknown")

            await interaction.response.defer(ephemeral=True)

            download_ok = await download_method(interaction, contact_name, resume_id)
            try:
                jobs_cog._audit_command(
                    interaction=interaction,
                    action="crm.match_candidates_resume_select",
                    result="success" if download_ok else "error",
                    metadata={"contact_name": contact_name},
                    resource_type="crm_contact",
                    resource_id=resume_id,
                )
            except Exception as audit_exc:
                logger.error("Audit write failed in match resume select: %s", audit_exc)
        except Exception as exc:
            logger.error("Unexpected error in match resume select: %s", exc)
            if jobs_cog is not None and hasattr(jobs_cog, "_audit_command"):
                try:
                    jobs_cog._audit_command(
                        interaction=interaction,
                        action="crm.match_candidates_resume_select",
                        result="error",
                        metadata={"error": str(exc)},
                        resource_type="discord_ui_action",
                        resource_id=self.values[0] if self.values else None,
                    )
                except Exception as audit_exc:
                    logger.error(
                        "Audit write failed in match resume select: %s", audit_exc
                    )
            await interaction.followup.send(
                "❌ An unexpected error occurred while downloading the resume.",
                ephemeral=True,
            )


@dataclass(frozen=True)
class ThreadPost:
    """Thread opener content split from forum tags.

    Auto-match and manual `/match-candidates` both need the same starter message,
    but only prepend tag names at the last moment so the raw text stays reusable.
    """

    starter: discord.Message
    tags: list[str]


@dataclass(frozen=True)
class CandidateSearchOutcome:
    """Resolved candidate search result, including any relaxed search note."""

    candidates: list[Any]
    effective_requirements: JobRequirements
    search_note: str | None = None


@dataclass(frozen=True)
class MatchCandidatesHttpResponse:
    """Minimal HTTP response payload for external JD fetches."""

    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()


class JobsCog(DiscordAuditCogMixin, commands.Cog):
    """Job posting and candidate matching workflows."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()
        self._jobs_channels_by_guild: dict[int, set[int]] = {}
        self._jobs_channel_types_by_guild: dict[int, dict[int, JobPostingType]] = {}
        self._auto_matched_thread_ids: OrderedDict[int, None] = OrderedDict()
        self._auto_matched_thread_lock = asyncio.Lock()
        self._startup_sync_done = False
        self._startup_sync_lock = asyncio.Lock()
        self._recruiting_reminder_task: asyncio.Task[None] | None = None

    async def cog_unload(self) -> None:
        """Stop background reminder checks when the cog unloads."""
        if self._recruiting_reminder_task is not None:
            self._recruiting_reminder_task.cancel()

    @staticmethod
    def _resolve_jobs_channel_target(
        interaction: discord.Interaction,
        channel: JobWatchChannel | None,
    ) -> JobWatchChannel | None:
        """Resolve explicit/implicit channel target for job-post registration."""
        if channel is not None:
            return channel

        current = interaction.channel
        if isinstance(current, discord.ForumChannel):
            return current

        if isinstance(current, discord.Thread) and isinstance(
            current.parent, discord.ForumChannel
        ):
            return current.parent

        return None

    async def _refresh_jobs_channel_cache(self, guild_id: int) -> set[int]:
        """Load registered job-post channels for one guild from Postgres."""
        configs = await asyncio.to_thread(
            list_registered_job_post_channel_configs,
            settings,
            guild_id=str(guild_id),
        )
        parsed_ids: set[int] = set()
        channel_types: dict[int, JobPostingType] = {}
        for config in configs:
            try:
                channel_id = int(config.channel_id)
            except ValueError:
                logger.warning(
                    "Skipping invalid job_post_channels row guild_id=%s channel_id=%s",
                    guild_id,
                    config.channel_id,
                )
                continue
            parsed_ids.add(channel_id)
            channel_types[channel_id] = config.posting_type
        self._jobs_channels_by_guild[guild_id] = parsed_ids
        self._jobs_channel_types_by_guild[guild_id] = channel_types
        return parsed_ids

    def _is_jobs_channel_registered(self, guild_id: int, channel_id: int) -> bool:
        """Return whether a channel is registered for automatic job matching."""
        return channel_id in self._jobs_channels_by_guild.get(guild_id, set())

    def _job_posting_type_for_thread(self, thread: discord.Thread) -> JobPostingType:
        """Resolve posting type from thread tags, falling back to registered channel type."""
        guild_id = thread.guild.id if thread.guild else None
        channel_id = thread.parent.id if thread.parent else None
        default = JobPostingType.PART_TIME
        if guild_id is not None and channel_id is not None:
            default = self._jobs_channel_types_by_guild.get(guild_id, {}).get(
                channel_id,
                default,
            )
        applied_tags = getattr(thread, "applied_tags", None) or []
        tag_names = [tag.name for tag in applied_tags]
        return infer_job_posting_type_from_labels(tag_names, default=default)

    async def _refresh_jobs_channel_cache_if_missing(self, guild_id: int) -> bool:
        """Ensure guild cache is loaded, retrying after startup-load failures."""
        if guild_id in self._jobs_channels_by_guild:
            return True
        try:
            await self._refresh_jobs_channel_cache(guild_id)
            return True
        except Exception as exc:
            logger.warning(
                "Failed to refresh jobs-channel cache for guild=%s: %s",
                guild_id,
                exc,
            )
            return False

    async def _mark_thread_auto_matched(self, thread_id: int) -> bool:
        """Deduplicate automatic matching when multiple events race."""
        async with self._auto_matched_thread_lock:
            if thread_id in self._auto_matched_thread_ids:
                self._auto_matched_thread_ids.move_to_end(thread_id)
                return False
            self._auto_matched_thread_ids[thread_id] = None
            if len(self._auto_matched_thread_ids) > AUTO_MATCH_DEDUPE_MAX:
                self._auto_matched_thread_ids.popitem(last=False)
            return True

    async def _unmark_thread_auto_matched(self, thread_id: int) -> None:
        """Allow retry when a thread was marked but processing could not start."""
        async with self._auto_matched_thread_lock:
            self._auto_matched_thread_ids.pop(thread_id, None)

    @staticmethod
    async def _read_thread_post(thread: discord.Thread) -> ThreadPost | None:
        """Read starter message content for a job thread, including forum tags."""
        starter = thread.starter_message
        if starter is None:
            try:
                starter = await thread.fetch_message(thread.id)
            except Exception:
                starter = None

        if starter is None:
            return None

        tags = [t.name for t in thread.applied_tags] if thread.applied_tags else []
        return ThreadPost(starter=starter, tags=tags)

    def _build_job_match_header_and_mentions(
        self,
        *,
        requirements: JobRequirements,
        candidates_count: int,
        guild: discord.Guild | None,
        search_note: str | None = None,
    ) -> tuple[list[str], str | None, list[int], str | None, list[int]]:
        """Build header lines plus discord/locality role mention details."""
        header_parts: list[str] = []
        role_mentions_line: str | None = None
        locality_mentions_line: str | None = None
        role_mentions_role_ids: list[int] = []
        locality_mentions_role_ids: list[int] = []
        excluded_role_names = {
            name.casefold() for name in DISCORD_ROLES_EXCLUDE_FROM_SYNC
        }

        def dedupe_role_names(role_names: list[str]) -> list[str]:
            seen: set[str] = set()
            deduped: list[str] = []
            for role_name in role_names:
                cleaned = role_name.strip()
                if not cleaned:
                    continue
                key = cleaned.casefold()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(cleaned)
            return deduped

        def build_role_mentions(role_names: list[str]) -> tuple[list[str], list[int]]:
            if not role_names:
                return [], []
            if guild is None:
                return [f"`{r}`" for r in role_names], []

            role_id_map = self._get_role_id_cache().get(guild.id)
            if role_id_map is None:
                self._refresh_role_id_cache(guild)
                role_id_map = self._get_role_id_cache().get(guild.id, {})

            mentions: list[str] = []
            seen_mentions: set[str] = set()
            allowed_role_ids: list[int] = []
            seen_role_ids: set[int] = set()
            for role_name in role_names:
                normalized_role_name = role_name.casefold()
                if normalized_role_name in excluded_role_names:
                    continue
                role_id = role_id_map.get(normalized_role_name)
                if role_id is not None:
                    mention = f"<@&{role_id}>"
                    if mention not in seen_mentions:
                        seen_mentions.add(mention)
                        mentions.append(mention)
                    if role_id not in seen_role_ids:
                        seen_role_ids.add(role_id)
                        allowed_role_ids.append(role_id)
                    continue
                role = None
                for guild_role in guild.roles:
                    guild_role_name = guild_role.name.casefold()
                    if guild_role_name in excluded_role_names:
                        continue
                    if guild_role_name == normalized_role_name:
                        role = guild_role
                        break
                if role is not None:
                    if role.mention not in seen_mentions:
                        seen_mentions.add(role.mention)
                        mentions.append(role.mention)
                    if role.id not in seen_role_ids:
                        seen_role_ids.add(role.id)
                        allowed_role_ids.append(role.id)
                else:
                    mention = f"`{role_name}`"
                    if mention not in seen_mentions:
                        seen_mentions.add(mention)
                        mentions.append(mention)
            return mentions, allowed_role_ids

        if requirements.title:
            header_parts.append(f"**{requirements.title}**")
        if requirements.discord_role_types:
            role_types = dedupe_role_names(requirements.discord_role_types)
            if role_types:
                role_mentions, role_ids = build_role_mentions(role_types)
                if role_mentions:
                    role_mentions_line = "Discord roles: " + ", ".join(role_mentions)
                    role_mentions_role_ids = role_ids

        locality_role_names: list[str] = []
        location_text_parts: list[str] = []
        if requirements.raw_location_text:
            location_text_parts.append(requirements.raw_location_text)
        if requirements.preferred_timezones:
            location_text_parts.extend(requirements.preferred_timezones)
        location_text = " ".join(location_text_parts).casefold()

        # Job postings describe location in loose prose, so we infer the broad
        # locality roles from both the normalized location type and the raw text.
        if requirements.location_type == "us_only" or "united states" in location_text:
            locality_role_names.append("USA")
        if "usa" in location_text:
            locality_role_names.append("USA")
        if (
            "europe" in location_text
            or "emea" in location_text
            or "e.u." in location_text
        ):
            locality_role_names.append("Europe")
        if (
            "americas" in location_text
            or "latin america" in location_text
            or "latam" in location_text
        ):
            locality_role_names.append("Americas")
        if "north america" in location_text or "south america" in location_text:
            locality_role_names.append("Americas")
        if (
            "asia" in location_text
            or "apac" in location_text
            or "asia pacific" in location_text
        ):
            locality_role_names.append("Asia")
        if "japan" in location_text:
            locality_role_names.append("Japan")
        if "taiwan" in location_text:
            locality_role_names.append("Taiwan")
        if "africa" in location_text:
            locality_role_names.append("Africa")

        if requirements.preferred_timezones:
            for tz in requirements.preferred_timezones:
                tz_prefix = (
                    tz.split("/", 1)[0].casefold() if "/" in tz else tz.casefold()
                )
                if tz_prefix == "europe":
                    locality_role_names.append("Europe")
                elif tz_prefix == "america":
                    locality_role_names.append("Americas")
                elif tz_prefix == "asia":
                    locality_role_names.append("Asia")
                elif tz_prefix == "africa":
                    locality_role_names.append("Africa")
                if tz.casefold() == "asia/tokyo":
                    locality_role_names.append("Japan")
                if tz.casefold() == "asia/taipei":
                    locality_role_names.append("Taiwan")

        locality_role_names = [
            role_name
            for role_name in dedupe_role_names(locality_role_names)
            if role_name.casefold() not in excluded_role_names
        ]
        if locality_role_names:
            locality_mentions, role_ids = build_role_mentions(locality_role_names)
            if locality_mentions:
                locality_mentions_line = "Locality: " + ", ".join(locality_mentions)
                locality_mentions_role_ids = role_ids

        if requirements.hard_required_skills:
            header_parts.append(
                "Hard needs: "
                + ", ".join(f"`{s}`" for s in requirements.hard_required_skills[:5])
            )
        elif requirements.required_skills:
            header_parts.append(
                "Skills: "
                + ", ".join(f"`{s}`" for s in requirements.required_skills[:8])
            )
        if requirements.hard_required_skills and requirements.soft_required_skills:
            header_parts.append(
                "Other needs: "
                + ", ".join(f"`{s}`" for s in requirements.soft_required_skills[:5])
            )
        if requirements.required_evidence:
            header_parts.append(
                "Evidence: "
                + ", ".join(f"`{item}`" for item in requirements.required_evidence[:3])
            )
        if requirements.seniority:
            header_parts.append(f"Seniority: `{requirements.seniority}`")
        if requirements.location_type == "us_only":
            header_parts.append("📍 US only")
        elif requirements.raw_location_text:
            header_parts.append(f"📍 {requirements.raw_location_text}")

        header_lines: list[str] = ["## Job Match Results"]
        if header_parts:
            header_lines.append(" · ".join(header_parts))
        if search_note:
            header_lines.append(search_note)
        header_lines.append(f"Found **{candidates_count}** candidate(s).")

        return (
            header_lines,
            role_mentions_line,
            role_mentions_role_ids,
            locality_mentions_line,
            locality_mentions_role_ids,
        )

    @staticmethod
    def _paginate_match_lines(lines: list[str]) -> list[str]:
        """Paginate long match output lines into Discord-sized messages."""
        messages: list[str] = []
        current = ""
        for line in lines:
            candidate_block = line + "\n"
            while len(candidate_block) > 1900:
                if current:
                    messages.append(current.rstrip())
                    current = ""
                messages.append(candidate_block[:1900].rstrip())
                candidate_block = candidate_block[1900:]
            if len(current) + len(candidate_block) > 1900:
                if current:
                    messages.append(current.rstrip())
                current = candidate_block
            else:
                current += candidate_block
        if current.strip():
            messages.append(current.rstrip())
        return messages

    @staticmethod
    def _sanitize_match_text(value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        escaped_mentions = discord.utils.escape_mentions(normalized).replace(
            "@", "@\u200b"
        )
        return discord.utils.escape_markdown(escaped_mentions)

    @classmethod
    def _format_match_inline_code(cls, value: str) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        escaped_mentions = discord.utils.escape_mentions(normalized).replace(
            "@", "@\u200b"
        )
        return f"`{escaped_mentions.replace('`', 'ˋ')}`"

    @staticmethod
    def _build_rerank_evidence(candidate: Any) -> list[str]:
        evidence: list[str] = []
        if getattr(candidate, "linkedin", None):
            evidence.append("linkedin profile on file")
        if getattr(candidate, "github_username", None):
            evidence.append("github profile on file")
        if getattr(candidate, "latest_resume_name", None):
            evidence.append("resume on file")
        if getattr(candidate, "matched_discord_roles", None):
            evidence.append("relevant discord role match")
        return evidence

    @staticmethod
    def _build_match_candidate_lines(
        *,
        candidates: list[Any],
        crm_base: str,
    ) -> tuple[list[str], list[tuple[str, str, str]]]:
        """Build candidate result lines and resume options for match output."""
        lines: list[str] = []
        resume_options: list[tuple[str, str, str]] = []

        for i, candidate in enumerate(candidates, start=1):
            label = "**[Member]**" if candidate.is_member else "[Prospect]"
            raw_crm_name = (
                candidate.crm_name.strip()
                if isinstance(candidate.crm_name, str) and candidate.crm_name.strip()
                else None
            )
            raw_display_name = (
                candidate.name.strip()
                if isinstance(candidate.name, str) and candidate.name.strip()
                else None
            )
            resolved_name = discord.utils.escape_mentions(
                raw_crm_name or raw_display_name or "Unknown"
            )
            normalized_discord_username = (
                candidate.discord_username.strip()
                if isinstance(candidate.discord_username, str)
                else None
            )
            discord_username = (
                discord.utils.escape_mentions(normalized_discord_username.lstrip("@"))
                if normalized_discord_username
                and normalized_discord_username.lstrip("@")
                else None
            )
            crm_link = (
                f"{crm_base}/#Contact/view/{candidate.crm_contact_id}"
                if candidate.has_crm_link and candidate.crm_contact_id
                else None
            )
            if crm_link:
                display_name = f"[{resolved_name}](<{crm_link}>)"
            else:
                display_name = resolved_name

            header_parts = [f"{i}. {label} {display_name}"]
            if discord_username:
                header_parts.append(f"`@{discord_username}`")

            if candidate.linkedin:
                header_parts.append(f"[LinkedIn](<{candidate.linkedin}>)")
            if (
                candidate.latest_resume_id
                and candidate.latest_resume_name
                and candidate.latest_resume_name not in AUTO_MATCH_EXCLUDED_RESUME_NAMES
            ):
                safe_resume_name = discord.utils.escape_mentions(
                    candidate.latest_resume_name
                )
                resume_options.append(
                    (resolved_name, candidate.latest_resume_id, safe_resume_name)
                )

            raw_city = (
                candidate.address_city.strip()
                if isinstance(getattr(candidate, "address_city", None), str)
                and candidate.address_city.strip()
                else None
            )
            raw_state = (
                candidate.address_state.strip()
                if isinstance(getattr(candidate, "address_state", None), str)
                and candidate.address_state.strip()
                else None
            )
            raw_country = (
                candidate.address_country.strip()
                if isinstance(getattr(candidate, "address_country", None), str)
                and candidate.address_country.strip()
                else None
            )
            location = ", ".join(
                discord.utils.escape_mentions(part)
                for part in (raw_city, raw_state, raw_country)
                if part
            )
            matched_hard_required_skills = list(
                getattr(candidate, "matched_hard_required_skills", []) or []
            )
            matched_soft_required_skills = list(
                getattr(candidate, "matched_soft_required_skills", []) or []
            )
            matched_required_skills = list(
                getattr(candidate, "matched_required_skills", []) or []
            )
            matched_preferred_skills = list(
                getattr(candidate, "matched_preferred_skills", []) or []
            )
            missing_hard_required_skills = list(
                getattr(candidate, "missing_hard_required_skills", []) or []
            )
            evidence_signals = list(getattr(candidate, "evidence_signals", []) or [])
            llm_risks = list(getattr(candidate, "llm_risks", []) or [])
            llm_missing_requirements = list(
                getattr(candidate, "llm_missing_requirements", []) or []
            )
            skill_info: list[str] = []
            location_info: list[str] = []
            followup_info: list[str] = []
            match_score = getattr(candidate, "match_score", None)
            if isinstance(match_score, (int, float)):
                skill_info.append(f"score: {match_score:.1f}")
            llm_fit_score = getattr(candidate, "llm_fit_score", None)
            if isinstance(llm_fit_score, (int, float)):
                skill_info.append(f"LLM fit: {llm_fit_score:.0f}/100")
            if matched_hard_required_skills:
                skill_info.append(
                    "hard: "
                    + ", ".join(
                        JobsCog._format_match_inline_code(s)
                        for s in matched_hard_required_skills[:4]
                    )
                )
            if matched_soft_required_skills:
                skill_info.append(
                    "soft: "
                    + ", ".join(
                        JobsCog._format_match_inline_code(s)
                        for s in matched_soft_required_skills[:4]
                    )
                )
            elif matched_required_skills and not matched_hard_required_skills:
                skill_info.append(
                    "✅ "
                    + ", ".join(
                        JobsCog._format_match_inline_code(s)
                        for s in matched_required_skills[:5]
                    )
                )
            if matched_preferred_skills:
                skill_info.append(
                    "preferred: "
                    + ", ".join(
                        JobsCog._format_match_inline_code(s)
                        for s in matched_preferred_skills[:3]
                    )
                )
            if candidate.matched_discord_roles:
                skill_info.append(
                    "🏷️ "
                    + ", ".join(
                        JobsCog._format_match_inline_code(r)
                        for r in candidate.matched_discord_roles
                    )
                )
            seniority_label = format_seniority_label(
                getattr(candidate, "seniority", None),
                default=None,
            )
            if seniority_label:
                skill_info.append(f"seniority: **{seniority_label}**")
            if location:
                location_info.append(f"location: **{location}**")
            if candidate.timezone:
                location_info.append(
                    f"tz: {JobsCog._format_match_inline_code(candidate.timezone)}"
                )
            if evidence_signals:
                followup_info.append(
                    "evidence: "
                    + " · ".join(
                        JobsCog._sanitize_match_text(signal)
                        for signal in evidence_signals[:4]
                    )
                )
            llm_summary = getattr(candidate, "llm_summary", None)
            if isinstance(llm_summary, str) and llm_summary.strip():
                followup_info.append(
                    "summary: " + JobsCog._sanitize_match_text(llm_summary)
                )
            missing_items = missing_hard_required_skills + [
                item
                for item in llm_missing_requirements
                if item not in missing_hard_required_skills
            ]
            if missing_items:
                followup_info.append(
                    "missing: "
                    + " · ".join(
                        JobsCog._format_match_inline_code(item)
                        for item in missing_items[:4]
                    )
                )
            elif llm_risks:
                followup_info.append(
                    "risks: "
                    + " · ".join(
                        JobsCog._sanitize_match_text(item) for item in llm_risks[:3]
                    )
                )
            line = " ".join(header_parts)
            if skill_info:
                line += "\n   " + " · ".join(skill_info)
            if location_info:
                line += "\n   " + " · ".join(location_info)
            for detail_line in followup_info:
                line += "\n   " + detail_line

            lines.append(line)
        return lines, resume_options

    @staticmethod
    def _candidate_rerank_id(candidate: Any, index: int) -> str:
        return f"candidate:{index}"

    @classmethod
    def _build_rerank_candidate_payload(
        cls,
        *,
        candidate: Any,
        index: int,
    ) -> dict[str, Any]:
        country = getattr(candidate, "address_country", None)
        return {
            "candidate_id": cls._candidate_rerank_id(candidate, index),
            "is_member": bool(getattr(candidate, "is_member", False)),
            "seniority": getattr(candidate, "seniority", None),
            "country": country.strip()
            if isinstance(country, str) and country.strip()
            else None,
            "timezone": getattr(candidate, "timezone", None),
            "hard_required_matches": list(
                getattr(candidate, "matched_hard_required_skills", []) or []
            ),
            "soft_required_matches": list(
                getattr(candidate, "matched_soft_required_skills", []) or []
            ),
            "preferred_matches": list(
                getattr(candidate, "matched_preferred_skills", []) or []
            ),
            "missing_hard_requirements": list(
                getattr(candidate, "missing_hard_required_skills", []) or []
            ),
            "matched_discord_roles": list(
                getattr(candidate, "matched_discord_roles", []) or []
            ),
            "evidence": cls._build_rerank_evidence(candidate),
            "has_linkedin": bool(getattr(candidate, "linkedin", None)),
            "has_github": bool(getattr(candidate, "github_username", None)),
            "has_resume": bool(getattr(candidate, "latest_resume_name", None)),
            "deterministic_match_score": getattr(candidate, "match_score", None),
        }

    @staticmethod
    def _apply_candidate_overrides(candidate: Any, **overrides: Any) -> Any:
        if is_dataclass(candidate):
            return replace(cast(Any, candidate), **overrides)
        for key, value in overrides.items():
            setattr(candidate, key, value)
        return candidate

    async def _rerank_candidates(
        self,
        *,
        posting: str,
        requirements: JobRequirements,
        candidates: list[Any],
    ) -> list[Any]:
        if len(candidates) < 2 or not settings.openai_api_key:
            return candidates

        shortlist = candidates[:12]
        payloads = [
            self._build_rerank_candidate_payload(candidate=candidate, index=index)
            for index, candidate in enumerate(shortlist, start=1)
        ]

        try:
            rerank_results = await asyncio.to_thread(
                rerank_shortlisted_candidates,
                posting,
                requirements=requirements,
                candidates=payloads,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
                model=settings.openai_model,
            )
        except Exception as exc:
            logger.warning(
                "Candidate rerank failed; keeping deterministic order: %s", exc
            )
            return candidates

        if not rerank_results:
            return candidates

        result_map = {result.candidate_id: result for result in rerank_results}
        result_rank = {
            result.candidate_id: rank for rank, result in enumerate(rerank_results)
        }
        reranked_shortlist: list[tuple[Any, str, int]] = []
        for index, candidate in enumerate(shortlist, start=1):
            candidate_id = self._candidate_rerank_id(candidate, index)
            result = result_map.get(candidate_id)
            if result is None:
                reranked_shortlist.append((candidate, candidate_id, index))
                continue
            reranked_shortlist.append(
                (
                    self._apply_candidate_overrides(
                        candidate,
                        llm_fit_score=result.fit_score,
                        llm_summary=result.summary,
                        llm_risks=result.risks,
                        llm_missing_requirements=result.missing_requirements,
                    ),
                    candidate_id,
                    index,
                )
            )

        reranked_shortlist.sort(
            key=lambda entry: (
                getattr(entry[0], "llm_fit_score", None) is None,
                -(float(getattr(entry[0], "llm_fit_score", 0.0) or 0.0)),
                result_rank.get(entry[1], len(result_rank)),
                -(float(getattr(entry[0], "match_score", 0.0) or 0.0)),
                entry[2],
            )
        )
        return [candidate for candidate, _, _ in reranked_shortlist] + candidates[
            len(shortlist) :
        ]

    @staticmethod
    def _has_match_requirements(requirements: JobRequirements) -> bool:
        return bool(requirements.required_skills or requirements.discord_role_types)

    @staticmethod
    def _build_candidate_search_plan(
        requirements: JobRequirements,
        *,
        min_match_score: float,
    ) -> list[tuple[JobRequirements, float, str | None]]:
        plan: list[tuple[JobRequirements, float, str | None]] = []
        seen: set[
            tuple[
                tuple[str, ...],
                tuple[str, ...],
                tuple[str, ...],
                tuple[str, ...],
                float,
            ]
        ] = set()

        def add_plan_step(
            planned_requirements: JobRequirements,
            planned_min_match_score: float,
            search_note: str | None,
        ) -> None:
            key = (
                tuple(planned_requirements.hard_required_skills),
                tuple(planned_requirements.soft_required_skills),
                tuple(planned_requirements.required_skills),
                tuple(planned_requirements.discord_role_types),
                planned_min_match_score,
            )
            if key in seen:
                return
            seen.add(key)
            plan.append((planned_requirements, planned_min_match_score, search_note))

        add_plan_step(requirements, min_match_score, None)

        required_language_keys = {
            skill.casefold() for skill in requirements.required_languages
        }
        language_hard_skills = [
            skill
            for skill in requirements.hard_required_skills
            if skill.casefold() in required_language_keys
            or is_language_requirement_skill(skill)
        ]
        seen_language_keys = {skill.casefold() for skill in language_hard_skills}
        language_hard_skills.extend(
            skill
            for skill in requirements.required_languages
            if skill.casefold() not in seen_language_keys
        )
        language_hard_keys = {skill.casefold() for skill in language_hard_skills}
        non_language_hard_skills = [
            skill
            for skill in requirements.hard_required_skills
            if skill.casefold() not in language_hard_keys
        ]

        if len(requirements.hard_required_skills) > 1:
            anchor_hard_skills = language_hard_skills[:]
            if non_language_hard_skills:
                anchor_hard_skills.append(non_language_hard_skills[0])
            if not anchor_hard_skills:
                anchor_hard_skills = requirements.hard_required_skills[:1]
            anchor_skill = anchor_hard_skills[-1]
            demoted_hard_skills = [
                skill
                for skill in requirements.hard_required_skills
                if skill.casefold()
                not in {item.casefold() for item in anchor_hard_skills}
            ]
            add_plan_step(
                replace(
                    requirements,
                    hard_required_skills=anchor_hard_skills,
                    soft_required_skills=demoted_hard_skills
                    + requirements.soft_required_skills,
                ),
                min_match_score,
                "Search note: no strict full-match candidates, so only anchor hard "
                f"need `{anchor_skill}` stayed mandatory.",
            )

        if requirements.required_skills:
            fallback_hard_skills = language_hard_skills
            fallback_hard_keys = {skill.casefold() for skill in fallback_hard_skills}
            fallback_soft_skills = [
                skill
                for skill in requirements.required_skills
                if skill.casefold() not in fallback_hard_keys
            ]
            fallback_note = (
                "Search note: still no strict matches, so matching was broadened to "
                "any relevant skill while keeping required language gates mandatory."
                if fallback_hard_skills
                else "Search note: still no strict matches, so matching was broadened "
                "to any relevant required skill."
            )
            add_plan_step(
                replace(
                    requirements,
                    hard_required_skills=fallback_hard_skills,
                    soft_required_skills=fallback_soft_skills,
                ),
                0.0,
                fallback_note,
            )
        elif requirements.discord_role_types and min_match_score > 0:
            add_plan_step(
                replace(
                    requirements,
                    required_skills=[],
                    hard_required_skills=[],
                    soft_required_skills=[],
                ),
                0.0,
                "Search note: still no strict matches, so matching was broadened to "
                "role-based signals.",
            )

        return plan

    async def _search_and_rerank_candidates(
        self,
        *,
        posting: str,
        requirements: JobRequirements,
        guild_id: str | None,
        limit: int,
        min_match_score: float = 0.0,
    ) -> CandidateSearchOutcome:
        search_plan = self._build_candidate_search_plan(
            requirements,
            min_match_score=min_match_score,
        )
        last_requirements = requirements
        last_search_note: str | None = None

        for (
            planned_requirements,
            planned_min_match_score,
            search_note,
        ) in search_plan:
            candidates = await asyncio.to_thread(
                search_candidates,
                settings,
                planned_requirements,
                guild_id=guild_id,
                limit=limit,
                min_match_score=planned_min_match_score,
            )
            last_requirements = planned_requirements
            last_search_note = search_note
            if not candidates:
                continue

            reranked_candidates = await self._rerank_candidates(
                posting=posting,
                requirements=planned_requirements,
                candidates=candidates,
            )
            return CandidateSearchOutcome(
                candidates=reranked_candidates,
                effective_requirements=planned_requirements,
                search_note=search_note,
            )

        return CandidateSearchOutcome(
            candidates=[],
            effective_requirements=last_requirements,
            search_note=last_search_note,
        )

    @staticmethod
    def _resume_file_extension(filename: str | None) -> str:
        return document_file_extension(filename)

    def _extract_resume_text(
        self,
        file_content: bytes,
        *,
        filename: str | None,
    ) -> str:
        extension = self._resume_file_extension(filename)
        extracted_text = ""

        try:
            extracted_text = extract_document_text(file_content, filename=filename)
        except Exception as exc:
            logger.warning(
                "Failed to extract resume text filename=%s extension=%s error=%s",
                filename,
                extension,
                exc,
            )

        if extracted_text:
            return extracted_text
        return file_content.decode("utf-8", errors="ignore")

    @staticmethod
    def _extract_urls_from_text(text: str) -> list[str]:
        if not text:
            return []
        urls: list[str] = []
        seen: set[str] = set()
        for raw in MATCH_CANDIDATES_URL_PATTERN.findall(text):
            normalized = raw.rstrip(".,);]>")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            urls.append(normalized)
        return urls

    @staticmethod
    def _is_probable_jd_url(url: str) -> bool:
        lowered = url.casefold()
        return any(hint in lowered for hint in MATCH_CANDIDATES_JD_URL_HINTS)

    @staticmethod
    def _strip_html_to_text(raw_html: str) -> str:
        without_scripts = re.sub(
            r"(?is)<(script|style|noscript).*?>.*?</\1>",
            " ",
            raw_html,
        )
        text_only = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
        unescaped = html.unescape(text_only)
        return re.sub(r"\s+", " ", unescaped).strip()

    @staticmethod
    def _json_ld_nodes(value: Any) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        if isinstance(value, dict):
            nodes.append(value)
            for nested in value.values():
                nodes.extend(JobsCog._json_ld_nodes(nested))
        elif isinstance(value, list):
            for item in value:
                nodes.extend(JobsCog._json_ld_nodes(item))
        return nodes

    @staticmethod
    def _json_ld_type_names(value: Any) -> set[str]:
        if isinstance(value, str):
            return {value.casefold()}
        if isinstance(value, list):
            return {
                item.casefold()
                for item in value
                if isinstance(item, str) and item.strip()
            }
        return set()

    @staticmethod
    def _extract_job_posting_location(value: Any) -> str | None:
        locations: list[str] = []
        seen: set[str] = set()

        def add_location(raw: str | None) -> None:
            if not isinstance(raw, str):
                return
            cleaned = re.sub(r"\s+", " ", raw).strip(" ,;/")
            key = cleaned.casefold()
            if not cleaned or key in seen:
                return
            seen.add(key)
            locations.append(cleaned)

        def walk(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, dict):
                return

            address = node.get("address")
            if isinstance(address, dict):
                parts = [
                    address.get("addressLocality"),
                    address.get("addressRegion"),
                    address.get("addressCountry"),
                ]
                location_text = ", ".join(
                    part.strip()
                    for part in parts
                    if isinstance(part, str) and part.strip()
                )
                add_location(location_text)
            name = node.get("name")
            if isinstance(name, str):
                add_location(name)
            if "jobLocation" in node:
                walk(node.get("jobLocation"))

        walk(value)
        if not locations:
            return None
        return " | ".join(locations)

    @classmethod
    def _extract_structured_job_posting_text(cls, raw_html: str) -> str | None:
        for match in re.finditer(
            r'(?is)<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            raw_html,
        ):
            raw_json = match.group(1).strip()
            if not raw_json:
                continue
            try:
                payload = json.loads(html.unescape(raw_json))
            except json.JSONDecodeError:
                continue

            for node in cls._json_ld_nodes(payload):
                if "jobposting" not in cls._json_ld_type_names(node.get("@type")):
                    continue

                sections: list[str] = []
                title = node.get("title")
                if isinstance(title, str) and title.strip():
                    sections.append(title.strip())

                header_parts: list[str] = []
                location_text = cls._extract_job_posting_location(
                    node.get("jobLocation")
                )
                if location_text:
                    header_parts.append(location_text)
                employment_type = node.get("employmentType")
                if isinstance(employment_type, str) and employment_type.strip():
                    header_parts.append(employment_type.strip())
                if header_parts:
                    sections.append(" | ".join(header_parts))

                description = node.get("description")
                if isinstance(description, str):
                    description_text = cls._strip_html_to_text(description)
                    if description_text:
                        sections.append(description_text)

                structured_text = "\n\n".join(
                    section for section in sections if section
                )
                if structured_text.strip():
                    return structured_text.strip()
        return None

    @staticmethod
    def _parse_ip_literal(value: str) -> IPAddress | None:
        try:
            return ipaddress.ip_address(value)
        except ValueError:
            return None

    @staticmethod
    def _is_public_ip(value: IPAddress) -> bool:
        return not (
            value.is_private
            or value.is_loopback
            or value.is_link_local
            or value.is_multicast
            or value.is_reserved
            or value.is_unspecified
        )

    @classmethod
    def _hostname_resolves_publicly(cls, host: str) -> bool:
        if host in {"localhost", "localhost.localdomain"}:
            return False

        ip_literal = cls._parse_ip_literal(host)
        if ip_literal is not None:
            return cls._is_public_ip(ip_literal)

        try:
            addr_infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return False
        except Exception:
            return False

        resolved_ips: set[IPAddress] = set()
        for _, _, _, _, sockaddr in addr_infos:
            if not sockaddr:
                continue
            ip_text = str(sockaddr[0]).strip()
            parsed_ip = cls._parse_ip_literal(ip_text)
            if parsed_ip is None:
                continue
            resolved_ips.add(parsed_ip)

        if not resolved_ips:
            return False
        return all(cls._is_public_ip(parsed_ip) for parsed_ip in resolved_ips)

    @classmethod
    def _validate_match_candidates_url_sync(cls, candidate_url: str) -> str | None:
        resolution = cls._resolve_match_candidates_request_target(
            candidate_url,
            require_https=True,
        )
        if isinstance(resolution, str):
            return resolution
        return None

    async def _validate_match_candidates_url(self, candidate_url: str) -> str | None:
        return await asyncio.to_thread(
            self._validate_match_candidates_url_sync, candidate_url
        )

    async def _read_match_candidates_attachment_text(
        self, attachment: discord.Attachment
    ) -> str | None:
        filename = attachment.filename or ""
        extension = self._resume_file_extension(filename)
        content_type = (attachment.content_type or "").strip().lower()
        is_supported_type = (
            extension in MATCH_CANDIDATES_SUPPORTED_ATTACHMENT_EXTENSIONS
            or content_type.startswith("text/")
        )
        if not is_supported_type:
            return None
        if attachment.size and attachment.size > MATCH_CANDIDATES_MAX_ATTACHMENT_BYTES:
            logger.info(
                "Skipping oversized match-candidates attachment filename=%s size=%s",
                filename,
                attachment.size,
            )
            return None

        try:
            file_content = await attachment.read()
        except Exception as exc:
            logger.warning(
                "Failed reading match-candidates attachment filename=%s error=%s",
                filename,
                exc,
            )
            return None

        extracted = self._extract_resume_text(file_content, filename=filename).strip()
        if not extracted:
            return None
        if len(extracted) > MATCH_CANDIDATES_MAX_ATTACHMENT_TEXT_CHARS:
            return (
                extracted[:MATCH_CANDIDATES_MAX_ATTACHMENT_TEXT_CHARS].rstrip()
                + "\n[attachment text truncated]"
            )
        return extracted

    @staticmethod
    def _match_candidates_html_looks_blocked(raw_html: str) -> bool:
        return any(
            pattern.search(raw_html) for pattern in MATCH_CANDIDATES_BLOCKED_PATTERNS
        )

    @staticmethod
    def _match_candidates_body_looks_like_html(raw_text: str) -> bool:
        normalized = raw_text.casefold()
        if any(
            marker in normalized
            for marker in ("<!doctype html", "<html", "<body", "<head")
        ):
            return True
        return any(
            marker in normalized for marker in MATCH_CANDIDATES_JS_RENDER_MARKERS
        )

    @classmethod
    def _match_candidates_response_looks_blocked(
        cls, response: MatchCandidatesHttpResponse
    ) -> bool:
        if response.status_code in MATCH_CANDIDATES_BLOCKED_STATUS_CODES:
            return True
        return cls._match_candidates_html_looks_blocked(
            response.body.decode("utf-8", errors="ignore")
        )

    @staticmethod
    def _match_candidates_html_needs_browser(
        raw_html: str, extracted_text: str
    ) -> bool:
        if not raw_html:
            return False
        normalized_html = raw_html.casefold()
        if any(
            marker in normalized_html for marker in MATCH_CANDIDATES_JS_RENDER_MARKERS
        ):
            stripped_text = extracted_text.strip()
            word_count = len(re.findall(r"\w+", stripped_text))
            if len(stripped_text) <= MATCH_CANDIDATES_JS_SPARSE_TEXT_MAX_CHARS and (
                word_count <= MATCH_CANDIDATES_JS_SPARSE_TEXT_MAX_WORDS
            ):
                return True
        return False

    @staticmethod
    def _clean_match_candidates_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) > MATCH_CANDIDATES_MAX_LINK_TEXT_CHARS:
            return cleaned[:MATCH_CANDIDATES_MAX_LINK_TEXT_CHARS].rstrip()
        return cleaned

    @classmethod
    def _create_match_candidates_http_session(cls) -> curl_requests.Session:
        return curl_requests.Session(
            headers={"User-Agent": MATCH_CANDIDATES_FETCH_USER_AGENT},
            impersonate=MATCH_CANDIDATES_FETCH_IMPERSONATE,
        )

    @staticmethod
    def _configure_match_candidates_http_session(
        session: curl_requests.Session,
        *,
        resolve_entries: list[str] | None,
    ) -> None:
        if resolve_entries:
            session.curl_options = {CurlOpt.RESOLVE: resolve_entries}
            return
        session.curl_options = {}

    @staticmethod
    def _read_match_candidates_http_response_body(response: Any) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                content_len_bytes = int(content_length)
            except (TypeError, ValueError):
                content_len_bytes = None
            if (
                content_len_bytes is not None
                and content_len_bytes > MATCH_CANDIDATES_MAX_LINK_BYTES
            ):
                raise ValueError(f"Oversized JD link body ({content_len_bytes} bytes)")

        payload = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            payload.extend(chunk)
            if len(payload) > MATCH_CANDIDATES_MAX_LINK_BYTES:
                raise ValueError("Oversized JD link body")
        return bytes(payload)

    @classmethod
    def _resolve_match_candidates_request_target(
        cls,
        candidate_url: str,
        *,
        require_https: bool,
    ) -> tuple[str, int, list[IPAddress], bool] | str:
        try:
            parsed = urlsplit(candidate_url)
        except Exception:
            return "Job description URL is invalid."

        scheme = parsed.scheme.lower()
        if require_https:
            if scheme != "https":
                return "Job description URL must use https."
        elif scheme not in {"http", "https"}:
            return f"Job description request URL scheme '{scheme}' is not allowed."

        if parsed.username or parsed.password:
            return "Job description URL must not include credentials."

        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return "Job description URL must include a hostname."

        try:
            port = parsed.port
        except ValueError:
            return "Job description URL port is invalid."
        if port is None:
            port = 443 if scheme == "https" else 80
        if port not in {80, 443}:
            return "Job description URL port must be 80 or 443."

        if host in {"localhost", "localhost.localdomain"}:
            return "Job description URL host resolves to a non-public address."

        ip_literal = cls._parse_ip_literal(host)
        if ip_literal is not None:
            if not cls._is_public_ip(ip_literal):
                return "Job description URL host resolves to a non-public address."
            return host, port, [ip_literal], True

        try:
            addr_infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return "Job description URL host resolves to a non-public address."
        except Exception:
            return "Job description URL host resolves to a non-public address."

        resolved_ips: set[IPAddress] = set()
        for _, _, _, _, sockaddr in addr_infos:
            if not sockaddr:
                continue
            parsed_ip = cls._parse_ip_literal(str(sockaddr[0]).strip())
            if parsed_ip is None:
                continue
            if not cls._is_public_ip(parsed_ip):
                return "Job description URL host resolves to a non-public address."
            resolved_ips.add(parsed_ip)

        if not resolved_ips:
            return "Job description URL host resolves to a non-public address."

        ordered_ips = sorted(resolved_ips, key=lambda parsed_ip: parsed_ip.compressed)
        return host, port, ordered_ips, False

    def _fetch_match_candidates_link_response_sync(
        self, url: str
    ) -> MatchCandidatesHttpResponse:
        current_url = url
        session = self._create_match_candidates_http_session()
        try:
            for _ in range(MATCH_CANDIDATES_MAX_LINK_REDIRECTS + 1):
                resolution = self._resolve_match_candidates_request_target(
                    current_url,
                    require_https=True,
                )
                if isinstance(resolution, str):
                    raise ValueError(resolution)
                host, port, resolved_ips, host_is_ip_literal = resolution

                try:
                    resolve_entries = None
                    if not host_is_ip_literal:
                        resolve_entries = [
                            f"{host}:{port}:{_format_curl_resolve_address(ip)}"
                            for ip in resolved_ips
                        ]
                    self._configure_match_candidates_http_session(
                        session,
                        resolve_entries=resolve_entries,
                    )
                    response = session.request(
                        "GET",
                        current_url,
                        timeout=MATCH_CANDIDATES_FETCH_TIMEOUT_SECONDS,
                        allow_redirects=False,
                        stream=True,
                    )
                except RequestsError as exc:
                    raise ValueError(f"JD fetch failed: {exc}") from exc

                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        redirect_to = response.headers.get("Location")
                        if not redirect_to:
                            raise ValueError("JD redirect missing Location header")
                        current_url = urljoin(current_url, redirect_to)
                        continue

                    headers = {
                        str(key).lower(): str(value)
                        for key, value in response.headers.items()
                    }
                    body = self._read_match_candidates_http_response_body(response)
                    return MatchCandidatesHttpResponse(
                        final_url=current_url,
                        status_code=int(response.status_code),
                        headers=headers,
                        body=body,
                    )
                finally:
                    response.close()
        finally:
            session.close()

        raise ValueError("JD URL exceeded redirect limit")

    def _extract_match_candidates_response_text(
        self, response: MatchCandidatesHttpResponse
    ) -> str | None:
        if not response.body:
            return None

        content_type = response.content_type
        decoded_body = response.body.decode("utf-8", errors="ignore")
        body_looks_like_html = self._match_candidates_body_looks_like_html(decoded_body)
        is_supported_content_type = content_type in {
            "application/pdf",
            "application/xhtml+xml",
        } or content_type.startswith("text/")
        if content_type and not is_supported_content_type and not body_looks_like_html:
            logger.info(
                "Skipping unsupported JD content type url=%s content_type=%s",
                response.final_url,
                content_type,
            )
            return None

        lower_final = response.final_url.casefold()
        if content_type == "application/pdf" or lower_final.endswith(".pdf"):
            text = self._extract_resume_text(response.body, filename="linked_jd.pdf")
        elif content_type in {"text/plain", "text/markdown"}:
            text = decoded_body
        else:
            text = self._extract_structured_job_posting_text(
                decoded_body
            ) or self._strip_html_to_text(decoded_body)

        cleaned = self._clean_match_candidates_text(text)
        return cleaned or None

    def _fetch_match_candidates_link_text_with_browser_sync(
        self, url: str
    ) -> str | None:
        try:
            from cloakbrowser import launch_context  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ValueError("JavaScript browser fallback is unavailable") from exc

        validation_error = self._validate_match_candidates_url_sync(url)
        if validation_error:
            raise ValueError(validation_error)

        context = None
        request_validation_cache: dict[tuple[str, int], str | None] = {}
        try:
            context = launch_context(
                user_agent=MATCH_CANDIDATES_FETCH_USER_AGENT,
                viewport={"width": 412, "height": 915},
                is_mobile=True,
                has_touch=True,
                device_scale_factor=2.625,
            )
            page = context.new_page()
            page.route(
                "**/*",
                lambda route: self._handle_match_candidates_browser_route(
                    route,
                    request_validation_cache=request_validation_cache,
                ),
            )
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=MATCH_CANDIDATES_BROWSER_TIMEOUT_MS,
            )
            page.wait_for_timeout(MATCH_CANDIDATES_BROWSER_POST_NAV_WAIT_MS)
            final_page_url = str(getattr(page, "url", "")).strip()
            validation_error = self._validate_match_candidates_url_sync(final_page_url)
            if validation_error:
                raise ValueError(validation_error)

            rendered_html = page.content()
            text = self._extract_structured_job_posting_text(
                rendered_html
            ) or self._strip_html_to_text(rendered_html)
            cleaned = self._clean_match_candidates_text(text)
            if not cleaned:
                return None

            word_count = len(re.findall(r"\w+", cleaned))
            if self._match_candidates_html_looks_blocked(rendered_html) and (
                len(cleaned) <= MATCH_CANDIDATES_JS_SPARSE_TEXT_MAX_CHARS
                and word_count <= MATCH_CANDIDATES_JS_SPARSE_TEXT_MAX_WORDS
            ):
                raise ValueError("JavaScript job-page fetch hit a scraping block")
            return cleaned
        except Exception as exc:
            raise ValueError(f"JavaScript JD fetch failed: {exc}") from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    logger.debug("Failed to close CloakBrowser for %s", url)

    def _validate_match_candidates_browser_request_url_sync(
        self,
        candidate_url: str,
        *,
        request_validation_cache: dict[tuple[str, int], str | None],
    ) -> str | None:
        parsed = urlsplit(candidate_url)
        scheme = parsed.scheme.lower()
        if not scheme:
            return "Job description request URL must specify a scheme."
        if scheme in {"data", "about", "blob"}:
            return None
        if scheme not in {"http", "https"}:
            return f"Job description request URL scheme '{scheme}' is not allowed."

        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return "Job description URL must include a hostname."

        try:
            port = parsed.port
        except ValueError:
            return "Job description URL port is invalid."
        if port is None:
            port = 443 if scheme == "https" else 80

        cache_key = (host, port)
        if cache_key in request_validation_cache:
            return request_validation_cache[cache_key]

        resolution = self._resolve_match_candidates_request_target(
            candidate_url,
            require_https=False,
        )
        error = resolution if isinstance(resolution, str) else None
        request_validation_cache[cache_key] = error
        return error

    def _handle_match_candidates_browser_route(
        self,
        route: Any,
        *,
        request_validation_cache: dict[tuple[str, int], str | None],
    ) -> None:
        request = route.request
        request_url = str(getattr(request, "url", "")).strip()
        scheme = urlsplit(request_url).scheme.lower()
        if scheme in {"data", "about", "blob"}:
            route.continue_()
            return

        validation_error = self._validate_match_candidates_browser_request_url_sync(
            request_url,
            request_validation_cache=request_validation_cache,
        )
        if validation_error:
            logger.info(
                "Blocked match-candidates browser request url=%s error=%s",
                request_url,
                validation_error,
            )
            route.abort()
            return
        route.continue_()

    async def _fetch_match_candidates_link_text(self, url: str) -> str | None:
        try:
            response = await asyncio.to_thread(
                self._fetch_match_candidates_link_response_sync, url
            )
        except Exception as exc:
            logger.info("Failed fetching JD link url=%s error=%s", url, exc)
            return None

        if (
            response.status_code >= 400
            and not self._match_candidates_response_looks_blocked(response)
        ):
            return None

        text = self._extract_match_candidates_response_text(response)
        decoded_body = response.body.decode("utf-8", errors="ignore")
        raw_html = (
            decoded_body
            if response.content_type in {"text/html", "application/xhtml+xml"}
            or response.content_type.startswith("text/")
            or self._match_candidates_body_looks_like_html(decoded_body)
            else ""
        )
        needs_browser_fallback = self._match_candidates_response_looks_blocked(
            response
        ) or (
            bool(raw_html)
            and self._match_candidates_html_needs_browser(raw_html, text or "")
        )

        if needs_browser_fallback:
            try:
                browser_text = await asyncio.to_thread(
                    self._fetch_match_candidates_link_text_with_browser_sync,
                    response.final_url,
                )
            except Exception as exc:
                logger.info(
                    "JD browser fallback failed url=%s error=%s",
                    response.final_url,
                    exc,
                )
            else:
                if browser_text:
                    return browser_text

        return text

    async def _build_match_candidates_posting(
        self, starter: discord.Message
    ) -> tuple[str, dict[str, Any]]:
        base_text = starter.content.strip()
        attachment_chunks: list[str] = []
        attachment_urls: list[str] = []
        scanned_attachments = 0

        # We cap attachment and link scanning so one noisy job post does not turn
        # into an unbounded amount of file parsing, network fetches, or LLM input.
        for attachment in starter.attachments[:MATCH_CANDIDATES_MAX_ATTACHMENT_SCAN]:
            scanned_attachments += 1
            extracted = await self._read_match_candidates_attachment_text(attachment)
            if not extracted:
                continue
            display_name = attachment.filename or "attachment"
            attachment_chunks.append(f"Attachment {display_name}:\n{extracted}")
            attachment_urls.extend(self._extract_urls_from_text(extracted))

        candidate_urls: list[str] = []
        candidate_urls.extend(self._extract_urls_from_text(base_text))
        candidate_urls.extend(attachment_urls)
        for embed in starter.embeds:
            if embed.url:
                candidate_urls.append(embed.url)

        deduped_urls: list[str] = []
        seen_urls: set[str] = set()
        for raw_url in candidate_urls:
            parsed = urlsplit(raw_url)
            if not parsed.scheme or not parsed.netloc:
                continue
            normalized = raw_url.strip()
            if not normalized or normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            deduped_urls.append(normalized)

        likely_jd_urls = [u for u in deduped_urls if self._is_probable_jd_url(u)]
        urls_to_fetch = likely_jd_urls[:MATCH_CANDIDATES_MAX_LINK_SCAN]

        fetched_link_chunks: list[str] = []
        fetched_links: list[str] = []
        for url in urls_to_fetch:
            link_text = await self._fetch_match_candidates_link_text(url)
            if not link_text:
                continue
            fetched_links.append(url)
            fetched_link_chunks.append(f"Source {url}:\n{link_text}")

        sections: list[str] = []
        if base_text:
            sections.append(base_text)
        if attachment_chunks:
            sections.append(
                "Attached job description documents (extracted text):\n\n"
                + "\n\n".join(attachment_chunks)
            )
        if deduped_urls:
            sections.append("Referenced links:\n" + "\n".join(deduped_urls))
        if fetched_link_chunks:
            sections.append(
                "Referenced job description pages (extracted text):\n\n"
                + "\n\n".join(fetched_link_chunks)
            )

        posting = "\n\n".join(part for part in sections if part.strip()).strip()
        if len(posting) > MATCH_CANDIDATES_MAX_POSTING_CHARS:
            posting = (
                posting[:MATCH_CANDIDATES_MAX_POSTING_CHARS].rstrip() + "\n[truncated]"
            )

        metadata = {
            "starter_has_text": bool(base_text),
            "attachments_seen": len(starter.attachments),
            "attachments_scanned": scanned_attachments,
            "attachments_extracted": len(attachment_chunks),
            "links_discovered": len(deduped_urls),
            "links_fetched": len(fetched_links),
        }
        return posting, metadata

    async def _publish_match_results(
        self,
        *,
        send: Callable[..., Awaitable[Any]],
        requirements: JobRequirements,
        candidates: list[Any],
        guild: discord.Guild | None,
        search_note: str | None = None,
    ) -> None:
        """Send the formatted match output for both manual and automatic runs."""
        (
            header_lines,
            role_mentions_line,
            role_mentions_role_ids,
            locality_mentions_line,
            locality_mentions_role_ids,
        ) = self._build_job_match_header_and_mentions(
            requirements=requirements,
            candidates_count=len(candidates),
            guild=guild,
            search_note=search_note,
        )

        safe_mentions = discord.AllowedMentions(
            roles=False,
            users=False,
            everyone=False,
        )
        for chunk in self._paginate_match_lines(header_lines):
            await send(chunk, allowed_mentions=safe_mentions)
        if role_mentions_line:
            allowed_role_mentions = (
                discord.AllowedMentions(
                    roles=[discord.Object(id=rid) for rid in role_mentions_role_ids],
                    users=False,
                    everyone=False,
                )
                if role_mentions_role_ids
                else safe_mentions
            )
            for chunk in self._paginate_match_lines([role_mentions_line]):
                await send(chunk, allowed_mentions=allowed_role_mentions)
        if locality_mentions_line:
            allowed_locality_mentions = (
                discord.AllowedMentions(
                    roles=[
                        discord.Object(id=rid) for rid in locality_mentions_role_ids
                    ],
                    users=False,
                    everyone=False,
                )
                if locality_mentions_role_ids
                else safe_mentions
            )
            for chunk in self._paginate_match_lines([locality_mentions_line]):
                await send(chunk, allowed_mentions=allowed_locality_mentions)

        crm_base = settings.espo_base_url.rstrip("/")
        lines, resume_options = self._build_match_candidate_lines(
            candidates=candidates,
            crm_base=crm_base,
        )
        for msg in self._paginate_match_lines(lines):
            await send(msg, allowed_mentions=safe_mentions)
        if resume_options:
            await send(
                "Resume download:",
                view=MatchResumeSelectView(resume_options),
            )

    async def _persist_thread_engagement_match(
        self,
        *,
        thread: discord.Thread,
        starter: discord.Message,
        posting: str,
        requirements: JobRequirements,
        candidates: list[Any],
        actor_discord_user_id: str | None,
        source: str,
    ) -> None:
        """Best-effort persistence for dashboard gig/candidate visibility."""
        try:
            thread_name = str(getattr(thread, "name", "") or "")
            starter_id = getattr(starter, "id", None) or thread.id
            status = parse_status_from_title(thread_name)
            title = (
                requirements.title
                or strip_status_from_title(thread_name)
                or thread_name
            )
            if not title:
                title = f"Discord gig {thread.id}"
            engagement_id = await asyncio.to_thread(
                upsert_discord_engagement,
                settings,
                DiscordEngagementInput(
                    guild_id=str(thread.guild.id) if thread.guild else None,
                    channel_id=str(thread.parent.id) if thread.parent else None,
                    channel_name=getattr(thread.parent, "name", None),
                    posting_type=self._job_posting_type_for_thread(thread).value,
                    message_id=str(starter_id),
                    thread_id=str(thread.id),
                    posted_by_discord_user_id=(
                        str(thread.owner_id) if thread.owner_id else None
                    ),
                    title=title,
                    body_raw=starter.content or None,
                    body_normalized=posting,
                    posted_at=getattr(starter, "created_at", None),
                    status=status,
                    required_skills=requirements.required_skills,
                    preferred_skills=requirements.preferred_skills,
                    requirements=requirements_to_payload(requirements),
                    preserve_existing_status=True,
                ),
            )
            saved = await asyncio.to_thread(
                upsert_suggested_applications,
                settings,
                engagement_id=engagement_id,
                candidates=candidates,
                source=EngagementApplicationSource.MATCH_CANDIDATES.value,
            )
            await asyncio.to_thread(
                add_engagement_event,
                settings,
                engagement_id=engagement_id,
                event_type="candidate_match_saved",
                actor_discord_user_id=actor_discord_user_id,
                payload={"source": source, "candidates_saved": saved},
            )
        except Exception as exc:
            logger.warning(
                "Failed persisting engagement match thread_id=%s source=%s: %s",
                thread.id,
                source,
                exc,
            )

    async def _persist_thread_engagement_index(
        self,
        thread: discord.Thread,
        *,
        source: str,
    ) -> bool:
        """Persist basic dashboard gig metadata for an existing forum thread."""
        try:
            post = await self._read_thread_post(thread)
            if post is None:
                return False

            thread_name = str(getattr(thread, "name", "") or "")
            starter_id = getattr(post.starter, "id", None) or thread.id
            body = post.starter.content or None
            title = strip_status_from_title(thread_name) or thread_name
            if not title:
                title = f"Discord gig {thread.id}"
            engagement_id = await asyncio.to_thread(
                upsert_discord_engagement,
                settings,
                DiscordEngagementInput(
                    guild_id=str(thread.guild.id) if thread.guild else None,
                    channel_id=str(thread.parent.id) if thread.parent else None,
                    channel_name=getattr(thread.parent, "name", None),
                    posting_type=self._job_posting_type_for_thread(thread).value,
                    message_id=str(starter_id),
                    thread_id=str(thread.id),
                    posted_by_discord_user_id=(
                        str(thread.owner_id) if thread.owner_id else None
                    ),
                    title=title,
                    body_raw=body,
                    body_normalized=body,
                    posted_at=getattr(post.starter, "created_at", None),
                    status=parse_status_from_title(thread_name),
                    preserve_existing_status=True,
                    refresh_activity=False,
                ),
            )
            await asyncio.to_thread(
                add_engagement_event,
                settings,
                engagement_id=engagement_id,
                event_type="gig_thread_indexed",
                payload={"source": source},
            )
            return True
        except Exception as exc:
            logger.warning(
                "Failed indexing gig thread channel=%s thread=%s source=%s: %s",
                getattr(getattr(thread, "parent", None), "id", "unknown"),
                getattr(thread, "id", "unknown"),
                source,
                exc,
            )
            return False

    async def _sync_job_forum_channel(
        self,
        channel: discord.ForumChannel,
        *,
        source: str,
    ) -> tuple[int, int]:
        """Backfill dashboard gig rows from active and recently archived forum posts."""
        seen_thread_ids: set[int] = set()
        indexed = 0
        failed = 0

        async def handle_thread(thread: discord.Thread) -> None:
            nonlocal indexed, failed
            if thread.id in seen_thread_ids:
                return
            seen_thread_ids.add(thread.id)
            if await self._persist_thread_engagement_index(thread, source=source):
                indexed += 1
            else:
                failed += 1

        for thread in getattr(channel, "threads", []) or []:
            await handle_thread(thread)

        try:
            async for thread in channel.archived_threads(
                limit=GIG_FORUM_BACKFILL_ARCHIVED_LIMIT
            ):
                await handle_thread(thread)
        except Exception as exc:
            logger.warning(
                "Failed reading archived job forum threads channel=%s: %s",
                channel.id,
                exc,
            )

        return indexed, failed

    async def _sync_registered_job_forum_channels(
        self,
        guild: discord.Guild,
        channel_ids: set[int],
        *,
        source: str,
    ) -> tuple[int, int]:
        """Backfill all registered job forums for one guild."""
        indexed = 0
        failed = 0
        for channel_id in channel_ids:
            channel_obj: object = guild.get_channel(channel_id)
            if channel_obj is None:
                try:
                    channel_obj = await self.bot.fetch_channel(channel_id)
                except Exception as exc:
                    logger.warning(
                        "Failed fetching registered jobs channel guild=%s channel=%s: %s",
                        guild.id,
                        channel_id,
                        exc,
                    )
                    continue
            if not isinstance(channel_obj, discord.ForumChannel):
                logger.warning(
                    "Skipping registered jobs channel that is not a forum guild=%s channel=%s",
                    guild.id,
                    channel_id,
                )
                continue
            channel_indexed, channel_failed = await self._sync_job_forum_channel(
                channel_obj,
                source=source,
            )
            indexed += channel_indexed
            failed += channel_failed
        return indexed, failed

    async def _recruiting_reminder_loop(self) -> None:
        """Periodically ask stale recruiting gig posters for status updates."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self._send_due_recruiting_reminders()
            await asyncio.sleep(GIG_RECRUITING_REMINDER_CHECK_SECONDS)

    async def _send_due_recruiting_reminders(self) -> None:
        """Send Discord reminders for recruiting gigs without recent updates."""
        try:
            due_rows = await asyncio.to_thread(
                list_due_recruiting_reminders,
                settings,
                stale_days=settings.gig_recruiting_stale_days,
            )
        except Exception as exc:
            logger.warning("Failed loading due recruiting reminders: %s", exc)
            return

        for row in due_rows:
            thread_id = row.get("discord_thread_id")
            poster_id = row.get("posted_by_discord_user_id")
            engagement_id = row.get("id")
            if not thread_id or not poster_id or not engagement_id:
                continue
            try:
                thread = self.bot.get_channel(int(thread_id))
                if thread is None:
                    thread = await self.bot.fetch_channel(int(thread_id))
                if not isinstance(thread, discord.Thread):
                    continue
                age_days = int(
                    row.get("age_days") or settings.gig_recruiting_stale_days
                )
                title = str(row.get("title") or "this gig")
                message = await thread.send(
                    (
                        f"<@{poster_id}> any update on `{title}`? "
                        f"It has been recruiting with no updates for {age_days} day(s). "
                        "Please update the dashboard status to FILLED, OUTDATED, "
                        "UNKNOWN, or leave a thread reply if it is still active."
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
                await asyncio.to_thread(
                    mark_recruiting_reminder_sent,
                    settings,
                    engagement_id=str(engagement_id),
                    message_id=str(message.id),
                )
            except Exception as exc:
                logger.warning(
                    "Failed sending recruiting reminder engagement=%s thread=%s: %s",
                    engagement_id,
                    thread_id,
                    exc,
                )

    @staticmethod
    def _message_expresses_gig_interest(content: str | None) -> bool:
        """Detect conservative direct-interest replies in gig threads."""
        text = str(content or "").strip()
        if len(text) < 3:
            return False
        if GIG_INTEREST_NEGATION_RE.search(text):
            return False
        return any(pattern.search(text) for pattern in GIG_INTEREST_PATTERNS)

    async def _persist_thread_direct_interest(self, message: discord.Message) -> None:
        """Best-effort persistence for direct interest expressed in a gig thread."""
        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return
        if not self._message_expresses_gig_interest(message.content):
            return
        if getattr(message.author, "bot", False):
            return
        if thread.owner_id and message.author.id == thread.owner_id:
            return

        post = await self._read_thread_post(thread)
        if post is None:
            return

        try:
            thread_name = str(getattr(thread, "name", "") or "")
            starter_id = getattr(post.starter, "id", None) or thread.id
            engagement_id = await asyncio.to_thread(
                upsert_discord_engagement,
                settings,
                DiscordEngagementInput(
                    guild_id=str(thread.guild.id) if thread.guild else None,
                    channel_id=str(thread.parent.id) if thread.parent else None,
                    channel_name=getattr(thread.parent, "name", None),
                    posting_type=self._job_posting_type_for_thread(thread).value,
                    message_id=str(starter_id),
                    thread_id=str(thread.id),
                    posted_by_discord_user_id=(
                        str(thread.owner_id) if thread.owner_id else None
                    ),
                    title=strip_status_from_title(thread_name)
                    or thread_name
                    or f"Discord gig {thread.id}",
                    body_raw=post.starter.content or None,
                    body_normalized=post.starter.content or None,
                    posted_at=getattr(post.starter, "created_at", None),
                    status=parse_status_from_title(thread_name),
                    preserve_existing_status=True,
                ),
            )
            await asyncio.to_thread(
                upsert_discord_interest_application,
                settings,
                engagement_id=engagement_id,
                discord_user_id=str(message.author.id),
                discord_username=getattr(message.author, "name", None),
                source=EngagementApplicationSource.DIRECT_INTEREST.value,
                message_id=str(message.id),
                message_content=message.content,
            )
        except Exception as exc:
            logger.warning(
                "Failed persisting direct gig interest thread_id=%s message_id=%s: %s",
                thread.id,
                message.id,
                exc,
            )

    async def _run_auto_match_candidates_for_thread(
        self,
        *,
        thread: discord.Thread,
        trigger: Literal["thread_create", "message_create"],
    ) -> None:
        """Best-effort automatic matching for a newly created job thread."""
        # Thread creation and first-message events can both fire for the same post.
        # We mark first so only one execution path publishes match output.
        if not await self._mark_thread_auto_matched(thread.id):
            return

        guild = thread.guild

        post = await self._read_thread_post(thread)
        if post is None:
            await self._unmark_thread_auto_matched(thread.id)
            await thread.send(
                "⚠️ Could not read thread opening message. "
                "Run `/match-candidates` manually after fixing permissions."
            )
            return

        posting, _posting_metadata = await self._build_match_candidates_posting(
            post.starter
        )
        if not posting.strip():
            await self._unmark_thread_auto_matched(thread.id)
            await thread.send(
                "⚠️ Could not extract a job description from this forum post. "
                "Run `/match-candidates` manually after adding details, attachments, or links."
            )
            return

        if post.tags:
            tag_names = ", ".join(post.tags)
            posting = f"Thread tags: {tag_names}\n\n{posting}"

        try:
            requirements = await asyncio.to_thread(
                extract_job_requirements,
                posting,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
                model=settings.openai_model,
                webhook_url=settings.discord_logs_webhook_url,
            )
        except Exception as exc:
            await self._unmark_thread_auto_matched(thread.id)
            logger.warning(
                "Auto match failed while extracting requirements "
                "(server=%s thread=%s trigger=%s): %s",
                thread.guild.id if thread.guild else "unknown",
                thread.id,
                trigger,
                exc,
            )
            await thread.send(
                "⚠️ Failed to analyze this posting automatically. "
                "Run `/match-candidates` manually in this thread."
            )
            return

        if not self._has_match_requirements(requirements):
            await self._unmark_thread_auto_matched(thread.id)
            await thread.send(
                "⚠️ No useful hard/soft skills or role types could be extracted automatically. "
                "Run `/match-candidates` manually after updating the posting."
            )
            return

        try:
            search_outcome = await self._search_and_rerank_candidates(
                posting=posting,
                requirements=requirements,
                guild_id=str(thread.guild.id) if thread.guild else None,
                limit=10,
            )
        except Exception as exc:
            await self._unmark_thread_auto_matched(thread.id)
            logger.warning(
                "Auto candidate search failed (guild=%s thread=%s trigger=%s): %s",
                thread.guild.id if thread.guild else "unknown",
                thread.id,
                trigger,
                exc,
            )
            await thread.send(
                "⚠️ Automatic candidate search failed. "
                "Run `/match-candidates` manually in this thread."
            )
            return

        await self._persist_thread_engagement_match(
            thread=thread,
            starter=post.starter,
            posting=posting,
            requirements=search_outcome.effective_requirements,
            candidates=search_outcome.candidates,
            actor_discord_user_id=None,
            source=f"auto_{trigger}",
        )

        await self._publish_match_results(
            send=thread.send,
            requirements=search_outcome.effective_requirements,
            candidates=search_outcome.candidates,
            guild=guild,
            search_note=search_outcome.search_note,
        )

    async def _bulk_sync_guild_roles(
        self, guild: discord.Guild
    ) -> tuple[int, int, int]:
        """Sync discord_roles for all non-bot guild members.

        Returns (updated, skipped, failed). Per-member failures are logged and
        skipped so one bad record never aborts the full run.
        Roles in DISCORD_ROLES_EXCLUDE_FROM_SYNC (Bots, FixTweet, @everyone)
        are excluded from the stored list.
        """
        updated = 0
        skipped = 0
        failed = 0
        for member in guild.members:
            if member.bot:
                continue
            role_names = [
                r.name
                for r in member.roles
                if r.name not in DISCORD_ROLES_EXCLUDE_FROM_SYNC
            ]
            try:
                await asyncio.to_thread(
                    upsert_discord_member,
                    settings,
                    discord_user_id=str(member.id),
                    guild_id=str(guild.id),
                    discord_username=member.name,
                    display_name=member.display_name,
                    roles=role_names,
                )
                did_update = await asyncio.to_thread(
                    update_person_discord_roles,
                    settings,
                    str(member.id),
                    role_names,
                )
            except Exception as exc:
                failed += 1
                logger.warning(
                    "bulk role sync: failed for user_id=%s: %s", member.id, exc
                )
                continue
            if did_update:
                updated += 1
            else:
                skipped += 1
        return updated, skipped, failed

    def _get_role_id_cache(self) -> dict[int, dict[str, int]]:
        cache = getattr(self, "_role_id_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_role_id_cache", cache)
        return cache

    def _refresh_role_id_cache(self, guild: discord.Guild) -> None:
        excluded_names = {name.casefold() for name in DISCORD_ROLES_EXCLUDE_FROM_SYNC}
        role_id_map: dict[str, int] = {}
        sorted_roles = sorted(
            guild.roles,
            key=lambda role: (-getattr(role, "position", 0), role.id),
        )
        for role in sorted_roles:
            normalized_name = role.name.casefold()
            if normalized_name in excluded_names or normalized_name in role_id_map:
                continue
            role_id_map[normalized_name] = role.id
        self._get_role_id_cache()[guild.id] = role_id_map

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        self._refresh_role_id_cache(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        self._refresh_role_id_cache(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_update(
        self, before: discord.Role, after: discord.Role
    ) -> None:
        self._refresh_role_id_cache(after.guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._get_role_id_cache().pop(guild.id, None)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Warm caches and sync role-backed candidate data on startup."""
        async with self._startup_sync_lock:
            if self._startup_sync_done:
                return

            for guild in self.bot.guilds:
                self._refresh_role_id_cache(guild)
                try:
                    channel_ids = await self._refresh_jobs_channel_cache(guild.id)
                    logger.info(
                        "Loaded %d registered jobs channel(s) for guild=%s",
                        len(channel_ids),
                        guild.name,
                    )
                    indexed, failed = await self._sync_registered_job_forum_channels(
                        guild,
                        channel_ids,
                        source="startup",
                    )
                    logger.info(
                        "Startup job forum index: guild=%s indexed=%d failed=%d",
                        guild.name,
                        indexed,
                        failed,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed loading/indexing jobs channel registrations for guild %s: %s",
                        guild.name,
                        exc,
                    )

                try:
                    updated, skipped, failed = await self._bulk_sync_guild_roles(guild)
                    logger.info(
                        "Startup discord role sync: guild=%s updated=%d skipped=%d failed=%d",
                        guild.name,
                        updated,
                        skipped,
                        failed,
                    )
                except Exception as exc:
                    logger.warning(
                        "Startup discord role sync failed for guild %s: %s",
                        guild.name,
                        exc,
                    )

            self._startup_sync_done = True
            if self._recruiting_reminder_task is None:
                self._recruiting_reminder_task = asyncio.create_task(
                    self._recruiting_reminder_loop()
                )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        """Auto-run matching for new forum posts in registered forum channels."""
        guild = thread.guild
        parent = thread.parent
        if guild is None or not isinstance(parent, discord.ForumChannel):
            return
        if not await self._refresh_jobs_channel_cache_if_missing(guild.id):
            return

        if not self._is_jobs_channel_registered(guild.id, parent.id):
            return

        if parent.permissions_for(guild.default_role).view_channel:
            logger.info(
                "Skipping auto-match for publicly visible forum channel %s (%s) in guild %s",
                parent.id,
                parent.name,
                guild.name,
            )
            return

        owner = guild.get_member(thread.owner_id) if thread.owner_id else None
        if owner is None or owner.bot:
            return
        if not check_user_roles_with_hierarchy(owner.roles, ["Member"]):
            return

        await self._run_auto_match_candidates_for_thread(
            thread=thread,
            trigger="thread_create",
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Track direct interest replies in registered gig forum threads."""
        if getattr(message.author, "bot", False):
            return
        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return
        guild = thread.guild
        parent = thread.parent
        if guild is None or not isinstance(parent, discord.ForumChannel):
            return
        if not await self._refresh_jobs_channel_cache_if_missing(guild.id):
            return
        if not self._is_jobs_channel_registered(guild.id, parent.id):
            return
        await self._persist_thread_direct_interest(message)

    @app_commands.command(
        name="register-jobs-channel",
        description="Register a forum channel for automatic job-post matching.",
    )
    @app_commands.describe(
        channel="Forum channel to watch. Defaults to the current forum or its post thread.",
        posting_type="Default posting type for posts in this forum.",
    )
    @app_commands.choices(
        posting_type=[
            app_commands.Choice(name="Part-time / contract", value="part_time"),
            app_commands.Choice(name="Full-time", value="full_time"),
            app_commands.Choice(
                name="Part-time or full-time", value="part_time_or_full_time"
            ),
            app_commands.Choice(name="Unknown", value="unknown"),
        ]
    )
    @require_role("Steering Committee")
    async def register_jobs_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.ForumChannel | None = None,
        posting_type: str = "part_time",
    ) -> None:
        """Register a forum channel that triggers automatic candidate matching."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "⚠️ This command must be used inside a server.",
                ephemeral=True,
            )
            return

        target_channel = self._resolve_jobs_channel_target(interaction, channel)
        if target_channel is None:
            await interaction.response.send_message(
                "⚠️ Choose a forum channel or run this inside one of its post threads.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        normalized_posting_type = normalize_job_posting_type(posting_type)
        try:
            created = await asyncio.to_thread(
                register_job_post_channel,
                settings,
                guild_id=str(guild.id),
                channel_id=str(target_channel.id),
                posting_type=normalized_posting_type,
            )
            self._jobs_channels_by_guild.setdefault(guild.id, set()).add(
                target_channel.id
            )
            self._jobs_channel_types_by_guild.setdefault(guild.id, {})[
                target_channel.id
            ] = normalized_posting_type
            indexed, failed = await self._sync_job_forum_channel(
                target_channel,
                source="register_jobs_channel",
            )
        except Exception as exc:
            logger.warning(
                "Failed to register jobs channel guild=%s channel=%s: %s",
                guild.id,
                target_channel.id,
                exc,
            )
            await interaction.followup.send(
                "❌ Failed to register this channel. Please try again.",
                ephemeral=True,
            )
            return

        try:
            self._audit_command(
                interaction=interaction,
                action="crm.register_jobs_channel",
                result="success",
                metadata={
                    "guild_id": str(guild.id),
                    "channel_id": str(target_channel.id),
                    "channel_name": target_channel.name,
                    "posting_type": normalized_posting_type.value,
                    "created": created,
                },
            )
        except Exception as exc:
            logger.warning("Audit write failed for crm.register_jobs_channel: %s", exc)

        if created:
            await interaction.followup.send(
                f"✅ Registered <#{target_channel.id}> for automatic job matching "
                f"as `{normalized_posting_type.value}`. Backfilled {indexed} post(s)"
                f"{f' ({failed} failed)' if failed else ''}.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"ℹ️ <#{target_channel.id}> is already registered as "
                f"`{normalized_posting_type.value}`. Backfilled {indexed} post(s)"
                f"{f' ({failed} failed)' if failed else ''}.",
                ephemeral=True,
            )

    @app_commands.command(
        name="unregister-jobs-channel",
        description="Stop automatic job-post matching for a forum channel.",
    )
    @app_commands.describe(
        channel="Forum channel to stop watching. Defaults to the current forum or its post thread."
    )
    @require_role("Steering Committee")
    async def unregister_jobs_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.ForumChannel | None = None,
    ) -> None:
        """Unregister a forum channel from automatic candidate matching."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "⚠️ This command must be used inside a server.",
                ephemeral=True,
            )
            return

        target_channel = self._resolve_jobs_channel_target(interaction, channel)
        if target_channel is None:
            await interaction.response.send_message(
                "⚠️ Choose a forum channel or run this inside one of its post threads.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            removed = await asyncio.to_thread(
                unregister_job_post_channel,
                settings,
                guild_id=str(guild.id),
                channel_id=str(target_channel.id),
            )
            self._jobs_channels_by_guild.setdefault(guild.id, set()).discard(
                target_channel.id
            )
        except Exception as exc:
            logger.warning(
                "Failed to unregister jobs channel guild=%s channel=%s: %s",
                guild.id,
                target_channel.id,
                exc,
            )
            await interaction.followup.send(
                "❌ Failed to unregister this channel. Please try again.",
                ephemeral=True,
            )
            return

        try:
            self._audit_command(
                interaction=interaction,
                action="crm.unregister_jobs_channel",
                result="success",
                metadata={
                    "guild_id": str(guild.id),
                    "channel_id": str(target_channel.id),
                    "channel_name": target_channel.name,
                    "removed": removed,
                },
            )
        except Exception as exc:
            logger.warning(
                "Audit write failed for crm.unregister_jobs_channel: %s",
                exc,
            )

        if removed:
            await interaction.followup.send(
                f"✅ Unregistered <#{target_channel.id}> from automatic job matching.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"ℹ️ <#{target_channel.id}> was not registered.",
                ephemeral=True,
            )

    @app_commands.command(
        name="match-candidates",
        description="Rank matching candidates from this thread's job details.",
    )
    @app_commands.describe(
        private="Set to `true`, `1`, `yes`, `y`, or `on` to post privately."
    )
    @require_role("Member")
    async def match_candidates(
        self,
        interaction: discord.Interaction,
        private: str | None = None,
    ) -> None:
        """Parse the thread's starter message and find matching candidates ranked by fit.

        Must be invoked inside a thread. The starter message is used as the job posting text.
        The response is posted publicly in the thread.
        """
        is_private = _parse_match_candidates_private(private)
        if is_private is None:
            await interaction.response.send_message(
                "⚠️ Invalid value for `private`. Use `true`, `1`, `yes`, `y`, or `on`.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.Thread) or not isinstance(
            interaction.channel.parent, discord.ForumChannel
        ):
            self._audit_command_safe(
                interaction=interaction,
                action="crm.match_candidates",
                result="error",
                metadata={"stage": "not_thread"},
            )
            await interaction.response.send_message(
                "⚠️ This command must be used inside a forum post thread.",
                ephemeral=True,
            )
            return

        thread: discord.Thread = interaction.channel
        starter = thread.starter_message
        fetch_error = None
        if starter is None:
            try:
                starter = await thread.fetch_message(thread.id)
            except Exception as exc:
                fetch_error = exc
                starter = None

        if starter is None:
            metadata = {"stage": "starter_message_unavailable"}
            if fetch_error is not None:
                error_text = (
                    str(fetch_error).replace("\r", " ").replace("\n", " ").strip()
                )
                if len(error_text) > 300:
                    error_text = f"{error_text[:297]}..."
                metadata["error"] = error_text
            self._audit_command_safe(
                interaction=interaction,
                action="crm.match_candidates",
                result="error",
                metadata=metadata,
            )
            await interaction.response.send_message(
                "⚠️ Could not read the thread's opening message. "
                "Make sure the thread was created from a job posting message.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=is_private)

        posting, posting_metadata = await self._build_match_candidates_posting(starter)
        if not posting.strip():
            self._audit_command_safe(
                interaction=interaction,
                action="crm.match_candidates",
                result="error",
                metadata={
                    "stage": "starter_message_empty_after_scan",
                    **posting_metadata,
                },
            )
            await interaction.followup.send(
                "⚠️ Could not extract a job description from the thread opener, "
                "its attachments, or linked pages.",
                ephemeral=True,
            )
            return

        if thread.applied_tags:
            tag_names = ", ".join(t.name for t in thread.applied_tags)
            posting = f"Thread tags: {tag_names}\n\n{posting}"

        try:
            requirements = await asyncio.to_thread(
                extract_job_requirements,
                posting,
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
                model=settings.openai_model,
                webhook_url=settings.discord_logs_webhook_url,
            )
        except Exception as exc:
            self._audit_command_safe(
                interaction=interaction,
                action="crm.match_candidates",
                result="error",
                metadata={"stage": "extract_requirements", "error": str(exc)},
            )
            await interaction.followup.send(
                f"❌ Failed to analyze the job posting: {exc}",
                ephemeral=True,
            )
            return

        if not self._has_match_requirements(requirements):
            self._audit_command_safe(
                interaction=interaction,
                action="crm.match_candidates",
                result="error",
                metadata={"stage": "no_requirements_extracted"},
            )
            await interaction.followup.send(
                "⚠️ No useful hard/soft skills or role types could be extracted from this posting. "
                "Please include explicit requirements and try again.",
                ephemeral=True,
            )
            return

        try:
            search_outcome = await self._search_and_rerank_candidates(
                posting=posting,
                requirements=requirements,
                guild_id=str(interaction.guild.id) if interaction.guild else None,
                limit=20,
                min_match_score=8.0,
            )
        except Exception as exc:
            logger.error("Candidate search failed: %s", exc)
            self._audit_command_safe(
                interaction=interaction,
                action="crm.match_candidates",
                result="error",
                metadata={"stage": "search_candidates", "error": str(exc)},
            )
            await interaction.followup.send(
                "❌ Candidate search failed. Please try again later.",
                ephemeral=True,
            )
            return

        await self._persist_thread_engagement_match(
            thread=thread,
            starter=starter,
            posting=posting,
            requirements=search_outcome.effective_requirements,
            candidates=search_outcome.candidates,
            actor_discord_user_id=str(interaction.user.id),
            source="manual_match_candidates",
        )

        async def _send_match_result(message: str, **kwargs: Any) -> None:
            if is_private:
                kwargs["ephemeral"] = True
            await interaction.followup.send(message, **kwargs)

        await self._publish_match_results(
            send=_send_match_result,
            requirements=search_outcome.effective_requirements,
            candidates=search_outcome.candidates,
            guild=interaction.guild,
            search_note=search_outcome.search_note,
        )

        self._audit_command_safe(
            interaction=interaction,
            action="crm.match_candidates",
            result="success",
            metadata={
                "title": requirements.title,
                "hard_required_skills_count": len(requirements.hard_required_skills),
                "soft_required_skills_count": len(requirements.soft_required_skills),
                "required_skills_count": len(requirements.required_skills),
                "preferred_skills_count": len(requirements.preferred_skills),
                "required_evidence_count": len(requirements.required_evidence),
                "discord_role_types": requirements.discord_role_types,
                "candidates_returned": len(search_outcome.candidates),
                **posting_metadata,
            },
        )

    @app_commands.command(
        name="sync-discord-roles",
        description="Re-sync all server members' Discord roles into the candidate database.",
    )
    @require_role("Steering Committee")
    async def sync_discord_roles(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Manually trigger a full guild role sync (also runs automatically on startup)."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "⚠️ This command must be used inside a server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        updated, skipped, failed = await self._bulk_sync_guild_roles(guild)

        self._audit_command_safe(
            interaction=interaction,
            action="crm.sync_discord_roles",
            result="success",
            metadata={
                "updated": updated,
                "skipped_no_db_match": skipped,
                "failed": failed,
                "total_members_scanned": updated + skipped + failed,
            },
        )

        await interaction.followup.send(
            f"✅ Discord role sync complete.\n"
            f"Updated: **{updated}** · No DB match (skipped): **{skipped}** · Failed: **{failed}**",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        """Automatically sync discord roles/names on member updates."""
        if after.guild is None or after.bot:
            return
        roles_changed = before.roles != after.roles
        name_changed = (
            before.display_name != after.display_name or before.name != after.name
        )
        if not roles_changed and not name_changed:
            return

        role_names = [
            r.name for r in after.roles if r.name not in DISCORD_ROLES_EXCLUDE_FROM_SYNC
        ]

        try:
            await asyncio.to_thread(
                upsert_discord_member,
                settings,
                discord_user_id=str(after.id),
                guild_id=str(after.guild.id),
                discord_username=after.name,
                display_name=after.display_name,
                roles=role_names,
            )
            if roles_changed:
                await asyncio.to_thread(
                    update_person_discord_roles,
                    settings,
                    str(after.id),
                    role_names,
                )
        except Exception as exc:
            logger.warning(
                "on_member_update: failed to sync roles for user %s: %s",
                after.id,
                exc,
            )


async def setup(bot: commands.Bot) -> None:
    """Add the jobs cog to the bot."""
    await bot.add_cog(JobsCog(bot))
