"""
Email monitoring cog for the 508.dev Discord bot.

This cog monitors an IMAP email inbox and forwards new messages to a Discord channel.
It supports both plain text and HTML emails, automatically chunks long messages,
and provides commands to start/stop monitoring and check status.
"""

import imaplib
import email
import logging
from textwrap import wrap
from discord.ext import commands, tasks
import discord

from five08.discord_bot.config import settings

logger = logging.getLogger(__name__)


class EmailMonitor(commands.Cog):
    """
    Email monitoring cog that polls IMAP inbox and forwards emails to Discord.

    This cog automatically starts monitoring when loaded and provides commands
    for manual control of the monitoring process.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # self.task_poll_inbox.start()

    async def cog_unload(self) -> None:
        """Cancel the background task when cog is unloaded."""
        self.task_poll_inbox.cancel()

    @tasks.loop(minutes=settings.check_email_wait)
    async def task_poll_inbox(self) -> None:
        """Poll IMAP inbox for new emails and forward them to Discord."""
        channel = self.bot.get_channel(settings.channel_id)
        if not channel or not isinstance(channel, discord.abc.Messageable):
            logger.error(f"Could not find channel {settings.channel_id}")
            return

        logger.info(f"Reading inbox of {settings.email_username}")

        # create an IMAP4 class with SSL and authenticate
        mail = imaplib.IMAP4_SSL(settings.imap_server)
        mail.login(settings.email_username, settings.email_password)

        status, messages = mail.select("INBOX")
        # get unseen messages
        (retcode, messages) = mail.search(None, "(UNSEEN)")
        if retcode == "OK" and messages[0]:
            for idx, num in enumerate(messages[0].split()):
                typ, data = mail.fetch(num.decode(), "(RFC822)")
                for response_part in data:
                    if isinstance(response_part, tuple):
                        original = email.message_from_string(
                            response_part[1].decode("utf-8")
                        )
                        received = original["Received"]
                        if received:
                            received = received.split(";")[-1]
                        else:
                            received = "Unknown"

                        logger.debug(f"From: {original['From']}")
                        logger.debug(f"Subject: {original['Subject']}")
                        logger.debug(f"Received: {received}")
                        msg_count = len(messages[0].split()) if messages[0] else 0
                        await channel.send(
                            f"{'=' * 30} Message {idx + 1} of {msg_count} {'=' * 30}"
                        )
                        await channel.send(
                            f"**FROM:** {original['From']}\n**SUBJECT:** {original['Subject']} \n**RECEIVED:** {received}"
                        )
                        if original.is_multipart():
                            # iterate over email parts
                            for part in original.walk():
                                # extract content type of email
                                content_type = part.get_content_type()
                                content_disposition = str(
                                    part.get("Content-Disposition")
                                )
                                try:
                                    # get the email body
                                    payload = part.get_payload(decode=True)
                                    if isinstance(payload, bytes):
                                        body = payload.decode()
                                    else:
                                        continue
                                except Exception:
                                    continue
                                if (
                                    content_type == "text/plain"
                                    and "attachment" not in content_disposition
                                ):
                                    # logger.debug text/plain emails and skip attachments
                                    logger.debug(wrap(body, width=3900))
                                    await channel.send("**BODY**:")
                                    for line in wrap(
                                        body,
                                        width=settings.discord_sendmsg_character_limit
                                        - 1,
                                    ):
                                        await channel.send(line)
                                elif "attachment" in content_disposition:
                                    # download attachment
                                    logger.debug("attachment case")
                        else:
                            # extract content type of email
                            content_type = original.get_content_type()
                            # get the email body
                            payload = original.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                body = payload.decode()
                            else:
                                continue
                            if content_type == "text/plain":
                                # logger.debug only text email parts
                                logger.debug(body)
                                await channel.send("**BODY**:")
                                for line in wrap(
                                    body,
                                    width=settings.discord_sendmsg_character_limit - 1,
                                ):
                                    await channel.send(line)
                            if content_type == "text/html":
                                logger.debug("html case")
                                logger.debug(body)
                                await channel.send("**BODY**:")
                                for line in wrap(
                                    body,
                                    width=settings.discord_sendmsg_character_limit - 1,
                                ):
                                    await channel.send(line)
                        logger.debug("=" * 100)
                        await channel.send("=" * 71)

                        # mark the mail as seen so it doesn't come up again
                        typ, data = mail.store(num.decode(), "+FLAGS", "\\Seen")

        msg_count = len(messages[0].split()) if messages[0] else 0
        logger.debug("Login complete, # of new messages: " + str(msg_count))

        # close the connection and logout
        mail.close()
        mail.logout()
        logger.debug("end of this iteration")

    # @app_commands.command(name="start-email", description="Start email polling task")
    # async def st(self, interaction: discord.Interaction) -> None:
    #     """Start email polling task."""
    #     await interaction.response.send_message(
    #         f"Polling for emails every {settings.check_email_wait} minutes"
    #     )
    #     if not self.task_poll_inbox.is_running():
    #         self.task_poll_inbox.start()

    # @app_commands.command(
    #     name="email-status", description="Check if email polling task is running"
    # )
    # async def is_running(self, interaction: discord.Interaction) -> None:
    #     """Check if email polling task is running."""
    #     status = "is" if self.task_poll_inbox.is_running() else "isn't"
    #     await interaction.response.send_message(
    #         f"Inbox polling task *{status}* running"
    #     )


async def setup(bot: commands.Bot) -> None:
    """Add the EmailMonitor cog to the bot."""
    cog = EmailMonitor(bot)
    await bot.add_cog(cog)
    # Slash commands will be synced automatically in bot.py
