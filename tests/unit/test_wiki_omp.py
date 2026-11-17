"""Regression tests for removal of the unsafe local OMP launch path."""

from __future__ import annotations

import pytest

from five08.wiki_editing.omp import (
    OmpWikiAuthoringRunner,
    WikiAuthoringUnavailableError,
)


def test_legacy_local_omp_runner_is_hard_disabled() -> None:
    with pytest.raises(WikiAuthoringUnavailableError, match="Local OMP launching"):
        OmpWikiAuthoringRunner(
            omp_executable="/usr/local/bin/omp",
            omp_launcher_path="/safe/wiki-omp-launcher.sh",
            openrouter_api_key="must-not-be-used",
            model="openrouter/test",
        )
