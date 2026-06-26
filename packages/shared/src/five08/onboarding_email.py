"""Deterministic 508 onboarding email draft generation."""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape
import re
import smtplib
import ssl
from typing import Literal

TriState = Literal["yes", "no", "unknown"]

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

DISCORD_INVITE_URL = "https://discord.gg/9zAKxmUZJf"
PROSPECTIVE_MEMBERS_CHANNEL_URL = (
    "https://discord.com/channels/1336096360772141148/1336628706160017469"
)
CONTRIBUTION_REQUIREMENT_URL = "https://wiki.508.dev/s/contributing-to-508"
MEMBER_AGREEMENT_URL = "https://wiki.508.dev/s/values/doc/member-agreement-BIZFA9Xfi4"
WIKI_URL = "https://wiki.508.dev/"
ONBOARDING_INSTRUCTIONS_URL = "https://wiki.508.dev/s/onboarding"

TRI_STATE_VALUES: frozenset[str] = frozenset({"yes", "no", "unknown"})


@dataclass(frozen=True, slots=True)
class OnboardingEmailRequest:
    """Inputs that determine the onboarding email sections."""

    candidate_name: str
    sender_name: str
    has_contributed: bool
    discord_joined: TriState = "unknown"
    membership_agreement_signed: TriState = "unknown"


@dataclass(frozen=True, slots=True)
class OnboardingEmailDraft:
    """Rendered email suitable for previewing or sending."""

    subject: str
    text_body: str
    markdown_body: str
    html_body: str


@dataclass(frozen=True, slots=True)
class OnboardingEmailSmtpConfig:
    """SMTP settings needed to send onboarding email."""

    smtp_server: str | None
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    smtp_starttls: bool = False
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_timeout_seconds: float = 20.0


def build_onboarding_email(request: OnboardingEmailRequest) -> OnboardingEmailDraft:
    """Build the onboarding email draft from explicit candidate state."""
    candidate_name = _required_text(request.candidate_name, "candidate_name")
    sender_name = _required_text(request.sender_name, "sender_name")
    discord_joined = _tri_state(request.discord_joined, "discord_joined")
    agreement_signed = _tri_state(
        request.membership_agreement_signed,
        "membership_agreement_signed",
    )

    if request.has_contributed:
        paragraphs = _new_member_paragraphs(
            discord_joined=discord_joined,
            agreement_signed=agreement_signed,
        )
    else:
        paragraphs = _prospective_member_paragraphs(
            discord_joined=discord_joined,
            agreement_signed=agreement_signed,
        )

    candidate_first_name = _first_name(candidate_name)
    greeting = f"Great talking {candidate_first_name},"
    text_lines = [greeting, ""]
    markdown_lines = [greeting, ""]
    html_paragraphs = [escape(greeting)]
    for paragraph in paragraphs:
        text_lines.append(_render_text_paragraph(paragraph))
        text_lines.append("")
        markdown_lines.append(_render_markdown_paragraph(paragraph))
        markdown_lines.append("")
        html_paragraphs.append(_render_html_paragraph(paragraph))
    text_lines.extend(["Cheers,", sender_name])
    markdown_lines.extend(["Cheers,", sender_name])
    html_paragraphs.append(f"Cheers,<br>{escape(sender_name)}")

    return OnboardingEmailDraft(
        subject="508.dev onboarding",
        text_body="\n".join(text_lines).strip() + "\n",
        markdown_body="\n".join(markdown_lines).strip() + "\n",
        html_body=_render_html_document(html_paragraphs),
    )


def build_onboarding_email_message(
    *,
    recipient_email: str,
    reply_to_email: str,
    sender_name: str,
    sender_email: str,
    cc_email: str | None = None,
    subject: str,
    text_body: str,
    html_body: str,
) -> EmailMessage:
    """Build an EmailMessage for onboarding delivery."""
    normalized_sender = validate_plain_email(
        sender_email,
        "ONBOARDING_EMAIL_SENDER_EMAIL",
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, normalized_sender))
    message["To"] = validate_plain_email(recipient_email, "recipient_email")
    message["Reply-To"] = formataddr(
        (sender_name, validate_plain_email(reply_to_email, "reply_to_email"))
    )
    if cc_email:
        message["Cc"] = validate_plain_email(cc_email, "cc_email")
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def send_onboarding_email_message(
    message: EmailMessage,
    *,
    config: OnboardingEmailSmtpConfig,
) -> None:
    """Send an onboarding email through configured SMTP."""
    if not onboarding_email_smtp_ready(config):
        _validate_onboarding_email_smtp_config(config)

    tls_context = ssl.create_default_context()
    if config.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            (config.smtp_server or "").strip(),
            config.smtp_port,
            timeout=config.smtp_timeout_seconds,
            context=tls_context,
        ) as smtp:
            smtp.login(
                (config.smtp_username or "").strip(),
                (config.smtp_password or "").strip(),
            )
            smtp.send_message(message)
        return

    with smtplib.SMTP(
        (config.smtp_server or "").strip(),
        config.smtp_port,
        timeout=config.smtp_timeout_seconds,
    ) as smtp:
        if config.smtp_starttls:
            smtp.starttls(context=tls_context)
        smtp.login(
            (config.smtp_username or "").strip(),
            (config.smtp_password or "").strip(),
        )
        smtp.send_message(message)


