"""Mailbox-based resume intake helpers for automated CRM updates."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr
from typing import Any
from uuid import uuid4

import aiohttp

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.discord_bot.config import Settings
from five08.discord_bot.utils.audit import DiscordAuditLogger
from five08.queue import get_postgres_connection

logger = logging.getLogger(__name__)


PRIVILEGED_ROLE_NAMES = {"admin", "steering committee", "owner"}


@dataclass(frozen=True)
class ResumeAttachment:
    """One resume-like email attachment."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class ResumeMailboxResult:
    """Result metadata for one processed mailbox message."""

    sender_email: str | None
    sender_name: str | None
    processed_attachments: int
    skipped_reason: str | None = None


class ResumeMailboxProcessor:
    """Process inbound mailbox messages into CRM resume extraction/apply flow."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.espo_api = EspoAPI(api_url, settings.espo_api_key)
        self.audit_logger = DiscordAuditLogger(
            base_url=settings.audit_api_base_url,
            shared_secret=settings.api_shared_secret,
            timeout_seconds=settings.audit_api_timeout_seconds,
        )

    async def process_message(self, message: Message) -> ResumeMailboxResult:
        """Process one email message and trigger resume extraction/apply jobs."""
        sender_name, sender_email = self._sender_identity(message)
        correlation_id = self._mailbox_correlation_id(message)

        def finalize(result: ResumeMailboxResult) -> ResumeMailboxResult:
            self._audit_mailbox_outcome(
                sender_email=sender_email,
                sender_name=sender_name,
                correlation_id=correlation_id,
                message=message,
                result=result,
            )
            return result

        if not sender_email:
            return finalize(
                ResumeMailboxResult(
                    sender_email=None,
                    sender_name=sender_name,
                    processed_attachments=0,
                    skipped_reason="missing_sender_email",
                )
            )

        if (
            self.settings.email_require_sender_auth_headers
            and not self._has_authenticated_sender(message)
        ):
            return finalize(
                ResumeMailboxResult(
                    sender_email=sender_email,
                    sender_name=sender_name,
                    processed_attachments=0,
                    skipped_reason="sender_authentication_failed",
                )
            )

        sender_is_authorized = await asyncio.to_thread(
            self._sender_is_authorized,
            sender_email,
        )
        if not sender_is_authorized:
            return finalize(
                ResumeMailboxResult(
                    sender_email=sender_email,
                    sender_name=sender_name,
                    processed_attachments=0,
                    skipped_reason="sender_not_authorized",
                )
            )

        attachments = self._extract_resume_attachments(message)
        if not attachments:
            return finalize(
                ResumeMailboxResult(
                    sender_email=sender_email,
                    sender_name=sender_name,
                    processed_attachments=0,
                    skipped_reason="no_resume_attachments",
                )
            )

        staging_contact = await asyncio.to_thread(self._find_or_create_staging_contact)
        staging_contact_id = str(staging_contact.get("id", "")).strip()
        if not staging_contact_id:
            return finalize(
                ResumeMailboxResult(
                    sender_email=sender_email,
                    sender_name=sender_name,
                    processed_attachments=0,
                    skipped_reason="staging_contact_id_missing",
                )
            )

        processed = 0
        for attachment in attachments:
            if len(attachment.content) > self._max_attachment_size_bytes:
                logger.warning(
                    "Skipping oversized resume attachment filename=%s size_bytes=%s sender=%s",
                    attachment.filename,
                    len(attachment.content),
                    sender_email,
                )
                continue

            try:
                ok = await self._process_attachment(
                    staging_contact_id=staging_contact_id,
                    attachment=attachment,
                )
            except Exception as exc:
                ok = False
                logger.exception(
                    "Failed processing resume attachment staging_contact_id=%s filename=%s sender=%s error=%s",
                    staging_contact_id,
                    attachment.filename,
                    sender_email,
                    exc,
                )

            if ok:
                processed += 1

        skipped_reason = None
        if processed == 0:
            skipped_reason = "resume_processing_failed"

        return finalize(
            ResumeMailboxResult(
                sender_email=sender_email,
                sender_name=sender_name,
                processed_attachments=processed,
                skipped_reason=skipped_reason,
            )
        )

    @property
    def _max_attachment_size_bytes(self) -> int:
        return max(1, self.settings.email_resume_max_file_size_mb) * 1024 * 1024

    @property
    def _allowed_resume_extensions(self) -> set[str]:
        raw = self.settings.email_resume_allowed_extensions
        values = {f".{item.strip().lower().lstrip('.')}" for item in raw.split(",")}
        return {item for item in values if item != "."}

    def _sender_identity(self, message: Message) -> tuple[str | None, str | None]:
        display_name, email_address = parseaddr(str(message.get("From", "")).strip())
        sender_name = display_name.strip() or None
        sender_email = self._normalize_email(email_address)
        return sender_name, sender_email

    def _has_authenticated_sender(self, message: Message) -> bool:
        """Require pass results from SPF/DKIM/DMARC headers to reduce spoof risk."""
        auth_results = str(message.get("Authentication-Results", "")).lower()
        received_spf = str(message.get("Received-SPF", "")).lower()

        dmarc_pass = "dmarc=pass" in auth_results
        dkim_pass = "dkim=pass" in auth_results
        spf_pass = "spf=pass" in auth_results or received_spf.startswith("pass")
        return dmarc_pass or (dkim_pass and spf_pass)

    def _sender_is_authorized(self, sender_email: str) -> bool:
        in_people_db = self._sender_has_privileged_role_in_people_db(sender_email)
        if not in_people_db:
            return False
        in_crm = self._sender_has_privileged_role_in_crm(sender_email)
        return in_crm

    def _sender_has_privileged_role_in_people_db(self, sender_email: str) -> bool:
        query = """
            SELECT 1
            FROM people
            WHERE sync_status = 'active'
              AND (lower(email) = %s OR lower(email_508) = %s)
              AND (
                    COALESCE(discord_roles, '[]'::jsonb) ? 'Admin'
                    OR COALESCE(discord_roles, '[]'::jsonb) ? 'Steering Committee'
                    OR COALESCE(discord_roles, '[]'::jsonb) ? 'Owner'
                )
            LIMIT 1;
        """

        with get_postgres_connection(self.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (sender_email, sender_email))
                row = cursor.fetchone()
        return row is not None

    def _sender_has_privileged_role_in_crm(self, sender_email: str) -> bool:
        sender_contact = self._find_contact_by_email(sender_email)
        if sender_contact is None:
            return False

        raw_roles = sender_contact.get("cDiscordRoles")
        parsed_roles = self._parse_role_names(raw_roles)
        return any(role in PRIVILEGED_ROLE_NAMES for role in parsed_roles)

    def _parse_role_names(self, raw_roles: Any) -> set[str]:
        parsed: list[str] = []

        if isinstance(raw_roles, list):
            parsed = [str(item).strip() for item in raw_roles]
        elif isinstance(raw_roles, str):
            parsed = [item.strip() for item in raw_roles.split(",")]
        elif isinstance(raw_roles, dict):
            parsed = [str(value).strip() for value in raw_roles.values()]

        return {value.casefold() for value in parsed if value}

    def _find_contact_by_email(self, sender_email: str) -> dict[str, Any] | None:
        search_params = {
            "where": [
                {
                    "type": "or",
                    "value": [
                        {
                            "type": "equals",
                            "attribute": "emailAddress",
                            "value": sender_email,
                        },
                        {
                            "type": "equals",
                            "attribute": "c508Email",
                            "value": sender_email,
                        },
                    ],
                }
            ],
            "maxSize": 1,
            "select": "id,name,emailAddress,c508Email,cDiscordRoles",
        }

        try:
            response = self.espo_api.request("GET", "Contact", search_params)
        except EspoAPIError as exc:
            logger.warning(
                "CRM contact lookup by email failed email=%s error=%s",
                sender_email,
                exc,
            )
            return None

        contacts = response.get("list", [])
        if not isinstance(contacts, list) or not contacts:
            return None

        first = contacts[0]
        return first if isinstance(first, dict) else None

    def _create_contact_for_email(
        self,
        email_address: str,
        display_name: str | None,
    ) -> dict[str, Any]:
        """Create a fallback contact when no existing CRM record can be resolved."""
        local_part = email_address.split("@", 1)[0]
        fallback_name = local_part.replace(".", " ").replace("_", " ").strip().title()
        payload: dict[str, Any] = {
            "name": display_name or fallback_name or "Resume Intake",
        }
        if email_address.endswith("@508.dev"):
            payload["c508Email"] = email_address
        else:
            payload["emailAddress"] = email_address

        return self.espo_api.request("POST", "Contact", payload)

    def _find_or_create_staging_contact(self) -> dict[str, Any]:
        """Resolve a stable non-sender contact used only for initial extraction."""
        staging_email = self._normalize_email(self.settings.email_username)
        if not staging_email:
            raise ValueError("EMAIL_USERNAME is required for staging contact lookup")

        existing = self._find_contact_by_email(staging_email)
        if existing is not None:
            return existing

        return self._create_contact_for_email(
            staging_email,
            "Resume Intake Staging",
        )

    def _extract_resume_attachments(self, message: Message) -> list[ResumeAttachment]:
        attachments: list[ResumeAttachment] = []
        allowed_extensions = self._allowed_resume_extensions

        for part in message.walk():
            filename = part.get_filename()
            if not filename:
                continue

            extension = self._file_extension(filename)
            if extension not in allowed_extensions:
                continue

            payload = part.get_payload(decode=True)
            if not isinstance(payload, (bytes, bytearray)) or not payload:
                continue

            attachments.append(
                ResumeAttachment(filename=filename, content=bytes(payload))
            )

        return attachments

    async def _process_attachment(
        self,
        *,
        staging_contact_id: str,
        attachment: ResumeAttachment,
    ) -> bool:
        staging_attachment_id = await asyncio.to_thread(
            self._upload_contact_resume,
            staging_contact_id,
            attachment,
        )
        if not staging_attachment_id:
            return False

        staging_extract_job_id = await self._enqueue_resume_extract_job(
            contact_id=staging_contact_id,
            attachment_id=staging_attachment_id,
            filename=attachment.filename,
        )
        staging_extract_job = await self._wait_for_worker_job_result(
            staging_extract_job_id
        )
        if staging_extract_job is None:
            return False

        staging_status = str(staging_extract_job.get("status", ""))
        if staging_status != "succeeded":
            return False

        staging_extract_result = staging_extract_job.get("result")
        if not isinstance(staging_extract_result, dict):
            return False

        if not bool(staging_extract_result.get("success", False)):
            return False

        candidate_email = self._candidate_email_from_extract_result(
            staging_extract_result
        )
        if not candidate_email:
            logger.info(
                "Skipping resume attachment filename=%s due to missing candidate email in extraction",
                attachment.filename,
            )
            return False

        candidate_contact = await asyncio.to_thread(
            self._find_contact_by_email,
            candidate_email,
        )
        if candidate_contact is None:
            candidate_contact = await asyncio.to_thread(
                self._create_contact_for_email,
                candidate_email,
                None,
            )

        candidate_contact_id = str(candidate_contact.get("id", "")).strip()
        if not candidate_contact_id:
            return False

        candidate_attachment_id = await asyncio.to_thread(
            self._upload_contact_resume,
            candidate_contact_id,
            attachment,
        )
        if not candidate_attachment_id:
            return False

        candidate_link_ok = await asyncio.to_thread(
            self._append_contact_resume,
            candidate_contact_id,
            candidate_attachment_id,
        )
        if not candidate_link_ok:
            return False

        candidate_extract_job_id = await self._enqueue_resume_extract_job(
            contact_id=candidate_contact_id,
            attachment_id=candidate_attachment_id,
            filename=attachment.filename,
        )
        candidate_extract_job = await self._wait_for_worker_job_result(
            candidate_extract_job_id
        )
        if candidate_extract_job is None:
            return False

        candidate_status = str(candidate_extract_job.get("status", ""))
        if candidate_status != "succeeded":
            return False

        candidate_extract_result = candidate_extract_job.get("result")
        if not isinstance(candidate_extract_result, dict):
            return False

        if not bool(candidate_extract_result.get("success", False)):
            return False

        proposed_updates_raw = candidate_extract_result.get("proposed_updates")
        if not isinstance(proposed_updates_raw, dict) or not proposed_updates_raw:
            return True

        proposed_updates = {
            str(field): str(value)
            for field, value in proposed_updates_raw.items()
            if value is not None and str(value).strip()
        }
        if not proposed_updates:
            return True

        apply_job_id = await self._enqueue_resume_apply_job(
            contact_id=candidate_contact_id,
            updates=proposed_updates,
        )
        apply_job = await self._wait_for_worker_job_result(apply_job_id)
        if apply_job is None:
            return False

        apply_status = str(apply_job.get("status", ""))
        if apply_status != "succeeded":
            return False

        apply_result = apply_job.get("result")
        if not isinstance(apply_result, dict):
            return False

        return bool(apply_result.get("success", False))

    def _candidate_email_from_extract_result(
        self, extract_result: dict[str, Any]
    ) -> str | None:
        extracted_profile_raw = extract_result.get("extracted_profile")
        if isinstance(extracted_profile_raw, dict):
            email_value = self._normalize_email(
                str(extracted_profile_raw.get("email", "")).strip()
            )
            if email_value:
                return email_value

        proposed_updates = extract_result.get("proposed_updates")
        if isinstance(proposed_updates, dict):
            email_value = self._normalize_email(
                str(proposed_updates.get("emailAddress", "")).strip()
            )
            if email_value:
                return email_value

        return None

    def _upload_contact_resume(
        self,
        contact_id: str,
        attachment: ResumeAttachment,
    ) -> str | None:
        try:
            uploaded = self.espo_api.upload_file(
                file_content=attachment.content,
                filename=attachment.filename,
                related_type="Contact",
                related_id=contact_id,
                field="resume",
            )
        except EspoAPIError as exc:
            logger.warning(
                "Failed uploading resume to CRM contact_id=%s filename=%s error=%s",
                contact_id,
                attachment.filename,
                exc,
            )
            return None

        attachment_id = uploaded.get("id")
        if not isinstance(attachment_id, str) or not attachment_id.strip():
            return None
        return attachment_id

    def _append_contact_resume(self, contact_id: str, attachment_id: str) -> bool:
        try:
            contact = self.espo_api.request("GET", f"Contact/{contact_id}")
            current_resume_ids = contact.get("resumeIds", [])
            if not isinstance(current_resume_ids, list):
                current_resume_ids = []

            if attachment_id not in current_resume_ids:
                current_resume_ids.append(attachment_id)

            self.espo_api.request(
                "PUT",
                f"Contact/{contact_id}",
                {"resumeIds": current_resume_ids},
            )
            return True
        except EspoAPIError as exc:
            logger.warning(
                "Failed linking resume attachment in CRM contact_id=%s attachment_id=%s error=%s",
                contact_id,
                attachment_id,
                exc,
            )
            return False

    async def _enqueue_resume_extract_job(
        self,
        *,
        contact_id: str,
        attachment_id: str,
        filename: str,
    ) -> str:
        payload = {
            "contact_id": contact_id,
            "attachment_id": attachment_id,
            "filename": filename,
        }
        return await self._enqueue_worker_job("/jobs/resume-extract", payload)

    async def _enqueue_resume_apply_job(
        self,
        *,
        contact_id: str,
        updates: dict[str, str],
    ) -> str:
        payload = {
            "contact_id": contact_id,
            "updates": updates,
            "link_discord": None,
        }
        return await self._enqueue_worker_job("/jobs/resume-apply", payload)

    async def _enqueue_worker_job(self, path: str, payload: dict[str, Any]) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._worker_url(path),
                headers=self._worker_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data = await response.json()
                if response.status != 202:
                    raise ValueError(f"Worker enqueue failed path={path}: {data}")
                job_id = data.get("job_id")
                if not isinstance(job_id, str) or not job_id.strip():
                    raise ValueError("Missing worker job_id in response")
                return job_id

    def _worker_headers(self) -> dict[str, str]:
        if not self.settings.api_shared_secret:
            raise ValueError("API_SHARED_SECRET is required for worker API requests")
        return {
            "X-API-Secret": self.settings.api_shared_secret,
            "Content-Type": "application/json",
        }

    def _worker_url(self, path: str) -> str:
        return f"{self.settings.worker_api_base_url.rstrip('/')}{path}"

    async def _wait_for_worker_job_result(
        self,
        job_id: str,
        *,
        timeout_seconds: int = 180,
        poll_seconds: float = 2.0,
    ) -> dict[str, Any] | None:
        terminal = {"succeeded", "dead", "canceled"}
        max_attempts = max(1, int(timeout_seconds / poll_seconds))

        for _ in range(max_attempts):
            job = await self._get_worker_job_status(job_id)
            status = str(job.get("status", ""))
            if status in terminal:
                return job
            await asyncio.sleep(poll_seconds)

        return None

    async def _get_worker_job_status(self, job_id: str) -> dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                self._worker_url(f"/jobs/{job_id}"),
                headers=self._worker_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data = await response.json()
                if response.status != 200:
                    raise ValueError(f"Worker job status failed: {data}")
                if not isinstance(data, dict):
                    raise ValueError("Worker job status response must be an object")
                return data

    def _file_extension(self, filename: str) -> str:
        if "." not in filename:
            return ""
        return "." + filename.rsplit(".", 1)[-1].lower().strip()

    def _normalize_email(self, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().lower()
        return normalized or None

    def _mailbox_correlation_id(self, message: Message) -> str:
        message_id = str(message.get("Message-ID", "")).strip()
        if message_id:
            return message_id
        return f"mailbox-{uuid4()}"

    def _audit_mailbox_outcome(
        self,
        *,
        sender_email: str | None,
        sender_name: str | None,
        correlation_id: str,
        message: Message,
        result: ResumeMailboxResult,
    ) -> None:
        if not sender_email:
            return

        audit_result = "error"
        if result.skipped_reason in {
            "sender_not_authorized",
            "sender_authentication_failed",
        }:
            audit_result = "denied"
        elif result.skipped_reason in {None, "no_resume_attachments"}:
            audit_result = "success"

        metadata = {
            "subject": str(message.get("Subject", "")).strip() or None,
            "mailbox_username": self.settings.email_username,
            "processed_attachments": result.processed_attachments,
            "skipped_reason": result.skipped_reason,
        }

        self.audit_logger.log_admin_sso_action(
            action="crm.resume_mailbox_ingest",
            result=audit_result,
            actor_email=sender_email,
            actor_display_name=sender_name,
            metadata=metadata,
            resource_type="mailbox_message",
            resource_id=correlation_id,
            correlation_id=correlation_id,
        )
