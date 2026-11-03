"""Shared fixtures for CLI tests using Basic auth with config file."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx
import pytest

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

# Type alias for the fixture setup function
FixtureSetup = Callable[[str, Callable[[httpx.Request], httpx.Response]], None]


@pytest.fixture
def jira_basic_auth_fixture(tmp_path: Path, monkeypatch: MonkeyPatch) -> FixtureSetup:
    """Set up a fake .scratch/app-credentials.yaml and monkeypatch JiraClient.

    Returns a function that takes (base_url, handler_function) and returns None.
    This fixture writes credentials to .scratch/app-credentials.yaml and monkeypatches
    JiraClient to accept url, *, user, password and wrap with mock transport.
    """

    def setup(
        base_url: str, handler: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        scratch = tmp_path / ".scratch"
        scratch.mkdir(exist_ok=True)
        (scratch / "app-credentials.yaml").write_text(
            f"apps:\n"
            f"  - name: jira-dc\n"
            f"    url: {base_url}\n"
            f"    user: admin\n"
            f"    password: private-password\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        real_client = cli.JiraClient

        def make_client(
            url: str, *, user: str, password: str, **kwargs: object
        ) -> cli.JiraClient:
            return real_client(
                url,
                user=user,
                password=password,
                requests_per_minute=60_000,
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

        monkeypatch.setattr(cli, "JiraClient", make_client)

    return setup
