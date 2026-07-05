"""Compatibility package for the CRM cog implementation.

The public extension/import path remains ``five08.discord_bot.cogs.crm`` while
the implementation lives in ``five08.discord_bot.cogs.crm.core``.
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import core as _core

__all__ = [name for name in dir(_core) if not name.startswith("_")]

for name in dir(_core):
    if name.startswith("__") and name.endswith("__"):
        continue
    globals()[name] = getattr(_core, name)


class _CRMPackageModule(ModuleType):
    def __getattr__(self, name: str) -> object:
        return getattr(_core, name)

    def __setattr__(self, name: str, value: object) -> None:
        if not (name.startswith("__") and name.endswith("__")) and hasattr(_core, name):
            setattr(_core, name, value)
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if not (name.startswith("__") and name.endswith("__")) and hasattr(_core, name):
            delattr(_core, name)
        super().__delattr__(name)


sys.modules[__name__].__class__ = _CRMPackageModule
