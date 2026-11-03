"""YAML credentials are composed with the Jira client only at the CLI boundary."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from cli import main as cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch

    # Import type alias from conftest
    from conftest import FixtureSetup


def test_cli_uses_yaml_user_and_password(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/rest/api/2/field"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/rest/api/2/serverInfo"):
            return httpx.Response(404)
        return httpx.Response(200, json={"values": [], "total": 0})

    jira_basic_auth_fixture("https://jira.example.com/jira", handler)

    assert cli.main(["export"]) == 0
    assert (
        json.loads((tmp_path / "export" / "manifest.json").read_text())["coverage"][
            "fields"
        ]["count"]
        == 0
    )
    assert requests[0].url.path == "/jira/rest/api/2/field"
    assert requests[0].headers["Authorization"].startswith("Basic ")
    assert "private-password" not in str(requests[0].url) + str(capsys.readouterr())


def test_cli_reports_missing_config_without_secret(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["export"]) == 1
    assert "Jira configuration unavailable" in capsys.readouterr().err
    assert not (tmp_path / "export").exists()


def test_cli_exits_with_status_2_for_two_positional_args(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Test that CLI exits with status 2 when given two positional arguments."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["https://jira.example.com", "output"])
    exit_code_for_usage_error = 2
    assert exc_info.value.code == exit_code_for_usage_error
