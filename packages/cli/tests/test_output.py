"""Run output: preflight, output location, run state, and per-record progress dots."""

from __future__ import annotations

import re
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


def _handler(
    tab_field_failures: list[int] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    failures = tab_field_failures if tab_field_failures is not None else []

    def handler(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(
                200,
                json=[
                    {"id": "customfield_2", "custom": True, "name": "B"},
                    {"id": "customfield_1", "custom": True, "name": "A"},
                    {"id": "customfield_3", "custom": True, "name": "C"},
                ],
            )
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if "/options" in path:
            return httpx.Response(200, json={"options": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(
                200,
                json={
                    "screens": [{"id": 10, "name": "S"}, {"id": 11, "name": "T"}],
                    "total": 2,
                },
            )
        if path.endswith("/tabs"):
            return httpx.Response(200, json=[{"id": 5, "name": "A"}, {"id": 6}])
        if path.endswith("/fields"):
            if failures and path.endswith("/screens/11/tabs/5/fields"):
                failures.pop()
                return httpx.Response(503, json={"error": "down"})
            return httpx.Response(200, json=[{"id": "summary"}])
        raise AssertionError(path)

    return handler


def test_fresh_run_reports_preflight_location_state_and_dots(
    tmp_path: Path, capsys: CaptureFixture[str], jira_basic_auth_fixture: FixtureSetup
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _handler())

    assert cli.main(["export", "out"]) == 0

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("✓ Preflight: https://jira.example.com as admin")
    assert "credentials accepted" in lines[0]
    assert lines[1] == f"Output directory: {tmp_path / 'out'}"
    assert lines[2] == "Starting a fresh export (no previous run found)."
    # One dot per record written: 3 fields, 2 screens, 4 tabs, 4 tab fields.
    assert "Extracting fields... ..." in out
    assert re.search(r"(?m)^Extracting screens\.\.\. \.\.$", out)
    assert re.search(r"(?m)^Extracting screen_tabs\.\.\. \.\.\.\.$", out)
    assert re.search(r"(?m)^Extracting screen_tab_fields\.\.\. \.\.\.\.$", out)


def test_rerun_after_failure_describes_resume_and_skips_finished_stages(
    tmp_path: Path, capsys: CaptureFixture[str], jira_basic_auth_fixture: FixtureSetup
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _handler([1]))
    assert cli.main(["export", "out"]) == 1
    capsys.readouterr()

    assert cli.main(["export", "out"]) == 0

    out = capsys.readouterr().out
    assert "Resuming run" in out
    assert (
        "complete: fields, custom_fields, custom_field_options, app_inventory, "
        "associations, projects, issue_types, issue_type_schemes, "
        "project_workflow_schemes, project_issue_type_view_screens, screens"
    ) in out
    assert "partial:  screen_tab_fields (2 records kept; continues at unit 2)" in out
    assert "Skipping fields: already complete (3 records)" in out
    assert "Skipping screens: already complete (2 records)" in out
    assert re.search(
        r"(?m)^Extracting screen_tab_fields\.\.\. \(2 already written\) \.\.$", out
    )


def test_completed_export_says_nothing_will_be_fetched(
    tmp_path: Path, capsys: CaptureFixture[str], jira_basic_auth_fixture: FixtureSetup
) -> None:
    jira_basic_auth_fixture("https://jira.example.com", _handler())
    assert cli.main(["export", "out"]) == 0
    capsys.readouterr()

    assert cli.main(["export", "out"]) == 0

    assert "every stage is already complete" in capsys.readouterr().out


def test_failed_preflight_stops_before_creating_output(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    scratch = tmp_path / ".scratch"
    scratch.mkdir()
    (scratch / "app-credentials.yaml").write_text(
        "apps:\n"
        "  - name: jira-dc\n"
        "    url: https://jira.example.com\n"
        "    user: admin\n"
        "    password: private-password\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "run_checks",
        lambda _load: [
            CheckResult("config", "pass", "https://jira.example.com as admin"),
            CheckResult("dns", "fail", "gaierror: [Errno 8] no host"),
            CheckResult("auth", "skip", "dns failed"),
        ],
    )

    assert cli.main(["export", "out"]) == 1

    captured = capsys.readouterr()
    assert "Preflight check 'dns' failed" in captured.err
    assert "[Errno 8] no host" in captured.err
    assert "jira-config-export doctor" in captured.err
    assert "Output directory" not in captured.out
    assert not (tmp_path / "out").exists()


def test_rate_limit_wait_is_shown_inline_with_the_dots(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
    jira_basic_auth_fixture: FixtureSetup,
) -> None:
    inner = _handler()
    limited = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal limited
        if request.url.path.endswith("/rest/api/2/screens") and not limited:
            limited = True
            return httpx.Response(429, headers={"Retry-After": "2"})
        return inner(request)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("jira.client.asyncio.sleep", no_sleep)
    jira_basic_auth_fixture("https://jira.example.com", handler)

    assert cli.main(["export", "out"]) == 0

    captured = capsys.readouterr()
    assert re.search(r"(?m)^Extracting screens\.\.\. +\(wait:2s\) +\.\.$", captured.out)
    assert "429" not in captured.err  # the indicator replaces the log line


def test_wait_indicator_rounds_up_to_whole_seconds(
    capsys: CaptureFixture[str],
) -> None:
    cli._announce_wait(1.2)

    assert capsys.readouterr().out == " (wait:2s) "


def test_client_uses_the_pacing_constant_and_wait_indicator(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def record(url: str, **kwargs: object) -> object:
        captured.update(kwargs, url=url)
        return object()

    monkeypatch.setattr(cli, "JiraClient", record)

    cli._new_client("https://jira.example.com", user="admin", password="pw")

    assert captured["requests_per_minute"] == cli.REQUESTS_PER_MINUTE
    assert captured["on_rate_limit"] is cli._announce_wait
