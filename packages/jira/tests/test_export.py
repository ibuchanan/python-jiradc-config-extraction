"""CLI behavior using Jira HTTP fixtures, without a live site."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from jira import cli

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from _pytest.capture import CaptureFixture
    from _pytest.monkeypatch import MonkeyPatch


def use_http_fixture(
    monkeypatch: MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Route CLI requests to a local HTTP fixture instead of Jira."""
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

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", "my-secret-pat")

    assert cli.main(["https://jira.example.com/jira", str(tmp_path)]) == 0
    records = [
        json.loads(line)
        for line in (tmp_path / "fields.jsonl").read_text().splitlines()
    ]
    for record in records:
        assert datetime.fromisoformat(record.pop("extracted_at")).tzinfo == UTC
    assert records == [
        {
            "schema_version": 1,
            "source": {
                "system": "jira",
                "entity": "field",
                "id": "customfield_10001",
                "endpoint": "/rest/api/2/field",
            },
            "field": {"name": "Team"},
        },
        {
            "schema_version": 1,
            "source": {
                "system": "jira",
                "entity": "field",
                "id": "customfield_10002",
                "endpoint": "/rest/api/2/field",
            },
            "field": {"name": "Cost"},
        },
    ]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == "https://jira.example.com/jira/rest/api/2/field"
    assert requests[0].headers["Authorization"] == "Bearer my-secret-pat"
    assert "2 fields exported" in capsys.readouterr().out


def test_cli_omits_untrusted_metadata_and_redacts_secret_like_names(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    secret = "super-secret-fixture-value"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "customfield_2",
                    "name": f"password={secret}",
                    "custom": True,
                    "description": secret,
                    "schema": {"secret": secret},
                },
                {"id": "customfield_1", "name": "Team", "custom": True},
            ],
        )

    use_http_fixture(monkeypatch, handler)
    monkeypatch.setenv("JIRA_PAT", secret)
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    exported = (tmp_path / "fields.jsonl").read_text()
    assert secret not in exported + str(capsys.readouterr())
    records = [json.loads(line) for line in exported.splitlines()]
    assert [record["source"]["id"] for record in records] == [
        "customfield_1",
        "customfield_2",
    ]
    assert records[1]["field"] == {"name": "[REDACTED]"}


def test_cli_reports_api_failure_without_token(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(403, json={"errorMessages": ["Denied my-secret-pat"]})

    use_http_fixture(monkeypatch, handler)
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

    use_http_fixture(monkeypatch, handler)
    monkeypatch.delenv("JIRA_PAT", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "prompted-pat")
    assert cli.main(["https://jira.example.com", str(tmp_path)]) == 0
    assert "prompted-pat" not in str(capsys.readouterr())
