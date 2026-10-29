"""CLI behavior using Jira HTTP fixtures, without a live site."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

from jira import cli

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch


def test_cli_exports_only_custom_fields(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": "summary", "name": "Summary", "custom": False},
                {"id": "customfield_10001", "name": "Team", "custom": True},
                {"id": "customfield_10002", "name": "Cost", "custom": True},
            ],
        )

    real_client = cli.JiraClient
    monkeypatch.setattr(
        cli,
        "JiraClient",
        lambda url, token: real_client(
            url,
            token,
            requests_per_minute=60_000,
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")

    assert cli.main(["https://jira.example.com/jira", str(tmp_path)]) == 0
    assert [
        json.loads(line)
        for line in (tmp_path / "fields.jsonl").read_text().splitlines()
    ] == [
        {"id": "customfield_10001", "name": "Team", "custom": True},
        {"id": "customfield_10002", "name": "Cost", "custom": True},
    ]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://jira.example.com/jira/rest/api/2/field"
    assert requests[0].headers["Authorization"] == "Bearer my-secret-pat"
    assert "2 fields exported" in capsys.readouterr().out


def test_cli_reports_api_failure_without_token(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(403, json={"errorMessages": ["Denied"]})

    real_client = cli.JiraClient
    monkeypatch.setattr(
        cli,
        "JiraClient",
        lambda url, token: real_client(
            url,
            token,
            requests_per_minute=60_000,
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "403" in output.err
    assert "/rest/api/2/field" in output.err
    assert "my-secret-pat" not in output.out + output.err
    assert not (tmp_path / "fields.jsonl").exists()


def test_cli_prompts_without_environment_token(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer prompted-pat"
        return httpx.Response(200, json=[])

    real_client = cli.JiraClient
    monkeypatch.setattr(
        cli,
        "JiraClient",
        lambda url, token: real_client(
            url,
            token,
            requests_per_minute=60_000,
            transport=httpx.MockTransport(handler),
        ),
    )
    monkeypatch.delenv("JIRA_PAT", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "prompted-pat")
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    assert "prompted-pat" not in str(capsys.readouterr())
