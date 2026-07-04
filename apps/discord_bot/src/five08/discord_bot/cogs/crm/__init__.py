"""Compatibility package for the CRM cog implementation.

The public extension/import path remains ``five08.discord_bot.cogs.crm`` while
the implementation lives in ``five08.discord_bot.cogs.crm.core``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from five08.discord_bot.cogs.crm import core as _core

_core.__package__ = __name__
_core.__path__ = [str(Path(__file__).parent)]  # type: ignore[attr-defined]
sys.modules[__name__] = _core
