"""End-to-end coverage reporting at the CLI and artifact seams."""

import json
from typing import TYPE_CHECKING

import httpx

from jira import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch


def fixture(
    monkeypatch: MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = cli.JiraClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        cli,
        "JiraClient",
        lambda url, token: real_client(
            url,
            token,
            requests_per_minute=60_000,
            transport=httpx.MockTransport(respond),
        ),
    )
    monkeypatch.setenv("JIRA_PAT", "private-pat")
    return requests


def test_cli_summarizes_empty_queried_stages_and_optional_app_gaps(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(
                200, json={"version": "11.2.1", "serverTitle": "private-pat"}
            )
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith(("/customFields", "/screens")):
            return httpx.Response(200, json={"values": [], "total": 0})
        raise AssertionError(path)

    requests = fixture(monkeypatch, respond)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["jira_version"] == "11.2.1"
    assert manifest["app_versions"] == {}
    assert manifest["coverage"]["screens"] == {"status": "complete", "count": 0}
    output = capsys.readouterr().out
    assert "Extracting screens..." in output
    assert "screens: complete (0 records, not found)" in output
    assert "elements_connect: unsupported" in output
    assert "dynamic_forms: unsupported" in output
    assert "scriptrunner: unsupported" in output
    assert "private-pat" not in output + (tmp_path / "manifest.json").read_text()
    assert all(request.method == "GET" for request in requests)


def test_field_failure_reports_not_queried_stages(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/field")
        return httpx.Response(503)

    requests = fixture(monkeypatch, respond)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 1
    coverage = json.loads((tmp_path / "manifest.json").read_text())["coverage"]
    assert coverage["fields"]["status"] == "failed"
    assert coverage["screens"] == {
        "status": "not_queried",
        "reason": "fields incomplete",
    }
    assert "screens: not_queried" in capsys.readouterr().out
    assert len(requests) == 1


def test_unsupported_queried_jira_stage_is_incomplete(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(404)
        if path.endswith("/field"):
            return httpx.Response(200, json=[{"id": "customfield_1", "custom": True}])
        if path.endswith("/customFields"):
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/options"):
            return httpx.Response(404)
        if path.endswith("/screens"):
            return httpx.Response(200, json={"values": [], "total": 0})
        raise AssertionError(path)

    fixture(monkeypatch, respond)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 1
    assert "field_options: unsupported" in capsys.readouterr().out
    assert (tmp_path / "fields.jsonl").exists()


def test_cli_fails_on_required_jira_stage_and_preserves_retryable_data(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        path = request.url.path
        if path.endswith("/serverInfo"):
            return httpx.Response(403)
        if path.endswith("/field"):
            return httpx.Response(200, json=[])
        if path.endswith("/customFields"):
            calls += 1
            if calls == 1:
                return httpx.Response(403, json={"error": "private-pat"})
            return httpx.Response(200, json={"values": [], "total": 0})
        if path.endswith("/screens"):
            return httpx.Response(200, json={"values": [], "total": 0})
        raise AssertionError(path)

    requests = fixture(monkeypatch, respond)
    args = ["https://jira.example.com", str(tmp_path)]
    assert cli.main(args) != 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["jira_version"] is None
    assert manifest["coverage"]["field_applicability"]["status"] == "unauthorized"
    assert (tmp_path / "fields.jsonl").exists()
    assert "field_applicability: unauthorized" in capsys.readouterr().out
    assert cli.main(args) == 0
    expected_attempts = 2
    assert calls == expected_attempts
    assert all(request.method == "GET" for request in requests)
    assert "private-pat" not in "".join(
        path.read_text() for path in tmp_path.iterdir() if path.is_file()
    ) + str(capsys.readouterr())
