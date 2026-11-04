"""The export URL comes from the command line or the credentials file, safely."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from cli import main as cli
from cli.doctor import CheckResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from conftest import FixtureSetup

    Handler = Callable[[httpx.Request], httpx.Response]


def _recording_handler(
    requests: list[httpx.Request],
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/rest/api/2/field"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"values": [], "screens": [], "total": 0})

    return handler


def test_export_uses_credentials_file_url_when_option_omitted(
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []
    jira_basic_auth_fixture(
        "https://jira.example.com/jira", _recording_handler(requests)
    )

    assert cli.main(["export", "export"]) == 0
    assert str(requests[0].url) == "https://jira.example.com/jira/rest/api/2/field"


def test_export_url_option_takes_precedence_over_credentials_file_url(
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []
    jira_basic_auth_fixture("https://jira.example.com", _recording_handler(requests))

    assert cli.main(["export", "--url", "https://jira.example.com/jira", "export"]) == 0
    assert str(requests[0].url) == "https://jira.example.com/jira/rest/api/2/field"


def test_export_rejects_url_on_another_host_before_touching_output(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    requests: list[httpx.Request] = []
    jira_basic_auth_fixture("https://jira.example.com", _recording_handler(requests))

    assert cli.main(["export", "--url", "https://other.example.com", "export"]) == 1
    error = capsys.readouterr().err
    assert "does not match the credentials file url" in error
    assert "scheme, host, and port must be the same" in error
    assert requests == []
    assert not (tmp_path / "export").exists()


def test_export_rejects_invalid_url_before_touching_output(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _recording_handler([]))

    assert cli.main(["export", "--url", "jira.example.com", "export"]) == 1
    assert "url must look like https://host[:port][/context]" in capsys.readouterr().err
    assert not (tmp_path / "export").exists()


def test_export_rejects_url_with_embedded_credentials(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _recording_handler([]))

    assert (
        cli.main(
            [
                "export",
                "--url",
                "https://admin:private-password@jira.example.com",
                "export",
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "url must not include a user name or password" in error
    assert "private-password" not in error
    assert not (tmp_path / "export").exists()


def test_export_rejects_unreachable_url_before_touching_output(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    jira_basic_auth_fixture: FixtureSetup,
    monkeypatch: MonkeyPatch,
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _recording_handler([]))
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda _load: [
            CheckResult("config", "pass", "https://jira.example.com as admin"),
            CheckResult("dns", "fail", "gaierror: [Errno 8] no host"),
        ],
    )

    assert cli.main(["export", "export"]) == 1
    assert "[Errno 8] no host" in capsys.readouterr().err
    assert not (tmp_path / "export").exists()


def test_export_has_no_password_argument(
    tmp_path: Path,
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _recording_handler([]))

    usage_error = 2
    assert (
        cli.main(["export", "--password", "private-password", "export"]) == usage_error
    )
    assert not (tmp_path / "export").exists()
