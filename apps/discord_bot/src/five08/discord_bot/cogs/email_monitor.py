"""Email monitoring cog for mailbox-driven resume intake workflows."""

from __future__ import annotations

import contextlib
import email
import imaplib
import logging
from typing import Any

from discord.ext import commands, tasks

from five08.discord_bot.config import settings
from five08.discord_bot.utils.resume_mail_ingest import (
    ResumeMailboxProcessor,
    ResumeMailboxResult,
)

logger = logging.getLogger(__name__)


class EmailMonitor(commands.Cog):
    """Poll an IMAP inbox and run resume intake on new messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.resume_processor = ResumeMailboxProcessor(settings)

    async def cog_load(self) -> None:
        """Start polling task when this cog is loaded."""
        if (
            settings.email_resume_intake_enabled
            and not self.task_poll_inbox.is_running()
        ):
            self.task_poll_inbox.start()

    async def cog_unload(self) -> None:
        """Cancel the background task when cog is unloaded."""
        self.task_poll_inbox.cancel()

    @tasks.loop(minutes=settings.check_email_wait)
    async def task_poll_inbox(self) -> None:
        """Poll IMAP inbox for new messages and process resume attachments."""
        logger.info("Reading inbox of %s", settings.email_username)
        mail = imaplib.IMAP4_SSL(settings.imap_server)
        try:
            mail.login(settings.email_username, settings.email_password)
            mail.select("INBOX")
            retcode, message_batches = mail.search(None, "(UNSEEN)")

            if retcode != "OK" or not message_batches or not message_batches[0]:
                logger.debug("Login complete, # of new messages: 0")
                return

            message_ids = message_batches[0].split()
            total_messages = len(message_ids)

            for index, raw_num in enumerate(message_ids, start=1):
                num = raw_num.decode()
                typ, data = mail.fetch(num, "(RFC822)")
                if typ != "OK":
                    logger.warning(
                        "Skipping message %s due to failed fetch status=%s", num, typ
                    )
                    continue

                result: ResumeMailboxResult
                try:
                    result = await self._process_fetched_message(data)
                except Exception as exc:
                    logger.exception(
                        "Failed processing inbound email num=%s error=%s", num, exc
                    )
                    result = ResumeMailboxResult(
                        sender_email=None,
                        sender_name=None,
                        processed_attachments=0,
                        skipped_reason="message_processing_error",
                    )

                self._log_processing_result(
                    result=result, index=index, total=total_messages
                )

                # Mark mail as seen to avoid reprocessing on next poll.
                mail.store(num, "+FLAGS", "\\Seen")

            logger.debug("Login complete, # of new messages: %s", total_messages)
        finally:
            with contextlib.suppress(Exception):
                mail.close()
            with contextlib.suppress(Exception):
                mail.logout()

    async def _process_fetched_message(self, data: list[Any]) -> ResumeMailboxResult:
        for response_part in data:
            if not isinstance(response_part, tuple):
                continue

            raw_payload = response_part[1]
            if not isinstance(raw_payload, (bytes, bytearray)):
                continue

            message = email.message_from_bytes(bytes(raw_payload))
            return await self.resume_processor.process_message(message)

        return ResumeMailboxResult(
            sender_email=None,
            sender_name=None,
            processed_attachments=0,
            skipped_reason="message_payload_missing",
        )

    def _log_processing_result(
        self,
        *,
        result: ResumeMailboxResult,
        index: int,
        total: int,
    ) -> None:
        sender = result.sender_email or "unknown"
        if result.skipped_reason:
            logger.info(
                "Resume inbox message %s/%s sender=%s skipped reason=%s",
                index,
                total,
                sender,
                result.skipped_reason,
            )
            return

        logger.info(
            "Resume inbox message %s/%s sender=%s processed_attachments=%s",
            index,
            total,
            sender,
            result.processed_attachments,
        )


async def setup(bot: commands.Bot) -> None:
    """Add the EmailMonitor cog to the bot."""
    cog = EmailMonitor(bot)
    await bot.add_cog(cog)
