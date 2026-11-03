"""Shared fixtures for CLI tests using Basic auth with config file."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cli import main as cli
from cli.doctor import CheckResult

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

# Type alias for the fixture setup function
FixtureSetup = Callable[..., None]


def _with_empty_inventories(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    """Answer the project/issue-type/scheme endpoints with empty inventories.

    Tests about other stages need not mock them; pass ``defaults=False`` to a
    fixture call to handle these endpoints yourself.
    """

    def wrapped(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(("/rest/api/2/project", "/rest/api/2/issuetype")):
            return httpx.Response(200, json=[])
        if path.endswith("/rest/api/2/issuetypescheme"):
            return httpx.Response(200, json={"schemes": []})
        return handler(request)

    return wrapped


@pytest.fixture
def jira_basic_auth_fixture(tmp_path: Path, monkeypatch: MonkeyPatch) -> FixtureSetup:
    """Set up a fake .scratch/app-credentials.yaml and monkeypatch JiraClient.

    Returns a function that takes (base_url, handler_function) and returns None.
    This fixture writes credentials to .scratch/app-credentials.yaml and monkeypatches
    JiraClient to accept url, *, user, password and wrap with mock transport.
    """

    def setup(
        base_url: str,
        handler: Callable[[httpx.Request], httpx.Response],
        *,
        defaults: bool = True,
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
            url: str,
            *,
            user: str,
            password: str,
            **kwargs: Any,  # noqa: ANN401
        ) -> cli.JiraClient:
            kwargs["requests_per_minute"] = 60_000  # tests must not wait
            return real_client(
                url,
                user=user,
                password=password,
                transport=httpx.MockTransport(
                    _with_empty_inventories(handler) if defaults else handler
                ),
                **kwargs,
            )

        monkeypatch.setattr(cli, "JiraClient", make_client)

    return setup


@pytest.fixture(autouse=True)
def passing_preflight(monkeypatch: MonkeyPatch) -> None:
    """Skip real DNS/TCP/HTTP in the export preflight; doctor has its own tests."""
    results = [
        CheckResult("config", "pass", "https://jira.example.com as admin"),
        CheckResult("auth", "pass", "credentials accepted (HTTP 200)"),
    ]
    monkeypatch.setattr(cli, "run_checks", lambda _load: results)