def onboarding_email_smtp_ready(config: OnboardingEmailSmtpConfig) -> bool:
    """Return whether SMTP settings are complete enough to attempt delivery."""
    smtp_server = (config.smtp_server or "").strip()
    smtp_username = (config.smtp_username or "").strip()
    smtp_password = (config.smtp_password or "").strip()
    return bool(
        smtp_server
        and smtp_username
        and smtp_password
        and (config.smtp_use_ssl or config.smtp_starttls)
    )


def _validate_onboarding_email_smtp_config(config: OnboardingEmailSmtpConfig) -> None:
    smtp_server = (config.smtp_server or "").strip()
    smtp_username = (config.smtp_username or "").strip()
    smtp_password = (config.smtp_password or "").strip()
    if not smtp_server:
        raise ValueError("ONBOARDING_EMAIL_SMTP_SERVER is required to send.")
    if not smtp_username or not smtp_password:
        raise ValueError(
            "ONBOARDING_EMAIL_SMTP_USERNAME and "
            "ONBOARDING_EMAIL_SMTP_PASSWORD are required to send."
        )
    if not config.smtp_use_ssl and not config.smtp_starttls:
        raise ValueError(
            "Onboarding email SMTP requires TLS. Enable "
            "ONBOARDING_EMAIL_SMTP_USE_SSL or ONBOARDING_EMAIL_SMTP_STARTTLS."
        )


def validate_plain_email(value: str, field_name: str) -> str:
    """Validate a plain email address without display-name syntax."""
    normalized = value.strip()
    parsed_name, parsed_email = parseaddr(normalized)
    if parsed_name or parsed_email != normalized:
        raise ValueError(f"{field_name} must be a plain email address.")
    if not EMAIL_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a valid email address.")
    return normalized


def markdown_body_to_text(markdown_body: str) -> str:
    """Convert dashboard-edited Markdown draft to plain-text email body."""
    text = MARKDOWN_LINK_RE.sub(
        lambda match: f"{match.group(1)} ({match.group(2)})",
        markdown_body,
    )
    return text.strip() + "\n"


def markdown_body_to_html(markdown_body: str) -> str:
    """Convert dashboard-edited Markdown draft to minimal HTML email body."""
    paragraphs = [
        paragraph
        for paragraph in re.split(r"\n{2,}", markdown_body.strip())
        if paragraph.strip()
    ]
    body = "\n".join(
        f"  <p>{_markdown_fragment_to_html(paragraph)}</p>" for paragraph in paragraphs
    )
    return _render_html_document_body(body)


def _prospective_member_paragraphs(
    *,
    discord_joined: TriState,
    agreement_signed: TriState,
) -> list[list[tuple[str, str | None]]]:
    paragraphs: list[list[tuple[str, str | None]]] = [
        [("The main part of the 508 community is our Discord server.", None)],
    ]
    if discord_joined == "yes":
        paragraphs.append(
            [
                (
                    "Since you have already joined, you should be limited to the ",
                    None,
                ),
                ("#prospective-members", PROSPECTIVE_MEMBERS_CHANNEL_URL),
                (
                    " channel for now. Feel free to ask any questions there.",
                    None,
                ),
            ]
        )
    else:
        paragraphs.append(
            [
                ("The invite link you will need is the ", None),
                ("508 Discord server", DISCORD_INVITE_URL),
                (
                    ". When you join, you will be limited to the ",
                    None,
                ),
                ("#prospective-members", PROSPECTIVE_MEMBERS_CHANNEL_URL),
                (
                    " channel. Feel free to ask any questions there.",
                    None,
                ),
            ]
        )

    paragraphs.append(
        [
            (
                "In order to be a full member, we have a contribution requirement: ",
                None,
            ),
            ("contributing to 508", CONTRIBUTION_REQUIREMENT_URL),
            (".", None),
        ]
    )
    if agreement_signed != "yes":
        paragraphs.append(
            [
                ("You will also need to sign a ", None),
                ("member agreement", MEMBER_AGREEMENT_URL),
                (
                    ", which we will send to you after the contribution requirement.",
                    None,
                ),
            ]
        )
    return paragraphs


