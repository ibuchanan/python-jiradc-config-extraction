"""Keep captured output deterministic regardless of the developer's terminal.

Rich fixes its colour settings when a ``Console`` is constructed, and the CLI
builds its consoles at import time. Colour-forcing environment variables from
the developer's shell would otherwise inject ANSI escapes into captured output
and break assertions on plain text.
"""

from __future__ import annotations

import os

import pytest

_COLOR_FORCING_VARIABLES = ("FORCE_COLOR", "CLICOLOR_FORCE", "TTY_COMPATIBLE")


def _use_plain_output() -> None:
    for name in _COLOR_FORCING_VARIABLES:
        os.environ.pop(name, None)
    os.environ["NO_COLOR"] = "1"


# Conftest modules load before any test module imports the CLI, so the consoles
# are already plain by the time they are built.
_use_plain_output()


@pytest.fixture(autouse=True)
def _plain_console_environment() -> None:
    """Re-apply plain output in case a test or plugin changes the environment."""
    _use_plain_output()
