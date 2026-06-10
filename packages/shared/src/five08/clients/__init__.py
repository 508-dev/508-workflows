"""API clients shared across services."""

from . import authentik, discord_bot, docuseal, erpnext, espo, migadu

__all__ = [
    "authentik",
    "discord_bot",
    "docuseal",
    "erpnext",
    "espo",
    "migadu",
]