def _new_member_paragraphs(
    *,
    discord_joined: TriState,
    agreement_signed: TriState,
) -> list[list[tuple[str, str | None]]]:
    paragraphs: list[list[tuple[str, str | None]]] = [
        [("The main part of the 508 community is our Discord server.", None)],
    ]
    if discord_joined == "yes":
        paragraphs.append(
            [
                (
                    "Since you have already joined Discord, you may need to wait for "
                    "an admin to give you the Member role to see all channels in the "
                    "server.",
                    None,
                )
            ]
        )
    else:
        paragraphs.append(
            [
                ("The invite link you will need is the ", None),
                ("508 Discord server", DISCORD_INVITE_URL),
                (
                    ". When you join, you will be limited to the ",
                    None,
                ),
                ("#prospective-members", PROSPECTIVE_MEMBERS_CHANNEL_URL),
                (
                    " channel. You may need to wait for an admin to give you the "
                    "Member role to see all channels in the server.",
                    None,
                ),
            ]
        )

    paragraphs.append(
        [
            (
                "Once you have the Member role and can see all channels, make sure "
                "to introduce yourself in #new-members, and use the #roles channel "
                "to mark your technical expertise.",
                None,
            )
        ]
    )
    if agreement_signed != "yes":
        paragraphs.append(
            [
                (
                    "You will need to sign a membership agreement to fully onboard. "
                    "Look out for one in your email, or ask your 508 contact if you "
                    "do not see it.",
                    None,
                )
            ]
        )
    paragraphs.extend(
        [
            [
                (
                    "DM @caleb or @michaelmwu on Discord with the @508.dev email "
                    "you want, and a backup email to send the invitation to.",
                    None,
                )
            ],
            [
                ("You will be given access to our ", None),
                ("wiki", WIKI_URL),
                (
                    " using that email address to log in. If not, please ask an "
                    "admin to give you access.",
                    None,
                ),
            ],
            [
                (
                    "As you get settled in, feel free to ask questions in the "
                    "Discord server, particularly the #new-members channel, "
                    "especially if anything goes wrong in onboarding. Or feel free "
                    "to email me.",
                    None,
                )
            ],
            [
                ("After getting set up with the wiki, please read the ", None),
                ("onboarding instructions", ONBOARDING_INSTRUCTIONS_URL),
                (
                    ". The wiki is a great place to learn information in general "
                    "about 508.dev as well.",
                    None,
                ),
            ],
        ]
    )
    return paragraphs


def _render_text_paragraph(parts: list[tuple[str, str | None]]) -> str:
    output: list[str] = []
    for label, url in parts:
        output.append(label)
        if url is not None:
            output.append(f" ({url})")
    return "".join(output)


def _render_markdown_paragraph(parts: list[tuple[str, str | None]]) -> str:
    output: list[str] = []
    for label, url in parts:
        if url is None:
            output.append(label)
        else:
            escaped_label = label.replace("[", r"\[").replace("]", r"\]")
            output.append(f"[{escaped_label}]({url})")
    return "".join(output)


def _render_html_paragraph(parts: list[tuple[str, str | None]]) -> str:
    output: list[str] = []
    for label, url in parts:
        if url is None:
            output.append(escape(label))
        else:
            output.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
    return "".join(output)


def _render_html_document(paragraphs: list[str]) -> str:
    body = "\n".join(f"  <p>{paragraph}</p>" for paragraph in paragraphs)
    return _render_html_document_body(body)


def _render_html_document_body(body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        "  <title>508.dev onboarding</title>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _markdown_fragment_to_html(markdown_fragment: str) -> str:
    output: list[str] = []
    cursor = 0
    for match in MARKDOWN_LINK_RE.finditer(markdown_fragment):
        output.append(escape(markdown_fragment[cursor : match.start()]))
        label = match.group(1)
        url = match.group(2)
        output.append(f'<a href="{escape(url, quote=True)}">{escape(label)}</a>')
        cursor = match.end()
    output.append(escape(markdown_fragment[cursor:]))
    return "".join(output).replace("\n", "<br>")


def _required_text(value: str, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _first_name(value: str) -> str:
    normalized = _required_text(value, "candidate_name")
    return normalized.split(" ", 1)[0]


def _tri_state(value: str, field_name: str) -> TriState:
    normalized = value.strip().lower()
    if normalized not in TRI_STATE_VALUES:
        raise ValueError(f"{field_name} must be yes, no, or unknown")
    return normalized  # type: ignore[return-value]
